#!/usr/bin/env bash
# install.sh - link this skill into the skill directories of supported agent platforms.
#
# Usage:
#   ./install.sh                 # link into every platform directory that exists (or --all to create them)
#   ./install.sh --all           # create and link all platform directories
#   ./install.sh --workbuddy --claude --codex --opencode   # pick platforms
#   ./install.sh --opencode-agents   # also render relay-a/b/c agent files for opencode from your config
#   ./install.sh --uninstall     # remove the links created by this script
#
# Nothing is copied; a symlink points at this checkout so `git pull` updates every platform.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME="multi-model-relay"
CONFIG="${RELAY_CONFIG:-$HOME/.multi-model-relay/config.json}"

# platform -> skills directory (plain function: macOS ships bash 3.2 without associative arrays)
dir_for() {
  case "$1" in
    workbuddy)    echo "$HOME/.workbuddy/skills" ;;
    claude)       echo "$HOME/.claude/skills" ;;
    codex)        echo "$HOME/.agents/skills" ;;
    codex_legacy) echo "$HOME/.codex/skills" ;;
    opencode)     echo "$HOME/.config/opencode/skills" ;;
    *) echo ""; return 1 ;;
  esac
}

ALL=0; UNINSTALL=0; OPENCODE_AGENTS=0; SELECTED=()
for arg in "$@"; do
  case "$arg" in
    --all) ALL=1 ;;
    --uninstall) UNINSTALL=1 ;;
    --opencode-agents) OPENCODE_AGENTS=1; SELECTED+=(opencode) ;;
    --workbuddy|--claude|--codex|--opencode) SELECTED+=("${arg#--}") ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done
if [[ ${#SELECTED[@]} -eq 0 ]]; then SELECTED=(workbuddy claude codex opencode); fi

link_into() {
  local dir target
  dir="$1"
  target="$dir/$NAME"
  if [[ -z "$dir" ]]; then echo "skip   (unknown platform)"; return; fi
  if [[ $UNINSTALL -eq 1 ]]; then
    if [[ -L "$target" ]]; then rm "$target"; echo "removed $target"; fi
    return
  fi
  if [[ ! -d "$dir" ]]; then
    if [[ $ALL -eq 1 ]]; then mkdir -p "$dir"; else echo "skip   $dir (not present; use --all to create)"; return; fi
  fi
  if [[ -e "$target" && ! -L "$target" ]]; then
    echo "exists $target is a real directory, not touching it" >&2; return
  fi
  ln -sfn "$HERE" "$target"
  echo "linked $target -> $HERE"
}

for p in "${SELECTED[@]}"; do
  case "$p" in
    codex) link_into "$(dir_for codex)"; link_into "$(dir_for codex_legacy)" ;;
    *) link_into "$(dir_for "$p")" ;;
  esac
done

# Optional: render opencode agents (relay-a/b/c) from the user's config so each round can pin a model.
if [[ $OPENCODE_AGENTS -eq 1 && $UNINSTALL -eq 0 ]]; then
  if [[ ! -f "$CONFIG" ]]; then
    echo "no config at $CONFIG; copy assets/config.example.json there first" >&2; exit 1
  fi
  AGENT_DIR="$HOME/.config/opencode/agents"; mkdir -p "$AGENT_DIR"
  python3 - "$CONFIG" "$AGENT_DIR" <<'PY'
import json, sys, pathlib
cfg = json.load(open(sys.argv[1])); out = pathlib.Path(sys.argv[2])
instructions = """You are one seat in a multi-model relay. The prompt you receive is a <RELAY_ROUND> block.
Follow it literally: read every input path, write only inside the single round directory it names,
keep the draft in the requested language, use the required notes headings, and end with ONE line of JSON."""
for letter, slot in cfg.get("slots", {}).items():
    model = slot.get("model", ""); effort = slot.get("effort", "high")
    if not model:
        continue
    variant = f"#{effort}" if effort else ""
    text = f"""---
description: Multi-model relay seat {letter}
mode: subagent
model: {model}{variant}
tools:
  write: true
  edit: true
  bash: false
---
{instructions}
"""
    p = out / f"relay-{letter.lower()}.md"; p.write_text(text); print(f"wrote  {p} (model {model}{variant})")
print("note: opencode expects model as provider/model; edit the files if your ids differ.")
PY
fi

echo "done."
