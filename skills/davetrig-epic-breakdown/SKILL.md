---
name: davetrig-epic-breakdown
description: Use when given a product proposal, Jira story, or feature description and asked to plan, decompose, or break down engineering work into epics and stories as a tech lead.
allowed-tools:
  - Bash(acli jira*)
  - mcp__claude_ai_Atlassian_Rovo__getJiraIssue
  - mcp__claude_ai_Atlassian_Rovo__getJiraIssueRemoteIssueLinks
  - mcp__agentic-dev__dox_code_search
  - mcp__agentic-dev__dox_service_graph
  - mcp__agentic-dev__dox_assemble_context
  - mcp__agentic-dev__dox_resolve_route
  - mcp__claude_ai_Google_Drive__read_file_content
  - Agent
  - AskUserQuestion
  - Read
  - Bash(git -C * fetch *)
  - Bash(git -C * worktree *)
  - Bash(git -C * diff *)
user-invocable: true
---

# davetrig-epic-breakdown

A tech lead's workflow for turning a product proposal or feature ticket into a scoped epic with well-defined engineering stories. The process has six phases with three explicit approval gates. Nothing gets created in Jira until the user has approved the decomposition and reviewed the written content.

## When to Use

- User invokes `/davetrig-epic-breakdown` or asks to "break this down into stories"
- User pastes a product proposal or feature description and asks for an epic
- User shares a Jira ticket and asks how to scope or sequence the engineering work
- User shares a Google Docs proposal or a UI prototype branch to scope

## Red Flags — STOP If You Are Doing Any of These

- Writing stories before asking clarifying questions
- Proceeding past Phase 3 with unanswered blocking questions
- Creating tickets in Jira before the user approves the draft content
- Inventing constraints not stated in the proposal (caps, limits, rates)
- Using horizontal rules, emoji, or bold section headers in Jira content
- Writing filler phrases: "this feature aims to", "this will enable users to", "in order to"
- Fabricating ticket keys or claiming tickets were created before running acli commands

---

## Phase 1: Intake

Accept any of: Jira ticket key, Jira URL, pasted proposal text, Google Docs proposal link, or a UI prototype branch. Combine them when given together.

If a Jira key or URL is provided, fetch it:

```bash
acli jira workitem view TICKET-KEY
```

Or use `mcp__claude_ai_Atlassian_Rovo__getJiraIssue` if the MCP server is available.

Read the full content. Pull out: title, goal, scope signals, acceptance criteria, linked docs, and any existing comments that constrain the work. Note what is explicitly stated vs. what is implied or missing.

### Google Docs proposal

If given a `docs.google.com` link (Docs, Sheets, or Slides), extract the file ID from the path (`/d/<FILE_ID>/`) and read it:

```
mcp__claude_ai_Google_Drive__read_file_content  →  fileId=<FILE_ID>
```

Set `includeComments` only when the user asks about comments, feedback, or open questions. If the read fails or returns nothing, say so and ask the user to paste the text. Never guess the content.

Treat the document as the proposal. Pull out the same fields as a Jira ticket and note which ones the document leaves unstated.

### UI prototype branch

If given a branch name, or a branch and repo, review its UI code read-only. Never check out the branch in the user's working copy, and never run the app or its dev server. This follows the same worktree pattern as `davetrig-review-pr`:

```bash
git -C ~/work/<repo> fetch origin <branch>
REVIEW_DIR=$(mktemp -d)
git -C ~/work/<repo> worktree add --detach "$REVIEW_DIR" FETCH_HEAD
```

Use `git -C ~/work/<repo> diff --stat origin/master...FETCH_HEAD` to list changed files. Focus on new or changed components, routes, and screens. Record for Phase 2:
- screens and flows the prototype shows;
- components it adds or changes;
- what looks finished and what looks stubbed or mocked.

Treat the prototype as a description of intended behavior, not as an implementation to copy. Remove the worktree when finished:

```bash
git -C ~/work/<repo> worktree remove "$REVIEW_DIR"
```

If the repo isn't clear, ask. Do not guess.

---

## Phase 2: Codebase Exploration

Dispatch Explore subagents in parallel before asking questions. This makes the clarifying questions sharper and surfaces technical constraints the PM may not know about.

Look for:
- Affected services (`mcp__agentic-dev__dox_service_graph` or `mcp__agentic-dev__dox_code_search`)
- Existing patterns for similar features in the relevant service
- Data model: tables, associations, migrations that would be affected
- API surface: endpoints that would change or need to be added
- Frontend entry points if UI work is involved
- Feature flags or config that gates related behavior
- Any MEMORY.md notes about pitfalls in this area

Synthesize findings into a brief technical summary for yourself. Do not show this to the user yet. Use it to decide which probe questions are blocking vs. optional.

---

## Phase 3: Requirement Analysis — Hard Gate

