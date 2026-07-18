import {
  HAVEN_BASE_URL,
  ENDPOINTS,
  REQUEST_TIMEOUT_MS,
  SETTINGS_STORAGE_KEY,
  buildAuthHeader,
  isAlwaysPermittedHost,
  originPatternFor,
} from "./config.js";

// The server URL (and optional Basic Auth credentials) are user-configurable
// (see popup/popup.js), persisted in chrome.storage.local under
// SETTINGS_STORAGE_KEY. Read fresh on every request rather than cached, so a
// setting change takes effect on the very next call with no service-worker
// restart needed. `override` (popup.js's Test Connection button) lets a
// caller probe a serverUrl/credentials pair that hasn't been saved yet,
// without duplicating the fetch logic below.
async function resolveConnection(override) {
  if (override) {
    return {
      baseUrl: (override.serverUrl?.trim() ? override.serverUrl.trim() : HAVEN_BASE_URL).replace(/\/+$/, ""),
      authUser: override.authUser || "",
      authPassword: override.authPassword || "",
    };
  }
  const stored = await chrome.storage.local.get(SETTINGS_STORAGE_KEY);
  const settings = stored[SETTINGS_STORAGE_KEY] || {};
  const configured = settings.serverUrl;
  return {
    baseUrl: (configured && configured.trim() ? configured.trim() : HAVEN_BASE_URL).replace(/\/+$/, ""),
    authUser: settings.authUser || "",
    authPassword: settings.authPassword || "",
  };
}

// The FastAPI server's error responses are JSON bodies shaped like
// {"detail": "..."} -- `detail` is the human-readable reason (e.g. "Nothing
// worth remembering was found in that text.", or an unknown/expired
// review_id message). Prefer that over the raw response body, which is what
// showError() in content/controller.js displays verbatim to the user.
// Falls back to a plain, status-only message when the body isn't JSON or has
// no string `detail` -- a raw HTML error page or stray 502 body must never
// be shown verbatim to the user. The raw body still goes to console.error
// (truncated) so it's recoverable for debugging.
export function errorMessageFor(status, responseBody) {
  if (responseBody) {
    try {
      const detail = JSON.parse(responseBody)?.detail;
      if (typeof detail === "string" && detail) return detail;
    } catch (_parseError) {
      // Not JSON -- fall through to the raw form below.
    }
    const truncated =
      responseBody.length > 120 ? `${responseBody.slice(0, 120)}...` : responseBody;
    console.error(`Haven server error ${status}:`, truncated);
  }
  return `Haven server error (${status}). Please try again.`;
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  const endpoint = ENDPOINTS[message.type];
  if (!endpoint) return false; // not a Haven message; let other listeners handle it

  (async () => {
    // Aborted after REQUEST_TIMEOUT_MS so a hung or unreachable server
    // reports a clear timeout error instead of leaving the caller waiting
    // indefinitely (see config.js for why the window is generous).
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

    // Everything below -- including resolveConnection()'s chrome.storage read --
    // must stay inside this try. sendResponse() is the only thing standing
    // between this call and a "message port closed before a response was
    // received" error on the content-script side: an exception thrown before
    // sendResponse runs (e.g. chrome.storage.local.get rejecting while the
    // MV3 service worker is still waking from termination) would otherwise
    // leave the message channel open with nothing left to ever close it.
    try {
      const { baseUrl, authUser, authPassword } = await resolveConnection(message.connectionOverride);

      let hostname;
      try {
        hostname = new URL(baseUrl).hostname;
      } catch (_urlError) {
        sendResponse({ ok: false, error: `"${baseUrl}" isn't a valid server URL.` });
        return;
      }

      // Chrome only bypasses CORS -- and lets this fetch() reach a remote
      // server at all -- for origins covered by host_permissions (see
      // config.js's originPatternFor). manifest.json's *required*
      // host_permissions only ever cover localhost/127.0.0.1; any other
      // host needs a runtime-granted optional permission (popup.js requests
      // this from Save Settings/Test Connection, both user gestures).
      // Skipping straight to fetch() without this check would still fail,
      // but as an opaque browser-level "Failed to fetch" with no indication
      // of why -- checking first turns that into an actionable message.
      if (!isAlwaysPermittedHost(hostname)) {
        const granted = await chrome.permissions.contains({ origins: [originPatternFor(baseUrl)] });
        if (!granted) {
          sendResponse({
            ok: false,
            error: `Haven doesn't have permission to reach ${hostname} yet. Open the Haven popup and click "Test Connection" to grant access.`,
          });
          return;
        }
      }

      let url = baseUrl + endpoint.path;
      let body;
      if (endpoint.method === "GET") {
        // GET endpoints that take a payload (e.g. HAVEN_SEARCH_MEMORIES's
        // `query`) carry it as a query string, never a body.
        if (message.payload) {
          url += `?${new URLSearchParams(message.payload).toString()}`;
        }
      } else {
        body = message.payload ? JSON.stringify(message.payload) : undefined;
      }

      const headers = {};
      if (body) headers["Content-Type"] = "application/json";
      // Only sent when the user configured a username (see
      // deploy/alibaba-cloud/nginx.haven.conf's Basic Auth) -- a bare
      // localhost dev server never sees this header.
      const authHeader = buildAuthHeader(authUser, authPassword);
      if (authHeader) headers["Authorization"] = authHeader;

      const response = await fetch(url, {
        method: endpoint.method,
        headers: Object.keys(headers).length ? headers : undefined,
        body,
        signal: controller.signal,
      });
      if (!response.ok) {
        let responseBody = null;
        try {
          responseBody = await response.text();
        } catch (_readError) {
          responseBody = null;
        }
        sendResponse({
          ok: false,
          error: errorMessageFor(response.status, responseBody),
          status: response.status,
          responseBody,
        });
        return;
      }
      const data = await response.json();
      sendResponse({ ok: true, data });
    } catch (error) {
      const isTimeout = error.name === "AbortError";
      const message = isTimeout
        ? `Haven server did not respond within ${REQUEST_TIMEOUT_MS / 1000}s.`
        : error.message;
      // `timeout` lets callers (content/controller.js) tell "the request
      // timed out -- the server is probably still working" apart from a
      // genuine offline/unreachable server, which also has `status`
      // undefined but a different error string. Don't rely on the error
      // string itself for that distinction: it's not a stable contract.
      sendResponse({ ok: false, error: message, timeout: isTimeout });
    } finally {
      clearTimeout(timeoutId);
    }
  })();

  return true; // keep the message channel open for the async response
});
