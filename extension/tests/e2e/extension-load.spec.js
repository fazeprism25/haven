// Verifies the extension itself loads correctly under Playwright, with no
// dependency on ChatGPT or a login. Deliberately uses @playwright/test's
// plain `test` (not fixtures.js's, which gates every other spec behind an
// authenticated profile) -- this is the one spec that should always be able
// to run, including in a fresh checkout with no login step done yet, so
// `npm run test:e2e` always proves *something* real rather than skipping
// entirely on a clean machine.

import { test, expect } from "@playwright/test";
import { launchThrowawayExtensionContext, getExtensionId, openExtensionPopup } from "./helpers/extension.js";

test.describe("extension loading", () => {
  test("loads as an MV3 service worker and exposes a working popup", async () => {
    const { context, cleanup } = await launchThrowawayExtensionContext();
    try {
      const extensionId = await getExtensionId(context);
      expect(extensionId).toMatch(/^[a-p]{32}$/); // Chrome's extension-id alphabet/length

      const popup = await openExtensionPopup(context);
      await expect(popup.locator("h1")).toHaveText("Haven");
      await expect(popup.locator("#auto-remember")).toBeVisible();
      await expect(popup.locator("#server-url")).toBeVisible();
      await popup.close();
    } finally {
      await context.close();
      cleanup();
    }
  });
});
