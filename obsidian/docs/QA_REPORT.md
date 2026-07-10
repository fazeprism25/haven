# Haven V1 — Release Validation QA Report

Tested as a hackathon judge would: fresh-ish clone perspective, working directory
as currently on disk (uncommitted changes included — this is what a judge testing
the local copy would see; see RELEASE_CHECKLIST.md Phase 1 for the outstanding
commit gap).

Date: 2026-07-05

Legend: PASS / FAIL / FIXED (was FAIL, bug fixed, retested PASS)

---

## 1. Installation — PASS

**Test:** Follow README Quickstart step 1 (`pip install -r obsidian/server/requirements.txt`)
against the project's Python 3.14 environment, then confirm `obsidian` and
`obsidian.server.main` import cleanly.

**Result:** Dependencies (`fastapi>=0.115`, `uvicorn[standard]>=0.30`) already
satisfied (fastapi 0.139.0, uvicorn 0.49.0). `import obsidian; import
obsidian.server.main` succeeds with no errors.

**Environment note (not a Haven bug):** the project's `.venv` is `uv`-managed
(created via `uv venv`) and has no `pip` executable on its own — `uv`-created
venvs intentionally omit pip. Running the README's literal `pip install ...`
command against *this* venv fails with `No module named pip`; using `uv pip
install -r obsidian/server/requirements.txt` (equivalent, uv-native) succeeds.
A judge using a plain `python -m venv` + system Python would hit no such
issue. Not fixing — out of scope (environment quirk, not a product defect).

---

## 2. Server — PASS

**Test:** `uvicorn obsidian.server.main:app --port 8765` with `HAVEN_VAULT_DIR`/
`HAVEN_CONCEPT_DIR` pointed at a brand-new scratch path (simulating true
first-run), then `GET /api/v1/health`.

**Result:** Server starts cleanly ("Application startup complete"), auto-creates
empty `vault_fresh/` and `concepts_fresh/` directories exactly as documented,
and `/api/v1/health` returns `{"status":"ok"}` (HTTP 200) repeatedly.

---

## 3. API — PASS

**Test:** Against the fresh, empty-vault server: `GET /docs` (OpenAPI UI),
`POST /retrieve_context` on an empty vault, `POST /memory` with an invalid
`memory_type` enum value, `GET /dashboard/inspect/memory/{bad-uuid}`.

**Result:**
- `GET /docs` → 200.
- `POST /retrieve_context` on empty vault → `{"context":""}` as documented
  (no crash on zero candidates).
- Invalid `memory_type` → HTTP 422 with a clear Pydantic enum validation
  message listing all valid values, not a 500.
- Malformed memory id → HTTP 404 `{"detail":"Memory not found"}`, not a
  500 or unhandled exception.

---

## 4. Memory creation — PASS

**Test:** `POST /api/v1/memory` with `{"canonical_fact": "I use Terraform for
infra.", "memory_type": "decision"}` against the fresh scratch vault; inspect
the written Markdown file, the concept store, and `GET /api/v1/dashboard`.

**Result:**
- Response: `{"id": "...", "canonical_fact": "...", "memory_type": "decision"}`,
  HTTP 200.
- Vault file `<id>.md` written with correct YAML frontmatter (id, title,
  canonical_fact, memory_type, confidence 0.5, importance 0.5, valid_from
  timestamp, tags `memory/decision`) and body sections (Canonical Fact,
  Evidence Chain, Related Concepts, Relationships).
- `OntologyPipeline` correctly extracted a concept from the new fact — one
  new file in `concepts_fresh/`.
- `GET /api/v1/dashboard` reflects the write immediately (no restart
  needed): `vault_stats.total_memories: 1`, `by_type.decision: 1`,
  `concept_stats.total_concepts: 1`, `total_attachments: 1`.

---

## 5. Retrieval — PASS

**Test:** `POST /api/v1/retrieve_context` with a query semantically/keyword
related to the one seeded memory, and with an unrelated query.

**Result:**
- Matching query ("What does the user use for infrastructure?") →
  `{"context":"[1] I use Terraform for infra.\n    type: decision | confidence: 0.50 | importance: 0.50 | confirmations: 0\n    valid_from: ... | valid_until: none"}` —
  correct `ContextBuilder` flat-render format.