Run through the full probe checklist below. For each item where the answer is missing, ambiguous, or contradictory, write a clear question.

**Probe checklist:**

- Scope: mobile (iOS/Android), web, or both? API-only?
- Rollout: feature flag required? gradual rollout? kill switch? which flag system?
- Observability: what events need to be tracked? is there a dashboard or alerting requirement?
- Error handling: what happens when a dependent service fails? what does the user see?
- Performance: expected load, any latency SLAs?
- Data migration: does existing data need to be backfilled or transformed?
- Auth and permissions: who can access this? any role, tier, or org restrictions?
- Notifications: email, push, in-app — does this feature trigger any?
- Edge cases and empty states: what does the user see when there is no data?
- Success criteria: how do we know this is working correctly in production?
- External dependencies: third-party APIs, partner teams, another team's stories that block this?

Group questions as blocking (cannot design the stories without this answer) or non-blocking (should know, can assume and note the assumption). Present all of them to the user.

**Hard gate:** Do not move to Phase 4 until all blocking questions have been answered. If the user says "just make assumptions," explain which assumptions would be risky and why, and ask them to confirm each one explicitly. Do not proceed with silent assumptions.

---

## Phase 4: Story Decomposition — Approval Gate

Present a proposed breakdown. For each story:

- Summary in verb-noun format: "Add X endpoint", "Migrate Y table", "Build Z component"
- Estimated size: S / M / L / XL with a one-line rationale
- Whether it blocks or is blocked by other stories
- Any specific risks or unknowns

Include a brief dependency order showing which stories must land before others can start. Flag stories that can run in parallel.

**Approval gate:** Show this to the user and wait. Do not proceed to writing Jira content until the decomposition is explicitly approved. Accept revisions without pushing back unless a revision creates a technical problem worth flagging.

---

## Phase 5: Draft Jira Content — Review Gate

Draft the epic and all stories. Present everything to the user before creating a single ticket.

**Writing rules — no exceptions:**

- No horizontal rules
- No emoji
- No bold headers inside ticket descriptions
- No filler phrasing: "this feature aims to", "this will enable users to", "in order to", "this helps"
- Lead with why (context), not what
- Acceptance criteria as testable checkboxes, not prose paragraphs
- Technical notes only when implementation detail is a genuine constraint, not FYI commentary

**Epic format:**

```
Context
[2-3 sentences: problem being solved, business or user motivation]

Goals
- [measurable outcome]
- [measurable outcome]

Out of Scope
- [explicit exclusion]

Stories
- [ ] [summary]
- [ ] [summary]

Success Metrics
[observable signal that confirms the feature is working post-launch]
```

**Story format:**

```
Context
[1-2 sentences: why this story exists and where it fits in the sequence]

Acceptance Criteria
- [ ] [testable condition]
- [ ] [testable condition]

Technical Notes
[only if implementation detail is a constraint — omit if there is nothing to say here]
```

**Review gate:** Present all drafts. Do not create anything in Jira until the user approves.

---

## Phase 6: Create in Jira

Before running any acli commands, show a creation manifest and wait for explicit confirmation:

```
Ready to create the following in Jira:

Epic
- [epic summary]

Stories (in creation order)
1. [story summary]
2. [story summary]
...

Blocking links to set up
- Story 1 blocks Story 2
- Story 2 blocks Story 3

Proceed?
```

Do not run a single acli command until the user confirms. If they want changes, go back and revise.

Once confirmed, use the `jira-tickets` skill for creation. Follow this order:

1. Create the epic — capture its key
2. Create each story with `--parent EPIC-KEY`
3. Link sequential dependencies using the `jira-tickets` skill's blocking chain pattern
4. Verify all links with `acli jira workitem link list --key TICKET-ID`
5. Output a summary:

```
| Ticket | Summary | Suggested Points | Blocks |
|--------|---------|-----------------|--------|
| ...    | ...     | ...             | ...    |
```

Remind the user that story points cannot be set via acli and must be added manually in the Jira UI.

---

## Common Mistakes

| Mistake | Correction |
|---------|------------|
| Skipping clarifying questions and going straight to stories | Questions come before decomposition. Always. |
| Proceeding with unanswered blocking questions | Re-surface them. Explain why each one changes the design. |
| Inventing constraints not in the proposal | Ask product. Do not add caps, limits, or rules on your own. |
| Creating tickets before the user approves drafts | Phase 5 review gate is mandatory. |
| Running acli commands before showing the creation manifest | Show the full list of what will be created and wait for explicit confirmation before any acli call. |
| Using horizontal rules or emoji in Jira content | These are the clearest signal that a human did not write this. |
| Claiming "everything is in Jira" before running acli | Never fabricate ticket keys. Run the commands. |
| Skipping codebase exploration | You will miss migration scope, existing patterns, and PII risk areas. |
| Making silent assumptions | Every assumption that affects the design must be confirmed explicitly. |
