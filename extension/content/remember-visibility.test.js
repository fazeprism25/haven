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

// --- "Use Haven" bootstrap phase ---------------------------------------
// bootstrapStarted() is called once, synchronously, right before "Use
// Haven" mutates the compose box -- see controller.js's onButtonClick.

test("regression: retrievalSucceeded() from the bootstrap's own retrieval does not leave Remember visible through the bootstrap window", () => {
  // Reproduces the real bug: onButtonClick calls retrievalSucceeded()
  // (context found) *before* bootstrapStarted() (about to inject it) in the
  // same click -- without bootstrapStarted() retracting that, Remember
  // stayed visible for the entire bootstrap window via visibleFromRetrieval
  // regardless of the assistant-message phase guards, making them
  // effectively dead code in the one flow they exist for.
  const rv = createRememberVisibility();
  rv.conversationObserved("/c/abc");

  rv.retrievalStarted();
  rv.retrievalSucceeded(); // "Use Haven" found context for this click
  assert.equal(rv.isVisible(), true, "visible while the Insert dialog is up, same as before this fix");

  rv.bootstrapStarted(); // context is now being injected
  assert.equal(rv.isVisible(), false, "this click's own retrieval no longer keeps Remember visible once bootstrap starts");

  rv.userMessageObserved("<System>...</System>");
  rv.assistantMessageObserved("Here's a summary of what we've been working on.");
  assert.equal(rv.isVisible(), false);
});

test("bootstrapStarted() does not hide Remember already earned from a real prior exchange", () => {
  // The counterpart regression: bootstrapStarted() must only retract *this*
  // click's own retrieval-based visibility, never a Remember already earned
  // from a genuine assistant reply earlier in the same conversation -- same
  // principle the existing retrievalStarted() regression test enforces.
  const rv = createRememberVisibility();
  rv.conversationObserved("/c/abc");
  rv.userMessageObserved("what's the capital of France?");
  rv.assistantMessageObserved("Paris.");
  assert.equal(rv.isVisible(), true);

  rv.retrievalStarted();
  rv.retrievalSucceeded();
  rv.bootstrapStarted(); // the user clicks "Use Haven" for a follow-up query

  assert.equal(rv.isVisible(), true, "Remember must stay visible for the earlier real exchange");
});

test("the assistant's reply to a bootstrapped prompt does not earn Remember on its own", () => {
  const rv = createRememberVisibility();
  rv.conversationObserved("/c/abc");

  rv.bootstrapStarted();
  // The injected Working Context prompt being sent -- a new user turn, but
  // an internal one, not a genuine follow-up.
  rv.userMessageObserved("<System><HavenContext>...</HavenContext><UserRequest>...</UserRequest></System>");
  // The bootstrap's own reply.
  const crossedBoundary = rv.assistantMessageObserved("Here's a summary of what we've been working on.");

  assert.equal(rv.isVisible(), false);
  assert.equal(
    crossedBoundary,
    false,
    "the bootstrap's own reply must never signal Auto Remember's trigger"
  );
});

test("a regenerated bootstrap reply is still suppressed while awaiting a genuine user message", () => {
  const rv = createRememberVisibility();
  rv.conversationObserved("/c/abc");

  rv.bootstrapStarted();
  rv.userMessageObserved("<System>...</System>");
  rv.assistantMessageObserved("first bootstrap reply");
  assert.equal(rv.isVisible(), false);

  rv.assistantMessageObserved("regenerated bootstrap reply, different content");
  assert.equal(rv.isVisible(), false);
});

test("Remember becomes eligible again once the user sends a genuine follow-up after the bootstrap reply", () => {
  const rv = createRememberVisibility();
  rv.conversationObserved("/c/abc");

  rv.bootstrapStarted();
  rv.userMessageObserved("<System>...</System>");
  rv.assistantMessageObserved("Here's a summary of what we've been working on.");
  assert.equal(rv.isVisible(), false);

  rv.userMessageObserved("Actually, let's switch from PostgreSQL to MySQL.");
  const crossedBoundary = rv.assistantMessageObserved("Sure, here's what changes with MySQL.");

  assert.equal(rv.isVisible(), true);
  assert.equal(
    crossedBoundary,
    true,
    "the reply to the first genuine post-bootstrap message must signal Auto Remember's trigger"
  );
});

test("existing Remember behavior is unchanged for a normal conversation with no Use Haven click", () => {
  const rv = createRememberVisibility();
  rv.conversationObserved("/c/abc");

  rv.userMessageObserved("what's the capital of France?");
  const crossedBoundary = rv.assistantMessageObserved("Paris.");

  assert.equal(rv.isVisible(), true);
  assert.equal(
    crossedBoundary,
    false,
    "a conversation that never called bootstrapStarted() must never fire Auto Remember's trigger"
  );
});

// --- Auto Remember trigger (assistantMessageObserved's return value) ---
// The signal controller.js's updateRememberVisibility() uses, gated on
// settings.autoRemember, to decide whether to call onRememberClick()
// automatically -- see onButtonClick's comment on why the old unconditional
// "insert then immediately Remember" trigger was wrong.

test("Auto Remember trigger fires exactly once per bootstrap, not on every later assistant reply", () => {
  const rv = createRememberVisibility();
  rv.conversationObserved("/c/abc");

  rv.bootstrapStarted();
  rv.userMessageObserved("<System>...</System>");
  rv.assistantMessageObserved("bootstrap reply");

  rv.userMessageObserved("first genuine follow-up");
  assert.equal(rv.assistantMessageObserved("first genuine reply"), true);

  // A second exchange in the same conversation, well after the bootstrap
  // boundary was already crossed -- behaves like a plain conversation from
  // here on, same as if "Use Haven" had never been clicked.
  rv.userMessageObserved("second genuine follow-up");
  assert.equal(rv.assistantMessageObserved("second genuine reply"), false);
});

test("Auto Remember trigger never fires while streaming/regenerating the bootstrap's own reply, even across multiple content changes", () => {
  const rv = createRememberVisibility();
  rv.conversationObserved("/c/abc");

  rv.bootstrapStarted();
  rv.userMessageObserved("<System>...</System>");
  // Simulates streaming: the same turn's content changing across several
  // mutation-observer ticks before the reply finishes.
  assert.equal(rv.assistantMessageObserved("Here"), false);
  assert.equal(rv.assistantMessageObserved("Here's a"), false);
  assert.equal(rv.assistantMessageObserved("Here's a summary."), false);
});

test("Auto Remember trigger does not fire for the injected prompt's own turn, only for the reply to the genuine follow-up after it", () => {
  const rv = createRememberVisibility();
  rv.conversationObserved("/c/abc");

  rv.bootstrapStarted();
  assert.equal(
    rv.assistantMessageObserved("stray reply observed before the injected prompt was even sent"),
    false
  );
});

test("navigating to a different conversation resets the bootstrap phase", () => {
  const rv = createRememberVisibility();
  rv.conversationObserved("/c/abc");
  rv.bootstrapStarted();
  rv.userMessageObserved("<System>...</System>");

  rv.conversationObserved("/c/xyz");

  // A fresh conversation behaves normally: the very next assistant reply
  // earns Remember, with no lingering bootstrap suppression.
  rv.assistantMessageObserved("an unrelated reply in the new conversation");
  assert.equal(rv.isVisible(), true);
});
