# Haven extension — E2E test suite (Playwright)

Real, in-browser end-to-end tests: a real Chromium instance, the actual
unpacked Haven extension, and a real chatgpt.com page — driven the way a
user actually drives them (click "Use Haven", type a follow-up, wait for a
reply), asserting on visible UI (dialogs, buttons, suggestion cards). This
is a different layer than `node --test` in `extension/*.test.js`, which
exercises pure logic modules (`remember-visibility.js`, `rewrite-suggestion.js`,
...) directly with fake DOM nodes — that suite is fast and has no external
dependencies; this one is slower and needs a real login, and exists to catch
things unit tests structurally cannot: wiring bugs, real DOM timing, real
ChatGPT markup.

## Install

```bash
cd extension
npm install
npx playwright install chromium
```

## One-time: authenticate the test profile

There is no API to log into ChatGPT programmatically, and this repo will
never hardcode credentials. Instead, the suite reuses a **persistent browser
profile** — you log in once, by hand, and every test run after that reuses
the saved session cookies.

```bash
npm run test:e2e:login
```

This opens a real, visible Chromium window (with the extension already
loaded) at chatgpt.com. Log in normally, then come back to the terminal and
press Enter. The session is saved to `tests/e2e/.auth/chrome-profile/`
(gitignored — never commit it, it holds real session cookies). Re-run this
command whenever the session expires.

### Alternative: reuse your real Opera GX login instead

If you're already logged into ChatGPT in your everyday Opera GX, you can
point the suite at that instead of doing a separate login. Two modes:

```bash
# Direct — automates your real, live Opera GX profile. Opera GX must be
# FULLY CLOSED first (see the warning below for why).
HAVEN_E2E_BROWSER=opera-gx npm run test:e2e

# Copy — one-time copy of your profile into tests/e2e/.auth/opera-gx-copy/
# (gitignored), then every run after that leaves your live Opera GX alone.
npm run test:e2e:opera-copy-profile   # also requires Opera GX closed, once
HAVEN_E2E_BROWSER=opera-gx-copy npm run test:e2e
```

`helpers/opera.js` locates your install and profile itself (no hardcoded
path) by checking `%LOCALAPPDATA%\Programs\Opera GX` /
`%ProgramFiles%\Opera GX` for the executable and `%APPDATA%\Opera
Software\Opera GX Stable` for the profile data; override either with
`HAVEN_E2E_OPERA_EXECUTABLE` / `HAVEN_E2E_OPERA_USER_DATA_DIR` if yours live
elsewhere. If you have more than one Opera GX profile, the folder names
(`Default`, `Profile 1`, ...) are listed in any error message this throws —
open `opera://version` in the profile you want and check "Profile Path" to
tell them apart, then set `HAVEN_E2E_OPERA_PROFILE` to that folder name.

**Read before using `opera-gx` mode:** Chromium refuses to let a second
process automate a profile that's already open elsewhere — confirmed
against a real running Opera GX install, it fails fast with "Opening in
existing browser session" rather than hanging or corrupting anything, but
it does mean **you cannot browse in Opera GX while tests run in this
mode**, at all, in that profile. `opera-gx-copy` mode exists specifically
to avoid this — see the recommended workflow below.

#### Recommended day-to-day workflow: `opera-gx-copy`

```bash
# Whenever your Opera GX session changes (first time, or after re-logging
# in, or just periodically to stay fresh):
#   1. Close Opera GX completely.
#   2. Refresh the copy:
npm run test:e2e:opera-copy-profile
#   3. Reopen Opera GX immediately -- it's independent of the copy from here.

# Any time after that, as many times as you want, Opera GX open or not:
HAVEN_E2E_BROWSER=opera-gx-copy npm run test:e2e
```

`copyOperaGxProfile()` (`helpers/opera.js`) fully replaces whatever was
copied before -- it never modifies your real Opera GX profile (it only ever
reads from it), and it never touches it again once the copy exists. Two
independent safety checks guard the refresh itself:

