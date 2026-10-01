#!/usr/bin/env python3
"""relay_api.py - script backend for the multi-model-relay skill.

Runs ONE relay round by calling an OpenAI-compatible `POST /v1/chat/completions` directly.
Use it when the host agent platform cannot spawn a sub-agent with a chosen model, or when the
user explicitly asks for backend=script. Pure standard library (urllib), no external deps.

Provider resolution (first hit wins):
  1. --base-url / --api-key (or env RELAY_BASE_URL / RELAY_API_KEY)
  2. ~/.multi-model-relay/config.json  -> providers.custom[] entry whose id == model
  3. ~/.workbuddy/models.json          -> entry whose id/name == model (WorkBuddy custom models)

Typical use (after `relay_run.py next-round --create` returned a JSON object saved in next.json):
  relay_api.py --next next.json
  relay_api.py --next next.json --effort high --timeout 900
  relay_api.py --next next.json --dry-run            # render the prompt, no network call

Outputs: writes <round_dir>/draft<ext>, <round_dir>/notes.md, <round_dir>/raw_response.md
and prints one JSON line (same shape as the agent backend's return JSON).
Never prints or logs API keys.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
CONFIG_PATH = Path(os.environ.get("RELAY_CONFIG", "~/.multi-model-relay/config.json")).expanduser()
WORKBUDDY_MODELS = Path("~/.workbuddy/models.json").expanduser()
EFFORT_ORDER = ["minimal", "low", "medium", "high", "xhigh", "max"]
DRAFT_BEGIN, DRAFT_END = "<<<DRAFT_BEGIN>>>", "<<<DRAFT_END>>>"
NOTES_BEGIN, NOTES_END = "<<<NOTES_BEGIN>>>", "<<<NOTES_END>>>"
SUMMARY_BEGIN, SUMMARY_END = "<<<SUMMARY_BEGIN>>>", "<<<SUMMARY_END>>>"


def out(obj: dict, code: int = 0) -> None:
    print(json.dumps(obj, ensure_ascii=False))
    sys.exit(code)


def fail(msg: str, **extra) -> None:
    out({"status": "failed", "reason": msg, **extra}, 1)


def ensure_dir(path: Path) -> None:
    """mkdir that tolerates sandboxed interpreters which raise PermissionError/EEXIST for existing dirs."""
    if path.is_dir():
        return
    try:
        path.mkdir(parents=True, exist_ok=True)
    except (FileExistsError, PermissionError):
        if not path.is_dir():
            raise


def mask(s: str | None) -> str:
    if not s:
        return "(none)"
    return s[:4] + "…" + s[-3:] if len(s) > 10 else "***"


# --------------------------------------------------------------------------- providers
def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as e:
        fail(f"invalid JSON in {path}: {e}")


def resolve_provider(model: str, base_url: str | None, api_key: str | None) -> dict:
    if base_url or api_key:
        if not (base_url and api_key):
            fail("--base-url and --api-key must be given together (or via RELAY_BASE_URL / RELAY_API_KEY)")
        return {"id": model, "url": base_url, "apiKey": api_key, "efforts": None, "source": "cli"}
    cfg = load_json(CONFIG_PATH) or {}
    for p in (cfg.get("providers") or {}).get("custom") or []:
        if p.get("id") == model or p.get("name") == model:
            key = os.environ.get(p.get("api_key_env", ""), "") or p.get("api_key", "")
            if not key:
                fail(f"provider '{model}' found in config but env var '{p.get('api_key_env')}' is empty")
            return {"id": p.get("model_id") or model, "url": p["url"], "apiKey": key,
                    "efforts": p.get("efforts"), "source": "config"}
    wb = load_json(WORKBUDDY_MODELS)
    entries = wb if isinstance(wb, list) else (wb or {}).get("models") or []
    for m in entries:
        if m.get("id") == model or m.get("name") == model:
            return {"id": m["id"], "url": m["url"], "apiKey": m.get("apiKey", ""),
                    "efforts": (m.get("reasoning") or {}).get("supportedEfforts"), "source": "workbuddy"}
    fail(f"model '{model}' not found in CLI args, {CONFIG_PATH} or {WORKBUDDY_MODELS}")


def choose_effort(requested: str | None, supported: list[str] | None) -> tuple[str | None, str | None]:
    """Return (effort_to_send, note). Downgrade to the nearest supported level if needed."""
    if not requested or requested == "none":
        return None, None
    if not supported:
        return requested, None
    if requested in supported:
        return requested, None
    if requested not in EFFORT_ORDER:
        return None, f"unknown effort '{requested}', sent without reasoning_effort"
    idx = EFFORT_ORDER.index(requested)
    for cand in EFFORT_ORDER[idx::-1]:  # walk down
        if cand in supported:
            return cand, f"effort '{requested}' not supported by model, downgraded to '{cand}'"
    for cand in EFFORT_ORDER[idx:]:  # walk up
        if cand in supported:
            return cand, f"effort '{requested}' not supported by model, upgraded to '{cand}'"
    return None, "no overlapping effort levels, sent without reasoning_effort"


# --------------------------------------------------------------------------- prompt
def read_text(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")


def load_review_dimensions(task_type: str) -> str:
    ref = SKILL_ROOT / "references" / "task-types.md"
    if not ref.exists():
        return "(review dimensions file missing)"
    text = ref.read_text(encoding="utf-8")
    m = re.search(rf"^## {re.escape(task_type)}\s*\n```\n(.*?)\n```", text, re.S | re.M)
    return m.group(1).strip() if m else "(no dimensions for task type)"


def build_prompt(nx: dict, effort: str | None, extra: str | None, lang: str) -> tuple[str, str]:
    """Return (system, user). Inlines file contents because the model has no tools here."""
    n, total = nx["round"], nx["round_total"]
    role = nx["role"]
    constraints = "\n".join(f"- {c}" for c in (nx.get("constraints") or ["none"]))
    originals = "\n\n".join(f"### FILE: {Path(p).name}\n{read_text(p)}" for p in nx["original_paths"])
    prev_draft = read_text(nx["prev_draft_path"]) if nx.get("prev_draft_path") else None
    prev_notes = read_text(nx["prev_notes_path"]) if nx.get("prev_notes_path") else None
    goal = nx.get("goal") or "(use the default goal for the task type)"

    system = (
        f"You are model {nx['slot']} in a multi-model relay (round {n} of {total}, role: {role}). "
        "Each round a different model improves the same piece of work. Work carefully and think deeply; "
        "quality matters more than speed. Follow the output format exactly."
    )
    parts = [
        f"## Goal & Constraints\nTask type: {nx['task_type']}\nGoal: {goal}\nConstraints / red lines (never violate):\n{constraints}\n"
        f"Output language for the draft: {lang}\n",
        f"## Review Dimensions\n{load_review_dimensions(nx['task_type'])}\n",
        f"## ORIGINAL\n{originals}\n",
    ]
    if prev_draft is not None:
        parts.append(f"## PREVIOUS DRAFT (round {n-1})\n{prev_draft}\n")
        parts.append(f"## PREVIOUS NOTES (round {n-1})\n{prev_notes or '(missing)'}\n")
    else:
        parts.append("## PREVIOUS DRAFT\nnone (you are round 1)\n")
    if extra:
        parts.append(f"## EXTRA INPUT FROM THE USER\n{extra}\n")
    procedure = (
        "## Procedure\n"
        "1. Read the ORIGINAL fully.\n"
        "2. If there is a PREVIOUS DRAFT, judge each previous change against the goal: keep what helps, revert what hurts, and say so under Rejected.\n"
        "3. Identify the highest-impact remaining problems using the review dimensions.\n"
        "4. Write the new draft. Preserve the author's voice, facts, structure and intent unless the goal says otherwise. No padding, no commentary inside the draft.\n"
    )
    if role == "finalizer":
        procedure += (
            "5. FINALIZER DUTIES: converge, do not restart. Prefer consolidating and polishing over sweeping rewrites. "
            "Under Rejected, explicitly list changes from earlier rounds you reverted or declined, and why. "
            "Resolve contradictions between rounds; choose one approach and state it.\n"
        )
    fmt = (
        "## Output format (exactly, in this order)\n"
        f"{DRAFT_BEGIN}\n<the complete revised content, nothing else>\n{DRAFT_END}\n"
        f"{NOTES_BEGIN}\n# Round {n} notes — {nx['model']}\n\n## Assessment\n(3-8 bullets)\n\n## Changes Made\n(numbered; where + what + why)\n\n"
        "## Rejected\n(previous changes or ideas not kept, with reasons; 'none' allowed)\n\n## Open Questions\n('none' allowed)\n\n"
        f"## Handoff\n(<= 150 words for the next model)\n{NOTES_END}\n"
    )
    if role == "finalizer":
        fmt += (
            f"{SUMMARY_BEGIN}\n## What changed overall\n(5-15 bullets grouped by theme)\n\n## Decisions taken\n(where rounds disagreed)\n\n"
            f"## Open questions for the author\n\n## Suggested next step\n{SUMMARY_END}\n"
        )
    rules = (
        "## Hard rules\n"
        f"- Keep the draft in {lang}. Do not translate or switch languages mid-text.\n"
        "- Do not invent facts, citations, numbers, APIs or test results.\n"
        "- Do not shorten or expand beyond the constraints.\n"
        "- Output nothing outside the markers.\n"
    )
    parts += [procedure, fmt, rules]
    return system, "\n".join(parts)


def detect_lang(paths: list[str]) -> str:
    sample = "".join(read_text(p)[:4000] for p in paths)
    cjk = sum(1 for ch in sample if "\u4e00" <= ch <= "\u9fff")
    return "zh" if cjk > len(sample) * 0.15 else "en"


# --------------------------------------------------------------------------- http
def post_chat(url: str, key: str, body: dict, timeout: int) -> tuple[int, str]:
    endpoint = url.rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint += "/chat/completions"
    req = urllib.request.Request(
        endpoint, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "Accept": "application/json", "User-Agent": "multi-model-relay/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


EFFORT_ERR = re.compile(r"reasoning[_ ]?effort|unknown_parameter|unsupported parameter|ReasoningEffort", re.I)


def call_model(prov: dict, system: str, user: str, effort: str | None, timeout: int, max_tokens: int | None,
               temperature: float | None) -> tuple[str, dict]:
    body: dict = {"model": prov["id"], "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                  "stream": False}
    if effort:
        body["reasoning_effort"] = effort
    if max_tokens:
        body["max_tokens"] = max_tokens
    if temperature is not None:
        body["temperature"] = temperature
    meta = {"effort_sent": effort, "retries": []}
    for attempt in range(3):
        t0 = time.time()
        status, text = post_chat(prov["url"], prov["apiKey"], body, timeout)
        meta["duration_s"] = round(time.time() - t0, 1)
        if status == 200:
            try:
                data = json.loads(text)
                choice = data["choices"][0]
                msg = choice.get("message") or {}
                content = msg.get("content")
            except Exception:
                meta["retries"].append({"attempt": attempt, "reason": "unparsable 200 body"})
                continue
            usage = data.get("usage") or {}
            meta["usage"] = usage
            meta["model_reported"] = data.get("model")
            meta["finish_reason"] = choice.get("finish_reason")
            if not (content or "").strip():
                details = usage.get("completion_tokens_details") or {}
                reasoning = details.get("reasoning_tokens")
                if msg.get("reasoning_content") or (reasoning and reasoning >= usage.get("completion_tokens", 0)):
                    fail("model returned only reasoning tokens and no content: the max_tokens budget was consumed by "
                         "reasoning. Re-run without --max-tokens (or with a much larger value), or lower --effort.", meta=meta)
                fail("model returned empty content", meta=meta)
            if choice.get("finish_reason") == "length":
                meta["truncated"] = True
            return content, meta
        snippet = text[:300].replace("\n", " ")
        if "reasoning_effort" in body and status in (400, 422, 500) and EFFORT_ERR.search(text):
            meta["retries"].append({"attempt": attempt, "reason": f"HTTP {status} rejected reasoning_effort; retrying without it"})
            body.pop("reasoning_effort", None)
            meta["effort_sent"] = None
            continue
        if status in (408, 429, 500, 502, 503, 504) and attempt < 2:
            meta["retries"].append({"attempt": attempt, "reason": f"HTTP {status}: {snippet}"})
            time.sleep(5 * (attempt + 1))
            continue
        fail(f"HTTP {status} from provider: {snippet}", meta=meta)
    fail("gave up after retries", meta=meta)


# --------------------------------------------------------------------------- parse & write
def between(text: str, a: str, b: str) -> str | None:
    i = text.find(a)
    if i < 0:
        return None
    j = text.find(b, i + len(a))
    return text[i + len(a): j if j >= 0 else len(text)].strip("\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--next", required=True, help="JSON file (or '-' for stdin) produced by relay_run.py next-round")
    ap.add_argument("--model", help="override model id from next.json")
    ap.add_argument("--effort", help="override effort (minimal/low/medium/high/xhigh/max/none)")
    ap.add_argument("--no-effort", action="store_true", help="never send reasoning_effort")
    ap.add_argument("--base-url", default=os.environ.get("RELAY_BASE_URL"))
    ap.add_argument("--api-key", default=os.environ.get("RELAY_API_KEY"))
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--max-tokens", type=int)
    ap.add_argument("--temperature", type=float)
    ap.add_argument("--extra", help="extra user input / feedback text, or @file")
    ap.add_argument("--lang", help="output language (default: from next.json, auto-detect if 'auto')")
    ap.add_argument("--dry-run", action="store_true", help="render prompt and provider info, no network")
    a = ap.parse_args()

    nx = json.loads(sys.stdin.read() if a.next == "-" else read_text(a.next))
    if nx.get("status") != "ok":
        fail(f"next.json status is '{nx.get('status')}', refusing to run")
    round_dir = Path(nx["round_dir"])
    model = a.model or nx["model"]
    lang = a.lang or nx.get("lang") or "auto"
    if lang == "auto":
        lang = detect_lang(nx["original_paths"])
    extra = a.extra
    if extra and extra.startswith("@"):
        extra = read_text(extra[1:])

    prov = resolve_provider(model, a.base_url, a.api_key)
    requested = None if a.no_effort else (a.effort or nx.get("effort"))
    effort, effort_note = choose_effort(requested, prov.get("efforts"))
    system, user = build_prompt(nx, effort, extra, lang)

    if a.dry_run:
        out({"status": "dry-run", "model": prov["id"], "provider_source": prov["source"], "base_url": prov["url"],
             "api_key": mask(prov["apiKey"]), "effort": effort, "effort_note": effort_note, "lang": lang,
             "round_dir": str(round_dir), "prompt_chars": len(system) + len(user),
             "prompt_preview": user[:1200] + ("…" if len(user) > 1200 else "")})

    ensure_dir(round_dir)
    content, meta = call_model(prov, system, user, effort, a.timeout, a.max_tokens, a.temperature)
    (round_dir / "raw_response.md").write_text(content, encoding="utf-8")

    draft = between(content, DRAFT_BEGIN, DRAFT_END)
    notes = between(content, NOTES_BEGIN, NOTES_END)
    if draft is None or notes is None:
        fail("response missing DRAFT/NOTES markers; raw response saved", raw=str(round_dir / "raw_response.md"), meta=meta)
    draft_path = round_dir / f"draft{nx['ext']}"
    notes_path = round_dir / "notes.md"
    draft_path.write_text(draft.strip() + "\n", encoding="utf-8")
    notes_path.write_text(notes.strip() + "\n", encoding="utf-8")
    result = {"status": "ok", "draft_path": str(draft_path), "notes_path": str(notes_path), "chars": len(draft),
              "model": prov["id"], "effort": meta.get("effort_sent"), "effort_note": effort_note, "lang": lang,
              "duration_s": meta.get("duration_s"), "usage": meta.get("usage"), "retries": meta.get("retries")}
    if nx["role"] == "finalizer":
        final_dir = Path(nx["final_dir"]); ensure_dir(final_dir)
        final_path = final_dir / f"FINAL{nx['ext']}"
        final_path.write_text(draft.strip() + "\n", encoding="utf-8")
        summary = between(content, SUMMARY_BEGIN, SUMMARY_END) or "(finalizer did not emit a summary block)"
        summary_path = final_dir / "SUMMARY.md"
        summary_path.write_text("# Relay summary\n\n" + summary.strip() + "\n", encoding="utf-8")
        result.update({"final_path": str(final_path), "summary_path": str(summary_path)})
    # summary line for the orchestrator: the Handoff section
    m = re.search(r"## Handoff\s*\n(.*)", notes, re.S)
    result["summary"] = (m.group(1).strip()[:600] if m else "")[:600]
    out(result)


if __name__ == "__main__":
    main()
