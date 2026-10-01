#!/usr/bin/env python3
"""relay_run.py - run-directory manager for the multi-model-relay skill.

Implements the contract in references/run-layout.md. Pure standard library.

Sub-commands
  init        create <root>/relay-runs/<stamp>-<slug>/ with task.md, 00-original/, log.jsonl
  next-round  compute the next round directory, refuse if it already exists
  seal        hash + read-only every file in a round dir, append to log.jsonl
  verify      re-hash sealed dirs and report tampering
  log         append an arbitrary event to log.jsonl
  handoff     print only the "## Handoff" section of a notes.md (keeps orchestrator context small)
  report      generate REPORT.md from task.md + round dirs + log.jsonl

All commands print a single JSON object on stdout so an agent can parse the result.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import stat
import sys
from pathlib import Path

ROUND_DIR_RE = re.compile(r"^r(\d+)-(.+?)(-retry(\d+))?$")
NOTES_HEADINGS = ["## Assessment", "## Changes Made", "## Rejected", "## Open Questions", "## Handoff"]


# --------------------------------------------------------------------------- helpers
def now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def out(obj: dict, code: int = 0) -> None:
    print(json.dumps(obj, ensure_ascii=False))
    sys.exit(code)


def fail(msg: str, **extra) -> None:
    out({"status": "error", "message": msg, **extra}, 1)


def ensure_dir(path: Path, parents: bool = True) -> None:
    """mkdir that tolerates sandboxed interpreters which raise PermissionError/EEXIST for existing dirs."""
    if path.is_dir():
        return
    try:
        path.mkdir(parents=parents, exist_ok=True)
    except (FileExistsError, PermissionError):
        if not path.is_dir():
            raise


def slugify(text: str, max_words: int = 6) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())
    slug = "-".join(words[:max_words]) if words else ""
    return slug or "task"


def safe_model_id(model: str) -> str:
    return re.sub(r"[\s/\\:]+", "_", model.strip())


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def append_log(run: Path, event: dict) -> None:
    event = {"ts": now_iso(), **event}
    with (run / "log.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def read_log(run: Path) -> list[dict]:
    p = run / "log.jsonl"
    if not p.exists():
        return []
    events = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            events.append({"event": "unparsable", "raw": line[:200]})
    return events


def count_chars(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return 0


def list_round_dirs(run: Path) -> list[tuple[int, str, int, Path]]:
    """Return [(round_n, model, retry_n, path)] sorted by round then retry."""
    found = []
    for p in run.iterdir():
        if not p.is_dir():
            continue
        m = ROUND_DIR_RE.match(p.name)
        if m:
            found.append((int(m.group(1)), m.group(2), int(m.group(4) or 0), p))
    return sorted(found, key=lambda t: (t[0], t[2]))


def extract_section(md: str, heading: str) -> str:
    """Return the body of `heading` (e.g. '## Handoff') up to the next heading of same/higher level."""
    lines = md.splitlines()
    level = len(heading) - len(heading.lstrip("#"))
    body: list[str] = []
    capturing = False
    for line in lines:
        if line.strip().lower().startswith(heading.lower()):
            capturing = True
            continue
        if capturing and re.match(r"^#{1,%d}\s" % level, line):
            break
        if capturing:
            body.append(line)
    return "\n".join(body).strip()


# --------------------------------------------------------------------------- init
TASK_TEMPLATE = """# Relay Task

