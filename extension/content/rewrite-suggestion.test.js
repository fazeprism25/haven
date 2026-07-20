import { test } from "node:test";
import assert from "node:assert/strict";

import {
  isEligibleForRewrite,
  isSuggestionStillRelevant,
  isAcceptedRewriteEcho,
  createRewriteSuppression,
} from "./rewrite-suggestion.js";

test("a short in-progress fragment is not eligible", () => {
  assert.equal(isEligibleForRewrite("hey"), false);
  assert.equal(isEligibleForRewrite("   "), false);
  assert.equal(isEligibleForRewrite(""), false);
});

test("a short but complete query still clears the length gate", () => {
  assert.equal(isEligibleForRewrite("What is Python?"), true);
});

test("a long draft is eligible", () => {
  assert.equal(
    isEligibleForRewrite("remind me where we left off on the Haven project"),
    true
  );
});

test("null/undefined text is not eligible", () => {
  assert.equal(isEligibleForRewrite(null), false);
  assert.equal(isEligibleForRewrite(undefined), false);
});

test("a changed rewrite matching the current text is relevant", () => {
  const response = { ok: true, data: { original: "remind me where we left off", rewritten: "Retrieve the latest architecture decisions", changed: true } };
  assert.equal(isSuggestionStillRelevant(response, "remind me where we left off"), true);
});

test("an unchanged rewrite is never relevant, even if the text still matches", () => {
  const response = { ok: true, data: { original: "What is Python?", rewritten: "What is Python?", changed: false } };
  assert.equal(isSuggestionStillRelevant(response, "What is Python?"), false);
});

test("a stale response (text changed since the request was sent) is not relevant", () => {
  const response = { ok: true, data: { original: "remind me where we left off", rewritten: "Retrieve the latest architecture decisions", changed: true } };
  assert.equal(isSuggestionStillRelevant(response, "remind me where we left off, also what about auth"), false);
});

test("a failed request is never relevant", () => {
  const response = { ok: false, error: "Haven server is offline." };
  assert.equal(isSuggestionStillRelevant(response, "remind me where we left off"), false);
});

test("trims the current text before comparing", () => {
  const response = { ok: true, data: { original: "remind me where we left off", rewritten: "Retrieve the latest architecture decisions", changed: true } };
  assert.equal(isSuggestionStillRelevant(response, "  remind me where we left off  "), true);
});

test("the programmatic echo of a just-accepted rewrite is recognized", () => {
  assert.equal(
    isAcceptedRewriteEcho(
      "what was the last topic or decision we discussed",
      "what was the last topic or decision we discussed"
    ),
    true
  );
});

test("trims the current text before comparing against the accepted rewrite", () => {
  assert.equal(
    isAcceptedRewriteEcho(
      "  what was the last topic or decision we discussed  ",
      "what was the last topic or decision we discussed"
    ),
    true
  );
});

test("no accepted rewrite on record is never an echo", () => {
  assert.equal(isAcceptedRewriteEcho("anything", null), false);
});

test("text that diverges from the accepted rewrite is not an echo", () => {
  assert.equal(
    isAcceptedRewriteEcho(
      "what was the last topic or decision we discussed, also about auth",
      "what was the last topic or decision we discussed"
    ),
    false
  );
});

test("createRewriteSuppression: isSuppressed() is false outside runSuppressed()", () => {
  const suppression = createRewriteSuppression();
  assert.equal(suppression.isSuppressed(), false);
});

test("createRewriteSuppression: isSuppressed() is true for the whole duration of the mutation, including multiple synchronous events", () => {
  // Regression: an earlier "consume the next single event" design assumed a
  // compose-box mutation fires exactly one native "input" event. Confirmed
  // against real Chromium, execCommand("insertText", ...) with a multi-line
  // string -- which the injected Working Context prompt always is -- fires
  // one "input" event *per line*. A mutation that fires several events must
  // have every one of them suppressed, not just the first.
  const suppression = createRewriteSuppression();
  const observedDuringMutation = [];
  suppression.runSuppressed(() => {
    observedDuringMutation.push(suppression.isSuppressed());
    observedDuringMutation.push(suppression.isSuppressed());
    observedDuringMutation.push(suppression.isSuppressed());
  });
  assert.deepEqual(observedDuringMutation, [true, true, true]);
});

test("createRewriteSuppression: isSuppressed() is false again once runSuppressed() returns", () => {
  const suppression = createRewriteSuppression();
  suppression.runSuppressed(() => {});
  assert.equal(suppression.isSuppressed(), false, "real typing after the mutation must not be suppressed");
});

test("createRewriteSuppression: clears suppression even if the mutation throws", () => {
  const suppression = createRewriteSuppression();
  assert.throws(() => {
    suppression.runSuppressed(() => {
      throw new Error("boom");
    });
  });
  assert.equal(suppression.isSuppressed(), false);
});