- **Source check** (unchanged from before): refuses if your real Opera GX
  profile is still open, for the same reason a database backup wants the
  database quiesced first — copying live SQLite files (`Cookies`,
  `Login Data`, ...) mid-write risks a torn, inconsistent copy.
- **Destination check** (new): refuses if the *existing copy* is currently
  in use — i.e. a Playwright run is mid-flight with
  `HAVEN_E2E_BROWSER=opera-gx-copy` right now — using the exact same
  lockfile detection as the source check, so a refresh can never corrupt or
  yank the profile out from under a test that's actively using it. Stop
  that run first, then refresh.

Both checks print exactly which profile is being read from and which
destination is being updated before copying anything, so it's always clear
what a given run of `npm run test:e2e:opera-copy-profile` is about to do.

Direct `opera-gx` mode is still available for a genuine one-off check where
you don't want a copy sitting on disk, but its "close your daily browser to
run tests" tradeoff makes it a poor fit for repeated use — `opera-gx-copy`
is byte-for-byte identical at the moment you refreshed it (a plain
recursive file copy, nothing decrypted, read, or transmitted) without that
cost.

Either mode also means real personal data — saved passwords/autofill,
other logged-in accounts, browsing history, other extensions — is present
in the automated profile, not just a ChatGPT session. A test failure's
trace/screenshot/video (see Debugging below) could in principle capture
fragments of whatever else was in that profile at the time; both are
already gitignored, but they're still unencrypted on disk. Neither mode
is the default — you opt in explicitly via `HAVEN_E2E_BROWSER`.

## Run

```bash
npm run test:e2e            # headless where possible (see Known Limitations)
npm run test:e2e:headed     # watch it click through the UI
npm run test:e2e:ui         # Playwright's interactive UI mode — best for debugging one spec
```

`extension-load.spec.js` runs with no login required and always executes —
it's the fastest way to sanity-check the extension itself still loads.
Every other spec skips itself with an explicit message if
`npm run test:e2e:login` hasn't been run yet, rather than failing on an
opaque timeout.

## Structure

```
playwright.config.js         Chromium-only config; traces/screenshots/video on failure
tests/e2e/
  fixtures.js                 shared `test`/`expect` — extension context, mock server, auth gate
  helpers/
    extension.js               launch persistent context, extension id, popup settings
    chatgpt.js                 open/type/send/clickUseHaven/clickRemember/wait-for-reply
    selectors.js                every CSS selector, in one place
    mock-server.js              in-process fake Haven backend (see below)
    auth.js                    persistent-profile path + "looks logged in" check
    opera.js                    reuse a real Opera GX profile (see "Alternative" above)
  setup-login.js               one-time interactive login (npm run test:e2e:login)
  copy-opera-profile.js        one-time Opera GX profile copy (npm run test:e2e:opera-copy-profile)
  extension-load.spec.js       no-login-required: extension loads, popup works
  bootstrap.spec.js            Scenario 1: bootstrap reply triggers neither Rewrite nor Remember
  remember.spec.js             Scenarios 2+3: Auto Remember fires once, not repeatedly; manual Remember still works
  rewrite.spec.js              Scenario 5: Query Rewrite fires normally outside bootstrap
  regression.spec.js           Scenario 4: a plain conversation is unaffected by any of this
```

## Why a mock Haven backend, not the real one

`tests/e2e/helpers/mock-server.js` is a small Node `http` server implementing
exactly the endpoints `background.js`'s `HavenClient` calls (health,
retrieve_working_context, memory/preview, memory/commit, memory/cancel,
query/rewrite), listening on `127.0.0.1:8765` — the extension's default
server URL, and a host `config.js` always permits without a Chrome
permission prompt. Each test gets its own instance and can override any
endpoint's response (see `remember.spec.js`'s custom `memoryPreview`).