- **Title**: {title}
- **Task type**: {task_type}
- **Goal**: {goal}
- **Constraints / red lines**:
{constraints}
- **Output language**: {lang}
- **Rounds**: {rounds_arrow}
- **Slots**:
{slots}
- **Executor** (optional): {executor}
- **Verifier slot**: {verifier_slot}
- **Backend**: {backend}
- **Input**: {n_files} file(s), {n_chars} characters total
- **Created**: {created}
"""


def cmd_init(a: argparse.Namespace) -> None:
    root = Path(a.root or os.getcwd()).resolve()
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M")
    slug = a.slug or slugify(a.title)
    run = root / "relay-runs" / f"{stamp}-{slug}"
    if run.exists():
        # same minute, same slug: add a suffix instead of failing
        i = 2
        while (root / "relay-runs" / f"{stamp}-{slug}-{i}").exists():
            i += 1
        run = root / "relay-runs" / f"{stamp}-{slug}-{i}"
    orig = run / "00-original"
    orig.mkdir(parents=True, exist_ok=False)

    # collect inputs
    copied: list[tuple[str, int]] = []
    for f in a.input or []:
        src = Path(f).expanduser().resolve()
        if not src.exists():
            shutil.rmtree(run, ignore_errors=True)
            fail(f"input not found: {src}")
        if src.is_dir():
            for sub in sorted(p for p in src.rglob("*") if p.is_file()):
                rel = sub.relative_to(src)
                dst = orig / src.name / rel
                ensure_dir(dst.parent)
                shutil.copy2(sub, dst)
                copied.append((str(dst.relative_to(orig)), count_chars(dst)))
        else:
            dst = orig / src.name
            shutil.copy2(src, dst)
            copied.append((dst.name, count_chars(dst)))
    if a.text is not None:
        text = sys.stdin.read() if a.text == "-" else a.text
        dst = orig / "input.md"
        dst.write_text(text, encoding="utf-8")
        copied.append((dst.name, len(text)))
    if not copied:
        shutil.rmtree(run, ignore_errors=True)
        fail("no input given (use --input FILE... and/or --text TEXT|-)")

    n_chars = sum(c for _, c in copied)
    manifest = ["# Original input manifest", ""] + [f"- `{n}` — {c} chars" for n, c in copied]
    manifest += ["", f"Total: {len(copied)} file(s), {n_chars} characters"]
    (orig / "MANIFEST.md").write_text("\n".join(manifest) + "\n", encoding="utf-8")

    rounds = [r.strip().upper() for r in (a.rounds or "A,B,C").split(",") if r.strip()]
    slots: dict[str, dict] = {}
    for spec in a.slot or []:
        # format: A=model-id[:effort]
        m = re.match(r"^([A-Za-z])=([^:]+)(?::(\w+))?$", spec.strip())
        if not m:
            shutil.rmtree(run, ignore_errors=True)
            fail(f"bad --slot '{spec}', expected LETTER=model[:effort]")
        slots[m.group(1).upper()] = {"model": m.group(2).strip(), "effort": (m.group(3) or a.effort or "high")}
    missing = [r for r in rounds if r not in slots]
    if missing:
        shutil.rmtree(run, ignore_errors=True)
        fail(f"rounds reference slots without a model: {missing}")

    constraints = [c.strip() for c in (a.constraint or []) if c.strip()] or ["none"]
    slot_lines = []
    for letter in rounds:
        tag = "  <- finalizer" if letter == rounds[-1] else ""
        slot_lines.append(f"  - {letter}: {slots[letter]['model']} (effort: {slots[letter]['effort']}){tag}")
    primary_ext = Path(copied[0][0]).suffix or ".md"
    task_md = TASK_TEMPLATE.format(
        title=a.title,
        task_type=a.task_type,
        goal=a.goal or "(default for task type)",
        constraints="\n".join(f"  - {c}" for c in constraints),
        lang=a.lang or "auto",
        rounds_arrow=" -> ".join(rounds),
        slots="\n".join(slot_lines),
        executor=a.executor or "none",
        verifier_slot=(a.verifier_slot or rounds[-1]).upper(),
        backend=a.backend,
        n_files=len(copied),
        n_chars=n_chars,
        created=now_iso(),
    )
    (run / "task.md").write_text(task_md, encoding="utf-8")
    meta = {
        "title": a.title, "slug": slug, "task_type": a.task_type, "goal": a.goal,
        "constraints": constraints, "lang": a.lang or "auto", "rounds": rounds, "slots": slots,
        "executor": a.executor, "verifier_slot": (a.verifier_slot or rounds[-1]).upper(),
        "backend": a.backend, "primary_ext": primary_ext, "input_files": [n for n, _ in copied],
        "input_chars": n_chars, "created": now_iso(),
    }
    (run / "task.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    append_log(run, {"event": "init", "run": str(run), "task_type": a.task_type, "rounds": rounds,
                     "slots": slots, "input_chars": n_chars})
    warn = None
    if n_chars > a.max_input_chars:
        warn = f"input is {n_chars} chars (> {a.max_input_chars}); ask the user whether to split or trim context"
    out({"status": "ok", "run": str(run), "original_dir": str(orig), "task_md": str(run / "task.md"),
         "original_files": [str(orig / n) for n, _ in copied], "input_chars": n_chars,
         "primary_ext": primary_ext, "rounds": rounds, "slots": slots, "warning": warn})


# --------------------------------------------------------------------------- next-round
def cmd_next_round(a: argparse.Namespace) -> None:
    run = Path(a.run).resolve()
    if not (run / "task.json").exists():
        fail(f"not a run directory (task.json missing): {run}")
    meta = json.loads((run / "task.json").read_text(encoding="utf-8"))
    rounds: list[str] = meta["rounds"]
    existing = list_round_dirs(run)
    # determine the next round number = highest sealed/ok round + 1 unless --round given
    if a.round:
        n = a.round
    else:
        done = {r for r, _, _, p in existing if (p / "notes.md").exists() and (p / f"draft{meta['primary_ext']}").exists()}
        n = (max(done) + 1) if done else 1
    if n > len(rounds):
        out({"status": "done", "message": f"all {len(rounds)} rounds already have outputs", "run": str(run)})
    slot = rounds[n - 1]
    model = meta["slots"][slot]["model"]
    effort = meta["slots"][slot]["effort"]
    base = f"r{n}-{safe_model_id(model)}"
    target = run / base
    retry = 0
    if target.exists():
        if not a.retry:
            fail(f"round directory already exists: {target}. Pass --retry to create a -retryN directory.",
                 existing=str(target))
        retry = 1
        while (run / f"{base}-retry{retry}").exists():
            retry += 1
        target = run / f"{base}-retry{retry}"
    prev = None
    if n > 1:
        cands = [p for r, _, _, p in existing if r == n - 1 and (p / "notes.md").exists()]
        if not cands:
            fail(f"round {n-1} has no completed directory (notes.md missing); cannot start round {n}")
        prev = sorted(cands, key=lambda p: p.name)[-1]  # highest retry wins
    is_last = n == len(rounds)
    result = {
        "status": "ok", "run": str(run), "round": n, "round_total": len(rounds), "slot": slot,
        "model": model, "effort": effort, "role": "finalizer" if is_last else "relay",
        "round_dir": str(target), "retry": retry, "ext": meta["primary_ext"],
        "original_paths": [str(run / "00-original" / f) for f in meta["input_files"]],
        "prev_draft_path": str(prev / f"draft{meta['primary_ext']}") if prev else None,
        "prev_notes_path": str(prev / "notes.md") if prev else None,
        "final_dir": str(run / "final") if is_last else None,
        "task_type": meta["task_type"], "goal": meta["goal"], "constraints": meta["constraints"],
        "lang": meta["lang"],
    }
    if a.create:
        target.mkdir(parents=True, exist_ok=False)
        if is_last:
            ensure_dir(run / "final")
        append_log(run, {"event": "round_start", "round": n, "slot": slot, "model": model, "effort": effort,
                         "backend": a.backend or meta.get("backend"), "dir": target.name, "retry": retry})
    out(result)


# --------------------------------------------------------------------------- seal / verify
def _check_notes(path: Path) -> list[str]:
    if not path.exists():
        return ["notes.md missing"]
    text = path.read_text(encoding="utf-8", errors="replace")
    problems = [h for h in NOTES_HEADINGS if h.lower() not in text.lower()]
    return [f"missing heading: {h}" for h in problems]


def cmd_seal(a: argparse.Namespace) -> None:
    d = Path(a.dir).resolve()
    run = d.parent
    if not d.is_dir():
        fail(f"not a directory: {d}")
    files = sorted(p for p in d.rglob("*") if p.is_file())
    if not files:
        fail(f"nothing to seal in {d}")
    hashes = {str(p.relative_to(d)): sha256_file(p) for p in files}
    problems = _check_notes(d / "notes.md") if ROUND_DIR_RE.match(d.name) else []
    drafts = [p for p in files if p.name.startswith("draft")]
    if ROUND_DIR_RE.match(d.name) and not drafts:
        problems.append("draft.<ext> missing")
    if problems and not a.force:
        fail("round outputs incomplete; fix or pass --force", problems=problems, dir=str(d))
    ro_ok = True
    for p in files:
        try:
            p.chmod(p.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
        except Exception:
            ro_ok = False
    chars = count_chars(drafts[0]) if drafts else None
    append_log(run, {"event": "seal", "dir": d.name, "files": hashes, "read_only": ro_ok, "problems": problems})
    if ROUND_DIR_RE.match(d.name):
        m = ROUND_DIR_RE.match(d.name)
        append_log(run, {"event": "round_end", "round": int(m.group(1)), "dir": d.name,
                         "status": "ok" if not problems else "ok-with-warnings", "chars": chars,
                         "duration_s": a.duration})
    out({"status": "ok", "dir": str(d), "files": hashes, "read_only": ro_ok, "chars": chars, "warnings": problems})


def cmd_verify(a: argparse.Namespace) -> None:
    run = Path(a.run).resolve()
    seals = [e for e in read_log(run) if e.get("event") == "seal"]
    if not seals:
        fail("no seal events in log.jsonl")
    report = []
    ok = True
    for e in seals:
        d = run / e["dir"]
        for rel, h in e.get("files", {}).items():
            p = d / rel
            if not p.exists():
                report.append({"dir": e["dir"], "file": rel, "state": "missing"}); ok = False
            elif sha256_file(p) != h:
                report.append({"dir": e["dir"], "file": rel, "state": "modified"}); ok = False
            else:
                report.append({"dir": e["dir"], "file": rel, "state": "intact"})
    out({"status": "ok" if ok else "tampered", "checked": len(report),
         "issues": [r for r in report if r["state"] != "intact"]})


# --------------------------------------------------------------------------- log / handoff
def cmd_log(a: argparse.Namespace) -> None:
    run = Path(a.run).resolve()
    try:
        payload = json.loads(a.json) if a.json else {}
    except json.JSONDecodeError as e:
        fail(f"--json is not valid JSON: {e}")
    append_log(run, {"event": a.event, **payload})
    out({"status": "ok"})


def cmd_handoff(a: argparse.Namespace) -> None:
    p = Path(a.notes).resolve()
    if not p.exists():
        fail(f"notes not found: {p}")
    text = p.read_text(encoding="utf-8", errors="replace")
    section = extract_section(text, "## Handoff")
    if a.max_chars and len(section) > a.max_chars:
        section = section[: a.max_chars] + " …[truncated]"
    out({"status": "ok", "notes": str(p), "handoff": section or "(no Handoff section found)",
         "missing_headings": [h for h in NOTES_HEADINGS if h.lower() not in text.lower()]})


# --------------------------------------------------------------------------- report
def cmd_report(a: argparse.Namespace) -> None:
    run = Path(a.run).resolve()
    meta = json.loads((run / "task.json").read_text(encoding="utf-8"))
    events = read_log(run)
    ends = {e["dir"]: e for e in events if e.get("event") == "round_end"}
    starts = {e["dir"]: e for e in events if e.get("event") == "round_start"}
    orig_chars = meta.get("input_chars", 0)
    rows = []
    changelog = []
    for n, model, retry, p in list_round_dirs(run):
        draft = p / f"draft{meta['primary_ext']}"
        notes = p / "notes.md"
        if not notes.exists():
            rows.append(f"| {n} | {model} | — | — | — | incomplete (no notes.md) |")
            continue
        chars = count_chars(draft) if draft.exists() else 0
        delta = (chars - orig_chars) / orig_chars * 100 if orig_chars else 0
        st = starts.get(p.name, {})
        en = ends.get(p.name, {})
        dur = en.get("duration_s")
        rows.append(f"| {n} | {model}{' (retry %d)' % retry if retry else ''} | {st.get('effort','?')} | "
                    f"{st.get('backend','?')} | {chars} ({delta:+.1f}%) | {en.get('status','?')}"
                    f"{' · %.0fs' % dur if isinstance(dur,(int,float)) else ''} |")
        text = notes.read_text(encoding="utf-8", errors="replace")
        changelog.append(f"### Round {n} — {model}\n\n**Changes made**\n\n"
                         f"{extract_section(text, '## Changes Made') or '(none listed)'}\n\n"
                         f"**Rejected**\n\n{extract_section(text, '## Rejected') or '(none listed)'}\n\n"
                         f"**Open questions**\n\n{extract_section(text, '## Open Questions') or 'none'}\n")
    calls = [e for e in events if e.get("event") == "calls"]
    paid = calls[-1].get("paid_calls") if calls else sum(1 for e in events if e.get("event") == "round_start")
    planned = calls[-1].get("planned_calls") if calls else len(meta["rounds"])
    final = run / "final" / f"FINAL{meta['primary_ext']}"
    summary = run / "final" / "SUMMARY.md"
    exec_dir = run / "exec"
    lines = [
        f"# Relay Report — {meta['title']}", "",
        f"- Task type: `{meta['task_type']}` · Rounds: {' -> '.join(meta['rounds'])} · Backend: {meta.get('backend')}",
        f"- Goal: {meta.get('goal') or '(default)'}",
        f"- Constraints: {'; '.join(meta.get('constraints') or ['none'])}",
        f"- Original: {len(meta['input_files'])} file(s), {orig_chars} chars",
        f"- Paid model calls: **{paid}** (planned {planned})",
        f"- Final draft: `{final.relative_to(run)}`" if final.exists() else "- Final draft: (not produced)",
        f"- Summary: `{summary.relative_to(run)}`" if summary.exists() else "",
        "", "## Rounds", "",
        "| # | Model | Effort | Backend | Draft chars (Δ vs original) | Status |",
        "|---|---|---|---|---|---|", *rows, "",
    ]
    if exec_dir.exists():
        lines += ["## Execution (step 4)", ""]
        for name in ("plan.md", "result.md", "verify.md"):
            if (exec_dir / name).exists():
                lines.append(f"- `exec/{name}`")
        lines.append("")
    lines += ["## Consolidated change log", ""] + changelog
    errs = [e for e in events if e.get("event") in ("error", "retry")]
    if errs:
        lines += ["## Errors / retries", ""] + [f"- {e.get('ts')}: {e.get('event')} round {e.get('round')} — {e.get('reason') or e.get('message')}" for e in errs] + [""]
    (run / "REPORT.md").write_text("\n".join(l for l in lines if l is not None), encoding="utf-8")
    out({"status": "ok", "report": str(run / "REPORT.md"), "rounds": len(rows), "paid_calls": paid})


# --------------------------------------------------------------------------- cli
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="create a run directory")
    p.add_argument("--title", required=True)
    p.add_argument("--task-type", default="generic", choices=["article", "requirement-doc", "code-solution", "generic"])
    p.add_argument("--goal")
    p.add_argument("--constraint", action="append", help="repeatable")
    p.add_argument("--lang", help="zh | en | ... (default auto = same as original)")
    p.add_argument("--rounds", default="A,B,C", help="comma-separated slot letters in order")
    p.add_argument("--slot", action="append", help="repeatable: A=model-id[:effort]")
    p.add_argument("--effort", default="high", help="default effort for slots without one")
    p.add_argument("--executor")
    p.add_argument("--verifier-slot")
    p.add_argument("--backend", default="agent", choices=["agent", "script"])
    p.add_argument("--input", action="append", help="repeatable: file or directory")
    p.add_argument("--text", help="pasted text, or '-' to read stdin")
    p.add_argument("--slug")
    p.add_argument("--root", help="workspace root (default: cwd)")
    p.add_argument("--max-input-chars", type=int, default=60000)
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("next-round", help="compute (and optionally create) the next round directory")
    p.add_argument("--run", required=True)
    p.add_argument("--round", type=int, help="force a specific round number")
    p.add_argument("--retry", action="store_true", help="allow creating a -retryN directory")
    p.add_argument("--create", action="store_true", help="create the directory and log round_start")
    p.add_argument("--backend")
    p.set_defaults(fn=cmd_next_round)

    p = sub.add_parser("seal", help="hash + read-only a round directory")
    p.add_argument("--dir", required=True)
    p.add_argument("--duration", type=float, help="seconds the round took (for the report)")
    p.add_argument("--force", action="store_true", help="seal even if notes/draft are incomplete")
    p.set_defaults(fn=cmd_seal)

    p = sub.add_parser("verify", help="check sealed directories for tampering")
    p.add_argument("--run", required=True)
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("log", help="append an event")
    p.add_argument("--run", required=True)
    p.add_argument("--event", required=True)
    p.add_argument("--json", help="extra fields as a JSON object")
    p.set_defaults(fn=cmd_log)

    p = sub.add_parser("handoff", help="print the Handoff section of a notes.md")
    p.add_argument("--notes", required=True)
    p.add_argument("--max-chars", type=int, default=1200)
    p.set_defaults(fn=cmd_handoff)

    p = sub.add_parser("report", help="write REPORT.md")
    p.add_argument("--run", required=True)
    p.set_defaults(fn=cmd_report)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
