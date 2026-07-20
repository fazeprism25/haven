// Launches Chromium with the unpacked Haven extension loaded, via a
// persistent context -- Playwright/Chromium only support loading unpacked
// MV3 extensions through launchPersistentContext (chromium.launch() +
// context.newPage() cannot see extensions at all), and MV3's service-worker
// background page is not reliably reachable under headless mode, so this
// defaults to headed. Set HAVEN_E2E_HEADLESS=1 to try headless anyway (see
// README's Known Limitations for why that's not the default).

import path from "node:path";
import os from "node:os";
import fs from "node:fs";
import { chromium } from "@playwright/test";
import { authProfileDir } from "./auth.js";
import { EXTENSION_PATH } from "./paths.js";
import { launchOperaGxContext, defaultOperaCopyDestDir } from "./opera.js";

export { EXTENSION_PATH };

// Defaults to the shared, gitignored, login-persisting profile
// (helpers/auth.js, a fresh Chromium profile authenticated once via
// setup-login.js) -- pass an explicit `profileDir` for a spec that has no
// business touching a real chatgpt.com session at all (see
// extension-load.spec.js), so it never pollutes the shared profile just by
// launching.
//
// Set HAVEN_E2E_BROWSER=opera-gx or =opera-gx-copy to reuse a real,
// already-logged-in Opera GX profile instead -- see helpers/opera.js and
// README.md's "Using your real Opera GX login" section. Every spec and
// every other helper is unaffected either way: they only ever see a
// standard Playwright BrowserContext back from this function, regardless
// of which underlying browser/profile produced it.
function launchPlainChromiumContext({ headless, profileDir }) {
  return chromium.launchPersistentContext(profileDir ?? authProfileDir(), {
    headless,
    args: [
      `--disable-extensions-except=${EXTENSION_PATH}`,
      `--load-extension=${EXTENSION_PATH}`,
      // Headless Chromium needs this explicit flag to load extensions at
      // all (regular headed mode does not) -- harmless when already headed.
      ...(headless ? ["--headless=new"] : []),
    ],
  });
}

export async function launchExtensionContext({ headless, profileDir } = {}) {
  const resolvedHeadless = headless ?? process.env.HAVEN_E2E_HEADLESS === "1";
  const browserMode = process.env.HAVEN_E2E_BROWSER;

  // An explicit profileDir (launchThrowawayExtensionContext below) always
  // means "a real chatgpt.com session is irrelevant here" -- HAVEN_E2E_BROWSER
  // must not override that, or extension-load.spec.js would start depending
  // on your real Opera GX profile just because it happens to be set globally
  // for other specs in the same run, defeating the entire point of a
  // throwaway profile.
  if (!profileDir) {
    if (browserMode === "opera-gx") {
      return launchOperaGxContext({ headless: resolvedHeadless });
    }
    if (browserMode === "opera-gx-copy") {
      return launchOperaGxContext({ headless: resolvedHeadless, userDataDir: defaultOperaCopyDestDir() });
    }
  }

  return launchPlainChromiumContext({ headless: resolvedHeadless, profileDir });
}

// A fresh, disposable profile dir per call -- for specs (extension-load.spec.js)
// that only need the extension itself, never a real chatgpt.com session, so
// they can't accidentally rely on (or pollute) the shared login profile, and
// can't be redirected to HAVEN_E2E_BROWSER's Opera GX profile either (see
// the profileDir check in launchExtensionContext above) -- always plain
// Chromium, regardless of what other specs in the same run are configured
// to use. Caller is responsible for both context.close() and cleanup().
export async function launchThrowawayExtensionContext({ headless } = {}) {
  const resolvedHeadless = headless ?? process.env.HAVEN_E2E_HEADLESS === "1";
  const profileDir = fs.mkdtempSync(path.join(os.tmpdir(), "haven-e2e-"));
  const context = await launchPlainChromiumContext({ headless: resolvedHeadless, profileDir });
  return { context, cleanup: () => fs.rmSync(profileDir, { recursive: true, force: true }) };
}

// MV3 service workers can take a moment to spin up after the context opens
// (and don't exist at all until the extension's first event), so this
// waits for one rather than assuming context.serviceWorkers() is
// immediately populated.
export async function getExtensionId(context) {
  let [worker] = context.serviceWorkers();
  if (!worker) worker = await context.waitForEvent("serviceworker", { timeout: 15000 });
  return new URL(worker.url()).hostname;
}

export async function openExtensionPopup(context) {
  const extensionId = await getExtensionId(context);
  const page = await context.newPage();
  await page.goto(`chrome-extension://${extensionId}/popup/popup.html`);
  return page;
}

// Toggles "Auto-remember after insert" via the real popup UI (not a direct
// chrome.storage.local write) so these helpers exercise the same path a
// user does, and closes the popup page afterward -- it's disposable, unlike
// the ChatGPT tab specs actually assert against.
async function setCheckbox(context, id, checked) {
  const popup = await openExtensionPopup(context);
  const box = popup.locator(`#${id}`);
  if ((await box.isChecked()) !== checked) await box.setChecked(checked);
  await popup.locator("#save-settings").click();
  await popup.getByText("Settings saved.").waitFor({ state: "visible", timeout: 5000 }).catch(() => {});
  await popup.close();
}

export const enableAutoRemember = (context) => setCheckbox(context, "auto-remember", true);
export const disableAutoRemember = (context) => setCheckbox(context, "auto-remember", false);
export const enableAutoPreview = (context) => setCheckbox(context, "auto-preview", true);
export const disableAutoPreview = (context) => setCheckbox(context, "auto-preview", false);

// Points the extension at the mock server for this test run. Not needed
// when the mock listens on config.js's DEFAULT_SETTINGS.serverUrl
// (127.0.0.1:8765, an always-permitted host) -- included for specs that
// want to be explicit, or a future mock running on a different port.
export async function setServerUrl(context, serverUrl) {
  const popup = await openExtensionPopup(context);
  await popup.locator("#server-url").fill(serverUrl);
  await popup.locator("#save-settings").click();
  await popup.getByText("Settings saved.").waitFor({ state: "visible", timeout: 5000 }).catch(() => {});
  await popup.close();
}
