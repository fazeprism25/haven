import { test } from "node:test";
import assert from "node:assert/strict";

import {
  isEligibleForRewrite,
  isSuggestionStillRelevant,
  isAcceptedRewriteEcho,
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
