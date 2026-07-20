// Scenario 5 from the original checklist: the Query Rewrite Assistant works
// normally in an ordinary conversation, and never appears during a "Use
// Haven" bootstrap (covered by bootstrap.spec.js's suppression assertion --
// this file only covers the positive case, to keep the two concerns in
// separate specs).

import { test as base, expect } from "./fixtures.js";
import { startMockServer } from "./helpers/mock-server.js";
import { haven as havenSel } from "./helpers/selectors.js";
import { openChatGPT, typePrompt } from "./helpers/chatgpt.js";

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

test("Query Rewrite suggests a rewrite for an eligible draft in a normal conversation", async ({ page, mockServer }) => {
  await openChatGPT(page);
  // isEligibleForRewrite (rewrite-suggestion.js) requires >= 12 trimmed
  // characters -- comfortably cleared here.
  await typePrompt(page, "remind me what we discussed about the vault schema last week");

  const card = page.locator(havenSel.rewriteCard);
  await expect(card).toBeVisible({ timeout: 5000 }); // 700ms debounce + round trip
  expect(mockServer.requestsTo("/api/v1/query/rewrite")).toHaveLength(1);

  await card.locator(havenSel.rewriteUseButton).click();
  await expect(card).toBeHidden();
  // Accepting a rewrite must not immediately re-arm a second suggestion
  // against its own echoed "input" event (isAcceptedRewriteEcho's contract).
  // The network assertion is the stronger claim here: onComposeInput's echo
  // guard means the endpoint is never even called a second time, not just
  // that a second suggestion happens not to be showing.
  await page.waitForTimeout(1000);
  await expect(card).toBeHidden();
  expect(mockServer.requestsTo("/api/v1/query/rewrite")).toHaveLength(1);
});
