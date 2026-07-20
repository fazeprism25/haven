// ChatGPT-authentication handling for the E2E suite. There is no API for
// logging in programmatically (and hardcoding credentials in a repo is
// exactly what we must not do) -- the supported approach is a *persistent
// browser profile*: log in once, by hand, in a real headed browser window
// (see ../setup-login.js), and every subsequent test run reuses that
// profile's cookies via launchPersistentContext. This file only resolves
// where that profile lives and whether it looks logged in; it never touches
// credentials itself.

import path from "node:path";
import fs from "node:fs";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Gitignored (see tests/e2e/.gitignore) -- this directory ends up holding
// real ChatGPT session cookies.
export function authProfileDir() {
  return process.env.HAVEN_E2E_PROFILE_DIR ?? path.resolve(__dirname, "../.auth/chrome-profile");
}

// A profile directory (and its "Default" subfolder) exists the moment
// *any* launchExtensionContext() call runs against this path -- including
// extension-load.spec.js, which never touches chatgpt.com at all -- so
// their mere presence proves nothing about being logged in. Worse,
// chatgpt.com renders a real compose box even for a signed-out visitor
// (anonymous chat), so "the compose box appeared" isn't a reliable login
// signal either; a naive check here can silently pass while actually
// exercising the real, rate-limited anonymous tier instead of a real
// account. setup-login.js writes this marker file itself, only after the
// person running it confirms (by pressing Enter) that they actually logged
// in -- so this is the one signal here that reflects a real human decision,
// not an inference from browser-profile side effects.
const LOGIN_MARKER_FILE = ".logged-in";

export function markLoggedIn() {
  fs.mkdirSync(authProfileDir(), { recursive: true });
  fs.writeFileSync(path.join(authProfileDir(), LOGIN_MARKER_FILE), new Date().toISOString());
}

export function hasAuthProfile() {
  return fs.existsSync(path.join(authProfileDir(), LOGIN_MARKER_FILE));
}
