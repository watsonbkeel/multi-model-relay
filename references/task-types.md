# Task Types and Default Review Dimensions

When the user says "default" (or gives no goal), the orchestrator classifies the input into one
of the task types below and injects the matching **Review Dimensions** block into every round
prompt. When the user gives a custom goal, the orchestrator still injects the dimensions as
secondary guidance unless the user says otherwise.

Classification hints (first match wins):

| Signal | Task type |
|---|---|
| user says "article / blog / essay / 文章 / 公众号 / 润色 / 可读性" or input is prose with headings | `article` |
| user says "PRD / requirement / spec / 需求 / 方案文档 / 产品" or input has user stories, acceptance criteria, scope sections | `requirement-doc` |
| user gives code files, stack traces, "bug / fix / refactor / 报错 / 代码问题", or asks for a technical solution | `code-solution` |
| anything else (reports, proposals, emails, slide outlines, research notes) | `generic` |

Each block below is inserted verbatim under `## Review Dimensions`.

---

## article

```
Review dimensions for ARTICLE (prioritised):
1. Clarity — every paragraph has one idea; sentences are short; jargon is explained or removed.
2. Structure — a reader can skim headings and get the argument; the opening states why it matters;
   the ending lands a clear takeaway.
3. Flow — transitions connect paragraphs; no abrupt jumps; consistent tense and voice.
4. Precision — vague claims are made concrete or cut; numbers and examples support key points;
   no invented facts.
5. Economy — redundant sentences, filler words and repeated ideas are removed; length within constraints.
6. Voice — the author's tone and point of view are preserved; do not make it sound generic.
7. Correctness — grammar, punctuation, terminology consistency, correct use of the output language.
Do NOT change: the author's core claims, the intended audience, the overall position.
```

## requirement-doc

```
Review dimensions for REQUIREMENT / PRODUCT DOCUMENT (prioritised):
1. Problem clarity — the user problem, target users and success metrics are explicit and measurable.
2. Scope — in-scope and out-of-scope are stated; no silent scope creep; MVP vs later is separated.
3. Completeness — user flows, edge cases, error states, permissions, data, non-functional needs
   (performance, privacy, compliance) are covered or explicitly deferred.
4. Testability — each requirement can be verified; acceptance criteria are concrete.
5. Consistency — terms, names, numbers and priorities do not contradict each other across sections.
6. Risk — assumptions, dependencies and open questions are listed with owners or next steps.
7. Readability — tables and lists over prose where appropriate; headings follow a logical order.
Do NOT change: product decisions already marked as final; business goals; audience.
Flag, do not silently fix: anything that looks like a product decision rather than a wording issue.
```

## code-solution

```
Review dimensions for CODE SOLUTION DOCUMENT (prioritised):
The draft is a SOLUTION DOCUMENT (markdown), not code. It must let an executor implement the fix
without guessing. Required sections: Problem restatement; Root-cause analysis (with evidence from
the provided files/logs); Proposed change (file-by-file, with code snippets where useful);
Alternatives considered; Risks and rollback; Verification plan (exact commands / tests / expected output).
1. Root cause — the explanation is supported by the actual code and logs provided, not assumed.
2. Minimality — the fix is the smallest safe change; no unrelated refactors.
3. Correctness — proposed code is syntactically plausible for the language and matches existing style/APIs.
4. Safety — side effects, data migration, backward compatibility and security are addressed.
5. Verifiability — the verification plan is concrete and reproducible.
6. Executability — an engineer (or executor model) can follow it step by step without missing context.
7. Honesty — unknowns are stated as unknowns; no invented APIs, flags or test results.
Do NOT: write to project files; the relay only produces the document. Implementation happens in step 4.
```

## generic

```
Review dimensions for GENERIC DOCUMENT (prioritised):
1. Purpose — the document's goal and audience are clear in the first few lines.
2. Structure — logical order; headings or lists where they help; nothing important buried.
3. Clarity — plain language; one idea per paragraph; ambiguity removed.
4. Accuracy — claims are consistent with the source material; no invented details.
5. Economy — redundancy and filler removed; length appropriate to the purpose.
6. Actionability — if the document asks for a decision or action, it is explicit.
7. Correctness — grammar, terminology, formatting in the output language.
Do NOT change: the author's intent, decisions or position.
```

---

## Default goal lines (used when the user says "default")

| Task type | Default goal |
|---|---|
| article | "Improve readability and flow while preserving the author's voice, facts and position. Length within ±10%." |
| requirement-doc | "Make the document complete, unambiguous and testable without changing product decisions." |
| code-solution | "Produce a root-cause-based, minimal, verifiable solution document an executor can implement directly." |
| generic | "Make the document clearer, better structured and more precise without changing its intent." |

Default constraints (always added unless the user overrides):

- Keep the output language the same as the original.
- Do not invent facts, data, citations, APIs or results.
- Do not change the author's core position or decisions already marked final.
