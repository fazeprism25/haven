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
} from "../config.js";

function send(type, payload) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ type, payload }, (response) => {
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

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str == null ? "" : String(str);
  return div.innerHTML;
}

const els = {
  statusDot: document.getElementById("status-dot"),
  statusText: document.getElementById("status-text"),
  serverUrl: document.getElementById("server-url"),
  autoPreview: document.getElementById("auto-preview"),
  autoRemember: document.getElementById("auto-remember"),
  saveSettingsBtn: document.getElementById("save-settings"),
  settingsMessage: document.getElementById("settings-message"),
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
  els.autoPreview.checked = settings.autoPreview;
  els.autoRemember.checked = settings.autoRemember;
  await refreshStatus();
  els.searchInput.focus();
}

els.saveSettingsBtn.addEventListener("click", async () => {
  const serverUrl =
    els.serverUrl.value.trim().replace(/\/+$/, "") || DEFAULT_SETTINGS.serverUrl;
  const settings = {
    serverUrl,
    autoPreview: els.autoPreview.checked,
    autoRemember: els.autoRemember.checked,
  };

  els.saveSettingsBtn.disabled = true;
  const originalLabel = els.saveSettingsBtn.textContent;
  els.saveSettingsBtn.innerHTML = '<span class="spinner" aria-hidden="true"></span>Saving…';
  try {
    await chrome.storage.local.set({ [SETTINGS_STORAGE_KEY]: settings });
    els.serverUrl.value = serverUrl;

    els.settingsMessage.textContent = "Settings saved.";
    els.settingsMessage.hidden = false;
    setTimeout(() => {
      els.settingsMessage.hidden = true;
    }, 2500);

    await refreshStatus();
  } finally {
    els.saveSettingsBtn.disabled = false;
    els.saveSettingsBtn.textContent = originalLabel;
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
