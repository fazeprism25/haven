# Haven V1 — Final Release Checklist

Ordered by dependency: each phase assumes every checked box in the phases
above it. Don't jump ahead — e.g. don't shoot screenshots before Demo Assets
is green, or you'll redo them once the real seeded data looks different.

Legend: `[ ]` not done · `[x]` verified done (as of 2026-07-04) · `[~]` partially done, gap noted.

---

## Phase 1 — Engineering (blocks everything below: code must be final before QA, benchmarks, docs, or demo capture)

- [ ] **Commit the working tree as the actual release commit.** `042e16e6`
      ("Prepare Haven v1.0 release candidate") is *not* what's on disk —
      as of 2026-07-10 there are 124 modified files, 34 deletions, and 166
      untracked paths beyond it (this gap has grown substantially since the
      numbers below were first measured — significant undocumented work,
      including several new benchmark adapters/datasets, landed since).
      Anyone who `git checkout`s the tag instead of copying your working
      directory gets a different, partially-broken tree.
- [ ] `git add extension/icons/` specifically — currently untracked. Without
      it, the extension fails to load from a fresh clone ("Could not load
      icon" in `chrome://extensions`). This blocks Phase 5 (Demo) and Phase 6
      (Screenshots) if you're testing against a fresh clone rather than your
      working copy.
- [ ] Confirm the staged deletion of `vault/Alice_works_at_Acme_Corp._00000000.md`
      (stray tracked demo output predating `haven_data/` being gitignored)
      lands in the commit.
- [ ] Resolve the version mismatch: `extension/manifest.json` still declares
      `"version": "0.1.0"` while the commit message and `final_report.md`
      call this "v1.0" / "Hackathon Submission." Pick one string and make it
      consistent — bump the manifest to `1.0.0` is the simpler fix.
- [ ] Optional/non-blocking: add a `.gitattributes` entry for line endings —
      every `git diff` under `obsidian/`, `benchmarks/`, and
      `extension/manifest.json` currently warns about LF→CRLF churn. Cosmetic,
      do only if time remains.

## Phase 2 — QA (depends on Phase 1: run against the code that will actually ship, not an intermediate state)

- [ ] Re-run both suites against the final committed tree (last measured:
      `pytest obsidian/tests -q` → 1504 passed; `pytest benchmarks/tests -q`
      → 83 passed — re-confirm after the Phase 1 commit lands, since several
      pipeline files in that diff — `knowledge_updater.py`, `models.py`,
      `context_builder.py`, `retrieval_models.py` — are part of it).
- [ ] Manual browser check of the extension (no Chrome automation available
      in this environment — must be done by hand):
  - [ ] Load unpacked in `chrome://extensions`, confirm icon renders at all
        sizes (no broken-image icon).
  - [ ] Popup opens, status dot reads "Connected" against a running local
        server.
  - [ ] "Use Haven" button appears near the ChatGPT compose box and pulls
        context on click.
  - [ ] "Remember" button appears after a reply and successfully `POST`s to
        `/api/v1/memory`.
  - [ ] Popup search returns results against a seeded vault.
- [ ] Run one Retrieval Inspector query (`GET /api/v1/dashboard/inspect?query=...`)
      against a *seeded* vault, not the empty scratch vault used in the last
      pass — confirm non-empty `candidates[]` with populated `score_breakdown`.
- [ ] Exercise `GET /api/v1/dashboard/inspect/memory/{id}` (the per-memory
      variant) — only the query-string variant has been directly tested so
      far. Click a memory card in the dashboard and confirm all five panel
      sections populate (General, Ontology, Retrieval, Acceptance, Pipeline).
- [ ] Confirm `--query-rewriter` fails open (no rewrites, no crash) when
      `QUERY_REWRITER_API_KEY` is unset — the default cold-start case.
- [ ] Confirm actual minimum Python version for the `obsidian/` package
      (dev env here is 3.14; root `AGENTS.md`'s "3.9+" covers the mem0 SDK,
      a separate package) — don't leave this ambiguous in README.

## Phase 3 — Benchmarks (depends on Phase 1: the measured run must reflect the shipped pipeline code)

- [ ] Re-run the mem0-vs-Haven comparison against the final committed code
      and refresh `benchmarks/results/final_report.md` if the numbers moved
      (last measured: precision 0.301 → 0.679, false-positive rate 0.500 →
      0.100, accepted candidates 278 → 110, ~0 ms latency delta — measured
      before the pending pipeline-file changes in Phase 1's diff).
- [ ] Confirm `benchmarks/README.md`'s fix (no longer claims "dataset
      creation in progress" for the 11 populated categories) is present in
      the commit that gets tagged.
- [ ] No action required on the disclosed keyword-overlap denominator bug —
      already documented as "root cause found, fix not yet done" in both
      `final_report.md` and `KNOWN_ISSUES.md`; consistent story, leave as is
      unless time allows an actual fix.
- [ ] Dataset coverage remains partial (159 cases / 11 of 17 category
      folders populated) and is self-disclosed in both README and
      `KNOWN_ISSUES.md` — no action needed, just be ready to name it
      proactively if asked.

## Phase 4 — Documentation (depends on Phases 1–3: docs should describe the code and numbers that actually shipped)

- [ ] Final read-through of `MEMORY_TYPES.md` against the actual `MemoryType`
      enum (`fact, preference, belief, decision, goal, project, person,
      task, event, skill, rule`) and `RELATIONSHIPS.md` against the actual
      relationship enum — both have pending unstaged edits, confirm they
      match code, not an intermediate draft.
- [ ] Final read of `docs/architecture/ACCEPTANCE_STAGE_DESIGN.md` and
      `docs/architecture/ONTOLOGY_SPEC.md` (root-level `docs/`, distinct from
      `obsidian/docs/`) — both have pending unstaged edits.
- [ ] Confirm `obsidian/docs/DECISION_MEMORY.md` (currently untracked) is
      committed and linked from `PROGRESS.md` (already referenced there per
      last check) and `obsidian/docs/README.md`.
- [ ] Add a one-line note to README / `obsidian/server/README.md`: running
      the test suite requires the root repo's dev deps (e.g. `pytest`), not
      just `pip install -r obsidian/server/requirements.txt`.
- [ ] Final read of root `README.md` itself — it has a pending unstaged edit;
      confirm it's the intended final copy before tagging.
- [ ] Confirm `HACKATHON_SCOPE.md` / `PROGRESS.md` still accurately reflect
      final must-have / nice-to-have delivery status for judges.

## Phase 5 — Demo Assets (depends on Phases 1–4: seeding and rehearsal should run against final code + docs)

- [ ] Re-run `scripts/seed_demo.py` against the final committed tree (last
      confirmed: seeds 30 memories through the real `VaultWriter` +
      `OntologyPipeline`, not fake/hardcoded records).
- [ ] Full pass of seed → curl a real query → confirm non-empty context
      (only done against an empty scratch vault so far, not the actual
      seeded 30-memory vault).
- [ ] Rehearse the full click-path once, end to end, before capturing
      anything in Phases 6–7: load extension → open ChatGPT → "Use Haven" →
      "Remember" → dashboard → click a memory card → Retrieval Inspector.
      This touches three separately-running pieces (uvicorn, Chrome
      extension, ChatGPT tab) — know it works before it's on camera.

## Phase 6 — Screenshots (depends on Phase 5: capture the rehearsed path, not an ad-hoc one)

None exist anywhere in the repo yet. Capture, in this order (matches the
rehearsed click-path):

- [ ] Browser extension popup (status "Connected")
- [ ] "Use Haven" / "Remember" buttons live on a ChatGPT page
- [ ] Memory Dashboard (`GET /dashboard`) — overview + categories
- [ ] Memory Inspector panel (opened from a dashboard card)
- [ ] Retrieval Inspector in use (populated `score_breakdown`)

## Phase 7 — Video (depends on Phase 6: reuse the same rehearsed path/shot list so nothing is re-planned)

- [ ] No recording exists yet. Script the walkthrough directly from the
      Phase 5 rehearsal + Phase 6 shot list, then record once — same order:
      install/run → extension popup → ChatGPT "Use Haven"/"Remember" →
      dashboard → memory card → Retrieval Inspector.
- [ ] Keep it short enough for a submission form's time limit if one is
      specified (check Phase 8 requirements first — don't record, then
      re-cut to fit).

## Phase 8 — Submission Materials (depends on all phases above being final)

- [ ] Create an explicit annotated tag once Phase 1–4 are committed, e.g.
      `git tag -a haven-v1.0 -m "Haven hackathon submission"`.
- [ ] **`origin` currently points at `github.com/mem0ai/mem0` (upstream),
      not a personal fork** — `main` is 83 commits ahead of `origin/main`
      and unpushed. If judging reads from a GitHub link rather than a local
      copy, decide where this actually needs to live (a personal fork
      remote, most likely) before pushing anything — do not push a tag or
      branch to `origin` (upstream mem0) without confirming that's intended.
- [ ] Confirm whether judging reads from a git host or a local/zipped copy
      of the working directory — these currently show different states
      until Phase 1 is committed and pushed to the right place.
- [ ] Assemble the submission package: README quickstart,
      `benchmarks/results/final_report.md`, the Phase 6 screenshot set, and
      the Phase 7 video link/file, per whatever the submission form/platform
      actually requires (Devpost, form upload, etc. — confirm the specific
      requirements before assembling).
- [ ] Double-check no secrets/credentials are in anything being submitted or
      pushed (last check: none found, only intentional `*.env.example`
      templates elsewhere in the monorepo).

---

## Reference: last verified state (2026-07-10)

| Suite | Command | Result |
|---|---|---|
| Haven core | `pytest obsidian/tests -q` | 2508 passed, 6138 warnings (deprecation only) |
| Benchmarks | `pytest benchmarks/tests -q` | 240 passed |

Warnings are exclusively the disclosed `datetime.utcnow()` deprecation
(non-blocking, future-Python-version risk only). Growth from the
2026-07-04 baseline (1504/83 passed) reflects substantial feature work
since — Memory Review, Working Context, Quick Capture, Obsidian import,
Benchmark Explorer, and several new benchmark adapters/datasets — none of
which is captured elsewhere in this checklist or in QA_REPORT.md; both
should be treated as covering only the subsystems that existed as of
2026-07-04/05, not the current tree, until a fresh QA pass is run.
