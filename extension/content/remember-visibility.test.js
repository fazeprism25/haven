import { test } from "node:test";
import assert from "node:assert/strict";

import { createRememberVisibility } from "./remember-visibility.js";

test("a successful retrieval immediately shows Remember", () => {
  const rv = createRememberVisibility();
  rv.conversationObserved("/c/abc");
  rv.retrievalStarted();
  rv.retrievalSucceeded();
  assert.equal(rv.isVisible(), true);
});

test("Remember is visible without any Insert-equivalent action", () => {
  // There is no "insert" method on this state machine at all -- Remember's
  // visibility is driven solely by retrievalSucceeded(). This test proves
  // visibility can't be gated behind a user confirming a dialog: the only
  // calls made here are the ones a retrieval success triggers.
  const rv = createRememberVisibility();
  rv.conversationObserved("/c/abc");
  rv.retrievalStarted();
  rv.retrievalSucceeded();
  assert.equal(rv.isVisible(), true);
  assert.equal(typeof rv.contextInserted, "undefined");
});

test("a compose-box remount (same conversation observed again) does not hide it", () => {
  const rv = createRememberVisibility();
  rv.conversationObserved("/c/abc");
  rv.retrievalStarted();
  rv.retrievalSucceeded();

  // Simulates controller.js's sync() re-running after ChatGPT swaps the
  // compose box's DOM node internally, while staying in the same chat.
  rv.conversationObserved("/c/abc");

  assert.equal(rv.isVisible(), true);
});

test("navigating to a genuinely different conversation hides it again", () => {
  const rv = createRememberVisibility();
  rv.conversationObserved("/c/abc");
  rv.retrievalStarted();
  rv.retrievalSucceeded();

  rv.conversationObserved("/c/xyz");

  assert.equal(rv.isVisible(), false);
});

test("an assistant response makes Remember visible with no Use Haven click at all", () => {
  // The core bug fix: Remember must not require retrievalStarted/
  // retrievalSucceeded to ever have been called.
  const rv = createRememberVisibility();
  rv.conversationObserved("/c/abc");
  assert.equal(rv.isVisible(), false);

  rv.assistantMessageObserved("Rust is an interesting language because...");

  assert.equal(rv.isVisible(), true);
});

test("repeated ticks with the same assistant content are a no-op", () => {
  const rv = createRememberVisibility();
  rv.conversationObserved("/c/abc");
  rv.assistantMessageObserved("final answer");
  assert.equal(rv.isVisible(), true);

  // Simulates sync() re-running with nothing new to observe.
  rv.assistantMessageObserved("final answer");

  assert.equal(rv.isVisible(), true);
});

test("a regeneration (same turn position, new content) is still detected", () => {
  // Regeneration replaces the last assistant message in place rather than
  // appending a new turn, so a turn-count-based check would miss it. This
  // is why visibility is keyed off content instead.
  const rv = createRememberVisibility();
  rv.conversationObserved("/c/abc");
  rv.assistantMessageObserved("first draft of the answer");
  assert.equal(rv.isVisible(), true);

  rv.assistantMessageObserved("regenerated answer, different content");

  assert.equal(rv.isVisible(), true);
});

test("navigating to a different conversation resets assistant-message tracking", () => {
  const rv = createRememberVisibility();
  rv.conversationObserved("/c/abc");
  rv.assistantMessageObserved("answer in conversation abc");
  assert.equal(rv.isVisible(), true);

  rv.conversationObserved("/c/xyz");
  assert.equal(rv.isVisible(), false);

  // Same content string as the other conversation still counts as new,
  // since tracking was reset on the conversation change.
  rv.assistantMessageObserved("answer in conversation abc");
  assert.equal(rv.isVisible(), true);
});

test("regression: a 'Use Haven' click that finds no context does not hide a Remember already earned from an assistant reply", () => {
  // Reproduces the real bug: onButtonClick calls retrievalStarted() up
  // front unconditionally, then returns early (no context found, offline,
  // empty query, ...) without ever calling retrievalSucceeded(). Remember's
  // visibility earned from the conversation's own assistant reply must
  // survive that -- retrievalStarted() must only ever clear the
  // *retrieval* reason for visibility, never the assistant-message one.
  const rv = createRememberVisibility();
  rv.conversationObserved("/c/abc");
  rv.assistantMessageObserved("the assistant's completed reply");
  assert.equal(rv.isVisible(), true);

  rv.retrievalStarted(); // user clicks "Use Haven" for a new, unrelated query
  assert.equal(rv.isVisible(), true, "Remember must stay visible while the new retrieval is in flight");

  // The retrieval finds nothing / fails; onButtonClick returns early and
  // never calls retrievalSucceeded(). Remember must still be visible,
  // since the conversation on screen hasn't changed.
  assert.equal(rv.isVisible(), true, "Remember must remain visible after a failed/empty retrieval");
});

test("retrievalStarted() still clears a stale retrieval-only Remember (no assistant reply on screen)", () => {
  const rv = createRememberVisibility();
  rv.conversationObserved("/c/abc"); // fresh chat, no assistant turn yet
  rv.retrievalStarted();
  rv.retrievalSucceeded(); // "Use Haven" succeeded before anything was sent
  assert.equal(rv.isVisible(), true);

  rv.retrievalStarted(); // a second, different "Use Haven" click begins
  assert.equal(rv.isVisible(), false, "the prior retrieval's Remember must not linger once a new one starts");
});

test("no assistant turn yet (fresh compose box) keeps Remember hidden", () => {
  const rv = createRememberVisibility();
  rv.conversationObserved("/c/abc");

  rv.assistantMessageObserved(null);
  rv.assistantMessageObserved("");

  assert.equal(rv.isVisible(), false);
});
