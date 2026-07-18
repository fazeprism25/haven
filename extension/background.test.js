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
let permissionsContainsImpl = async () => true;
globalThis.chrome = {
  runtime: {
    onMessage: {
      addListener(fn) {
        registeredListener = fn;
      },
    },
  },
  storage: { local: { get: (...args) => storageGetImpl(...args) } },
  permissions: { contains: (...args) => permissionsContainsImpl(...args) },
};

const { errorMessageFor } = await import("./background.js");

async function invoke(message) {
  const responses = [];
  registeredListener(message, {}, (response) => responses.push(response));
  await new Promise((resolve) => setTimeout(resolve, 0));
  return responses[0];
}

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

test("a remote host without a granted permission returns a clear, actionable error instead of attempting fetch", async () => {
  // Reproduces the real Alibaba Cloud bug: manifest.json's host_permissions
  // only ever covers localhost/127.0.0.1, so a configured remote serverUrl
  // needs a runtime-granted optional permission (see popup.js) before
  // background.js can fetch it at all -- without this check the failure
  // would instead surface as an opaque browser "Failed to fetch".
  storageGetImpl = async () => ({
    havenSettings: { serverUrl: "http://example.com:8765" },
  });
  permissionsContainsImpl = async () => false;

  const response = await invoke({ type: "HAVEN_HEALTH_CHECK" });

  assert.equal(response.ok, false);
  assert.match(response.error, /doesn't have permission/);
  assert.match(response.error, /example\.com/);

  storageGetImpl = async () => ({});
  permissionsContainsImpl = async () => true;
});

test("a configured username adds a Basic Auth Authorization header to the request", async () => {
  // deploy/alibaba-cloud/nginx.haven.conf puts every route except /health
  // behind HTTP Basic Auth -- the extension must send credentials itself,
  // since fetch() never triggers the browser's native Basic Auth prompt for
  // a service-worker request.
  storageGetImpl = async () => ({
    havenSettings: { serverUrl: "http://127.0.0.1:8765", authUser: "haven", authPassword: "secret" },
  });
  let capturedHeaders;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (_url, init) => {
    capturedHeaders = init.headers;
    return { ok: true, status: 200, json: async () => ({ status: "ok" }) };
  };

  const response = await invoke({ type: "HAVEN_HEALTH_CHECK" });

  assert.equal(response.ok, true);
  assert.equal(capturedHeaders.Authorization, `Basic ${btoa("haven:secret")}`);

  globalThis.fetch = originalFetch;
  storageGetImpl = async () => ({});
});

test("no Authorization header is sent when no username is configured", async () => {
  storageGetImpl = async () => ({ havenSettings: { serverUrl: "http://127.0.0.1:8765" } });
  let capturedHeaders;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (_url, init) => {
    capturedHeaders = init.headers;
    return { ok: true, status: 200, json: async () => ({ status: "ok" }) };
  };

  await invoke({ type: "HAVEN_HEALTH_CHECK" });

  assert.equal(capturedHeaders, undefined);

  globalThis.fetch = originalFetch;
  storageGetImpl = async () => ({});
});

test("a connectionOverride (Test Connection probing unsaved settings) is used instead of stored settings", async () => {
  storageGetImpl = async () => {
    throw new Error("stored settings should not be read when an override is provided");
  };
  permissionsContainsImpl = async () => false;

  const response = await invoke({
    type: "HAVEN_HEALTH_CHECK",
    connectionOverride: { serverUrl: "http://staging.example.com" },
  });

  assert.equal(response.ok, false);
  assert.match(response.error, /staging\.example\.com/);

  storageGetImpl = async () => ({});
  permissionsContainsImpl = async () => true;
});

test("a request that aborts on the REQUEST_TIMEOUT_MS deadline is tagged `timeout: true`, distinct from a genuine network failure", async () => {
  // Reproduces the real pre-hackathon bug: /memory/preview's multi-call LLM
  // chain can outrun the timeout on a slow-but-alive server. Both this and
  // an actually-unreachable server produce `status: undefined`, so
  // content/controller.js needs a separate signal (this `timeout` flag) to
  // avoid telling the user "Haven server is offline" when it's really just
  // still working -- see controller.js's isTimeoutFailure().
  storageGetImpl = async () => ({});
  const originalFetch = globalThis.fetch;
  // Simulates the effect of AbortController.abort() firing (what actually
  // happens once REQUEST_TIMEOUT_MS elapses) without waiting on a real
  // 60-second timer in this test.
  globalThis.fetch = async () => {
    const error = new Error("The operation was aborted.");
    error.name = "AbortError";
    throw error;
  };

  const response = await invoke({ type: "HAVEN_MEMORY_PREVIEW", payload: { canonical_fact: "test" } });

  assert.equal(response.ok, false);
  assert.equal(response.timeout, true);
  assert.equal(response.status, undefined);

  globalThis.fetch = originalFetch;
});

test("a genuine network failure (e.g. connection refused) is not tagged `timeout`", async () => {
  storageGetImpl = async () => ({});
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => {
    throw new Error("Failed to fetch");
  };

  const response = await invoke({ type: "HAVEN_HEALTH_CHECK" });

  assert.equal(response.ok, false);
  assert.equal(response.timeout, false);
  assert.equal(response.status, undefined);

  globalThis.fetch = originalFetch;
});
