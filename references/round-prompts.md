# Round Prompt Templates

Templates for every call the skill makes. The orchestrator fills the `{placeholders}` and sends
the result as the sub-agent prompt (agent backend) or as the user message (script backend —
`scripts/relay_api.py` renders the same templates from `prompt-templates/` embedded in the
script, so keep both in sync when editing).

Design rules behind these templates:

- **Paths, not content.** The sub-agent reads the inputs itself; the orchestrator never pastes
  the original or drafts into its own context. (Script backend is the exception: it inlines the
  files because the model has no tools.)
- **Files, not return values.** The sub-agent writes `draft.<ext>` and `notes.md` and returns a
  single-line JSON. Long prose never travels through the return channel.
- **One writable path.** Each round may write only inside its own directory.
- **Language lock.** `{lang}` is the output language; the template language is English but the
  draft must stay in `{lang}`.
- **Same role, different seat.** Rounds 1-N all use the *relay* template; the last round adds the
  *finalizer* block. Executor / verifier templates are only for step 4.

---

## Shared header (prepended to every template)

```
<RELAY_ROUND n="{round_n}" of="{round_total}" role="{role}" slot="{slot}" model="{model}" effort="{effort}" lang="{lang}">
You are model {slot} in a multi-model relay. Each round a different model improves the same
piece of work. You see the ORIGINAL, the PREVIOUS DRAFT and the PREVIOUS NOTES. Produce a better
draft and explain exactly what you changed. Work carefully and think deeply before writing;
quality matters more than speed.

## Goal & Constraints
Task type: {task_type}
Goal: {goal}
Constraints / red lines (never violate):
{constraints_bullets}
Output language for the draft: {lang}

## Review Dimensions
{review_dimensions}
```

`{review_dimensions}` comes from `references/task-types.md` for the task type (or the user's
custom list).

---

## Template R — relay round (rounds 1 .. N-1, and the first half of round N)

```
## Inputs (read them yourself; absolute paths)
- ORIGINAL: {original_paths}
- PREVIOUS DRAFT: {prev_draft_path}            ← "none (you are round 1)" in round 1
- PREVIOUS NOTES: {prev_notes_path}            ← "none (you are round 1)" in round 1
{extra_inputs}                                  ← e.g. error logs, user feedback added at a pause

## Procedure
1. Read the ORIGINAL fully. In round 1 this is your only input.
2. Read the PREVIOUS DRAFT and PREVIOUS NOTES. Judge each previous change against the goal:
   keep what helps, revert what hurts, and say so in "Rejected".
3. Identify the highest-impact remaining problems using the review dimensions.
4. Write the new draft. Preserve the author's voice, facts, structure and intent unless the goal
   says otherwise. Do not pad. Do not add commentary inside the draft.
5. Write notes.md with the required headings.

## Your Outputs (write these files; create the directory if needed)
- {round_dir}/draft{ext}   — the complete revised content, nothing else
- {round_dir}/notes.md     — exactly these headings, in this order:
    # Round {round_n} notes — {model}
    ## Assessment        (3-8 bullets on the incoming draft vs the goal)
    ## Changes Made      (numbered; each = where + what + why)
    ## Rejected          (previous changes or ideas you did NOT keep, with reasons; "none" allowed)
    ## Open Questions    (decisions only the human can make; "none" allowed)
    ## Handoff           (<= 150 words for the next model: what is solid, what still needs work)

## Return to orchestrator
Return ONLY one line of JSON, nothing before or after:
{"status":"ok","draft_path":"{round_dir}/draft{ext}","notes_path":"{round_dir}/notes.md","chars":<int>,"summary":"<= 150 words"}
If you could not finish: {"status":"failed","reason":"<short reason>"}

## Hard rules
- Write ONLY inside {round_dir}. Never modify, rename or delete anything else.
- Keep the draft in {lang}. Do not translate, do not switch languages mid-text.
- Do not invent facts, citations, numbers, APIs or test results.
- Do not shorten or expand the draft beyond the constraints.
- Do not include the notes or any meta-commentary inside draft{ext}.
</RELAY_ROUND>
```

---

## Template F — finalizer block (appended to Template R in the last round)

Insert before "## Your Outputs":

```
## Finalizer duties (you are the last round)
- Converge, do not restart. Prefer consolidating and polishing over sweeping rewrites. Large
  structural changes are allowed only if a review dimension is clearly still failing.
- In "Rejected", explicitly list changes from earlier rounds that you reverted or declined, and why.
- Resolve contradictions between earlier rounds; choose one approach and state it.
- Additionally write:
  - {final_dir}/FINAL{ext}     — identical to your draft{ext}
  - {final_dir}/SUMMARY.md     — sections: "What changed overall" (grouped by theme, 5-15 bullets),
    "Decisions taken" (where rounds disagreed), "Open questions for the author", "Suggested next step"
```

