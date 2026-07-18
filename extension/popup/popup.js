// Extension popup: settings (server URL, auto-preview, auto-remember) and a
// small memory search box. Talks to the Haven server exclusively through
// background.js's existing message-passing contract (chrome.runtime.sendMessage)
// — the same one content/controller.js uses — so there is exactly one place
// (background.js) that resolves the configured server URL and performs a
// fetch. Popup scripts support top-level ES module imports (unlike content
// scripts), so this reads the shared settings shape straight from config.js
// rather than duplicating it.

import {
  SETTINGS_STORAGE_KEY,
  DEFAULT_SETTINGS,
  loadSettings,
  MEMORY_TYPE_LABELS,
  isAlwaysPermittedHost,
  originPatternFor,
} from "../config.js";

function send(type, payload, connectionOverride) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ type, payload, connectionOverride }, (response) => {
      if (chrome.runtime.lastError) {
        console.error("Haven extension messaging error:", chrome.runtime.lastError.message);
      }
      resolve(
        chrome.runtime.lastError
          ? { ok: false, error: "Haven extension needs a page refresh." }
          : response
      );
    });
  });
}

// Single place both Save Settings and Test Connection validate a typed URL
// through -- rejecting a malformed one *before* it's persisted (chrome.storage
// keeps whatever was last saved, so a bad value here would otherwise silently
// stick until corrected) rather than only ever discovering it's broken when a
// later fetch fails.
function validateServerUrl(rawUrl) {
  let url;
  try {
    url = new URL(rawUrl);
  } catch (_urlError) {
    return {
      ok: false,
      error: "Enter a valid server URL, e.g. http://127.0.0.1:8765 or https://your-domain.com.",
    };
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    return { ok: false, error: "Server URL must start with http:// or https://." };
  }
  return { ok: true, hostname: url.hostname };
}

// Chrome only lets background.js's fetch() reach a host at all (bypassing
// CORS) if that origin is covered by a granted host permission --
// manifest.json's *required* host_permissions only ever cover
// localhost/127.0.0.1 (see background.js's isAlwaysPermittedHost check).
// Any other configured server needs a runtime-granted *optional* permission
// (manifest.json's optional_host_permissions), requested here rather than
// hardcoded, so a brand-new remote deployment works the moment the user
// grants it -- no extension rebuild/republish needed. Both callers
// (Save Settings, Test Connection) invoke this directly from a click
// handler so chrome.permissions.request()'s required user-gesture is met.
async function ensurePermission(serverUrl) {
  const validation = validateServerUrl(serverUrl);
  if (!validation.ok) return validation;
  const { hostname } = validation;
  if (isAlwaysPermittedHost(hostname)) return { ok: true };

  const pattern = originPatternFor(serverUrl);
  if (await chrome.permissions.contains({ origins: [pattern] })) return { ok: true };

  const granted = await chrome.permissions.request({ origins: [pattern] });
  return granted
    ? { ok: true }
    : {
        ok: false,
        error: `Permission denied for ${hostname} — Haven can't reach this server until you allow access.`,
      };
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str == null ? "" : String(str);
  return div.innerHTML;
}

const els = {
  statusDot: document.getElementById("status-dot"),
  statusText: document.getElementById("status-text"),
  serverUrl: document.getElementById("server-url"),
  authUser: document.getElementById("auth-user"),
  authPassword: document.getElementById("auth-password"),
  autoPreview: document.getElementById("auto-preview"),
  autoRemember: document.getElementById("auto-remember"),
  saveSettingsBtn: document.getElementById("save-settings"),
  settingsMessage: document.getElementById("settings-message"),
  testConnectionBtn: document.getElementById("test-connection"),
  testConnectionMessage: document.getElementById("test-connection-message"),
  searchForm: document.getElementById("search-form"),
  searchInput: document.getElementById("search-input"),
  searchError: document.getElementById("search-error"),
  searchResults: document.getElementById("search-results"),
};

async function refreshStatus() {
  els.statusDot.className = "status-dot";
  els.statusText.textContent = "checking…";
  const response = await send("HAVEN_HEALTH_CHECK");
  els.statusDot.className = `status-dot ${response.ok ? "connected" : "offline"}`;
  els.statusText.textContent = response.ok ? "Connected" : "Offline";
}

async function init() {
  const settings = await loadSettings();
  els.serverUrl.value = settings.serverUrl;
  els.authUser.value = settings.authUser;
  els.authPassword.value = settings.authPassword;
  els.autoPreview.checked = settings.autoPreview;
  els.autoRemember.checked = settings.autoRemember;
  await refreshStatus();
  els.searchInput.focus();
}

