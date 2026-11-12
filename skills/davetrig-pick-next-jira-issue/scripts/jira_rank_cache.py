#!/usr/bin/env python3
"""Deterministic Jira snapshot and staged-analysis cache for davetrig-pick-next-jira-issue."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
DEFAULT_FILTER_ID = "21689"


def default_cache_path() -> Path:
    if override := os.environ.get("CC_PICK_NEXT_JIRA_CACHE"):
        return Path(override).expanduser()
    if xdg_cache := os.environ.get("XDG_CACHE_HOME"):
        return Path(xdg_cache).expanduser() / "cc-pick-next-jira-issue" / "cache.json"
    # dox-agent permits durable writes under ~/work but can deny ~/.cache.
    work_root = Path.home() / "work"
    cache_root = work_root / ".cache" if work_root.is_dir() else Path.home() / ".cache"
    return cache_root / "cc-pick-next-jira-issue" / "cache.json"


DEFAULT_CACHE = default_cache_path()
DEFAULT_RUBRIC = (
    Path(__file__).resolve().parent.parent / "references" / "scoring-rubric.md"
)
DEFAULT_WORKSPACE = Path(__file__).resolve().parents[2]
OVERLAP_MINUTES = 10
DIMENSIONS = (
    "decision_ambiguity",
    "implementation_uncertainty",
    "validation_uncertainty",
    "dependency_uncertainty",
    "blast_radius",
)
HARD_GATES = {
    "unresolved_decision",
    "missing_required_context",
    "blocked_dependency",
    "auth_or_sensitive_data",
    "migration_or_destructive_operation",
    "shared_or_cross_service_contract",
    "no_reliable_validation",
    "stale_or_missing_code",
    "active_work_exists",
    "unsafe_blast_radius",
    "needs_code_spike",
}
STAGES = ("summary", "ticket", "code")
KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]+-\d+$")
SHA_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")


class CacheError(RuntimeError):
    pass


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value: Any) -> str:
    payload = value if isinstance(value, bytes) else canonical(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def emit(value: Any) -> None:
    json.dump(value, sys.stdout, indent=2, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")


def require_tool(name: str) -> str:
    resolved = shutil.which(name)
    if not resolved:
        raise CacheError(f"required command not found: {name}")
    return resolved


def execution_cwd() -> Path:
    configured = Path(os.environ.get("CC_PICK_NEXT_JIRA_WORKSPACE", DEFAULT_WORKSPACE))
    if not configured.is_dir():
        raise CacheError(f"Jira workspace does not exist: {configured}")
    return configured


def run_json(argv: list[str]) -> Any:
    try:
        result = subprocess.run(
            argv,
            check=True,
            capture_output=True,
            cwd=execution_cwd(),
            text=True,
        )
    except FileNotFoundError as exc:
        raise CacheError(f"required command not found: {argv[0]}") from exc
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or str(exc)).strip()
        raise CacheError(f"command failed ({' '.join(argv[:4])}): {message}") from exc
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CacheError(
            f"command returned invalid JSON: {' '.join(argv[:4])}"
        ) from exc


def run_text(argv: list[str], *, cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(
            argv,
            check=True,
            capture_output=True,
            cwd=cwd or execution_cwd(),
            text=True,
        )
    except FileNotFoundError as exc:
        raise CacheError(f"required command not found: {argv[0]}") from exc
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or str(exc)).strip()
        raise CacheError(f"command failed ({' '.join(argv[:4])}): {message}") from exc
    return result.stdout.strip()


def rubric_hash(path: Path) -> str:
    try:
        return digest(path.read_bytes())
    except OSError as exc:
        raise CacheError(f"cannot read scoring rubric {path}: {exc}") from exc


def fresh_cache(filter_id: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "filter": {"id": filter_id},
        "last_sync_at": None,
        "rubric_hash": None,
        "items": {},
        "observed_epics": {},
    }


def secure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass


def policy_path(cache_path: Path) -> Path:
    return cache_path.parent / "policy.json"


def load_policy(cache_path: Path) -> dict[str, list[str]]:
    path = policy_path(cache_path)
    if not path.exists():
        return {"excluded_epics": [], "included_epics": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CacheError(f"cannot read policy {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise CacheError(f"invalid policy {path}")
    normalized: dict[str, list[str]] = {}
    for field in ("excluded_epics", "included_epics"):
        epics = payload.get(field, [])
        if not isinstance(epics, list) or not all(
            isinstance(key, str) and KEY_RE.match(key) for key in epics
        ):
            raise CacheError(f"invalid {field} in policy {path}")
        normalized[field] = sorted(set(epics))
    return normalized


def save_policy(cache_path: Path, policy: dict[str, list[str]]) -> None:
    path = policy_path(cache_path)
    secure_parent(path)
    fd, tmp_name = tempfile.mkstemp(prefix=".policy.", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(policy, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        path.chmod(0o600)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


@contextlib.contextmanager
def cache_lock(path: Path) -> Iterator[None]:
    secure_parent(path)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            lock_path.chmod(0o600)
        except OSError:
            pass
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def preserve_bad_cache(path: Path, suffix: str) -> None:
    stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
    target = path.with_name(f"{path.name}.{suffix}-{stamp}")
    counter = 1
    while target.exists():
        target = path.with_name(f"{path.name}.{suffix}-{stamp}-{counter}")
        counter += 1
    path.replace(target)


def load_cache(path: Path, filter_id: str = DEFAULT_FILTER_ID) -> dict[str, Any]:
    if not path.exists():
        return fresh_cache(filter_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        preserve_bad_cache(path, "corrupt")
        return fresh_cache(filter_id)
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        preserve_bad_cache(path, "schema")
        return fresh_cache(filter_id)
    if not isinstance(data.get("items"), dict):
        preserve_bad_cache(path, "corrupt")
        return fresh_cache(filter_id)
    return data


def save_cache(path: Path, data: dict[str, Any]) -> None:
    secure_parent(path)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        path.chmod(0o600)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def result_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("issues", "values", "workItems"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    raise CacheError("unexpected Jira search response shape")


def value_name(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("name", "value", "displayName"):
            if value.get(key) is not None:
                return str(value[key])
        return None
    if value is None:
        return None
    return str(value)


def metadata_fingerprint(metadata: dict[str, Any]) -> str:
    """Track candidate-visible metadata; parent policy is queue-only state."""
    return digest(
        {
            key: value
            for key, value in metadata.items()
            if key not in {"filter_position", "fingerprint", "parent_key"}
        }
    )


def normalize_issue(issue: dict[str, Any], position: int) -> dict[str, Any]:
    key = issue.get("key")
    fields = issue.get("fields") or {}
    if not isinstance(key, str) or not KEY_RE.match(key):
        raise CacheError(f"invalid or missing Jira key in search result: {key!r}")
    labels = fields.get("labels") or []
    if not isinstance(labels, list):
        labels = []
    metadata = {
        "key": key,
        "summary": str(fields.get("summary") or ""),
        "issue_type": value_name(fields.get("issuetype")),
        "status": value_name(fields.get("status")),
        "priority": value_name(fields.get("priority")),
        "labels": sorted(str(label) for label in labels),
        "assigned": fields.get("assignee") is not None,
        "parent_key": (fields.get("parent") or {}).get("key"),
        "filter_position": position,
    }
    metadata["fingerprint"] = metadata_fingerprint(metadata)
    return metadata


def adf_text(value: Any) -> str:
    """Flatten Atlassian Document Format into bounded, model-readable text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(adf_text(item) for item in value)
    if not isinstance(value, dict):
        return str(value)

    node_type = value.get("type")
    text = str(value.get("text", ""))
    content = adf_text(value.get("content", []))
    if node_type in {"hardBreak", "rule"}:
        return "\n"
    if node_type in {"paragraph", "heading", "blockquote", "listItem", "tableRow"}:
        return f"{text}{content}\n"
    if node_type in {"bulletList", "orderedList", "table", "doc"}:
        return f"{text}{content}\n"
    return f"{text}{content}"


