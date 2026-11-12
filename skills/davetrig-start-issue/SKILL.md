---
name: davetrig-start-issue
description: Use when starting work on a JIRA issue or given a JIRA link to begin implementing. Covers ticket comprehension, codebase exploration, clarification, and solution planning before any code is written.
allowed-tools:
  - mcp__claude_ai_Atlassian_Rovo__getJiraIssue
  - mcp__claude_ai_Atlassian_Rovo__getJiraIssueRemoteIssueLinks
  - mcp__agentic-dev__dox_code_search
  - mcp__agentic-dev__dox_assemble_context
  - mcp__agentic-dev__dox_resolve_route
  - mcp__agentic-dev__dox_repo_list
  - mcp__agentic-dev__dox_service_graph
  - mcp__agentic-dev__dox_agent_context
  - EnterPlanMode
  - Read
  - Bash(find *)
  - Bash(grep *)
  - Bash(cat *)
user-invocable: true
---

# Start Issue

Structured ramp-up for a JIRA ticket: understand it, explore the code, ask the right questions, then plan — all before touching a single file.

## When to Use

- User invokes `/davetrig-start-issue` or `/start-issue`
- User pastes a JIRA link and says "start working on this" or similar
- User asks to pick up or investigate a ticket

---

## Step 1 — Get the JIRA Issue Key

Parse the issue key from the URL:
```
https://doximity.atlassian.net/browse/FAX-1234  →  FAX-1234
```

If no JIRA link was provided, ask:
> "Which JIRA ticket are we working on? Paste the URL or key."

---

## Step 2 — Clarify Repo Context (if needed)

If the repo isn't obvious from the ticket or conversation, ask — **do not guess**:

> "Which repo should I focus on? Common ones:
> - `doximity/doximity` — Rails monolith (backend + some frontend)
> - `doximity/doximity-client-vue` — Vue frontend
> - Other? (paste the repo name)"

Skip this step if the repo is already clear from context.

---

## Step 3 — Fetch and Understand the Ticket

Call `mcp__claude_ai_Atlassian_Rovo__getJiraIssue` with the issue key.

Read carefully:
- **Summary** — the one-liner
- **Description** — requirements, background, acceptance criteria
- **Labels / Epic link** — team ownership, feature area
- **Comments** — any prior investigation, decisions, or constraints
- **Linked issues** — blockers, related work, parent epics

If there are remote links (design docs, Confluence, etc.), call `mcp__claude_ai_Atlassian_Rovo__getJiraIssueRemoteIssueLinks` and note them — don't fetch unless directly relevant.

---

## Step 4 — Explore the Codebase

Use the ticket's subject matter to guide your search. Work progressively — broad first, then narrow.

### Start broad
```
mcp__agentic-dev__dox_code_search  →  search by domain terms from the ticket summary
mcp__agentic-dev__dox_assemble_context  →  get an overview of the relevant service
```

### Narrow by what you find
- **Route involved?** → `mcp__agentic-dev__dox_resolve_route` to trace it to the handler
- **Cross-service?** → `mcp__agentic-dev__dox_service_graph` to understand dependencies
- **Unknown repo layout?** → `mcp__agentic-dev__dox_agent_context` for the service

### Also check
- `MEMORY.md` in the repo root (if it exists) — prior pitfalls and decisions
- Existing tests covering the affected area (patterns tell you how to test your change)
- Feature flags or config that gates the behavior

Collect: affected files, key classes/modules, test coverage, and any red flags (auth, PII, shared libraries).

---

## Step 5 — Ask Clarifying Questions

Before proposing anything, surface what you've learned and ask the questions that will most change the approach. Aim for 3–5 focused questions. Good candidates:

- **Scope**: Backend-only, or does this touch the frontend too?
- **Behavior**: What should happen in [specific edge case from the ticket]?
- **Design**: Is there a mockup or design spec I should follow?
- **Testing**: Should I extend existing specs, or is this greenfield?
- **Risk**: Is [thing you found in code] expected to change, or must I work around it?

Don't ask questions whose answers you can reasonably infer from the ticket or codebase.

---

## Step 6 — Enter Planning Mode and Propose a Solution

Once you have enough context (ticket + exploration + answers), call `EnterPlanMode` and write a plan covering:

1. **Restatement** — what's in scope and what's explicitly not
2. **Services / files affected** — with file paths and why each is touched
3. **Implementation approach** — with specific patterns cited from the codebase
4. **Test strategy** — what needs new tests, what existing specs to extend
5. **Risks / open questions** — anything that could bite us

**STOP. Do not exit plan mode, do not write any code, do not commit anything.** Wait for explicit human approval of the plan before proceeding.

---

## Step 7 — Implement (after approval only)

Only after the human approves the plan:

1. Call `ExitPlanMode` to return to auto mode
2. Implement the changes in small, logical chunks per the approved plan
3. Run tests and linting — surface any failures immediately
4. **NEVER commit.** Leave all commits to the human. Your job ends at a clean, passing implementation.

---

## Non-Negotiable Rules

- **No commits, ever.** Do not run `git commit` under any circumstances. Not even "just a WIP commit."
- **No code before plan approval.** Plan mode is a gate, not a formality.
- **No skipping straight to implementation.** Even if the fix looks obvious, write the plan first.

---

## Common Mistakes

| Mistake | Correction |
|---------|------------|
| Guessing the repo without asking | Ask if it's not clear — wrong repo wastes time |
| Only reading the ticket summary | Read the full description and comments — constraints often live there |
| Skipping the test files | Tests reveal the intended behavior and required patterns |
| Proposing a solution before asking questions | Questions first — they often change the approach entirely |
| Starting code before plan approval | Plan mode is a gate. Wait for explicit approval. |
| Committing code | Never. Leave all commits to the human. |
| Missing cross-service impact | Always check `dox_service_graph` if the ticket touches an API boundary |
