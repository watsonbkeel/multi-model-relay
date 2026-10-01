---
name: multi-model-relay
description: Relay-style multi-model review and optimization. The user picks three models A, B, C (any models available on the host agent platform) plus a reasoning effort; the skill then runs the same piece of work through A, then B, then C, each round seeing the original, the previous draft and the previous change notes, and writing its own improved draft without overwriting earlier ones. Works for articles, product requirement docs, generic documents and code-problem solution documents; for code tasks an optional step lets a chosen executor model implement the final solution and a verifier check it. This skill should be used when the user asks for "multi-model relay", "three models take turns", "A then B then C optimize", "多模型接力", "三个模型轮流优化", "多轮多模型评审", or wants several different LLMs to successively refine one document or solution. Not for single-model edits or parallel independent reviews.
agent_created: true
---

# Multi-Model Relay

Run one document or solution through several *different* models in sequence. Every round the
next model reads the original, the previous draft and the previous notes, then writes a better
draft plus structured notes into its own directory. The last model converges and produces the
final version. Default: 3 rounds, A -> B -> C, 3 paid calls.

Everything the skill needs is bundled:

| Resource | Purpose |
|---|---|
| `references/run-layout.md` | Run-directory contract (paths, file names, immutability, log events) |
| `references/round-prompts.md` | Prompt templates: relay round, finalizer, executor, verifier, probe |
| `references/task-types.md` | Task classification + default review dimensions + default goals |
| `references/platform-notes.md` | How to pick a sub-model on WorkBuddy, Claude Code, Codex, opencode |
| `scripts/relay_run.py` | Run-dir manager: `init`, `next-round`, `seal`, `verify`, `handoff`, `log`, `report` |
| `scripts/relay_api.py` | Script backend: one round via OpenAI-compatible chat completions |
| `assets/config.example.json` | Template for `~/.multi-model-relay/config.json` |

Run scripts with the platform's managed Python when available (any Python >= 3.9, stdlib only).

## Non-negotiable rules

1. **Budget before spend.** Never start a paid model call before stating the planned number of
   calls and receiving explicit confirmation. Report the actual count at the end.
2. **Never overwrite earlier drafts.** Each round writes only inside its own `rN-<model>/`
   directory; `relay_run.py seal` makes it read-only. Retries get a `-retryN` directory.
3. **Never skip a slot.** A failed round is retried once; if it fails again, pause and ask.
4. **Keep the orchestrator's context small.** Pass file paths to sub-agents, not content.
   Never read a full draft into the orchestrator; read only `## Handoff` via
   `relay_run.py handoff`.
5. **Stay in the user's language** for conversation; the drafts stay in the original's
   language unless the user asks otherwise.

## Step 0 — Load or ask for the configuration

1. Read `~/.multi-model-relay/config.json` if it exists (schema in `assets/config.example.json`).
2. Show the user a compact table and ask for changes in one message:

   ```
   Slot A: <model> (effort high)
   Slot B: <model> (effort high)
   Slot C: <model> (effort high)  <- finalizer
   Executor: <model|none>   Verifier: C   Backend: auto   Pause each round: no
   Reply "default" to use this, or say what to change (e.g. "B = gpt-6-astra", "effort medium").
   ```

3. If there is no config, ask which three models to use. List the models available on the host
   platform when possible (WorkBuddy: `~/.workbuddy/models.json` ids + built-ins; see
   `references/platform-notes.md`). Model identifiers must be exactly as the platform knows them.
4. Persist the confirmed configuration back to `~/.multi-model-relay/config.json` (create the
   directory; never store API keys there unless the user explicitly adds a custom provider).

## Step 1 — Collect the task

Ask (one message, skip what the user already gave):

- **Input**: file path(s), a directory (code tasks), or pasted text.
- **Goal / constraints**: one or two sentences plus red lines. If the user says "default", use
  the default goal and constraints for the task type in `references/task-types.md`.
- **Task type**: classify using the hints in `references/task-types.md`; confirm only if unsure.
- **Output language**: default = language of the original.
- **Code tasks**: also ask for the project root the executor may edit, and whether the
  execute + verify step is wanted now or after seeing the final document.

Then create the run directory:

```bash
python3 <skill>/scripts/relay_run.py init \
  --title "<short title>" --task-type <article|requirement-doc|code-solution|generic> \
  --goal "<goal>" --constraint "<c1>" --constraint "<c2>" --lang <zh|en|auto> \
  --rounds A,B,C --slot A=<modelA>:<effort> --slot B=<modelB>:<effort> --slot C=<modelC>:<effort> \
  --executor <model|none> --verifier-slot C --backend <agent|script> \
  --input <path> [--input <path2> ...] [--text "<pasted>"] --root <workspace>
```

The command prints JSON with `run`, `original_files`, `input_chars`, `primary_ext` and a
`warning` when the input exceeds 60k characters. On that warning, stop and ask: split by
section (run the relay per section), or trim context (drop previous notes, keep only original +
previous draft). Pasted text goes through `--text` (or `--text -` via stdin).

## Step 2 — Confirm the budget

State exactly:

> Planned paid calls: 3 (A, B, C). The optional execute + verify step would add 1 executor + 1
> verifier call. Models: A=<..>, B=<..>, C=<..>, effort <..>. Proceed?

Do not continue without a yes. If the user asks for "pause after each round", note it.

## Step 3 — Choose the backend

Decision order (details and platform specifics in `references/platform-notes.md`):

1. **agent** (default): the platform can spawn a sub-agent with a chosen model. On WorkBuddy use
   the `Agent` tool with `subagent_type: general-purpose` and `model: <slot model id>`
   (verified: the sub-agent runs on the requested model and can write files). Reasoning effort
   is not passed per call on WorkBuddy; the session-level effort applies. Tell the user once if
   their requested effort cannot be enforced per round.
