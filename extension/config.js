export const HAVEN_BASE_URL = "http://127.0.0.1:8765";

const API_PREFIX = "/api/v1";

// Single registry background.js routes on. Adding a new backend capability
// (e.g. /store_memory, /stats) is one line here plus one HavenClient method
// in content/controller.js — background.js doesn't change shape. GET
// endpoints that take a payload (HAVEN_SEARCH_MEMORIES) have it serialised
// as a query string instead of a JSON body; see background.js.
export const ENDPOINTS = {
  HAVEN_HEALTH_CHECK: { method: "GET", path: `${API_PREFIX}/health` },
  HAVEN_RETRIEVE_CONTEXT: { method: "POST", path: `${API_PREFIX}/retrieve_context` },
  HAVEN_RETRIEVE_WORKING_CONTEXT: {
    method: "POST",
    path: `${API_PREFIX}/retrieve_working_context`,
  },
  // Memory Review: preview runs extraction and stops before writing;
  // commit reuses the preview's already-extracted memories (no second LLM
  // call) and persists; cancel discards a preview the user dismissed
  // without saving. See content/controller.js's onRememberClick.
  HAVEN_MEMORY_PREVIEW: { method: "POST", path: `${API_PREFIX}/memory/preview` },
  HAVEN_MEMORY_COMMIT: { method: "POST", path: `${API_PREFIX}/memory/commit` },
  HAVEN_MEMORY_CANCEL: { method: "POST", path: `${API_PREFIX}/memory/cancel` },
  HAVEN_SEARCH_MEMORIES: { method: "GET", path: `${API_PREFIX}/dashboard/inspect` },
};

// Every request background.js makes is aborted if the Haven server hasn't
// responded within this window, so a hung/unreachable server surfaces as a
// clear "timed out" error instead of leaving the UI stuck on "Loading…"
// forever. Generous because /memory/preview runs several synchronous LLM
// calls in sequence (Extractor -> Classifier -> ImportanceScorer ->
// CanonicalMatcher) and a fresh server process may still be warming up
// (loading the vault/concept index from disk), but short enough that a
// truly dead server is reported well within a user's patience.
export const REQUEST_TIMEOUT_MS = 60000;

// Settings persisted via chrome.storage.local, read by background.js (server
// URL) and content/controller.js (autoPreview/autoRemember). One shared
// shape so the popup, the content script, and background.js never disagree
// about a key name or a default.
export const SETTINGS_STORAGE_KEY = "havenSettings";

export const DEFAULT_SETTINGS = {
  serverUrl: HAVEN_BASE_URL,
  authUser: "",
  authPassword: "",
  autoPreview: true,
  autoRemember: false,
};

// Hostnames manifest.json's *required* host_permissions already cover --
// background.js's fetch() reaches these without ever needing Chrome's
// runtime permission-request flow (see originPatternFor/optional_host_permissions
// below), so Save Settings/Test Connection skip that dance entirely for them.
const ALWAYS_PERMITTED_HOSTNAMES = new Set(["localhost", "127.0.0.1"]);

export function isAlwaysPermittedHost(hostname) {
  return ALWAYS_PERMITTED_HOSTNAMES.has(hostname);
}

// Chrome match patterns have no port component -- a pattern matches its host
// on any port (see https://developer.chrome.com/docs/extensions/develop/concepts/match-patterns).
// manifest.json's optional_host_permissions ("http://*/*", "https://*/*") is
// declared broad on purpose so chrome.permissions.request() can ask for just
// this narrower per-host pattern at runtime -- the user is prompted for the
// one server they configured, not "all websites", and a brand-new remote
// deployment works the moment they grant it, no extension rebuild needed.
export function originPatternFor(serverUrl) {
  const url = new URL(serverUrl);
  return `${url.protocol}//${url.hostname}/*`;
}

// Builds the `Authorization: Basic ...` header value for servers deployed
// behind HTTP Basic Auth (see deploy/alibaba-cloud/nginx.haven.conf) --
// undefined when no username is configured, so callers can add the header
// conditionally rather than always sending an empty-credential one.
export function buildAuthHeader(authUser, authPassword) {
  if (!authUser) return undefined;
  return `Basic ${btoa(`${authUser}:${authPassword ?? ""}`)}`;
}

// Shared by content/controller.js (dynamic import -- classic content scripts
// can't use a top-level `import`) and popup/popup.js (static import) so
// both read the same merged-with-defaults settings shape from
// chrome.storage.local.
export async function loadSettings() {
  const stored = await chrome.storage.local.get(SETTINGS_STORAGE_KEY);
  return { ...DEFAULT_SETTINGS, ...(stored[SETTINGS_STORAGE_KEY] || {}) };
}

// Presentation-only labels for MemoryType values, used by content/controller.js's
// Memory Review dialog (save summary + type <select>) and popup/popup.js's
// search results. Singular form; pluralized with a trailing "s" by
// controller.js's pluralizeType() since every value here pluralizes regularly.
export const MEMORY_TYPE_LABELS = {
  fact: "Fact",
  preference: "Preference",
  decision: "Decision",
  goal: "Goal",
  project: "Project",
};
