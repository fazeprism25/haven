// User-facing actions against the real chatgpt.com page -- every fragile
// DOM detail lives behind these functions (and selectors.js) so specs read
// as a sequence of things a real user would do, never raw locators.

import { expect } from "@playwright/test";
import { chatgpt as sel, haven as havenSel } from "./selectors.js";

const CHATGPT_URL = "https://chatgpt.com/";

// Navigates to a fresh, unsent conversation. Throws a clear, actionable
// error (rather than timing out opaquely on a missing compose box) if the
// profile isn't actually logged in -- see auth.js/setup-login.js.
export async function openChatGPT(page) {
  await page.goto(CHATGPT_URL);
  const composeBox = page.locator(sel.composeBox).first();
  const ready = await composeBox
    .waitFor({ state: "visible", timeout: 20000 })
    .then(() => true)
    .catch(() => false);
  if (!ready) {
    throw new Error(
      "ChatGPT's compose box never appeared -- the test profile is probably not logged in. " +
        "Run `npm run test:e2e:login` once to authenticate it, then re-run the suite."
    );
  }
}

// Equivalent to openChatGPT for these tests' purposes -- chatgpt.com always
// opens on a fresh, unsent conversation, so there is no separate
// "click New chat" step needed. Kept as its own function so specs read
// intention-first ("start a new conversation") rather than re-explaining
// that chatgpt.com happens to boot into one.
export async function startNewConversation(page) {
  await openChatGPT(page);
}

export async function typePrompt(page, text) {
  const box = page.locator(sel.composeBox).first();
  await box.click();
  await box.fill(text);
}

export async function sendPrompt(page) {
  await page.locator(sel.sendButton).click();
}

export async function waitForStreamingToFinish(page) {
  const stopButton = page.locator(sel.stopStreamingButton);
  // Streaming may already be finished (or never even shown the stop button
  // for a very fast reply) by the time this runs -- both are success, not
  // an error, so a missing stop button is not awaited as a hard failure.
  await stopButton.waitFor({ state: "visible", timeout: 5000 }).catch(() => {});
  await stopButton.waitFor({ state: "hidden", timeout: 60000 }).catch(() => {});
}

export async function countAssistantMessages(page) {
  return page.locator(sel.assistantMessage).count();
}

// Waits for a new assistant turn to appear beyond `previousCount`, then for
// streaming to finish. `previousCount` should be
// (await countAssistantMessages(page)) captured *before* the action that
// triggers the new reply.
export async function waitForAssistantReply(page, previousCount) {
  await expect
    .poll(() => countAssistantMessages(page), { timeout: 60000, message: "waiting for a new assistant reply" })
    .toBeGreaterThan(previousCount);
  await waitForStreamingToFinish(page);
}

// Clicks "Use Haven", resolves the Working Context preview dialog if
// autoPreview is on (Insert), and returns without sending -- inserting
// context and sending the prompt are deliberately separate steps (see
// content/controller.js's onButtonClick comment: Insert never auto-sends),
// matching real usage, and letting bootstrap specs assert on state between
// insert and send if they need to.
export async function clickUseHaven(page) {
  await page.locator(havenSel.useHavenButton).click();
  const dialog = page.locator(havenSel.workingContextDialog);
  const dialogShown = await dialog
    .waitFor({ state: "visible", timeout: 5000 })
    .then(() => true)
    .catch(() => false);
  if (dialogShown) await dialog.locator(havenSel.dialogInsertButton).click();
}

export async function clickRemember(page) {
  await page.locator(havenSel.rememberButton).click();
}

// The Review Memories dialog specifically, distinguished from the Working
// Context / plain preview dialogs (which share the same overlay class) by
// its heading text.
export function reviewDialog(page) {
  return page.locator(havenSel.dialogOverlay).filter({ hasText: "Review memories" });
}