els.saveSettingsBtn.addEventListener("click", async () => {
  const serverUrl =
    els.serverUrl.value.trim().replace(/\/+$/, "") || DEFAULT_SETTINGS.serverUrl;

  const validation = validateServerUrl(serverUrl);
  if (!validation.ok) {
    els.settingsMessage.className = "inline-message error";
    els.settingsMessage.textContent = validation.error;
    els.settingsMessage.hidden = false;
    return; // never persist a URL the extension couldn't possibly reach
  }

  const settings = {
    serverUrl,
    authUser: els.authUser.value.trim(),
    authPassword: els.authPassword.value,
    autoPreview: els.autoPreview.checked,
    autoRemember: els.autoRemember.checked,
  };

  els.saveSettingsBtn.disabled = true;
  const originalLabel = els.saveSettingsBtn.textContent;
  els.saveSettingsBtn.innerHTML = '<span class="spinner" aria-hidden="true"></span>Saving…';
  try {
    // Requesting permission here (rather than only from Test Connection)
    // covers the common path: type a remote URL, click Save, done. Saving
    // still proceeds even if the user dismisses the prompt -- Test
    // Connection remains available to retry granting it later.
    const permission = await ensurePermission(serverUrl);

    await chrome.storage.local.set({ [SETTINGS_STORAGE_KEY]: settings });
    els.serverUrl.value = serverUrl;

    els.settingsMessage.className = `inline-message ${permission.ok ? "success" : "error"}`;
    els.settingsMessage.textContent = permission.ok
      ? "Settings saved."
      : `Settings saved, but ${permission.error}`;
    els.settingsMessage.hidden = false;
    setTimeout(() => {
      els.settingsMessage.hidden = true;
    }, permission.ok ? 2500 : 6000);

    await refreshStatus();
  } finally {
    els.saveSettingsBtn.disabled = false;
    els.saveSettingsBtn.textContent = originalLabel;
  }
});

function showTestConnectionResult(message, success) {
  els.testConnectionMessage.className = `inline-message ${success ? "success" : "error"}`;
  els.testConnectionMessage.textContent = message;
  els.testConnectionMessage.hidden = false;
}

els.testConnectionBtn.addEventListener("click", async () => {
  const serverUrl =
    els.serverUrl.value.trim().replace(/\/+$/, "") || DEFAULT_SETTINGS.serverUrl;
  const authUser = els.authUser.value.trim();
  const authPassword = els.authPassword.value;
  const connectionOverride = { serverUrl, authUser, authPassword };

  els.testConnectionBtn.disabled = true;
  const originalLabel = els.testConnectionBtn.textContent;
  els.testConnectionBtn.innerHTML = '<span class="spinner" aria-hidden="true"></span>Testing…';
  els.testConnectionMessage.hidden = true;

  try {
    const permission = await ensurePermission(serverUrl);
    if (!permission.ok) {
      showTestConnectionResult(permission.error, false);
      return;
    }

    const health = await send("HAVEN_HEALTH_CHECK", undefined, connectionOverride);
    if (!health.ok) {
      // No `status` means the request never got an HTTP response at all
      // (network error, DNS failure, connection refused, or our own
      // REQUEST_TIMEOUT_MS/permission-gate messages) -- background.js's
      // error string is already specific in every one of those cases.
      showTestConnectionResult(
        health.status === undefined
          ? health.error
          : `Server responded with an unexpected error (${health.status}).`,
        false
      );
      return;
    }

    // GET /api/v1/health is deliberately unauthenticated (see
    // deploy/alibaba-cloud/nginx.haven.conf), so a successful health check
    // alone doesn't prove Basic Auth credentials are correct -- reuse the
    // existing search endpoint (already authenticated, already used by the
    // search box below) as a lightweight authenticated probe.
    const authProbe = await send(
      "HAVEN_SEARCH_MEMORIES",
      { query: "haven-connection-test" },
      connectionOverride
    );
    if (!authProbe.ok) {
      if (authProbe.status === 401) {
        showTestConnectionResult(
          authUser
            ? "Connected, but the username/password were rejected."
            : "Connected, but this server requires a username and password.",
          false
        );
      } else if (authProbe.status !== undefined) {
        showTestConnectionResult(`Connected, but a request failed unexpectedly (${authProbe.status}).`, false);
      } else {
        showTestConnectionResult(authProbe.error, false);
      }
      return;
    }

    showTestConnectionResult(authUser ? "Connected and authenticated." : "Connected.", true);
  } finally {
    els.testConnectionBtn.disabled = false;
    els.testConnectionBtn.textContent = originalLabel;
  }
});

// Bumped on every search submission so a slow, superseded search response
// can recognise it's stale and skip rendering over a newer one.
let searchRequestId = 0;

els.searchForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = els.searchInput.value.trim();
  if (!query) return;

  const requestId = ++searchRequestId;
  const submitBtn = els.searchForm.querySelector('button[type="submit"]');
  els.searchError.hidden = true;
  els.searchResults.innerHTML =
    '<p class="empty"><span class="spinner" aria-hidden="true"></span>Searching…</p>';
  submitBtn.disabled = true;

  try {
    // Reuses the existing Retrieval Inspector endpoint
    // (GET /api/v1/dashboard/inspect?query=...) as a search — no separate
    // search logic on the backend.
    const response = await send("HAVEN_SEARCH_MEMORIES", { query });
    if (requestId !== searchRequestId) return; // a newer search has since been submitted

    if (!response.ok) {
      els.searchResults.innerHTML = "";
      els.searchError.textContent = response.error ?? "Search failed.";
      els.searchError.hidden = false;
      return;
    }

    const accepted = response.data.trace.candidates.filter((c) => c.accepted);
    renderResults(accepted);
  } finally {
    if (requestId === searchRequestId) submitBtn.disabled = false;
  }
});

function renderResults(candidates) {
  if (!candidates.length) {
    els.searchResults.innerHTML =
      '<p class="empty">No memories matched. Try different words, or check the dashboard to confirm your vault has data.</p>';
    return;
  }
  els.searchResults.innerHTML = candidates
    .map(
      (c) => `
        <div class="result">
          <div class="result-fact">${escapeHtml(c.canonical_fact)}</div>
          <div class="result-meta">
            <span class="badge">${escapeHtml(MEMORY_TYPE_LABELS[c.memory_type] ?? c.memory_type)}</span>
            <span>relevance ${c.final_score.toFixed(2)}</span>
          </div>
        </div>
      `
    )
    .join("");
}

init();