2. **script**: use when the platform cannot pick a sub-model, when the user asks for it
   (`backend: script`), or when a model is only reachable through a custom provider. Each round
   is one `relay_api.py` call. It sends `reasoning_effort`, downgrades to the nearest supported
   level, and retries without the parameter if the provider rejects it.
3. Mixed is fine: e.g. rounds A/B via agent, round C via script.

If backend is `auto` and the platform is unknown, run the probe from `references/round-prompts.md`
(Template P, cheapest model, counts as 1 paid call — include it in the budget).

## Step 4 — Run the rounds

Loop until `next-round` returns `"status":"done"`:

1. `python3 <skill>/scripts/relay_run.py next-round --run <run> --create` → JSON `nx` with
   `round`, `slot`, `model`, `effort`, `role` (`relay` | `finalizer`), `round_dir`,
   `original_paths`, `prev_draft_path`, `prev_notes_path`, `final_dir`, `ext`.
   Save it to `<run>/next.json` (overwrite is fine; it is not a draft).
2. Build the prompt from `references/round-prompts.md`: shared header + Template R; append
   Template F when `role == finalizer`. Fill placeholders from `nx` and `task.md`; inject the
   review dimensions block for the task type. If the user typed feedback at a pause, add it as
   `extra_inputs`.
3. Dispatch:
   - **agent backend**: `Agent(subagent_type="general-purpose", model=nx.model, max_turns=20, prompt=<prompt>)`.
     If a `relay-reviewer` sub-agent type is available on the platform, use it instead of
     `general-purpose` (it carries `effort: high`).
   - **script backend**: `python3 <skill>/scripts/relay_api.py --next <run>/next.json`
     (add `--extra "<feedback>"` when needed; do not set `--max-tokens` — reasoning models can
     exhaust a small budget before emitting content).
4. Parse the single-line JSON the round returns. Accept only `status: ok` with existing
   `draft_path` and `notes_path`. Anything else (missing files, missing headings, wrong
   directory, failed status) counts as a failure.
5. Seal: `python3 <skill>/scripts/relay_run.py seal --dir <round_dir> --duration <seconds>`.
   A seal error listing missing headings means the round is incomplete → treat as failure.
6. Report progress to the user with the Handoff only:
   `python3 <skill>/scripts/relay_run.py handoff --notes <notes_path>` → show
   "Round N (<model>) done — <handoff text>". Do not paste the draft.
7. If `pause_each_round` is on: stop, show the handoff and the draft path, ask whether to
   continue, change the next slot, or add instructions. Feed instructions into `extra_inputs`.

**Failure handling.** On failure: log it
(`relay_run.py log --run <run> --event error --json '{"round":N,"message":"..."}'`), then run
`next-round --run <run> --round N --retry --create` and dispatch the same slot again once. If
the retry also fails, log it, stop, show the raw error and the directories involved, and ask the
user how to proceed (retry again, swap the model for this slot, or switch backend). Never move
on to the next slot with a missing round.

## Step 5 — Finalize and deliver

1. Confirm `final/FINAL<ext>` and `final/SUMMARY.md` exist (the finalizer writes them; if the
   finalizer wrote only `draft<ext>`, copy it to `final/FINAL<ext>` and write a short
   `SUMMARY.md` from its notes — this is the only post-hoc write allowed).
2. `python3 <skill>/scripts/relay_run.py log --run <run> --event calls --json '{"paid_calls":N,"planned_calls":M}'`
3. `python3 <skill>/scripts/relay_run.py report --run <run>` → `REPORT.md`.
4. Present `final/FINAL<ext>`, `final/SUMMARY.md` and `REPORT.md` to the user, and say in one
   paragraph: what the three rounds mainly changed, what the finalizer rejected, open questions,
   and the actual paid-call count. Offer the execute + verify step for code tasks.

## Step 6 (optional, code tasks) — Execute and verify

Only after the user confirms (budget: +2 calls, or +1 if they decline verification):

1. Ask which model executes (default `executor` from config). The executor needs tools, so it
   always runs on the agent backend; if the platform cannot do that, tell the user and stop.
2. Create `<run>/exec/`, write `exec/plan.md` (the final document path, project root, and any
   user instructions), then dispatch Template X with `model=<executor>`.
3. Seal nothing in `exec/` (the project itself changes); log `exec_done` with the executor's
   JSON.
4. Dispatch Template V with the verifier slot's model (default C; the config's `verifier_slot`
   may name A to reduce self-confirmation bias). Show `exec/verify.md` verdict to the user.
5. Regenerate `REPORT.md` and present `exec/result.md` + `exec/verify.md`.

## Changing rounds, order and count

`--rounds` accepts any sequence of slot letters, e.g. `A,B,C,A` (4 calls) or `A,C` (2 calls).
The last letter is always the finalizer. Each round's directory name uses the model id, so the
same model may appear twice. Adjust the budget statement accordingly.

## Troubleshooting

| Symptom | Action |
|---|---|
| Sub-agent returns prose instead of JSON | Treat as failure if files are missing; if files exist and seal passes, continue. |
| `seal` reports missing headings | Round incomplete → retry once with the same slot. |
| `relay_api.py` says "only reasoning tokens" | Re-run without `--max-tokens`, or lower effort. |
| Provider rejects `reasoning_effort` | Script retries automatically without it; note it in the final summary. |
| `next-round` says directory exists | Use `--retry`; never delete or reuse a sealed directory. |
| Input > 60k chars | Ask: split by section or trim context. Do not silently truncate. |
| Model id unknown to the platform | Show the available ids (`platform-notes.md`) and ask the user to pick. |