And extend the return JSON with `"final_path":"{final_dir}/FINAL{ext}","summary_path":"{final_dir}/SUMMARY.md"`.

---

## Template X — executor (optional step 4, code tasks)

```
<RELAY_EXEC model="{model}" effort="{effort}">
You are the executor. A solution document was produced by a 3-model relay. Implement it.

## Inputs
- SOLUTION DOCUMENT (authoritative): {final_path}
- SUMMARY: {summary_path}
- PROBLEM STATEMENT & ORIGINAL MATERIALS: {original_paths}
- PROJECT ROOT: {project_root}

## Procedure
1. Read the solution document fully. If it is ambiguous or conflicts with the code you find,
   choose the safest interpretation and record the decision in result.md.
2. Implement the changes in the project. Keep edits minimal and scoped to the document.
3. Run the verification the document asks for (tests, build, repro script). Record real output.
4. Do not refactor unrelated code. Do not change the solution's approach silently.

## Your Outputs
- {exec_dir}/result.md with headings:
    ## Files changed       (path + one-line purpose each)
    ## Commands run        (command + exit code + trimmed output)
    ## Deviations          (anything done differently from the document, and why; "none" allowed)
    ## Status              (done | partial | blocked, with one sentence)

## Return to orchestrator
One line of JSON only:
{"status":"done|partial|blocked","result_path":"{exec_dir}/result.md","files_changed":<int>,"summary":"<= 100 words"}

## Hard rules
- Write project files only inside {project_root}; write reports only inside {exec_dir}.
- Never touch {run_dir} round directories or final/.
- Never run destructive commands (rm -rf, git reset --hard, force push, DB drops).
- Never fabricate test output.
</RELAY_EXEC>
```

---

## Template V — verifier (optional, after executor)

```
<RELAY_VERIFY model="{model}" effort="{effort}">
You are the verifier. Check whether the executor implemented the solution document correctly.

## Inputs
- SOLUTION DOCUMENT: {final_path}
- EXECUTOR REPORT: {exec_result_path}
- PROJECT ROOT: {project_root}
- ORIGINAL PROBLEM: {original_paths}

## Procedure
1. Compare the executor report against the solution document item by item.
2. Inspect the actual changed files (do not trust the report alone).
3. Re-run the key verification command(s) if they are cheap and safe; record real output.
4. Judge: does the original problem appear solved? What risks remain?

## Your Outputs
- {exec_dir}/verify.md with headings:
    ## Verdict             (pass | pass-with-notes | fail, one sentence)
    ## Checked items       (table: document item | implemented? | evidence)
    ## Issues found        (severity + location + fix suggestion; "none" allowed)
    ## Residual risks

## Return to orchestrator
One line of JSON only:
{"status":"pass|pass-with-notes|fail","verify_path":"{exec_dir}/verify.md","summary":"<= 100 words"}

## Hard rules
- Read-only on the project unless re-running a test requires temp files. Write only inside {exec_dir}.
- Do not fix issues yourself; report them.
</RELAY_VERIFY>
```

---

## Template P — capability probe (used once per platform, see SKILL.md "Backend decision")

```
<RELAY_PROBE>
Reply with ONE line of JSON and nothing else:
{"model":"<the model name you believe you are>","can_write_files":<true|false>,"effort_hint":"<any effort/reasoning setting you were given, or unknown>"}
Then, only if you have a file-writing tool, write that same JSON line to {probe_dir}/ok.txt.
</RELAY_PROBE>
```

The probe is deliberately tiny (a few hundred tokens round trip). Run it with the cheapest
configured model.

---

## Placeholder reference

| Placeholder | Source |
|---|---|
| `round_n`, `round_total`, `slot`, `model`, `effort`, `role` | orchestrator loop state |
| `task_type`, `goal`, `constraints_bullets`, `lang` | `task.md` |
| `review_dimensions` | `references/task-types.md` → section for `task_type`, or user-provided list |
| `original_paths` | files under `00-original/` (absolute paths, one per line) |
| `prev_draft_path`, `prev_notes_path` | previous round directory, or "none (you are round 1)" |
| `extra_inputs` | optional: error logs, user feedback typed at a pause ("- USER FEEDBACK: ...") |
| `round_dir`, `final_dir`, `exec_dir`, `run_dir`, `probe_dir` | `relay_run.py next-round` / layout |
| `ext` | primary input extension including the dot (`.md`, `.py`) |
| `project_root` | code tasks: the repository or folder the executor may edit |