- Unrelated query ("favorite pizza topping") → `{"context":""}`, correctly
  filtered out by acceptance stage rather than force-returning noise.

---

## 6. Working Context — PASS

**Test:** Seed a decision + goal + task all mentioning "Terraform", then
`POST /api/v1/retrieve_working_context` with (a) a narrow query, (b) a
broader query, (c) an empty query.

**Result:**
- Narrow query ("Terraform infra") → one `WorkingContext` grouping the
  decision + goal together (`status: "decided"`, `current_goal` and
  `recent_decisions` both populated correctly).
- Broader query ("Terraform infra networking module") → only the task
  candidate is accepted; the decision/goal are rejected with
  `rejection_reason: "score_gap_cut"` (`DeterministicRanker`'s acceptance
  stage correctly filters lower-relative-score candidates when one
  dominates — confirmed via the trace's `score_breakdown`/`threshold_used`/
  `score_gap` fields, not a bug: `context`, `working_contexts`, and
  `structured_prompt` all agree on the same 1-candidate acceptance set, so
  there's no divergence between the three renderers).
- Empty query → HTTP 200, `available: true`, degenerates to a single empty
  "General"/`reference` context — no crash, matches documented behavior.

---

## 7. Structured Prompt — PASS

**Test:** Save a memory whose `canonical_fact` contains XML-special
characters (`<script>alert(1)</script> & "quotes"`), then request
`retrieve_working_context`/`structured_prompt` with both a plain query and
one containing its own special characters, and inspect the raw XML text.

**Result:**
- Memory text is correctly escaped inside `<Memory>` element content:
  `<` → `&lt;`, `>` → `&gt;`, `&` → `&amp;`. Literal `"` is left as-is,
  which is correct XML (quotes only need escaping inside attribute
  values, not text content) — no injection into the surrounding
  `<HavenContext>` structure is possible.
- `<UserRequest>` text is escaped identically (`Does &lt;b&gt;this&lt;/b&gt;
  work &amp; "quotes"?`) — the user's own request text can't break out of
  its element either.
- Memory and request stay in disjoint subtrees as documented (`<HavenContext>`
  vs `<UserRequest>`).

---

## 8. Dashboard — PASS

**Test:** `GET /dashboard` (HTML page) and `GET /api/v1/dashboard` (JSON)
against the 4-memory scratch vault (1 decision, 1 goal, 1 task, 1 fact).

**Result:**
- `GET /dashboard` → HTTP 200, 46KB static page, references the
  `/api/v1/dashboard*` endpoints via `fetch` and includes the "Resume Work"
  section.
- `GET /api/v1/dashboard` → correctly bucketed: `decisions: 1`, `tasks: 1`,
  `recent_memories: 4`, `vault_stats.total_memories: 4`,
  `vault_stats.by_type` exactly matches what was seeded (fact:1, decision:1,
  goal:1, task:1), `working_contexts` populated (3 contexts).

---

## 9. Retrieval Inspector — PASS

**Test:** `GET /api/v1/dashboard/inspect?query=...` (already exercised above
in Working Context testing) and `GET /api/v1/dashboard/inspect/memory/{id}`
(the by-memory variant flagged in RELEASE_CHECKLIST.md as under-tested)
against the non-empty scratch vault.

**Result:**
- By-query variant: populated `trace.candidates[]` with full
  `score_breakdown` (activation, attachment_relevance, keyword_overlap,
  importance, confidence, recency, confirmation_count) for every candidate,
  accepted and rejected alike.
- By-memory variant: same trace shape, `source_memory_id` correctly set,
  and — unlike the by-query route — `ontology` is populated: the memory's
  attached concept ("Terraform") plus both its relationships, with
  human-readable `source_label`/`target_label`. `working_contexts` and
  `structured_prompt` are also present on this route.
- 404 behavior for a malformed/unknown id already verified in API (§3).

---

## 10. Browser Extension — PASS (static validation; manual Chrome check not run — see note)

**Test:** No Chrome automation is available in this environment (confirmed
consistent with RELEASE_CHECKLIST.md's own note). Performed the static
checks that *are* possible: `manifest.json` structure, icon file presence/
validity, JS syntax, and config/server contract consistency.

**Result:**
- `extension/icons/` (previously untracked-only per RELEASE_CHECKLIST) is
  present on disk with all 4 sizes manifest declares (16/32/48/128px),
  each a valid RGBA PNG at the exact declared dimensions — the "Could not
  load icon" failure mode the checklist warned about does not reproduce
  against this working copy.
- `node --check` passes with no syntax errors on `background.js`,
  `config.js`, `content/controller.js`, `popup/popup.js`.
- `extension/config.js`'s `HAVEN_BASE_URL` (`http://127.0.0.1:8765`) and
  `ENDPOINTS` map (`/health`, `/retrieve_context`,
  `/retrieve_working_context`, `/memory`, `/dashboard/inspect`) match the
  server's actual port and route table in `obsidian/server/main.py`
  exactly — no drift between extension and server.

**Gap, stated explicitly:** the actual manual Chrome checklist (load
unpacked, popup "Connected" status, "Use Haven"/"Remember" buttons live on
chatgpt.com, popup search) from RELEASE_CHECKLIST.md Phase 2 was **not**
executed — this requires a human with a Chrome browser, which this
environment doesn't have. Static validation only; do not report this as a
full pass to judges without that manual step.

---

## 11. Obsidian export — PASS

**Test:** Inspect the raw vault Markdown files (YAML frontmatter, wiki-links,
Related Concepts/Relationships sections) across memories written in sequence,
including one that introduces a new concept and a later one that mentions it.

**Result:**
- Frontmatter: valid YAML with `title`/`aliases` set to the fact text (so
  Obsidian's file list/Properties pane and link autocomplete show readable
  text, not the UUID filename) and `tags: [memory/<type>]` for the tag pane.
- The *first* memory to introduce "Terraform" (`6acd0048...md`, the
  decision) is written **before** `OntologyPipeline.process` attaches its
  concept, so its own Related Concepts/Relationships sections correctly
  show the "nothing yet" placeholder — verified this is documented,
  intentional behavior (`vault_writer.py` module docstring: "No
  synchronization, background job, or file watcher backfills links into
  already-written notes"), not a bug to fix.
- A *later* memory mentioning "Terraform" (`60f9a0d3...md`, the goal)
  correctly picks up the concept via the "mentioned" signal and renders a
  real Obsidian piped wiki-link: `[[5ca2cd8b-...|Terraform]]` — confirms
  the incremental-linking design works as documented, not just in theory.
- Filenames are the memory's UUID (`{id}.md`), consistent with the wiki-link
  targets used elsewhere (Decision supersession links, Concept links).

---

## 12. Regression tests — PASS

**Test:** `pytest obsidian/tests -q` and `pytest benchmarks/tests -q` against
the current working tree (uncommitted changes included).

**Result:**
- `obsidian/tests` → **1504 passed**, 1474 warnings (all the disclosed,
  non-blocking `datetime.utcnow()` deprecation) — matches
  RELEASE_CHECKLIST.md's last-verified baseline exactly.
- `benchmarks/tests` → **83 passed** — matches baseline exactly.
- No new failures introduced by anything exercised during this QA pass
  (4 memories created, 2 concepts/relationships written, multiple
  retrieval/working-context/inspector calls against the scratch vault).

---

# Summary

All 12 subsystems PASS. No product bugs found or fixed during this pass —
every subsystem worked exactly as designed and documented on first try.
Two things surfaced that are **not** Haven defects and were deliberately
left alone:

1. This dev machine's `.venv` is `uv`-managed and has no `pip` binary — a
   `uv`-specific quirk, unrelated to the product (§1).
2. A brand-new memory's own vault file never gets its Related
   Concepts/Relationships backfilled after `OntologyPipeline` attaches a
   concept to it moments later — verified this is intentional,
   already-documented behavior in `vault_writer.py`, not a bug (§11).

One gap remains **un**-closed by this pass and should not be reported as
verified: the manual Chrome extension checklist (§10) requires a human
with a browser, which isn't available in this environment. Everything else
in RELEASE_CHECKLIST.md Phase 2 (QA) is now confirmed.

Test data used during this validation (`qa_scratch/`) has been deleted;
no changes were made to `haven_data/` (the pre-existing seeded vault from
prior sessions was untouched — this pass used its own throwaway scratch
vault instead).

