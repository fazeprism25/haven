// See tests/e2e/README.md for setup, debugging, and CI notes.
//
// No `use.browserName`/`projects` matrix -- this suite only ever runs
// Chromium (extensions are Chromium-only; see helpers/extension.js), so a
// multi-browser project list would just be dead configuration.
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  testMatch: "**/*.spec.js",
  timeout: 90_000,
  fullyParallel: false, // one shared mock-server port (127.0.0.1:8765) per test; see helpers/mock-server.js
  workers: 1,
  // 0 locally: a local failure should mean something, not get silently
  // swallowed by a retry while iterating. 1 in CI: these specs drive a real
  // remote chatgpt.com, where a slow reply or a momentary hiccup is
  // possible and shouldn't fail a whole CI run by itself -- a real
  // regression will still fail twice in a row.
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    // "retain-on-failure" (not "on-first-retry") deliberately -- it
    // captures a trace on every failing attempt regardless of whether a
    // retry ever happens, so it still works with retries:0 locally; with
    // "on-first-retry" and retries:0, a local run would never produce a
    // trace at all.
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
});