The real Haven backend runs actual LLM extraction — slow, non-deterministic,
and requires provider credentials. This suite's job is to verify the
**extension's** behavior (does it call the right endpoint at the right
moment, with the compose box in the right state, rendering the right
dialog) — not to re-verify the server's extraction quality, which belongs
to `obsidian/`'s own test suite. Because the mock records every request it
receives, specs can assert not just "a dialog appeared" but "`/memory/preview`
was called exactly once" — the actual thing the Auto Remember bug in this
codebase was about (firing at the wrong time / too often), which DOM state
alone can't distinguish.

## Debugging

- `npm run test:e2e:headed` to watch it run.
- `npm run test:e2e:ui` — step through actions, inspect the DOM at each one.
- On any local failure, check `playwright-report/index.html` (open it
  directly in a browser) and `test-results/*/trace.zip`
  (`npx playwright show-trace test-results/.../trace.zip`) — both capture
  screenshots, DOM snapshots, and console/network logs for the failing test
  only (`retain-on-failure` in `playwright.config.js`).

## Known limitations

- **Headless is not the default and may not work.** Chromium's MV3
  service-worker background pages are unreliable under Playwright's
  headless mode. `launchExtensionContext()` defaults to headed; set
  `HAVEN_E2E_HEADLESS=1` to try headless (adds `--headless=new`), but treat
  a failure there as an environment issue, not a real bug, before digging in.
- **Real chatgpt.com selectors will drift.** `helpers/selectors.js`'s
  `chatgpt.*` block (compose box, send/stop buttons) is this suite's own
  best-known-good guess at ChatGPT's DOM — unlike `content/adapters/chatgpt.js`'s
  selectors, which are exercised continuously by real usage, these are only
  exercised when this suite runs. If every ChatGPT-dependent spec starts
  failing at once on the same step, check these selectors first.
- **The mock backend can't validate real extraction correctness** — e.g.
  "none of the injected Working Context is proposed for Remember" is a
  property of the real server's checkpoint/hash-based dedup
  (`obsidian/server/main.py`), not something a scripted mock response can
  meaningfully prove either way. This suite verifies the client-side
  contract (right endpoint, right call count, right timing, right dialog
  contents for whatever the mock returned) — extraction quality itself is
  the real backend's own test coverage.
- **Session expiry.** ChatGPT sessions eventually expire; re-run
  `npm run test:e2e:login` when specs start throwing the "compose box never
  appeared" error.

## CI readiness

Nothing here is CI-specific yet (no GitHub Actions workflow is added by this
change), but the pieces are already in place for one:

- `npx playwright install --with-deps chromium` in the CI image.
- The persistent, authenticated profile (`tests/e2e/.auth/chrome-profile/`)
  would need to be provisioned out-of-band — e.g. captured once locally and
  restored from a CI secret/artifact — since `setup-login.js` is
  interactive by design and must stay that way (see "one-time: authenticate"
  above for why credentials are never hardcoded). Point
  `HAVEN_E2E_PROFILE_DIR` at the restored directory.
- Linux CI runners have no display; wrap the run in `xvfb-run` (headed mode
  still needs a display server even in CI) rather than fighting headless
  MV3 support — e.g. `xvfb-run npm run test:e2e`.
- `extension-load.spec.js` needs none of the above and is a reasonable
  smoke test to wire up first.

## Recommendations for future coverage

- A dedicated spec for a *failed* "Use Haven" retrieval (mock returns
  `available: false` / zero memories) — the error-message path in
  `onButtonClick` has no E2E coverage yet.
  - A spec for ChatGPT's own conversation regeneration (regenerate the
  bootstrap reply) — `remember-visibility.js`'s "awaitingUserMessage"
  regeneration-safety branch is unit-tested but not exercised end-to-end.
- A spec for the offline/server-error path (stop the mock server mid-test,
  assert the "Haven: offline" status and error message render correctly).
