#!/usr/bin/env bash
# Link each skill in this repo into the shared skills directory that harnesses read.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd -P)"
TARGET_DIR="${SKILLS_TARGET_DIR:-$HOME/.config/dox-agent/skills}"

mkdir -p "$TARGET_DIR"

for skill in "$REPO_DIR"/skills/*/; do
  skill="${skill%/}"
  name="$(basename "$skill")"
  link="$TARGET_DIR/$name"

  if [ -L "$link" ] && [ "$(readlink "$link")" = "$skill" ]; then
    echo "ok       $name"
  elif [ -e "$link" ] || [ -L "$link" ]; then
    echo "skip     $name (existing entry at $link is not this skill)" >&2
  else
    ln -s "$skill" "$link"
    echo "linked   $name"
  fi
done
