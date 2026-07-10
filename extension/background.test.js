import { test } from "node:test";
import assert from "node:assert/strict";

// background.js registers a chrome.runtime.onMessage listener as an
// import-time side effect, so it needs a minimal chrome stub in place
// before it's imported at all. The addListener stub captures the real
// listener function so tests below can invoke it directly; storage.local.get
// is indirected through storageGetImpl so individual tests can swap its
// behavior (e.g. to simulate it rejecting) without re-importing the module.
let registeredListener;
let storageGetImpl = async () => ({});
globalThis.chrome = {
  runtime: {
    onMessage: {
      addListener(fn) {
        registeredListener = fn;
      },
    },
  },
  storage: { local: { get: (...args) => storageGetImpl(...args) } },
};

const { errorMessageFor } = await import("./background.js");

test("regression: a FastAPI {detail} error body is shown as its own message, not raw JSON", () => {
  // Reproduces the real bug: e.g. deleting every card in Memory Review and
  // clicking Save sends an empty item list, which the server 422s with
  // {"detail": "Nothing worth remembering was found in that text."}. The
  // extension's showError() displays this string verbatim -- it must be
  // the friendly detail, not the raw response body.
  const body = JSON.stringify({ detail: "Nothing worth remembering was found in that text." });
  assert.equal(
    errorMessageFor(422, body),
    "Nothing worth remembering was found in that text."
  );
});

test("a 404 with a detail message (e.g. expired review_id) also unwraps cleanly", () => {
  const body = JSON.stringify({ detail: "Unknown or expired review_id. Click Remember again." });
  assert.equal(errorMessageFor(404, body), "Unknown or expired review_id. Click Remember again.");
});

test("a non-JSON response body falls back to a plain status-only message, not the raw body", () => {
  assert.equal(
    errorMessageFor(500, "Internal Server Error"),
    "Haven server error (500). Please try again."
  );
});

test("an empty response body falls back to a plain status-only message", () => {
  assert.equal(errorMessageFor(503, ""), "Haven server error (503). Please try again.");
  assert.equal(errorMessageFor(503, null), "Haven server error (503). Please try again.");
});

test("JSON with no string `detail` field falls back to a plain status-only message, not the raw JSON", () => {
  const body = JSON.stringify({ message: "not the field we look for" });
  assert.equal(errorMessageFor(400, body), "Haven server error (400). Please try again.");
});

test("a long raw body is truncated before being logged, never shown to the user", () => {
  const longBody = "x".repeat(500);
  assert.equal(errorMessageFor(502, longBody), "Haven server error (502). Please try again.");
});

test("regression: an exception before fetch (e.g. chrome.storage rejecting while the service worker wakes) still calls sendResponse instead of leaving the message port hanging", async () => {
  // Reproduces the real bug: resolveBaseUrl()'s chrome.storage.local.get
  // used to run outside the listener's try/catch, so it throwing left
  // sendResponse never called -- the content script's chrome.runtime
  // callback would eventually fire with chrome.runtime.lastError set to
  // "The message port closed before a response was received." instead of
  // ever reaching HavenClient's { ok: false, ... } shape.
  storageGetImpl = async () => {
    throw new Error("storage unavailable during service worker wake-up");
  };

  const responses = [];
  const keptChannelOpen = registeredListener(
    { type: "HAVEN_MEMORY_PREVIEW", payload: { canonical_fact: "test" } },
    {},
    (response) => responses.push(response)
  );
  assert.equal(keptChannelOpen, true);

  // Flush the microtask queue so the listener's async IIFE runs to completion.
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(responses.length, 1);
  assert.equal(responses[0].ok, false);
  assert.match(responses[0].error, /storage unavailable/);

  storageGetImpl = async () => ({});
});
