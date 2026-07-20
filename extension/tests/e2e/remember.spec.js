// Scenarios 2 and 3 from the manual checklist: once a genuine post-bootstrap
// exchange happens, Auto Remember must fire exactly once (not on the
// bootstrap reply, not again on every reply after) -- the actual bug this
// whole redesign fixed. See content/remember-visibility.js's
// "awaitingGenuineReply" phase and controller.js's updateRememberVisibility.
//
// Overrides the `mockServer` fixture (see fixtures.js) with a
// memoryPreview handler this file controls, so assertions can check both
// what the UI shows AND exactly how many times /memory/preview was called
// -- call count is what actually proves "fired once", since two identical-
// looking dialogs are indistinguishable by DOM state alone.

import { test as base, expect } from "./fixtures.js";
import { startMockServer } from "./helpers/mock-server.js";
import { haven as havenSel } from "./helpers/selectors.js";
import {
  openChatGPT,
  typePrompt,
  sendPrompt,
  clickUseHaven,
  clickRemember,
  waitForAssistantReply,
  countAssistantMessages,
  reviewDialog,
} from "./helpers/chatgpt.js";
import { enableAutoRemember } from "./helpers/extension.js";

const test = base.extend({
  mockServer: async ({}, use) => {
    const server = await startMockServer({
      memoryPreview: () => ({
        status: "new",
        review_id: "review-genuine",
        items: [{ fact_index: 0, text: "The E2E suite uses a mock Haven server.", memory_type: "fact", evidence: "" }],
      }),
    });
    await use(server);
    await server.close();
  },
});

test("Auto Remember fires once after the first genuine post-bootstrap reply, not before or repeatedly", async ({
  context,
  page,
  mockServer,
}) => {
  await enableAutoRemember(context);
  await openChatGPT(page);

  await typePrompt(page, "What did we decide about the Haven E2E test framework?");
  await clickUseHaven(page);
  let beforeReply = await countAssistantMessages(page);
  await sendPrompt(page);
  await waitForAssistantReply(page, beforeReply);

  // Bootstrap's own reply: must not trigger Auto Remember.
  await expect(reviewDialog(page)).toBeHidden();
  expect(mockServer.requestsTo("/api/v1/memory/preview")).toHaveLength(0);

  // First genuine follow-up: this is the turn that introduces new
  // information the pre-fix code either never captured or captured too
  // early (against the pre-bootstrap conversation).
  await typePrompt(page, "Let's also add a spec for the Query Rewrite Assistant.");
  beforeReply = await countAssistantMessages(page);
  await sendPrompt(page);
  await waitForAssistantReply(page, beforeReply);

  await expect(reviewDialog(page)).toBeVisible();
  expect(mockServer.requestsTo("/api/v1/memory/preview")).toHaveLength(1);
  await expect(page.locator(havenSel.reviewCard).locator(havenSel.reviewTextarea)).toHaveValue(
    "The E2E suite uses a mock Haven server."
  );
  await reviewDialog(page).locator(havenSel.dialogCancelButton).click();
  await expect(reviewDialog(page)).toBeHidden();

  // Continue the conversation: Auto Remember must not fire again for every
  // subsequent reply -- this is the exact regression the pre-fix "trigger
  // on every assistant message" version had.
  await typePrompt(page, "Sounds good, anything else we should cover?");
  beforeReply = await countAssistantMessages(page);
  await sendPrompt(page);
  await waitForAssistantReply(page, beforeReply);

  await expect(reviewDialog(page)).toBeHidden();
  expect(mockServer.requestsTo("/api/v1/memory/preview")).toHaveLength(1);

  // Manual Remember remains available and independent of the auto-trigger
  // machinery -- clicking it explicitly still works mid-conversation.
  await clickRemember(page);
  await expect(reviewDialog(page)).toBeVisible();
  expect(mockServer.requestsTo("/api/v1/memory/preview")).toHaveLength(2);
  await reviewDialog(page).locator(havenSel.dialogInsertButton).click();
  await expect(reviewDialog(page)).toBeHidden();
  expect(mockServer.requestsTo("/api/v1/memory/commit")).toHaveLength(1);

  // The capstone assertion: the full request lifecycle, in order, across
  // the whole conversation -- not just each endpoint's isolated call count.
  // This is what actually proves the sequence this bug was about (bootstrap
  // retrieval, then exactly one auto-triggered preview at the right moment,
  // then the cancelled dialog's cancel, then one more preview for the
  // explicit manual Remember, then its commit) rather than five counts that
  // happen to add up right in the wrong order.
  expect(mockServer.paths()).toEqual([
    "/api/v1/retrieve_working_context",
    "/api/v1/memory/preview", // auto-triggered by the first genuine reply
    "/api/v1/memory/cancel", // that dialog was cancelled, not saved
    "/api/v1/memory/preview", // manual Remember click
    "/api/v1/memory/commit", // manual Remember's Save
  ]);
});
