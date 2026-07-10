import { test } from "node:test";
import assert from "node:assert/strict";

import { resolveQuery } from "./resolve-query.js";

test("compose box text is used as-is when present", () => {
  const turns = [{ role: "user", content: "ignored, box wins" }];
  assert.equal(resolveQuery("  what did we decide about auth?  ", turns), "what did we decide about auth?");
});

test("falls back to the most recent user turn when the compose box is empty", () => {
  const turns = [
    { role: "user", content: "first question" },
    { role: "assistant", content: "first answer" },
    { role: "user", content: "second question" },
    { role: "assistant", content: "second answer" },
  ];
  assert.equal(resolveQuery("", turns), "second question");
});

test("returns empty string when only assistant turns exist", () => {
  const turns = [
    { role: "assistant", content: "hello there" },
    { role: "assistant", content: "anything else?" },
  ];
  assert.equal(resolveQuery("   ", turns), "");
});

test("returns empty string when there is no conversation at all", () => {
  assert.equal(resolveQuery("", []), "");
});