def compact_text(value: Any) -> str:
    return re.sub(r"\n{3,}", "\n\n", adf_text(value)).strip()


EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE_RE = re.compile(
    r"(?<!\w)(?:\+?\d{1,3}[ .-]?)?(?:\(?\d{3}\)?[ .-]?)\d{3}[ .-]?\d{4}(?!\w)"
)
USER_ID_RE = re.compile(
    r"\b((?:internal\s+)?(?:user|member|patient)\s*(?:id|identifier)\s*[:=#]?\s*)[A-Za-z0-9_-]{3,}\b",
    re.IGNORECASE,
)


def redact_issue_text(value: Any) -> tuple[str, list[str]]:
    """Remove common direct identifiers before issue text leaves the CLI."""
    text = compact_text(value)
    redactions: list[str] = []
    for pattern, replacement, label in (
        (EMAIL_RE, "[REDACTED_EMAIL]", "email"),
        (PHONE_RE, "[REDACTED_PHONE]", "phone"),
        (USER_ID_RE, r"\1[REDACTED_ID]", "user_id"),
    ):
        text, count = pattern.subn(replacement, text)
        if count:
            redactions.append(label)
    return text, redactions


def normalized_link_target(
    target: dict[str, Any], relationship: str | None
) -> dict[str, Any]:
    fields = target.get("fields") or {}
    return {
        "relationship": relationship,
        "key": target.get("key"),
        "summary": fields.get("summary"),
        "status": value_name(fields.get("status")),
    }


def normalize_issue_detail(payload: dict[str, Any]) -> dict[str, Any]:
    fields = payload.get("fields") or {}
    comments = (fields.get("comment") or {}).get("comments") or []
    parent = fields.get("parent") or {}
    parent_fields = parent.get("fields") or {}
    subtasks = fields.get("subtasks") or []
    description, description_redactions = redact_issue_text(fields.get("description"))
    normalized_comments = []
    redactions = list(description_redactions)
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        body, body_redactions = redact_issue_text(comment.get("body"))
        redactions.extend(body_redactions)
        normalized_comments.append(
            {
                "created": comment.get("created"),
                "updated": comment.get("updated"),
                "body": body,
            }
        )
    links: dict[str, list[dict[str, Any]]] = {
        "blocks": [],
        "blocked_by": [],
        "related": [],
    }
    for link in fields.get("issuelinks") or []:
        if not isinstance(link, dict):
            continue
        link_type = link.get("type") or {}
        outward = link.get("outwardIssue")
        inward = link.get("inwardIssue")
        outward_label = str(link_type.get("outward") or value_name(link_type) or "")
        inward_label = str(link_type.get("inward") or value_name(link_type) or "")
        if isinstance(outward, dict):
            bucket = "blocks" if "block" in outward_label.lower() else "related"
            links[bucket].append(normalized_link_target(outward, outward_label or None))
        if isinstance(inward, dict):
            bucket = "blocked_by" if "block" in inward_label.lower() else "related"
            links[bucket].append(normalized_link_target(inward, inward_label or None))
    return {
        "key": payload.get("key"),
        "fields": {
            "summary": fields.get("summary"),
            "issue_type": value_name(fields.get("issuetype")),
            "status": value_name(fields.get("status")),
            "priority": value_name(fields.get("priority")),
            "labels": fields.get("labels") or [],
            "created": fields.get("created"),
            "updated": fields.get("updated"),
            "description": description,
            "comments": normalized_comments,
            "links": links,
            "redactions": sorted(set(redactions)),
            "parent": {
                "key": parent.get("key"),
                "summary": parent_fields.get("summary"),
            }
            if parent
            else None,
            "subtasks": [
                {
                    "key": subtask.get("key"),
                    "summary": (subtask.get("fields") or {}).get("summary"),
                }
                for subtask in subtasks
                if isinstance(subtask, dict)
            ],
        },
    }


def invalidate_item(item: dict[str, Any], stage: str) -> list[str]:
    if stage not in STAGES:
        raise CacheError(f"invalid stage: {stage}")
    index = STAGES.index(stage)
    removed: list[str] = []
    stages = item.setdefault("stages", {})
    for candidate in STAGES[index:]:
        if candidate in stages:
            stages.pop(candidate)
            removed.append(candidate)
    return removed


