# Agent-readiness scoring rubric

Use this rubric after reading Jira details and again after a minimal code feasibility sniff. Score uncertainty and risk, not implementation time. It ranks likely feasibility; it does not prove a fix.

## Qualification

An issue qualifies only when:

- `hard_gates` is empty;
- every required repository, dependency, reference, and tool is accessible;
- available evidence supports a plausible implementation and validation path;
- `assessment_confidence` is 4 or 5.

Never infer a missing product or architecture decision merely to improve a score. Do not debug, reproduce, trace full flows, or prove a solution to improve a score; represent remaining uncertainty in the dimensions and confidence.

## Summary screen

Use only the title, type, status, labels, and priority. Never add hard gates at this stage.

- **Promising:** Title describes a concrete behavior change or bug with a plausible contained implementation.
- **Uncertain:** Title lacks enough information to infer a safe implementation path.
- **Deprioritized:** Title strongly suggests broad discovery, migration, sensitive/security work, an Epic, or a non-implementation deliverable. This is queue order only, never a rejection.

Set `preliminary_complexity` from 1–5 based only on likely agentic uncertainty. Set summary confidence to 1–2; titles cannot establish implementation readiness.

Ticket-review queue order is exactly: disposition (`promising`, `uncertain`, `deprioritized`), lowest preliminary complexity, highest summary confidence, then Jira filter position. Code-review order is: ungated before refinable-gated, then overall ordering below.

## Hard gates

Use these exact codes:

| Code | Meaning | Refinable |
| --- | --- | --- |
| `unresolved_decision` | Product behavior, acceptance criteria, ownership, or architecture requires a human choice. | Yes |
| `missing_required_context` | Referenced design, discussion, dependency, reproduction, or system context is unavailable. | Yes |
| `blocked_dependency` | An unresolved linked dependency or prerequisite prevents implementation. | Sometimes |
| `auth_or_sensitive_data` | Work touches auth, authorization, PHI, PII, security, or compliance behavior. | No |
| `migration_or_destructive_operation` | Requires schema/data migration, backfill, deletion, or production operation. | No |
| `shared_or_cross_service_contract` | Changes a shared library, public API, event/schema contract, or coordinated service boundary. | No |
| `no_reliable_validation` | Correctness cannot be established with objective automated or safe manual evidence. | Yes |
| `stale_or_missing_code` | Required repository/default branch is missing or local default ref differs from the observed remote SHA. | No |
| `active_work_exists` | An open PR or clear active implementation already exists. | No |
| `unsafe_blast_radius` | A plausible wrong implementation could cause broad, irreversible, or difficult-to-detect harm. | No |
| `needs_code_spike` | Code discovery cannot establish a credible implementation path; investigate code/package ownership before implementation. | No |

`refinable` means additional Jira information could remove the gate. Mark an issue as a refinement opportunity only when every other gate is absent and its non-gated dimensions would make it competitive.

`refinement_confidence` is separate from completion confidence: score 1–5 for confidence that answering the listed refinement questions would remove every remaining hard gate. A score of 3 or higher may appear in refinement output. Final recommendations still require `assessment_confidence` 4 or 5 and no gates.

## Dimensions

Score each integer from 1 (agent-friendly) to 5 (agent-hostile).

### Decision ambiguity

- **1:** Observable behavior and edge cases are explicit; no choices remain.
- **2:** Minor details are resolved by one clear existing convention.
- **3:** Several plausible interpretations exist, but evidence favors one.
- **4:** A human likely must choose behavior or scope.
- **5:** Objective, user outcome, or ownership is unclear.

### Implementation uncertainty

Estimate from the smallest useful code signal. A likely extension point or nearby pattern is enough; do not establish root cause or full control flow.

- **1:** Exact extension point and canonical nearby pattern are known.
- **2:** Path is clear across a small number of well-understood areas.
- **3:** Discovery is incomplete or multiple approaches remain plausible.
- **4:** Architecture/repository ownership is uncertain.
- **5:** No credible implementation path is established.

### Validation uncertainty

Estimate likely validation from nearby tests/tooling. Do not write, run, or design a full test plan during feasibility ranking.

- **1:** Focused existing tests or deterministic reproduction prove behavior.
- **2:** New tests are straightforward with established fixtures/tooling.
- **3:** Validation spans layers or relies partly on manual judgment.
- **4:** Important outcomes are difficult to observe reliably.
- **5:** No trustworthy correctness oracle exists.

### Dependency uncertainty

- **1:** All context, repositories, tools, and dependencies are local and available.
- **2:** Dependencies are known and stable with clear interfaces.
- **3:** Some external context or coordination may be needed.
- **4:** Important dependency state or ownership is unknown.
- **5:** Required context or dependency is inaccessible/blocked.

### Blast radius

- **1:** Isolated, reversible behavior with narrow tests.
- **2:** Contained feature area with established rollback/failure behavior.
- **3:** Multiple modules or users affected, but boundaries are understood.
- **4:** Shared behavior, broad user impact, or subtle regression risk.
- **5:** Irreversible, security-sensitive, data-sensitive, or cross-service risk.

## Overall ordering

For qualified issues, sort by:

1. lowest worst dimension (`max(dimensions)`);
2. lowest dimension sum;
3. highest assessment confidence;
4. Jira filter position.

Display the worst dimension as `Complexity 1-5`. Do not average dimensions or invent weighted precision.

## Assessment confidence

- **1:** Guess from title/type only.
- **2:** Preliminary Jira evidence; important details unseen.
- **3:** Full ticket read; code/dependency evidence is limited but enough for a directional estimate.
- **4:** Ticket plus minimal default-branch signals support a plausible implementation and validation path.
- **5:** Clear ticket, nearby pattern, and likely validation signal with only minor residual uncertainty. This is still feasibility confidence, not proof of a fix.

Jira story points, priority, and estimated duration are supporting evidence only. Never let them override missing decisions, weak validation, or code evidence. Record soft readiness signals (for example, an estimate requested before work) in `readiness_annotations`; they are annotations, not gates.

## Record contracts

Ticket and code records use:

```json
{
  "key": "FAX-1234",
  "analysis": {
    "hard_gates": [
      {
        "code": "unresolved_decision",
        "reason": "Expected behavior for the empty state is unspecified",
        "refinable": true
      }
    ],
    "dimensions": {
      "decision_ambiguity": 4,
      "implementation_uncertainty": 2,
      "validation_uncertainty": 2,
      "dependency_uncertainty": 1,
      "blast_radius": 2
    },
    "assessment_confidence": 4,
    "refinement_confidence": 4,
    "inferred_repositories": ["doximity"],
    "implementation_path": "Extend the existing service pattern",
    "validation_path": "Focused model and request specs",
    "evidence": ["Acceptance criteria identify the main observable behavior"],
    "readiness_annotations": ["Estimate story points before implementation"],
    "refinement_questions": ["What should happen when no records exist?"]
  }
}
```

Code records additionally include:

```json
{
  "repositories": [
    {
      "name": "doximity",
      "path": "~/work/doximity",
      "default_ref": "origin/master",
      "local_sha": "<sha>",
      "remote_sha": "<sha>"
    }
  ],
  "active_prs": [],
  "runtime_profile": "dox-agent-v1"
}
```
