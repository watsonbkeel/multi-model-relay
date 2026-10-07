# multi-model-relay

**English** · [中文](#中文说明)

An [Agent Skill](https://agentskills.io) that runs one document or solution through several
*different* LLMs in sequence — A, then B, then C — where every round sees the original, the
previous draft and the previous change notes, writes an improved draft into its own directory
(earlier drafts are never overwritten), and the last model converges into a final version.

Works on any agent platform that reads `SKILL.md` (WorkBuddy, Claude Code, Codex CLI, opencode,
…). Verified end-to-end on WorkBuddy.

```
original ──► A (opus) ──► B (fable) ──► C (astra, finalizer) ──► final/FINAL.md + SUMMARY.md + REPORT.md
             r1-opus/     r2-fable/      r3-astra/
             draft+notes  draft+notes    draft+notes
```

## What it is good for

| Input | Example goal | Task type |
|---|---|---|
| An article / blog post / newsletter | "Improve readability, keep my voice, ±10% length" | `article` |
| A PRD / requirement doc | "Make it complete and testable without changing product decisions" | `requirement-doc` |
| A code problem (files + logs) | "Root-cause it and produce a minimal, verifiable fix plan" | `code-solution` |
| Anything else (reports, proposals) | "Clearer, better structured, more precise" | `generic` |

For `code-solution` tasks an optional step 4 lets a chosen **executor** model implement the
final solution document and a **verifier** model check the result.

## How it works

1. **Configure** – pick three models A / B / C (any ids your platform knows) and a reasoning
   effort. Saved to `~/.multi-model-relay/config.json`; next time just say "default".
2. **Describe** – give the input (file, directory or pasted text), the goal and red lines, or
   say "default" to use the built-in review dimensions for the task type.
3. **Budget** – the skill states "3 paid calls" (plus optional execute/verify) and waits for a yes.
4. **Relay** – each round: `relay_run.py next-round` → prompt from `references/round-prompts.md`
   → model writes `draft` + `notes` → `relay_run.py seal` (sha256 + read-only) → only the
   `## Handoff` paragraph comes back to the orchestrator.
5. **Deliver** – `final/FINAL.<ext>`, `final/SUMMARY.md`, `REPORT.md` (round table, change log,
   actual call count).

Two backends, chosen automatically:

- **agent** – the platform spawns a sub-agent on the chosen model (WorkBuddy: `Agent` tool with
  `model=`; Claude Code / Codex / opencode: pre-defined agents, see
  `references/platform-notes.md`). The model has tools and reads files itself.
- **script** – `scripts/relay_api.py` calls an OpenAI-compatible `/v1/chat/completions`
  directly with `reasoning_effort`. Platform-independent; reads WorkBuddy's `models.json` or
  your own provider list.

## Install

```bash
git clone https://github.com/watsonbkeel/multi-model-relay.git
cd multi-model-relay
./install.sh              # symlinks into every platform skill dir that exists
./install.sh --all        # ...or create them all
cp assets/config.example.json ~/.multi-model-relay/config.json   # optional, edit model ids
```

Manual: symlink or copy this folder to `~/.workbuddy/skills/`, `~/.claude/skills/`,
`~/.agents/skills/` or `~/.config/opencode/skills/`.

## Use

Say something like:

> Use multi-model-relay on `draft.md`. A = claude-opus-5-5, B = claude-fable-5-1, C = gpt-6-astra,
> effort high. Goal: improve readability, keep my position, length within ±10%.

or simply "多模型接力优化这篇文章，用默认配置".

Useful phrases: "pause after each round", "rounds A,B,C,A", "backend script", "B = another-model".

## Run directory

```
relay-runs/20261002-0930-readability-ai-article/
├── task.md / task.json      # goal, constraints, slots, language, input stats
├── 00-original/             # untouched input + MANIFEST.md
├── r1-claude-opus-5-5/      # draft.md + notes.md   (sealed, read-only)
├── r2-claude-fable-5-1/
├── r3-gpt-6-astra/
├── final/                   # FINAL.md + SUMMARY.md
├── exec/                    # optional: plan.md, result.md, verify.md
├── REPORT.md
└── log.jsonl
```

`notes.md` always has the same five headings: Assessment, Changes Made, Rejected, Open
Questions, Handoff — so every round (and the human) can see what was changed, what was reverted
and why.

## Scripts

| Command | Purpose |
|---|---|
| `relay_run.py init …` | create the run directory and `task.md` |
| `relay_run.py next-round --run R --create` | compute the next round (refuses to reuse a directory) |
| `relay_run.py seal --dir D` | hash + read-only; validates notes headings |
| `relay_run.py handoff --notes N` | print only the Handoff section |
| `relay_run.py verify --run R` | detect tampering of sealed rounds |
| `relay_run.py report --run R` | write `REPORT.md` |
| `relay_api.py --next next.json [--dry-run]` | run one round through an OpenAI-compatible API |

Python ≥ 3.9, standard library only.

## Privacy

No keys, endpoints or personal paths live in this repository. The script backend reads keys at
runtime from `~/.workbuddy/models.json`, from environment variables named in your config, or
from `--api-key`; logs mask them.

## License

MIT

---

# 中文说明

**multi-model-relay** 是一个 Agent Skill：把一篇文档或一个解决方案，依次交给 **三个不同的大模型
A → B → C** 接力优化。每一轮模型都能看到「原文 + 上一稿 + 上一轮修改说明」，产出自己的优化稿和
结构化说明，**绝不覆盖前面的稿件**；最后一轮负责收敛定稿。默认 3 轮 = 3 次付费调用。

适用于：文章润色、产品需求文档完善、代码问题的方案文档打磨、以及其他通用文档。代码场景可选第 4 步：
指定一个**执行模型**按定稿方案实施，再由**复核模型**检查结果。

## 使用方式

1. **选模型**：第一次使用时指定 A / B / C 三个模型（必须是你的 Agent 平台认识的模型 ID）和推理强度，
   配置保存在 `~/.multi-model-relay/config.json`，下次说"默认"即可。
2. **给任务**：文件路径 / 目录 / 粘贴文本 + 优化目标与红线（说"默认"则按任务类型用内置评审维度）。
3. **确认预算**：技能会先说明"本次 3 次调用（可选执行 + 复核再加 2 次）"，等你同意才开始。
4. **接力**：每轮产出 `rN-<模型>/draft` + `notes.md`，封存（sha256 + 只读），只把 `## Handoff`
   一段回传给编排者，不把全文塞进上下文。
5. **交付**：`final/FINAL.<ext>`、`final/SUMMARY.md`、`REPORT.md`（轮次表、合并修改日志、实际调用次数）。

示例口令：

> 用 multi-model-relay 优化 `draft.md`，A = claude-opus-5-5，B = claude-fable-5-1，C = gpt-6-astra，
> 推理 high。目标：提升可读性，不改观点，字数 ±10%。

其他：「每轮停一下」「轮次 A,B,C,A」「后端用 script」「B 换成 xxx」。

## 两种后端

- **agent**（默认）：由平台派生一个指定模型的子代理（WorkBuddy 用 `Agent` 工具的 `model` 参数，已实测；
  Claude Code / Codex / opencode 用预定义 agent，见 `references/platform-notes.md`）。模型自带工具，自己读文件。
- **script**：`scripts/relay_api.py` 直连 OpenAI 兼容接口 `/v1/chat/completions`，带 `reasoning_effort`，
  平台无关；可直接读取 WorkBuddy 的 `models.json`，或你自己的 provider 列表。

## 安装

```bash
git clone https://github.com/watsonbkeel/multi-model-relay.git
cd multi-model-relay
./install.sh          # 软链接到已存在的各平台 skills 目录
./install.sh --all    # 或全部创建
cp assets/config.example.json ~/.multi-model-relay/config.json   # 可选，改成你的模型 ID
```

## 隐私

仓库内不含任何密钥、接口地址或个人路径。脚本后端运行时才从 `~/.workbuddy/models.json`、配置里指定的
环境变量或 `--api-key` 读取密钥，日志中一律脱敏。

## 许可

MIT