def stage_fresh(item: dict[str, Any], stage: str, current_rubric: str) -> bool:
    record = (item.get("stages") or {}).get(stage)
    if not isinstance(record, dict) or record.get("rubric_hash") != current_rubric:
        return False
    if stage == "summary":
        return record.get("source_fingerprint") == (item.get("metadata") or {}).get(
            "fingerprint"
        )
    if stage == "ticket":
        revision = item.get("jira_updated")
        return bool(revision) and record.get("source_revision") == revision
    ticket = (item.get("stages") or {}).get("ticket")
    if not isinstance(ticket, dict) or record.get("source_revision") != ticket.get(
        "record_hash"
    ):
        return False
    for repo in (record.get("analysis") or {}).get("repositories") or []:
        path = repo.get("path")
        ref = repo.get("default_ref")
        saved_sha = repo.get("local_sha")
        if not path or not ref or not saved_sha:
            return False
        try:
            current_sha = subprocess.run(
                ["git", "-C", path, "rev-parse", ref],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        except (FileNotFoundError, subprocess.CalledProcessError):
            return False
        if current_sha != saved_sha:
            return False
    return True


def stage_stale_reason(item: dict[str, Any], stage: str, current_rubric: str) -> str:
    """Return the first actionable reason a staged record cannot be reused."""
    record = (item.get("stages") or {}).get(stage)
    if not isinstance(record, dict):
        return f"no {stage}-stage record"
    if record.get("rubric_hash") != current_rubric:
        return "scoring rubric changed"
    if stage == "summary":
        if record.get("source_fingerprint") != (item.get("metadata") or {}).get(
            "fingerprint"
        ):
            return "Jira summary metadata changed"
        return "summary record is not reusable"
    if stage == "ticket":
        if not stage_fresh(item, "summary", current_rubric):
            return f"summary stage is stale: {stage_stale_reason(item, 'summary', current_rubric)}"
        if not item.get("jira_updated"):
            return "Jira detail has not been fetched"
        if record.get("source_revision") != item.get("jira_updated"):
            return "Jira issue revision changed"
        return "ticket record is not reusable"
    if not stage_fresh(item, "ticket", current_rubric):
        return f"ticket stage is stale: {stage_stale_reason(item, 'ticket', current_rubric)}"
    if record.get("source_revision") != (
        (item.get("stages") or {}).get("ticket") or {}
    ).get("record_hash"):
        return "ticket analysis changed"
    for repo in (record.get("analysis") or {}).get("repositories") or []:
        path, ref, saved_sha = (
            repo.get("path"),
            repo.get("default_ref"),
            repo.get("local_sha"),
        )
        if not path or not ref or not saved_sha:
            return "repository snapshot is incomplete"
        try:
            current_sha = run_text(["git", "-C", path, "rev-parse", ref])
        except CacheError:
            return "repository default ref is unavailable"
        if current_sha != saved_sha:
            return "repository default ref changed"
    return "code record is not reusable"


def jira_search(
    *, filter_id: str | None = None, jql: str | None = None
) -> list[dict[str, Any]]:
    argv = [
        require_tool("acli"),
        "jira",
        "workitem",
        "search",
        "--fields",
        "key,summary,issuetype,status,priority,labels,assignee",
        "--paginate",
        "--json",
    ]
    if filter_id:
        argv.extend(["--filter", filter_id])
    elif jql:
        argv.extend(["--jql", jql])
    else:
        raise CacheError("jira_search requires filter_id or jql")
    return result_items(run_json(argv))


def sync(args: argparse.Namespace) -> None:
    cache_path = Path(args.cache).expanduser()
    rubric = rubric_hash(Path(args.rubric))
    started = utc_now()
    with cache_lock(cache_path):
        cache = load_cache(cache_path, args.filter_id)
        policy = load_policy(cache_path)
        filter_payload = run_json(
            [
                require_tool("acli"),
                "jira",
                "filter",
                "view",
                "--json",
            ]
        )
        if not isinstance(filter_payload, dict) or not filter_payload.get("jql"):
            raise CacheError(f"Jira filter {args.filter_id} returned no JQL")

        raw_members = jira_search(filter_id=args.filter_id)
        members = [
            normalize_issue(issue, position)
            for position, issue in enumerate(raw_members)
        ]
        excluded_parent_by_key: dict[str, str] = {}
        for epic in policy["excluded_epics"]:
            children = jira_search(jql=f"filter = {args.filter_id} AND parent = {epic}")
            for child in children:
                key = child.get("key")
                if isinstance(key, str):
                    excluded_parent_by_key[key] = epic
        for member in members:
            member["parent_key"] = excluded_parent_by_key.get(member["key"])
            member["fingerprint"] = metadata_fingerprint(member)
        member_by_key = {item["key"]: item for item in members}

        last_sync = cache.get("last_sync_at")
        if last_sync:
            since = parse_iso(last_sync) - dt.timedelta(minutes=OVERLAP_MINUTES)
            jira_since = since.astimezone().strftime("%Y-%m-%d %H:%M")
            delta_jql = f'filter = {args.filter_id} AND updated >= "{jira_since}"'
            changed_keys = {issue.get("key") for issue in jira_search(jql=delta_jql)}
        else:
            changed_keys = set(member_by_key)

        cache_items = cache.setdefault("items", {})
        new_count = changed_count = removed_count = reactivated_count = 0
        current_keys = set(member_by_key)

        for key, item in cache_items.items():
            if item.get("active") and key not in current_keys:
                item["active"] = False
                item["last_seen_at"] = iso(started)
                removed_count += 1

        for key, metadata in member_by_key.items():
            existing = cache_items.get(key)
            if existing is None:
                cache_items[key] = {
                    "active": True,
                    "first_seen_at": iso(started),
                    "last_seen_at": iso(started),
                    "jira_updated": None,
                    "metadata": metadata,
                    "stages": {},
                }
                new_count += 1
                continue

            was_active = bool(existing.get("active"))
            old_metadata = existing.get("metadata") or {}
            # Parent-epic policy is queue-only state: changing it must not throw
            # away summary analysis. Rehash old schema records compatibly.
            old_metadata["parent_key"] = metadata["parent_key"]
            old_metadata["fingerprint"] = metadata_fingerprint(old_metadata)
            existing["metadata"] = old_metadata
            old_fingerprint = old_metadata.get("fingerprint")
            metadata_changed = old_fingerprint != metadata["fingerprint"]
            jira_changed = key in changed_keys
            existing["active"] = True
            existing["last_seen_at"] = iso(started)
            existing["metadata"] = metadata
            if not was_active:
                reactivated_count += 1
                invalidate_item(existing, "ticket")
                existing["jira_updated"] = None
            if metadata_changed:
                invalidate_item(existing, "summary")
            elif jira_changed:
                invalidate_item(existing, "ticket")
            if metadata_changed or jira_changed:
                existing["jira_updated"] = None
                changed_count += 1

        filter_info = {
            "id": str(filter_payload.get("id") or args.filter_id),
            "name": str(filter_payload.get("name") or ""),
            "jql": str(filter_payload["jql"]),
        }
        filter_info["fingerprint"] = digest(filter_info["jql"])
        prior_excluded = set(cache.get("excluded_by_epic_keys", []))
        current_excluded = set(excluded_parent_by_key)
        cache["schema_version"] = SCHEMA_VERSION
        cache["filter"] = filter_info
        cache["rubric_hash"] = rubric
        cache["last_sync_at"] = iso(started)
        cache["excluded_by_epic_keys"] = sorted(current_excluded)
        save_cache(cache_path, cache)

        reusable = {stage: 0 for stage in STAGES}
        for item in cache_items.values():
            if item.get("active"):
                for stage in STAGES:
                    reusable[stage] += int(stage_fresh(item, stage, rubric))

        emit(
            {
                "cache_path": str(cache_path),
                "filter": filter_info,
                "current_count": len(current_keys),
                "new_count": new_count,
                "changed_count": changed_count,
                "removed_count": removed_count,
                "reactivated_count": reactivated_count,
                "reusable": reusable,
                "excluded_by_epic": len(excluded_parent_by_key),
                "newly_suppressed": sorted(current_excluded - prior_excluded),
                "newly_unsuppressed": sorted(prior_excluded - current_excluded),
                "last_sync_at": cache["last_sync_at"],
            }
        )


def issue(args: argparse.Namespace) -> None:
    if not KEY_RE.match(args.key):
        raise CacheError(f"invalid Jira key: {args.key}")
    cache_path = Path(args.cache).expanduser()
    payload = run_json(
        [
            require_tool("acli"),
            "jira",
            "workitem",
            "view",
            args.key,
            "--fields",
            "key,summary,issuetype,status,assignee,description,comment,issuelinks,labels,priority,updated,created,parent,subtasks",
            "--json",
        ]
    )
    fields = payload.get("fields") or {}
    current_updated = fields.get("updated")
    with cache_lock(cache_path):
        cache = load_cache(cache_path)
        item = cache.get("items", {}).get(args.key)
        if not item:
            raise CacheError(f"{args.key} is not present in the cache; run sync first")
        prior_updated = item.get("jira_updated")
        if prior_updated and current_updated and prior_updated != current_updated:
            invalidate_item(item, "ticket")
        item["jira_updated"] = current_updated
        item["last_issue_fetch_at"] = iso(utc_now())

        parent_key = (fields.get("parent") or {}).get("key")
        if isinstance(parent_key, str) and KEY_RE.match(parent_key):
            observed = cache.setdefault("observed_epics", {})
            entry = observed.setdefault(
                parent_key,
                {"first_seen_at": iso(utc_now()), "seen_count": 0},
            )
            entry["last_seen_at"] = iso(utc_now())
            entry["seen_count"] = int(entry.get("seen_count", 0)) + 1

        current_metadata = normalize_issue(
            payload, (item.get("metadata") or {}).get("filter_position", 0)
        )
        old_fingerprint = (item.get("metadata") or {}).get("fingerprint")
        if current_metadata["fingerprint"] != old_fingerprint:
            item["metadata"] = current_metadata
            invalidate_item(item, "summary")
        save_cache(cache_path, cache)
    emit(normalize_issue_detail(payload))


def git_ref_exists(path: Path, ref: str) -> bool:
    return (
        subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--verify", "--quiet", ref],
            capture_output=True,
            check=False,
            text=True,
        ).returncode
        == 0
    )


