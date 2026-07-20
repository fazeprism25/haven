// Makes (or safely refreshes) a filesystem copy of your real,
// already-logged-in Opera GX profile into this repo's gitignored
// tests/e2e/.auth/opera-gx-copy/, so the E2E suite can automate that copy
// indefinitely without ever touching (or being blocked by) your live,
// daily Opera GX session again.
//
// This does NOT log in, does NOT read/decrypt any credentials or cookie
// values, and does NOT modify your real Opera GX profile in any way -- it's
// a plain recursive file copy, same as dragging the folder in Explorer.
// Requires Opera GX to be fully closed first (see helpers/opera.js's
// isProfileLocked): copying a live, in-use profile risks a torn/corrupt
// snapshot of files like Cookies that Opera writes to continuously. Once
// this finishes, you can reopen Opera GX immediately -- the copy is
// independent of it from that point on.
//
// Safe to re-run any time your Opera GX session changes (new login,
// logged out, etc.) -- it replaces the previous copy outright, refusing to
// do so only if that previous copy is itself currently in use by a running
// Playwright test (see copyOperaGxProfile's own destination-lock check).
//
// Run with: npm run test:e2e:opera-copy-profile
// Then run tests with: HAVEN_E2E_BROWSER=opera-gx-copy npm run test:e2e

import { copyOperaGxProfile } from "./helpers/opera.js";

function main() {
  const destDir = copyOperaGxProfile();
  console.log(`\nDone: ${destDir}`);
  console.log("You can reopen Opera GX now.");
  console.log("Run the suite with: HAVEN_E2E_BROWSER=opera-gx-copy npm run test:e2e");
}

try {
  main();
} catch (error) {
  console.error(`\n${error.message}`);
  process.exit(1);
}
