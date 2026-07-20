// A minimal, deterministic stand-in for the real Haven backend
// (obsidian/server/main.py), implementing exactly the endpoints
// background.js's HavenClient calls (see extension/config.js's ENDPOINTS).
//
// Why a mock instead of the real server: the real Haven backend runs actual
// LLM extraction/classification, which is slow, non-deterministic, and
// requires provider credentials -- none of which this suite needs to answer
// its actual question ("does the extension's client-side lifecycle wiring
// fire Auto Remember at the right moment, with the right payload, and
// render the right UI"). This server returns whatever a test configures it
// to return and records every request it receives so a test can assert on
// call counts/payloads -- see e.g. remember.spec.js's "does not repeatedly
// trigger" assertion, which counts /memory/preview calls rather than
// inspecting DOM timing.
//
// Listens on 127.0.0.1:8765, the extension's DEFAULT_SETTINGS.serverUrl
// (config.js) and an "always permitted" host (config.js's
// isAlwaysPermittedHost) -- so no chrome.permissions.request() prompt ever
// has to be handled by these tests.

import http from "node:http";

const PORT = 8765;
const HOST = "127.0.0.1";

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      if (!chunks.length) return resolve(undefined);
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString("utf8")));
      } catch (error) {
        reject(error);
      }
    });
    req.on("error", reject);
  });
}

function sendJson(res, status, body) {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    "Content-Type": "application/json",
    "Content-Length": Buffer.byteLength(payload),
  });
  res.end(payload);
}

// Default fixtures: reasonable, self-consistent responses so a spec that
// only cares about e.g. bootstrap can start the server with zero config and
// still get a working "Use Haven" retrieval. Individual specs override just
// the pieces they care about via startMockServer(overrides).
function defaultFixtures() {
  let reviewCounter = 0;
  return {
    workingContext: {
      available: true,
      contexts: [
        {
          title: "Haven E2E Project",
          status: "active",
          memory_count: 2,
          current_goal: "Ship the Playwright E2E suite",
          current_focus: "Auto Remember lifecycle",
          recent_decisions: ["Use a mock Haven server instead of the real backend"],
          pending_tasks: [],
          open_questions: [],
        },
      ],
      structured_prompt:
        "<System>Working context for Haven E2E Project.</System>\n<UserRequest>{{QUERY}}</UserRequest>",
    },
    // Called with the request body; return value is sent as `data`. A
    // function (not a static object) so a spec can vary the response based
    // on what's actually being remembered.
    memoryPreview: (body) => ({
      status: "new",
      review_id: `review-${++reviewCounter}`,
      items: [
        {
          fact_index: 0,
          text: "New knowledge captured during the E2E run.",
          memory_type: "fact",
          evidence: (body?.conversation ?? []).at(-1)?.content?.slice(0, 80) ?? "",
        },
      ],
    }),
    memoryCommit: () => ({
      review_summary: "Saved 1 memory.",
      trace_id: "trace-e2e",
      decision_counts: { new: 1 },
    }),
    queryRewrite: (body) => ({
      original: body?.query ?? "",
      changed: false,
      rewritten: body?.query ?? "",
    }),
  };
}

// Starts the mock server and resolves once it's actually listening --
// callers must await this before configuring the extension to point at it,
// otherwise the first "Use Haven" click could race a server that isn't up
// yet.
export function startMockServer(overrides = {}) {
  const fixtures = { ...defaultFixtures(), ...overrides };

  // Every request this server has handled, in order -- specs use this to
  // assert not just "did the dialog appear" but "did Auto Remember fire
  // exactly once", which DOM state alone can't distinguish from "fired
  // twice but the second dialog looks the same".
  const requests = [];

  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, `http://${HOST}:${PORT}`);
    let body;
    try {
      body = await readBody(req);
    } catch (_error) {
      sendJson(res, 400, { detail: "Invalid JSON body." });
      return;
    }
    requests.push({ method: req.method, path: url.pathname, body, query: Object.fromEntries(url.searchParams) });

    switch (`${req.method} ${url.pathname}`) {
      case "GET /api/v1/health":
        sendJson(res, 200, { status: "ok" });
        return;
      case "POST /api/v1/retrieve_working_context": {
        const data = { ...fixtures.workingContext };
        if (typeof data.structured_prompt === "string") {
          data.structured_prompt = data.structured_prompt.replace("{{QUERY}}", body?.query ?? "");
        }
        sendJson(res, 200, data);
        return;
      }
      case "POST /api/v1/retrieve_context":
        sendJson(res, 200, { context: null });
        return;
      case "POST /api/v1/memory/preview":
        sendJson(res, 200, fixtures.memoryPreview(body));
        return;
      case "POST /api/v1/memory/commit":
        sendJson(res, 200, fixtures.memoryCommit(body));
        return;
      case "POST /api/v1/memory/cancel":
        sendJson(res, 200, { ok: true });
        return;
      case "POST /api/v1/query/rewrite":
        sendJson(res, 200, fixtures.queryRewrite(body));
        return;
      case "GET /api/v1/dashboard/inspect":
        sendJson(res, 200, { trace: { candidates: [] } });
        return;
      default:
        sendJson(res, 404, { detail: `No mock handler for ${req.method} ${url.pathname}` });
    }
  });

  return new Promise((resolve, reject) => {
    server.on("error", reject);
    server.listen(PORT, HOST, () => {
      resolve({
        url: `http://${HOST}:${PORT}`,
        requests,
        requestsTo(pathname) {
          return requests.filter((r) => r.path === pathname);
        },
        // The full call sequence, in order, across every endpoint -- lets a
        // spec assert on the *lifecycle* (e.g. retrieve_working_context,
        // then memory/preview, then memory/commit, in that order) rather
        // than only on each endpoint's isolated call count. See
        // remember.spec.js's final assertion.
        paths() {
          return requests.map((r) => r.path);
        },
        close() {
          return new Promise((res) => server.close(() => res()));
        },
      });
    });
  });
}