def resolve_default_ref(path: Path, requested: str | None) -> str:
    if requested:
        if not re.fullmatch(r"origin/[A-Za-z0-9._/-]+", requested):
            raise CacheError(f"invalid default ref: {requested}")
        return requested
    symbolic = subprocess.run(
        ["git", "-C", str(path), "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"],
        capture_output=True,
        check=False,
        text=True,
    )
    if symbolic.returncode == 0:
        ref = symbolic.stdout.strip().removeprefix("refs/remotes/")
        if ref.startswith("origin/"):
            return ref
    for ref in ("origin/master", "origin/main"):
        if git_ref_exists(path, ref):
            return ref
    raise CacheError(f"cannot resolve origin/main or origin/master in {path}")


def repo_snapshot(args: argparse.Namespace) -> None:
    path = Path(args.path).expanduser().resolve()
    if not (path / ".git").exists():
        raise CacheError(f"not a Git repository: {path}")
    ref = resolve_default_ref(path, args.default_ref)
    branch = ref.removeprefix("origin/")
    if args.refresh:
        run_text(
            [
                "git",
                "-C",
                str(path),
                "fetch",
                "--quiet",
                "origin",
                f"refs/heads/{branch}:refs/remotes/{ref}",
            ]
        )
    local_sha = run_text(["git", "-C", str(path), "rev-parse", ref])
    remote_line = run_text(
        [
            "git",
            "-C",
            str(path),
            "ls-remote",
            "--exit-code",
            "origin",
            f"refs/heads/{branch}",
        ]
    )
    remote_sha = remote_line.split(maxsplit=1)[0]
    emit(
        {
            "name": path.name,
            "path": str(path),
            "default_ref": ref,
            "local_sha": local_sha,
            "remote_sha": remote_sha,
            "fresh": local_sha.lower() == remote_sha.lower(),
            "refreshed": args.refresh,
        }
    )


def github_slug(path: Path) -> str:
    remote = run_text(["git", "-C", str(path), "remote", "get-url", "origin"])
    match = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", remote)
    if not match:
        raise CacheError(f"origin is not a GitHub repository: {remote}")
    return match.group(1)


def matching_branch_heads(path: Path, key: str) -> dict[str, list[str]]:
    refs = run_text(
        [
            "git",
            "-C",
            str(path),
            "for-each-ref",
            "--format=%(refname:short)",
            "refs/heads",
            "refs/remotes",
        ]
    ).splitlines()
    exact_key = re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(key)}(?![A-Za-z0-9])", re.IGNORECASE
    )
    local = {
        ref for ref in refs if not ref.startswith("origin/") and exact_key.search(ref)
    }
    remote = {
        ref.removeprefix("origin/")
        for ref in refs
        if ref.startswith("origin/")
        and exact_key.search(ref)
        and not ref.endswith("/HEAD")
    }
    return {"local": sorted(local), "remote": sorted(remote)}


def pr_candidates(args: argparse.Namespace) -> None:
    if not KEY_RE.match(args.key):
        raise CacheError(f"invalid Jira key: {args.key}")
    paths: list[Path] = []
    if args.path:
        paths = [Path(value).expanduser().resolve() for value in args.path]
    else:
        cache_path = Path(args.cache).expanduser()
        with cache_lock(cache_path):
            cache = load_cache(cache_path)
            item = cache.get("items", {}).get(args.key) or {}
            analysis = ((item.get("stages") or {}).get("ticket") or {}).get(
                "analysis", {}
            )
            root = Path(args.root).expanduser()
            paths = [root / name for name in analysis.get("inferred_repositories", [])]
    if not paths:
        raise CacheError(
            "provide --path or record inferred_repositories before PR lookup"
        )

    repositories: list[dict[str, Any]] = []
    pull_requests: list[dict[str, Any]] = []
    for path in paths:
        if not (path / ".git").exists():
            repositories.append({"path": str(path), "error": "not a Git repository"})
            continue
        slug = args.repo or github_slug(path)
        matched = matching_branch_heads(path, args.key)
        heads = sorted(set(matched["local"] + matched["remote"]))
        repo_prs: list[dict[str, Any]] = []
        for head in heads:
            payload = run_json(
                [
                    require_tool("gh"),
                    "pr",
                    "list",
                    "--repo",
                    slug,
                    "--head",
                    head,
                    "--state",
                    "open",
                    "--json",
                    "number,url,headRefName",
                ]
            )
            if not isinstance(payload, list):
                raise CacheError("gh pr list returned an unexpected response")
            for pr in payload:
                if isinstance(pr, dict) and pr not in repo_prs:
                    repo_prs.append(pr)
                if isinstance(pr, dict) and pr not in pull_requests:
                    pull_requests.append(pr)
        repositories.append(
            {
                "name": path.name,
                "path": str(path),
                "repo": slug,
                "branch_heads": heads,
                "local_branch_heads": matched["local"],
                "remote_branch_heads": matched["remote"],
                "pull_requests": repo_prs,
            }
        )
    emit(
        {
            "key": args.key,
            "repositories": repositories,
            "pull_requests": pull_requests,
        }
    )


def bounded_string(value: Any, field: str, limit: int = 500) -> str:
    if not isinstance(value, str):
        raise CacheError(f"{field} must be a string")
    value = value.strip()
    if len(value) > limit:
        raise CacheError(f"{field} exceeds {limit} characters")
    return value


def bounded_strings(value: Any, field: str, count: int, length: int) -> list[str]:
    if not isinstance(value, list) or len(value) > count:
        raise CacheError(f"{field} must be an array of at most {count} strings")
    return [bounded_string(item, field, length) for item in value]


