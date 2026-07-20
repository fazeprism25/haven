// Scenario 1 from the manual checklist this suite replaces: clicking
// "Use Haven" and getting its bootstrap reply must not surface either the
// Query Rewrite suggestion or the Review Memories dialog -- see
// content/remember-visibility.js's "bootstrap"/"awaitingUserMessage" phases
// and content/rewrite-suggestion.js's suppression, both of which exist
// specifically to prevent this.

import { test, expect } from "./fixtures.js";
import { haven as havenSel } from "./helpers/selectors.js";
import {
  openChatGPT,
  typePrompt,
  sendPrompt,
  clickUseHaven,
  waitForAssistantReply,
  countAssistantMessages,
  reviewDialog,
} from "./helpers/chatgpt.js";

test("bootstrap: Use Haven's own reply triggers neither Query Rewrite nor Auto Remember", async ({ page, mockServer }) => {
  await openChatGPT(page);
  await typePrompt(page, "What did we decide about the Haven E2E test framework?");

  await clickUseHaven(page);
  // insertContextAbove/setComposeText fire native "input" events for the
  // injected Working Context prompt -- createRewriteSuppression() exists
  // precisely so those never arm a rewrite suggestion; assert that held.
  await expect(page.locator(havenSel.rewriteCard)).toBeHidden();
  expect(mockServer.requestsTo("/api/v1/retrieve_working_context")).toHaveLength(1);

  const beforeReply = await countAssistantMessages(page);
  await sendPrompt(page);
  await waitForAssistantReply(page, beforeReply);

  await expect(page.locator(havenSel.rewriteCard)).toBeHidden();
  await expect(reviewDialog(page)).toBeHidden();
  // The UI assertion above only proves the dialog never rendered; this is
  // the stronger claim -- Auto Remember never even asked the server to
  // extract anything for the bootstrap's own reply. A UI-only check could
  // pass by accident (e.g. a dialog that opened and closed itself); a call
  // count can't.
  expect(mockServer.requestsTo("/api/v1/memory/preview")).toHaveLength(0);
});
