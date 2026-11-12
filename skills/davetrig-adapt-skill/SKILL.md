---
name: davetrig-adapt-skill
description: Adapt a skill from another person's repo into this repo for personal use. Copies it, renames it to the davetrig- prefix, strips the original author, flags owner-specific values, and verifies no references to the original owner or name remain. Use when the user asks to adapt, port, copy, or personalize a skill from another repo or colleague.
---

# Adapt Skill

Port a skill into `~/my-ai-skills/skills/` for the user's own use. The source repo is never modified.

## Inputs

Confirm before starting:
- **Source:** path to the skill directory, for example `~/corey/my-ai-skills/cc-start-issue`.
- **New name:** default is `davetrig-` plus the original name with any leading `cc-` removed. Confirm it.

Do not start without both.

## Steps

1. **Inventory the source.** List files, excluding `.DS_Store`, `__pycache__/`, and `*.pyc`. Read `SKILL.md`, every file in `references/`, `scripts/`, and `agents/`. Record:
   - the `author` block and any owner name or email;
   - the original name, and every mention of it in other skills in this repo;
   - absolute paths (`/Users/<name>/...`), `~/work/...` paths, and sibling-skill paths;
   - business values: Jira filter IDs, project keys, Epic keys, repo names, team names.

2. **Copy.** Use `rsync -a --exclude .DS_Store --exclude __pycache__ --exclude '*.pyc' <source>/ ~/my-ai-skills/skills/<new-name>/`. Never write into the source.

3. **Rename.** Update:
   - the frontmatter `name:`;
   - `agents/openai.yaml` `default_prompt` (`$<old-name>` becomes `$<new-name>`);
   - the script docstring that names the skill.

   Keep storage paths that include the old name, such as cache directories. Renaming them orphans existing data. Mention them in the report instead.

4. **Remove the author block.** Delete `author:` and its children from the frontmatter. Do not substitute another name.

5. **Flag owner-specific values.** Do not replace business values on your own. List each one with its location and ask the user what it should become. Examples: filter IDs, project keys in examples, Epic keys, team-specific repo lists. Apply the answers.

6. **Verify.**
   - `grep -rn -i` for the original owner's name, username, and email, and for the original absolute home path. Expect no hits.
   - Check that every sibling skill the adapted skill names exists under `~/my-ai-skills/skills/`. Report any missing ones.
   - For each Python script, run `python3 -I -c 'import ast,sys; ast.parse(open(sys.argv[1]).read())' <file>`. Run `bash -n` on shell scripts.
   - Run the skill's own tests with `python3 -I` if it has any.
   - Note any `allowed-tools` MCP servers that are not currently connected.

7. **Install only with approval.** Running `setup.sh` changes the shared harness directory, so ask before running it. Never run it silently.

8. **Commit only when asked.** Leave changes uncommitted unless the user asks to commit.

## Report

Give a short list:
- the new path and name;
- what was renamed and where;
- the grep result for the original owner;
- owner-specific values still pending, if any;
- storage paths kept, and why;
- checks run and their results;
- missing sibling skills, if any.