def score(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
        raise CacheError(f"{field} must be an integer from 1 to 5")
    return value


def validate_summary(analysis: Any) -> dict[str, Any]:
    if not isinstance(analysis, dict):
        raise CacheError("analysis must be an object")
    disposition = analysis.get("disposition")
    if disposition not in {"promising", "uncertain", "deprioritized"}:
        raise CacheError(
            "summary disposition must be promising, uncertain, or deprioritized"
        )
    return {
        "disposition": disposition,
        "preliminary_complexity": score(
            analysis.get("preliminary_complexity"), "preliminary_complexity"
        ),
        "assessment_confidence": score(
            analysis.get("assessment_confidence"), "assessment_confidence"
        ),
        "reasons": bounded_strings(analysis.get("reasons", []), "reasons", 5, 300),
    }


def validate_gates(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 10:
        raise CacheError("hard_gates must be an array of at most 10 objects")
    gates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for gate in value:
        if not isinstance(gate, dict) or gate.get("code") not in HARD_GATES:
            raise CacheError(f"invalid hard gate: {gate!r}")
        code = str(gate["code"])
        if code in seen:
            continue
        seen.add(code)
        gates.append(
            {
                "code": code,
                "reason": bounded_string(
                    gate.get("reason", ""), "hard gate reason", 500
                ),
                "refinable": bool(gate.get("refinable", False)),
            }
        )
    return gates


def validate_analysis(analysis: Any, stage: str) -> dict[str, Any]:
    if not isinstance(analysis, dict):
        raise CacheError("analysis must be an object")
    dimensions = analysis.get("dimensions")
    if not isinstance(dimensions, dict) or set(dimensions) != set(DIMENSIONS):
        raise CacheError(f"dimensions must contain exactly: {', '.join(DIMENSIONS)}")
    result: dict[str, Any] = {
        "hard_gates": validate_gates(analysis.get("hard_gates", [])),
        "dimensions": {name: score(dimensions[name], name) for name in DIMENSIONS},
        "assessment_confidence": score(
            analysis.get("assessment_confidence"), "assessment_confidence"
        ),
        "refinement_confidence": score(
            analysis.get(
                "refinement_confidence", analysis.get("assessment_confidence")
            ),
            "refinement_confidence",
        ),
        "inferred_repositories": bounded_strings(
            analysis.get("inferred_repositories", []), "inferred_repositories", 10, 100
        ),
        "implementation_path": bounded_string(
            analysis.get("implementation_path", ""), "implementation_path", 500
        ),
        "validation_path": bounded_string(
            analysis.get("validation_path", ""), "validation_path", 500
        ),
        "evidence": bounded_strings(analysis.get("evidence", []), "evidence", 8, 300),
        "readiness_annotations": bounded_strings(
            analysis.get("readiness_annotations", []), "readiness_annotations", 5, 300
        ),
        "refinement_questions": bounded_strings(
            analysis.get("refinement_questions", []), "refinement_questions", 8, 500
        ),
    }
    if stage == "code":
        repositories = analysis.get("repositories", [])
        if (
            not isinstance(repositories, list)
            or not repositories
            or len(repositories) > 10
        ):
            raise CacheError(
                "repositories must be a non-empty array of at most 10 objects"
            )
        normalized_repos: list[dict[str, str]] = []
        stale_repo = False
        for repo in repositories:
            if not isinstance(repo, dict):
                raise CacheError("each repository must be an object")
            normalized = {
                "name": bounded_string(repo.get("name", ""), "repository name", 100),
                "path": bounded_string(repo.get("path", ""), "repository path", 500),
                "default_ref": bounded_string(
                    repo.get("default_ref", ""), "default_ref", 100
                ),
                "local_sha": bounded_string(repo.get("local_sha", ""), "local_sha", 64),
                "remote_sha": bounded_string(
                    repo.get("remote_sha", ""), "remote_sha", 64
                ),
            }
            if not SHA_RE.match(normalized["local_sha"]) or not SHA_RE.match(
                normalized["remote_sha"]
            ):
                stale_repo = True
            if normalized["local_sha"].lower() != normalized["remote_sha"].lower():
                stale_repo = True
            normalized_repos.append(normalized)
        active_prs = bounded_strings(
            analysis.get("active_prs", []), "active_prs", 10, 500
        )
        result.update(
            {
                "repositories": normalized_repos,
                "active_prs": active_prs,
                "runtime_profile": bounded_string(
                    analysis.get("runtime_profile", "dox-agent-v1"),
                    "runtime_profile",
                    100,
                ),
            }
        )
        gate_codes = {gate["code"] for gate in result["hard_gates"]}
        if stale_repo and "stale_or_missing_code" not in gate_codes:
            raise CacheError(
                "stale/missing repository SHA requires stale_or_missing_code"
            )
        if active_prs and "active_work_exists" not in gate_codes:
            raise CacheError("active_prs requires active_work_exists")
        if (
            "stale_or_missing_code" in gate_codes
            and result["assessment_confidence"] >= 4
        ):
            raise CacheError(
                "stale_or_missing_code requires assessment_confidence below 4"
            )
    return result


def load_records(path: str) -> list[dict[str, Any]]:
    if path == "-":
        payload = json.load(sys.stdin)
    else:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list) or not payload:
        raise CacheError("record input must be a non-empty object or array")
    if not all(isinstance(record, dict) for record in payload):
        raise CacheError("every record must be an object")
    return payload


def record(args: argparse.Namespace) -> None:
    cache_path = Path(args.cache).expanduser()
    rubric = rubric_hash(Path(args.rubric))
    records = load_records(args.input)
    with cache_lock(cache_path):
        cache = load_cache(cache_path)
        saved: list[str] = []
        for supplied in records:
            key = supplied.get("key")
            if not isinstance(key, str) or not KEY_RE.match(key):
                raise CacheError(f"invalid Jira key: {key!r}")
            item = cache.get("items", {}).get(key)
            if not item or not item.get("active"):
                raise CacheError(f"{key} is not an active cached filter member")
            if args.stage == "summary":
                validated = validate_summary(supplied.get("analysis"))
                source = (item.get("metadata") or {}).get("fingerprint")
            elif args.stage == "ticket":
                if not stage_fresh(item, "summary", rubric):
                    raise CacheError(
                        f"{key} needs a fresh summary-stage record before ticket: "
                        f"{stage_stale_reason(item, 'summary', rubric)}"
                    )
                if not item.get("jira_updated"):
                    raise CacheError(
                        f"{key} has no Jira revision; run issue --key {key} first"
                    )
                validated = validate_analysis(supplied.get("analysis"), "ticket")
                source = item["jira_updated"]
            else:
                ticket = (item.get("stages") or {}).get("ticket")
                if not ticket or not stage_fresh(item, "ticket", rubric):
                    raise CacheError(
                        f"{key} needs a fresh ticket-stage record before code: "
                        f"{stage_stale_reason(item, 'ticket', rubric)}"
                    )
                validated = validate_analysis(supplied.get("analysis"), "code")
                source = ticket["record_hash"]
            saved_record = {
                "recorded_at": iso(utc_now()),
                "rubric_hash": rubric,
                "analysis": validated,
            }
            if args.stage == "summary":
                saved_record["source_fingerprint"] = source
            else:
                saved_record["source_revision"] = source
            saved_record["record_hash"] = digest(saved_record["analysis"])
            item.setdefault("stages", {})[args.stage] = saved_record
            if args.stage != "code":
                next_index = STAGES.index(args.stage) + 1
                for deeper in STAGES[next_index:]:
                    item["stages"].pop(deeper, None)
            saved.append(key)
        cache["rubric_hash"] = rubric
        save_cache(cache_path, cache)
    emit({"stage": args.stage, "saved": saved, "count": len(saved)})


def summary_sort(item: dict[str, Any], rubric: str) -> tuple[Any, ...]:
    summary = (item.get("stages") or {}).get("summary") or {}
    analysis = summary.get("analysis") or {}
    disposition_rank = {"promising": 0, "uncertain": 1, "deprioritized": 2}
    return (
        disposition_rank.get(analysis.get("disposition"), 3),
        analysis.get("preliminary_complexity", 6),
        -analysis.get("assessment_confidence", 0),
        (item.get("metadata") or {}).get("filter_position", 10**9),
    )


def complexity_tuple(
    analysis: dict[str, Any], position: int
) -> tuple[int, int, int, int]:
    values = [
        int((analysis.get("dimensions") or {}).get(name, 5)) for name in DIMENSIONS
    ]
    return (
        max(values),
        sum(values),
        -int(analysis.get("assessment_confidence", 0)),
        position,
    )


def queue(args: argparse.Namespace) -> None:
    cache_path = Path(args.cache).expanduser()
    rubric = rubric_hash(Path(args.rubric))
    with cache_lock(cache_path):
        cache = load_cache(cache_path)
        excluded_epics = set(load_policy(cache_path)["excluded_epics"])
        candidates: list[dict[str, Any]] = []
        excluded_by_epic = 0
        for item in cache.get("items", {}).values():
            if not item.get("active"):
                continue
            metadata = item.get("metadata") or {}
            if metadata.get("parent_key") in excluded_epics:
                excluded_by_epic += 1
                continue
            if args.stage == "summary":
                if stage_fresh(item, "summary", rubric):
                    continue
                sort_key = (metadata.get("filter_position", 10**9),)
            elif args.stage == "ticket":
                if not stage_fresh(item, "summary", rubric) or stage_fresh(
                    item, "ticket", rubric
                ):
                    continue
                sort_key = summary_sort(item, rubric)
            else:
                if not stage_fresh(item, "ticket", rubric) or stage_fresh(
                    item, "code", rubric
                ):
                    continue
                analysis = item["stages"]["ticket"]["analysis"]
                gates = analysis.get("hard_gates") or []
                if gates and not all(gate.get("refinable") for gate in gates):
                    continue
                gate_rank = 1 if gates else 0
                sort_key = (gate_rank,) + complexity_tuple(
                    analysis, metadata.get("filter_position", 10**9)
                )
            candidates.append(
                {
                    "key": metadata.get("key"),
                    "summary": metadata.get("summary"),
                    "issue_type": metadata.get("issue_type"),
                    "status": metadata.get("status"),
                    "priority": metadata.get("priority"),
                    "labels": metadata.get("labels"),
                    "parent_key": metadata.get("parent_key"),
                    "filter_position": metadata.get("filter_position"),
                    "sort_key": sort_key,
                }
            )
        candidates.sort(key=lambda candidate: tuple(candidate.pop("sort_key")))
        default_limit = {"summary": 50, "ticket": 12, "code": 6}[args.stage]
        limit = args.limit if args.limit is not None else default_limit
        if limit:
            candidates = candidates[:limit]
        emit(
            {
                "stage": args.stage,
                "count": len(candidates),
                "excluded_by_epic": excluded_by_epic,
                "candidates": candidates,
            }
        )


def candidate_output(item: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata") or {}
    analysis = record.get("analysis") or {}
    dimensions = analysis.get("dimensions") or {}
    values = [dimensions.get(name, 5) for name in DIMENSIONS]
    return {
        "key": metadata.get("key"),
        "summary": metadata.get("summary"),
        "issue_type": metadata.get("issue_type"),
        "status": metadata.get("status"),
        "priority": metadata.get("priority"),
        "filter_position": metadata.get("filter_position"),
        "complexity": max(values),
        "dimension_sum": sum(values),
        "dimensions": dimensions,
        "assessment_confidence": analysis.get("assessment_confidence"),
        "refinement_confidence": analysis.get("refinement_confidence"),
        "implementation_path": analysis.get("implementation_path"),
        "validation_path": analysis.get("validation_path"),
        "evidence": analysis.get("evidence"),
        "readiness_annotations": analysis.get("readiness_annotations"),
        "hard_gates": analysis.get("hard_gates"),
        "refinement_questions": analysis.get("refinement_questions"),
        "repositories": analysis.get("repositories"),
        "url": f"https://doximity.atlassian.net/browse/{metadata.get('key')}",
    }


def brief_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": candidate["key"],
        "summary": candidate["summary"],
        "complexity": candidate["complexity"],
        "assessment_confidence": candidate["assessment_confidence"],
        "hard_gate_codes": [gate["code"] for gate in candidate.get("hard_gates") or []],
        "implementation_path": candidate["implementation_path"],
        "validation_path": candidate["validation_path"],
        "url": candidate["url"],
    }


def recommendations(args: argparse.Namespace) -> None:
    cache_path = Path(args.cache).expanduser()
    rubric = rubric_hash(Path(args.rubric))
    result_limit = getattr(args, "limit", None) or 3
    with cache_lock(cache_path):
        cache = load_cache(cache_path)
        excluded_epics = set(load_policy(cache_path)["excluded_epics"])
        qualified: list[tuple[tuple[int, int, int, int], dict[str, Any]]] = []
        refinable: list[tuple[tuple[int, int, int, int], dict[str, Any]]] = []
        near_misses: list[tuple[tuple[int, int, int, int], dict[str, Any]]] = []
        code_spikes: list[tuple[tuple[int, int, int, int], dict[str, Any]]] = []
        active = summary_fresh = ticket_fresh = code_fresh = 0
        for item in cache.get("items", {}).values():
            if not item.get("active"):
                continue
            if (item.get("metadata") or {}).get("parent_key") in excluded_epics:
                continue
            active += 1
            summary_fresh += int(stage_fresh(item, "summary", rubric))
            ticket_fresh += int(stage_fresh(item, "ticket", rubric))
            if not stage_fresh(item, "code", rubric):
                continue
            code_fresh += 1
            record = item["stages"]["code"]
            analysis = record["analysis"]
            position = (item.get("metadata") or {}).get("filter_position", 10**9)
            rank = complexity_tuple(analysis, position)
            output = candidate_output(item, record)
            gates = analysis.get("hard_gates") or []
            confidence = analysis.get("assessment_confidence", 0)
            if not gates and confidence >= 4:
                qualified.append((rank, output))
            elif (
                gates
                and all(gate.get("refinable") for gate in gates)
                and analysis.get("refinement_confidence", 0) >= 3
            ):
                refinable.append((rank, output))
            elif gates:
                if any(gate.get("code") == "needs_code_spike" for gate in gates):
                    code_spikes.append((rank, output))
                else:
                    near_misses.append((rank, output))
        qualified.sort(key=lambda pair: pair[0])
        refinable.sort(key=lambda pair: pair[0])
        near_misses.sort(key=lambda pair: pair[0])
        code_spikes.sort(key=lambda pair: pair[0])
        result = {
            "cache": {
                "path": str(cache_path),
                "last_sync_at": cache.get("last_sync_at"),
                "active": active,
                "summary_fresh": summary_fresh,
                "ticket_fresh": ticket_fresh,
                "code_fresh": code_fresh,
            },
            "recommendations": [output for _, output in qualified[:result_limit]],
            "alternates": [
                output
                for _, output in qualified[
                    result_limit : None
                    if getattr(args, "all", False)
                    else result_limit + 3
                ]
            ],
            "refinement_opportunities": [output for _, output in refinable[:3]],
            "near_misses": [output for _, output in near_misses[:3]],
            "needs_code_spike": [output for _, output in code_spikes[:3]],
        }
        if getattr(args, "brief", False):
            for bucket in (
                "recommendations",
                "alternates",
                "refinement_opportunities",
                "near_misses",
                "needs_code_spike",
            ):
                result[bucket] = [brief_candidate(item) for item in result[bucket]]
        emit(result)


def show(args: argparse.Namespace) -> None:
    if not KEY_RE.match(args.key):
        raise CacheError(f"invalid Jira key: {args.key}")
    cache_path = Path(args.cache).expanduser()
    rubric = rubric_hash(Path(args.rubric))
    with cache_lock(cache_path):
        cache = load_cache(cache_path)
        item = cache.get("items", {}).get(args.key)
        if not item:
            raise CacheError(f"{args.key} is not present in cache")
        stages = item.get("stages") or {}
        emit(
            {
                "key": args.key,
                "active": bool(item.get("active")),
                "metadata": item.get("metadata"),
                "stages": {
                    stage: {
                        "fresh": stage_fresh(item, stage, rubric),
                        "stale_reason": None
                        if stage_fresh(item, stage, rubric)
                        else stage_stale_reason(item, stage, rubric),
                        "analysis": (stages.get(stage) or {}).get("analysis"),
                    }
                    for stage in STAGES
                    if stage in stages
                },
            }
        )


def verify(args: argparse.Namespace) -> None:
    if not KEY_RE.match(args.key):
        raise CacheError(f"invalid Jira key: {args.key}")
    cache_path = Path(args.cache).expanduser()
    excluded_epics = set(load_policy(cache_path)["excluded_epics"])
    matches = jira_search(jql=f"filter = {args.filter_id} AND key = {args.key}")
    member = bool(matches)
    current_updated = None
    changed = False
    if member:
        payload = run_json(
            [
                require_tool("acli"),
                "jira",
                "workitem",
                "view",
                args.key,
                "--fields",
                "key,summary,issuetype,status,assignee,labels,priority,updated",
                "--json",
            ]
        )
        current_updated = (payload.get("fields") or {}).get("updated")
    with cache_lock(cache_path):
        cache = load_cache(cache_path)
        item = cache.get("items", {}).get(args.key)
        excluded_by_epic = bool(
            item and (item.get("metadata") or {}).get("parent_key") in excluded_epics
        )
        cached_updated = item.get("jira_updated") if item else None
        if item:
            if not member:
                item["active"] = False
                changed = True
            elif current_updated != cached_updated:
                item["jira_updated"] = current_updated
                invalidate_item(item, "ticket")
                changed = True
            save_cache(cache_path, cache)
    emit(
        {
            "key": args.key,
            "filter_id": args.filter_id,
            "member": member,
            "cached_updated": cached_updated if item else None,
            "current_updated": current_updated,
            "changed": changed,
            "excluded_by_epic": excluded_by_epic,
            "safe_to_recommend": member and not changed and not excluded_by_epic,
        }
    )


def invalidate(args: argparse.Namespace) -> None:
    if not KEY_RE.match(args.key):
        raise CacheError(f"invalid Jira key: {args.key}")
    cache_path = Path(args.cache).expanduser()
    with cache_lock(cache_path):
        cache = load_cache(cache_path)
        item = cache.get("items", {}).get(args.key)
        if not item:
            raise CacheError(f"{args.key} is not present in cache")
        removed = invalidate_item(item, args.stage)
        save_cache(cache_path, cache)
    emit({"key": args.key, "invalidated": removed})


def clear(args: argparse.Namespace) -> None:
    if not args.yes:
        raise CacheError("refusing to clear cache without --yes")
    cache_path = Path(args.cache).expanduser()
    with cache_lock(cache_path):
        existed = cache_path.exists()
        if existed:
            cache_path.unlink()
        temp_dir = cache_path.parent / "tmp"
        removed_temp = temp_dir.exists()
        if removed_temp:
            shutil.rmtree(temp_dir)
    emit(
        {
            "cache_path": str(cache_path),
            "cleared": existed,
            "removed_temp": removed_temp,
        }
    )


def policy(args: argparse.Namespace) -> None:
    cache_path = Path(args.cache).expanduser()
    dry_run = getattr(args, "dry_run", False)
    if dry_run and not args.add_epic:
        raise CacheError("--dry-run requires --add-epic")
    if dry_run:
        if not KEY_RE.match(args.add_epic):
            raise CacheError(f"invalid Jira key: {args.add_epic}")
        affected = sorted(
            {
                issue["key"]
                for issue in jira_search(
                    jql=f"filter = {args.filter_id} AND parent = {args.add_epic}"
                )
                if isinstance(issue.get("key"), str)
            }
        )
        emit(
            {
                "dry_run": True,
                "epic": args.add_epic,
                "suppressed_keys": affected,
                "count": len(affected),
            }
        )
        return
    with cache_lock(cache_path):
        current = load_policy(cache_path)
        epics = set(current["excluded_epics"])
        included = set(current["included_epics"])
        if args.add_epic:
            if not KEY_RE.match(args.add_epic):
                raise CacheError(f"invalid Jira key: {args.add_epic}")
            epics.add(args.add_epic)
            included.discard(args.add_epic)
        if args.remove_epic:
            if not KEY_RE.match(args.remove_epic):
                raise CacheError(f"invalid Jira key: {args.remove_epic}")
            epics.discard(args.remove_epic)
        allow_epic = getattr(args, "allow_epic", None)
        if allow_epic:
            if not KEY_RE.match(allow_epic):
                raise CacheError(f"invalid Jira key: {allow_epic}")
            included.add(allow_epic)
            epics.discard(allow_epic)
        current["excluded_epics"] = sorted(epics)
        current["included_epics"] = sorted(included)
        if args.add_epic or args.remove_epic or allow_epic:
            save_policy(cache_path, current)
    emit({"policy_path": str(policy_path(cache_path)), **current})


def status(args: argparse.Namespace) -> None:
    cache_path = Path(args.cache).expanduser()
    rubric = rubric_hash(Path(args.rubric))
    with cache_lock(cache_path):
        cache = load_cache(cache_path)
        excluded_epics = set(load_policy(cache_path)["excluded_epics"])
        included_epics = set(load_policy(cache_path)["included_epics"])
        counts = {
            "total": len(cache.get("items", {})),
            "active": 0,
            "inactive": 0,
            "summary_fresh": 0,
            "ticket_fresh": 0,
            "code_fresh": 0,
            "excluded_by_epic": 0,
        }
        stale_reasons: dict[str, int] = {}
        record_rubric_hashes: dict[str, int] = {}
        for item in cache.get("items", {}).values():
            state = "active" if item.get("active") else "inactive"
            counts[state] += 1
            if item.get("active"):
                if (item.get("metadata") or {}).get("parent_key") in excluded_epics:
                    counts["excluded_by_epic"] += 1
                    continue
                for stage in STAGES:
                    record = (item.get("stages") or {}).get(stage)
                    if isinstance(record, dict) and isinstance(
                        record.get("rubric_hash"), str
                    ):
                        record_hash = record["rubric_hash"]
                        record_rubric_hashes[record_hash] = (
                            record_rubric_hashes.get(record_hash, 0) + 1
                        )
                    if stage_fresh(item, stage, rubric):
                        counts[f"{stage}_fresh"] += 1
                    elif (item.get("stages") or {}).get(stage):
                        reason = stage_stale_reason(item, stage, rubric)
                        stale_reasons[reason] = stale_reasons.get(reason, 0) + 1
        counts["eligible_active"] = counts["active"] - counts["excluded_by_epic"]
        emit(
            {
                "cache_path": str(cache_path),
                "filter": cache.get("filter"),
                "last_sync_at": cache.get("last_sync_at"),
                "rubric": {
                    "current_hash": rubric,
                    "cached_hash": cache.get("rubric_hash"),
                    "record_hashes": record_rubric_hashes,
                    "changed": cache.get("rubric_hash") not in (None, rubric),
                },
                "counts": counts,
                "stale_reasons": stale_reasons,
                "policy": {
                    "excluded_epics": sorted(excluded_epics),
                    "included_epics": sorted(included_epics),
                },
                "unclassified_observed_epics": [
                    {"key": key, **value}
                    for key, value in sorted(
                        (cache.get("observed_epics") or {}).items(),
                        key=lambda pair: (-int(pair[1].get("seen_count", 0)), pair[0]),
                    )
                    if key not in excluded_epics and key not in included_epics
                ],
            }
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", default=str(DEFAULT_CACHE), help="cache JSON path")
    parser.add_argument(
        "--rubric", default=str(DEFAULT_RUBRIC), help="scoring rubric path"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser(
        "sync", help="refresh filter membership and Jira deltas"
    )
    sync_parser.add_argument("--filter-id", default=DEFAULT_FILTER_ID)
    sync_parser.set_defaults(func=sync)

    issue_parser = subparsers.add_parser(
        "issue", help="fetch one full Jira issue and update its revision"
    )
    issue_parser.add_argument("--key", required=True)
    issue_parser.set_defaults(func=issue)

    snapshot_parser = subparsers.add_parser(
        "repo-snapshot",
        help="refresh and snapshot a repository default branch without switching worktrees",
    )
    snapshot_parser.add_argument("--path", required=True)
    snapshot_parser.add_argument("--default-ref")
    snapshot_parser.add_argument("--no-refresh", dest="refresh", action="store_false")
    snapshot_parser.set_defaults(func=repo_snapshot, refresh=True)

    pr_parser = subparsers.add_parser(
        "pr-candidates", help="find open PRs via exact Jira-key branch matches"
    )
    pr_parser.add_argument("--key", required=True)
    pr_parser.add_argument(
        "--path", action="append", help="repository path; repeat for plausible repos"
    )
    pr_parser.add_argument("--root", default=str(Path.home() / "work"))
    pr_parser.add_argument("--repo")
    pr_parser.set_defaults(func=pr_candidates)

    queue_parser = subparsers.add_parser(
        "queue", help="list candidates needing a stage"
    )
    queue_parser.add_argument("--stage", choices=STAGES, required=True)
    queue_parser.add_argument("--limit", type=int)
    queue_parser.set_defaults(func=queue)

    record_parser = subparsers.add_parser(
        "record", help="validate and save staged analysis"
    )
    record_parser.add_argument("--stage", choices=STAGES, required=True)
    record_parser.add_argument(
        "--input", required=True, help="JSON file or - for stdin"
    )
    record_parser.set_defaults(func=record)

    recommendations_parser = subparsers.add_parser(
        "recommendations", help="print qualified and refinable cached results"
    )
    recommendations_parser.add_argument(
        "--brief",
        action="store_true",
        help="omit dimensions/evidence/repository detail",
    )
    recommendations_parser.add_argument(
        "--limit", type=int, default=3, help="number of primary recommendations"
    )
    recommendations_parser.add_argument(
        "--all", action="store_true", help="return every remaining qualified alternate"
    )
    recommendations_parser.set_defaults(func=recommendations)

    show_parser = subparsers.add_parser(
        "show", help="print sanitized cached analysis without Jira access"
    )
    show_parser.add_argument("--key", required=True)
    show_parser.set_defaults(func=show)

    verify_parser = subparsers.add_parser(
        "verify", help="live-check filter membership and Jira revision"
    )
    verify_parser.add_argument("--key", required=True)
    verify_parser.add_argument("--filter-id", default=DEFAULT_FILTER_ID)
    verify_parser.set_defaults(func=verify)

    invalidate_parser = subparsers.add_parser(
        "invalidate", help="invalidate one stage and deeper stages"
    )
    invalidate_parser.add_argument("--key", required=True)
    invalidate_parser.add_argument("--stage", choices=STAGES, required=True)
    invalidate_parser.set_defaults(func=invalidate)

    clear_parser = subparsers.add_parser(
        "clear", help="delete the local cache after explicit confirmation"
    )
    clear_parser.add_argument("--yes", action="store_true")
    clear_parser.set_defaults(func=clear)

    policy_parser = subparsers.add_parser(
        "policy", help="show or update local finder policy"
    )
    policy_actions = policy_parser.add_mutually_exclusive_group()
    policy_actions.add_argument("--add-epic")
    policy_actions.add_argument("--remove-epic")
    policy_actions.add_argument("--allow-epic")
    policy_parser.add_argument(
        "--dry-run", action="store_true", help="preview affected members; do not save"
    )
    policy_parser.add_argument("--filter-id", default=DEFAULT_FILTER_ID)
    policy_parser.set_defaults(func=policy)

    status_parser = subparsers.add_parser(
        "status", help="show cache state without Jira access"
    )
    status_parser.set_defaults(func=status)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "limit", None) is not None and args.limit < 0:
        parser.error("--limit must be zero or greater")
    try:
        args.func(args)
    except (CacheError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
