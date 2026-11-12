---
name: davetrig-review-pr
description: >-
  Doximity-specific review of GitHub pull requests from a URL, owner/repo#number reference, or PR number in the current repository. Use when the user asks to review a PR, review this pull request, inspect a GitHub PR for defects, summarize code-review findings, or says "review this PR: [github url or PR number]".
---

# Davetrig Review PR

## Core rules

- Review only. Do not post GitHub comments, submit a review, approve/request changes, push commits, or edit files or switch branches in the user's checkout unless the user explicitly asks. A detached worktree for inspection (step 4) is allowed.
- Even when the user asks to post comments, first show the exact draft comments and wait for approval before posting.
- Review from the diff by default. Pull deeper repo context only when correctness, risk, or a likely finding depends on surrounding code.
- For Doximity repos, apply repo instructions and Doximity engineering norms: follow `AGENTS.md`/`CLAUDE.md`, protect HIPAA/PII, respect auth boundaries, prefer existing service patterns, and use `dox-do` for Rails/app commands if commands are needed.
- Do not run spec tests by default. CI runs specs, and authors are expected to fix failing tests.

## Workflow

1. **Confirm GitHub access**
   - Run `gh auth status` before GitHub CLI work.
   - If `gh` is missing or auth fails, report the blocker and ask for authentication/repo access instead of guessing from incomplete data.

2. **Resolve the PR reference**
   - For a GitHub PR URL, parse `OWNER/REPO` and PR number from `/pull/NUMBER`.
   - For `OWNER/REPO#NUMBER`, use that owner/repo and number.
   - For a bare number, infer the repo from the current checkout with `gh repo view --json nameWithOwner -q .nameWithOwner`.
   - If a bare number is provided outside a GitHub checkout, ask for the repo.

