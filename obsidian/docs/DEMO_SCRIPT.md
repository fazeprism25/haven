# Demo Script

The live, judge-facing walkthrough — what to say and show, in order, and
roughly how long each part should take. This is the presentation script;
for the underlying material see
[README's "See Haven in action"](../../README.md#-see-haven-in-action) and
the [interactive article](media/haven-article.html#why-different).

## Flow

1. **Introduction — the problem** (~10s, much shorter than before)
2. **Why Haven is different** (~15–20s) — dashboard hero card
3. **Architecture diagram** (~5–10s)
4. **Live product demo** — the actual walkthrough (browser extension →
   dashboard → Retrieval Inspector → Memory Browser → Obsidian vault)

Total for steps 1–3: **30–40 seconds.** Everything after that is the live
product demo — take as long as it needs.

## 1. Introduction (problem)

> **Aditya** — Hello everyone, I'm Aditya.
>
> **Siddhartha** — And I'm Siddhartha.
>
> **Both** — And this is Haven.
>
> *(Haven logo)*
>
> **Aditya** — We use AI every day to build projects, solve problems and
> accumulate knowledge.
>
> **Siddhartha** — But every new conversation starts from scratch, forcing
> us to repeatedly explain the same context.
>
> **Aditya** — We built Haven to solve that.

Keep this tight — it's the setup, not the pitch. The pitch is next.

## 2. Transition into the product

- Open the Haven dashboard.
- Show the **"Why Haven is different"** hero card (the `hero-why` block
  under the stat row — see `obsidian/server/static/dashboard.html`).
- Say the core claim: traditional memory systems retrieve similar memories;
  Haven reconstructs your **Working Context** before the model starts
  reasoning, so it resumes work instead of just recalling facts.
- Briefly point out the capability chips (Working Context Reconstruction,
  Structured Memories, Ontology Graph, Explainable Retrieval, Memory
  Spaces, Local Markdown Vault, Incremental Ingestion, Browser Extension)
  — don't read every one, just gesture at the row to signal breadth.

## 3. Architecture diagram

- Point at the pipeline diagram (`obsidian/docs/media/architecture.svg`,
  also embedded in the article's
  [How it works](media/haven-article.html#how-it-works) section).
- One sentence: conversation → extraction → classification → importance →
  canonical matching → vault, and query → retrieval → Working Context →
  structured prompt, with a receipt at every step.

## 4. Live product demo

Only after the above — switch to the actual ChatGPT + dashboard demo. This
is the part that isn't scripted line-by-line; follow
[README's "See Haven in action"](../../README.md#-see-haven-in-action) for
the six things to actually show (extension, dashboard, Working Context,
Retrieval Inspector, Memory Browser, Obsidian vault).

## Why this order

Leading with **Why Haven is different** instead of the product mechanics
means the judges hear the differentiator (Working Context reconstruction,
not just retrieval) before they see any screen — so every screen that
follows is read as evidence for that claim, not as a generic memory-search
demo they have to reverse-engineer the pitch from.
