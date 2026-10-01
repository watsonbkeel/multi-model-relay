# Platform Notes

How each host platform (a) discovers this skill, (b) lets the orchestrator run one round on a
*specific* model, and (c) exposes reasoning effort. "Verified" means tested by the skill author;
"documented" means taken from the platform's docs and not yet exercised.

Quick matrix:

| Platform | Skill location | Sub-model per round | Effort per round | Recommended backend |
|---|---|---|---|---|
| WorkBuddy (desktop) | `~/.workbuddy/skills/<name>/` | `Agent` tool `model` param — **verified** | not passed; session effort applies | agent |
| Claude Code | `~/.claude/skills/<name>/` or `.claude/skills/` | subagent frontmatter `model` / per-call `model` — documented | subagent `effort` field — documented | agent |
| Codex CLI | `~/.agents/skills/<name>/` (legacy `~/.codex/skills/`) | `~/.codex/agents/<x>.toml` with `model` + `model_reasoning_effort` — documented | yes (toml) | agent (pre-defined agents) or script |
| opencode | `~/.config/opencode/skills/<name>/` | agents `~/.config/opencode/agents/<x>.md` with `model: provider/model#variant` — documented | variant suffix (`#high`) | agent (pre-defined agents) or script |
| Anything else with shell + Python | wherever SKILL.md is read | — | — | script |

Model identifiers must be exactly what the platform uses. When unsure, list them for the user.

---

## WorkBuddy

- **Skill path**: `~/.workbuddy/skills/multi-model-relay/` (a symlink to a git checkout is fine).
- **Available models**: built-in models plus custom ones in `~/.workbuddy/models.json`
  (list of objects with `id`, `name`, `url`, `apiKey`, `reasoning.supportedEfforts`). To list ids:
  `python3 -c "import json;print([m['id'] for m in json.load(open('$HOME/.workbuddy/models.json'))])"`.
- **Sub-model per round (verified)**: `Agent(subagent_type="general-purpose", model="<id>", prompt=...)`.
  The sub-agent reported the requested model and wrote files with its own tools.
- **Effort**: the `Agent` tool has no effort parameter; sub-agents inherit the session's effort.
  A skill-level `agents/relay-reviewer.md` (with `effort: high`) is shipped for platforms that
  load skill agents; on WorkBuddy it is not guaranteed to appear as a sub-agent type. Ask the
  user to set the session effort to the desired level, or use the script backend for exact
  control.
- **Script backend**: `relay_api.py` reads `~/.workbuddy/models.json` directly, so custom
  models work without extra configuration. `reasoning_effort` was accepted by an OpenAI-compatible
  relay (verified).
- **Sandbox quirk**: the sandboxed Python may raise `PermissionError(EEXIST)` from
  `Path.mkdir(exist_ok=True)` for existing directories; both scripts use a tolerant `ensure_dir()`.

## Claude Code

- **Skill path**: `~/.claude/skills/multi-model-relay/` (personal) or `.claude/skills/` (project).
- **Sub-model per round (documented)**: subagents are Markdown files in `~/.claude/agents/` or
  `.claude/agents/` with frontmatter `model: <alias | full model id | inherit>` and an optional
  `effort:` field. The `Task` tool also accepts a per-call model. Two options:
  1. Call the built-in general agent with the model set per call (if the running version
     supports it), passing the Template R prompt.
  2. Copy `agents/relay-reviewer.md` into `~/.claude/agents/` three times as `relay-a.md`,
     `relay-b.md`, `relay-c.md`, each with its own `model:`; invoke the matching one per round.
- **Effort**: `effort:` in the subagent frontmatter (documented). Session-level effort otherwise.
- **Unknown frontmatter keys** (such as `agent_created`) are tolerated per community reports;
  if the loader complains, delete that line from `SKILL.md`.

## OpenAI Codex CLI

- **Skill path**: `~/.agents/skills/multi-model-relay/` is the documented location;
  `~/.codex/skills/` is still scanned on older versions. `install.sh` links both.
- **Sub-model per round (documented)**: define agents in `~/.codex/agents/<name>.toml`:
  ```toml
  # ~/.codex/agents/relay-a.toml
  model = "<model id as Codex knows it>"
  model_reasoning_effort = "high"
  instructions = """
  You are one seat in a multi-model relay. Follow the <RELAY_ROUND> block literally. ...
  """
  ```
  Create `relay-a`, `relay-b`, `relay-c` and spawn the matching one per round with the
  Template R prompt. Global defaults: `[agents] default_subagent_model`,
  `default_subagent_reasoning_effort`.
- **Script backend** works unchanged (needs provider entries in `~/.multi-model-relay/config.json`
  with `api_key_env`, because Codex does not expose its own keys as a models file).

## opencode

- **Skill path**: `~/.config/opencode/skills/multi-model-relay/` (`skill/` singular also works;
  `~/.claude/skills/` and `~/.agents/skills/` are read too).
- **Sub-model per round (documented)**: agents live in `~/.config/opencode/agents/<name>.md`:
  ```markdown
  ---
  description: Relay seat A
  mode: subagent
  model: <provider>/<model>#high
  tools:
    write: true
    edit: true
  ---
  You are one seat in a multi-model relay. Follow the <RELAY_ROUND> block literally. ...
  ```
  Skills cannot switch models dynamically; pre-define `relay-a`, `relay-b`, `relay-c` and call
  the matching subagent per round. `install.sh --opencode-agents` renders these three files from
  `~/.multi-model-relay/config.json`.
- **Effort**: the `#variant` suffix (e.g. `#high`) when the provider defines variants.

## Script backend provider configuration

`~/.multi-model-relay/config.json` → `providers.custom[]`:

```json
{"id": "my-gpt", "url": "https://api.example.com/v1", "api_key_env": "MY_GPT_KEY",
 "model_id": "gpt-6-astra", "efforts": ["low", "medium", "high"]}
```

- `id` is what the user types as the slot model; `model_id` (optional) is what is sent to the
  API when it differs.
- Keys are read from the environment variable named in `api_key_env`. Storing keys inline
  (`api_key`) is supported but discouraged; the config directory is outside the repository and
  `.gitignore`d by default anyway.
- Alternatively pass `--base-url` / `--api-key` (or `RELAY_BASE_URL` / `RELAY_API_KEY`) for a
  one-off provider.
