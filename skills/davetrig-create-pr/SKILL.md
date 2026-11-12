---
name: davetrig-create-pr
description: >-
  Doximity-specific creation of GitHub pull requests for the current branch. Use by default whenever the user asks to create a PR, open a pull request, draft a PR, prepare a GitHub PR, or create a PR with Doximity/Jira/QA conventions, even when the user does not explicitly name this skill.
---

# Davetrig Create PR

## Core rules

- Always create PRs in draft mode.
- Always use the repository's existing PR template. If multiple templates exist, ask which one to use. If no template exists, tell the user and create a sensible Doximity-style fallback body for review.
- Preserve untouched template sections. Fill sections only when confident; leave existing headings/placeholders for the user to update or remove.
- Review the generated PR title with the user before creating the PR. Do not require review of the full body unless there is no template, major uncertainty, or the user asks.
- Ask before pushing. If upstream exists, use `git push`; if no upstream exists and the user approves pushing, use `git push -u origin HEAD`.
- Ask before committing, stashing, discarding, or otherwise modifying uncommitted changes.
- Let GitHub/CODEOWNERS add reviewers automatically. Do not add reviewers, projects, or milestones unless the user explicitly asks. The only label added by default is `no-qa` (see below); add other labels only when the user asks.
- Mention only the Jira ticket that belongs to this PR. Do not put any other ticket's URL or bare key (for example `FAX-1234`) in the title, body, comments, or commit messages: linking other tickets auto-moves them to `Deployed` when the app is deployed. Describe related work in words instead (for example "the follow-up webhook story").
- Final response after successful creation should be only the PR URL.

## Doximity prerequisites

- Run `gh auth status` before GitHub work. If it fails, fix/report auth before continuing.
- When Jira context is needed, run `acli auth status` before Jira work. If it fails, fall back to branch/commit-derived wording and a best-effort Jira link.
- Follow repo instructions such as `AGENTS.md`, `CLAUDE.md`, README, Makefile, and CI config.
- Use `dox-do` for Rails/app commands that need the Doximity container environment.
- Protect HIPAA/PII. Do not paste real PII into the PR body, QA instructions, screenshots, logs, or comments.

## Workflow

1. **Inspect branch state**
   - Collect:
     ```bash
     git status --short
     git branch --show-current
     git rev-parse --abbrev-ref --symbolic-full-name @{u}
     gh repo view --json nameWithOwner,defaultBranchRef
     ```
   - Warn about uncommitted changes and ask before committing/stashing/discarding. PRs should normally be created from committed changes only.
   - Check whether a PR already exists for the branch:
     ```bash
     gh pr status --json currentBranch
     ```
     If one exists, ask whether to return it, update it, or continue differently.

2. **Determine the base branch**
   - Prefer `master` for Doximity repos.
   - If `master` does not exist or the branch clearly targets another base, ask.
   - Compare against base:
     ```bash
     git fetch origin BASE_BRANCH
     git log --oneline origin/BASE_BRANCH..HEAD
     git diff --stat origin/BASE_BRANCH...HEAD
     git diff --name-only origin/BASE_BRANCH...HEAD
     ```
   - If there are no commits ahead of base, ask before proceeding.

3. **Resolve Jira context**
   - Detect Jira keys from the branch name and commits.
   - If multiple Jira keys are detected, ask which one to use.
   - If one key is detected and `acli auth status` succeeds, fetch the ticket title and link with `acli`.
   - Use the Jira ticket title as the PR title, prefixed with the ticket key: `KEY-1234: Ticket title`.
   - If Jira lookup fails, derive a clear title from branch/commits and still include a best-effort Jira link when a key is known.
   - Include the Jira ticket link in the PR body even if the repo template does not have a dedicated place for it.

4. **Load the PR template**
   - Find the repo template in common locations such as `.github/pull_request_template.md`, `.github/PULL_REQUEST_TEMPLATE.md`, `docs/pull_request_template.md`, or `.github/PULL_REQUEST_TEMPLATE/*.md`.
   - If multiple templates exist, ask which one to use.
   - If no template exists, tell the user and draft a Doximity-style fallback body with sections for Jira, Summary, QA Instructions, Screenshots, Risks/Rollout, and Notes.

5. **Draft the PR body**
   - Preserve the template structure and untouched sections.
   - In the doximity repo's template (`.github/pull_request_template.md`), check every box under **Ready for QA** and **Pre-Production** (`- [x]`). Leave the other sections as described above.
   - Fill a concise summary from the commits and diff.
   - Add or fill the Jira link.
   - Before creating the PR, scan the drafted title and body for Jira keys and remove any that are not this PR's own ticket.
   - Fill Risks/Rollout only when the template has those sections and useful information can be inferred; otherwise preserve placeholders.
   - For UI changes, attempt to run the app locally and add screenshots when practical and appropriate. If local app/screenshot capture is not practical, state in the body or notes that screenshots should be added from QA afterward.

6. **Write QA Instructions for humans**
   - QA Instructions are for human testers, not developers.
   - Do not include spec-test commands in QA Instructions.
   - Include required setup: feature flags, roles, seed data, dynamic/QA environment assumptions, admin steps, navigation, and expected results when relevant.
   - Use a detailed human flow: setup, steps, expected visible behavior, edge cases/regressions.
   - Format QA with dash bullets:
     - Use `-` bullets for tester actions/setup/navigation.
     - Use empty checkbox bullets `- [ ]` for things the tester should verify.
     - Prefer checkboxes for expected visible behavior, regressions, and acceptance checks.
     - Do not use numbered lists unless the repo template requires them.
   - Example:
     ```markdown
     - Enable the relevant feature flag for the test user.
     - Log in as a user with access to the updated flow.
     - Navigate to the updated page or workflow.
     - [ ] Confirm the new behavior is visible.
     - [ ] Confirm the previous behavior still works for the regression case.
     ```
   - If there are no human-testable aspects, write exactly `No QA`.
   - When the QA Instructions are `No QA`, add the `no-qa` label when creating the PR (`gh pr create --label no-qa`). Don't add it when there are QA steps.

7. **Run useful non-spec checks**
   - Run obvious non-spec checks when appropriate, such as lint, typecheck, formatting, build, or static validation.
   - Do not run spec tests unless the user explicitly asks. CI runs specs, and authors are expected to fix failing tests.
   - Summarize any non-spec checks in the PR body only if the template asks for validation/testing notes.

8. **Push and create the draft PR**
   - If the branch is not pushed, ask before pushing.
   - Use `git push` when upstream exists. Use `git push -u origin HEAD` only after approval when no upstream exists.
   - Show the proposed PR title and ask for approval.
   - Create the PR with a body file to preserve Markdown formatting. Write it to a unique directory so concurrent runs don't overwrite each other:
     ```bash
     PR_DIR=$(mktemp -d)
     # write the body to "$PR_DIR/pr-body.md", then:
     gh pr create --draft --base BASE_BRANCH --title "TITLE" --body-file "$PR_DIR/pr-body.md"   # add --label no-qa for a No QA PR
     rm -rf "$PR_DIR"
     ```
   - Do not add reviewers manually.

## Fallback PR body

Use this only when the repository has no PR template:

```markdown
## Jira
[KEY-1234](https://doximity.atlassian.net/browse/KEY-1234)

## Summary
- 

## QA Instructions
No QA

## Screenshots
N/A

## Risks / Rollout
- 

## Notes
- Draft PR created from the current branch.
```