3. **Collect PR context**
   - Fetch machine-readable metadata:
     ```bash
     gh pr view PR_NUMBER --repo OWNER/REPO --json number,title,url,body,state,isDraft,author,baseRefName,headRefName,additions,deletions,changedFiles,commits,files,reviewDecision,mergeStateStatus
     ```
   - Fetch existing discussion before reviewing:
     ```bash
     gh pr view PR_NUMBER --repo OWNER/REPO --json comments,reviews
     gh api repos/OWNER/REPO/pulls/PR_NUMBER/comments --paginate
     gh api repos/OWNER/REPO/issues/PR_NUMBER/comments --paginate
     ```
   - Fetch review-thread resolution status via GraphQL (the REST endpoints above don't carry it), then
     drop any inline comment belonging to a resolved thread from the working set entirely — treat it
     as if it did not exist for the rest of the review (not surfaced, not concurred with, not used to
     judge whether a new finding is a duplicate):
     ```bash
     gh api graphql -f query='
       query($owner: String!, $repo: String!, $number: Int!, $cursor: String) {
         repository(owner: $owner, name: $repo) {
           pullRequest(number: $number) {
             reviewThreads(first: 100, after: $cursor) {
               pageInfo { hasNextPage endCursor }
               nodes {
                 isResolved
                 comments(first: 100) { nodes { databaseId } }
               }
             }
           }
         }
       }' -f owner=OWNER -f repo=REPO -F number=PR_NUMBER --paginate
     ```
     Collect the `databaseId`s of every comment under a thread where `isResolved` is true into a
     resolved-ID set; paginate with `cursor` if `hasNextPage` is true. Filter the REST inline-comment
     list (`pulls/PR_NUMBER/comments`) against that set before using it anywhere below. Top-level
     issue comments (from the `issues/PR_NUMBER/comments` endpoint) have no thread-resolution concept
     and are never filtered.
   - Fetch the patch to a temp file in a unique directory:
     ```bash
     PR_DIR=$(mktemp -d)
     gh pr diff PR_NUMBER --repo OWNER/REPO --patch > "$PR_DIR/pr-PR_NUMBER.patch"
     ```
     Remove `$PR_DIR` when the review is finished.
   - Read the PR description, commit list, changed-file list, the *filtered* existing comments/reviews, and patch before forming conclusions.

4. **Review diff-first, then deepen selectively**
   - Start from the patch and changed-file list.
   - Inspect surrounding code, call sites, schemas, migrations, configs, tests, or docs only when the diff suggests a real risk or ambiguity.
   - If already in the target repo and deeper local inspection is useful, create a detached worktree at the PR head. This leaves the user's checkout, branch, and uncommitted changes untouched:
     ```bash
     git fetch origin pull/PR_NUMBER/head
     REVIEW_DIR=$(mktemp -d)
     git worktree add --detach "$REVIEW_DIR" FETCH_HEAD
     ```
     Read files from `$REVIEW_DIR`. When the review is done, clean up:
     ```bash
     git worktree remove "$REVIEW_DIR"
     ```
   - If the repo differs or the checkout is not a git repo, review from the patch and `gh api`/`gh pr view` data.
   - Never run `gh pr checkout`; it switches the user's branch.
   - Follow local repo instructions before running any command. Use non-interactive commands only. Do not run specs unless explicitly requested.

5. **Review for Doximity-relevant risk**
   - Prioritize correctness, security/privacy, HIPAA/PII exposure, auth/authorization, data loss, API compatibility, migrations, background jobs, concurrency/idempotency, performance regressions, observability, analytics, and user/product behavior.
   - Treat test gaps as findings only when they create concrete risk for changed behavior; do not ask for tests just because coverage changed.
   - Include minor style/readability concerns only as non-blocking `nitpick` comments.
   - Avoid broad refactors or speculative issues. Each finding must be actionable and supported by code evidence.
   - If a finding overlaps with an existing, unresolved PR comment, do not draft a duplicate inline comment. Note that you concur with the existing comment; only mark it as `[nitpick]` if it is non-blocking.
   - Resolved-thread comments were already dropped in step 3 — treat them as settled. Do not re-surface them, do not list them under "Existing comments I concur with," and do not independently re-derive the same finding from the diff; a resolved thread is out of scope for this review, full stop.

6. **Do not check CI status**
   - CI status is not part of the review. Do not run `gh pr checks`, and do not mention CI status in the output.

## Output format

Use inline-comment draft style with a non-redundant summary:

```markdown
## Recommendation
Ready | Comment (non-severe concerns) | Needs changes | Needs author clarification

## Draft inline comments

### `path/to/file.ext:LINE`
Short title.

Concise comment text that could be posted on GitHub. Include why it matters and a suggested fix direction. Do not include a severity tag for blocking or major issues; the fact that they are called out in the review is enough.

### `path/to/file.ext:LINE`
[nitpick] Short title.

Non-blocking style/readability/maintainability concerns only. Mark with `[nitpick]` so the author can triage them quickly.

## Existing comments I concur with
- @reviewer on `path/to/file.ext:LINE`: brief reason for concurrence (blocking or major issue).
- [nitpick] @reviewer on `path/to/file.ext:LINE`: brief reason for concurrence.

## Summary comment
Very short overall verdict. Do not restate the inline comments.
- No issues: "LGTM" or a thumbs-up.
- Only nitpicks: "A few nitpicks, but otherwise LGTM".
- Real concerns: "Needs changes" or "A few issues to address" (do not re-list them).

Do not include a CI / Checks section. Ignore CI status entirely; it is not part of the review.
```

Omit empty sections except `Recommendation` and `Summary comment`.

Recommendation guidance:

- Reserve `Needs changes` for severe issues: likely bugs, security/privacy/PHI risks, data loss, broken build, or release blockers.
- For non-severe concerns, recommend leaving a `comment` review (not blocking or approving) and do not mark the PR as "Needs changes".
- `Needs author clarification`: the review is blocked by an important product/technical ambiguity.
- `Ready`: no substantive concerns; nitpicks may remain.

Severity guidance (used internally, not shown in the output except for nitpicks):

- `blocking`: severe issue that should block merge until fixed.
- `major`: real defect or risky gap, but generally not a merge blocker unless it is severe.
- `nitpick`: non-blocking style/readability/maintainability polish. Mark these with `[nitpick]` in the output so they are easy to identify.
