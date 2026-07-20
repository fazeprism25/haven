// Scenario 4 (Regression) from the manual checklist: a conversation that
// never touches "Use Haven" at all must behave exactly as it did before
// this feature existed -- Query Rewrite and manual Remember both unaffected
// by the bootstrap-lifecycle machinery, since that machinery is only ever
// entered by bootstrapStarted() (see remember-visibility.js), which nothing
// in this spec ever calls.

import { test as base, expect } from "./fixtures.js";
import { startMockServer } from "./helpers/mock-server.js";
import { haven as havenSel } from "./helpers/selectors.js";
import {
  openChatGPT,
  typePrompt,
  sendPrompt,
  clickRemember,
  waitForAssistantReply,
  countAssistantMessages,
  reviewDialog,
} from "./helpers/chatgpt.js";

const test = base.extend({
  mockServer: async ({}, use) => {
    const server = await startMockServer({
      queryRewrite: (body) => ({
        original: body?.query ?? "",
        changed: true,
        rewritten: `${body?.query ?? ""} (rewritten by Haven)`,
      }),
    });
    await use(server);
    await server.close();
  },
});

test("a plain conversation without Use Haven behaves exactly as before", async ({ page, mockServer }) => {
  await openChatGPT(page);
  await typePrompt(page, "what is the capital of France and why does it matter historically");

  await expect(page.locator(havenSel.rewriteCard)).toBeVisible({ timeout: 5000 });

  const beforeReply = await countAssistantMessages(page);
  await sendPrompt(page);
  await waitForAssistantReply(page, beforeReply);

  await clickRemember(page);
  await expect(reviewDialog(page)).toBeVisible();
  expect(mockServer.requestsTo("/api/v1/memory/preview")).toHaveLength(1);
  await reviewDialog(page).locator(havenSel.dialogCancelButton).click();
  await expect(reviewDialog(page)).toBeHidden();
  expect(mockServer.requestsTo("/api/v1/memory/cancel")).toHaveLength(1);

  // Never having clicked "Use Haven" should mean exactly that -- the
  // bootstrap-lifecycle endpoint was never hit at all, not just that its
  // dialog never happened to render.
  expect(mockServer.requestsTo("/api/v1/retrieve_working_context")).toHaveLength(0);
});
