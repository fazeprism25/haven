// Site-agnostic. No selectors, no DOM knowledge about any particular site —
// all of that lives in content/adapters/*.js. This file only knows how to:
// route to the right adapter module, mount/reposition a button + status dot
// + preview dialog, and talk to the background service worker.
//
// Statically-declared MV3 content scripts always load as classic scripts
// (there's no manifest option to mark one a module), so this file can't use
// a top-level `import`. It uses dynamic import() instead, which works from
// classic scripts, to load the adapter as a real ES module at runtime.

(async () => {
  const SITE_ADAPTERS = [
    {
      test: (hostname) => hostname === "chatgpt.com" || hostname === "chat.openai.com",
      module: "content/adapters/chatgpt.js",
    },
    // { test: (hostname) => hostname === "claude.ai", module: "content/adapters/claude.js" },
  ];

  const site = SITE_ADAPTERS.find((entry) => entry.test(location.hostname));
  if (!site) return;

  let adapter;
  try {
    ({ adapter } = await import(chrome.runtime.getURL(site.module)));
  } catch (error) {
    console.error("Haven: failed to load site adapter", error);
    return;
  }

  let HAVEN_BASE_URL,
    SETTINGS_STORAGE_KEY,
    DEFAULT_SETTINGS,
    REQUEST_TIMEOUT_MS,
    loadSettings,
    MEMORY_TYPE_LABELS,
    resolveQuery,
    createRememberVisibility,
    isEligibleForRewrite,
    isSuggestionStillRelevant,
    isAcceptedRewriteEcho,
    createRewriteSuppression;
  try {
    ({
      HAVEN_BASE_URL,
      SETTINGS_STORAGE_KEY,
      DEFAULT_SETTINGS,
      REQUEST_TIMEOUT_MS,
      loadSettings,
      MEMORY_TYPE_LABELS,
    } = await import(chrome.runtime.getURL("config.js")));
    ({ resolveQuery } = await import(
      chrome.runtime.getURL("content/resolve-query.js")
    ));
    ({ createRememberVisibility } = await import(
      chrome.runtime.getURL("content/remember-visibility.js")
    ));
    ({
      isEligibleForRewrite,
      isSuggestionStillRelevant,
      isAcceptedRewriteEcho,
      createRewriteSuppression,
    } = await import(chrome.runtime.getURL("content/rewrite-suggestion.js")));
  } catch (error) {
    console.error("Haven: a content-script module failed to load", error);
    return;
  }
  const rememberVisibility = createRememberVisibility();
  // Armed by "Use Haven" right before it programmatically mutates the
  // compose box (see onButtonClick) so the native "input" event that
  // mutation fires doesn't get treated as user-authored input by
  // onComposeInput below -- see createRewriteSuppression's own comment.
  const rewriteSuppression = createRewriteSuppression();

  // Debounce window between the user's last keystroke and the rewrite
  // request firing -- long enough that a normal typing cadence never fires
  // one mid-word, short enough that the suggestion still feels responsive
  // once they pause.
  const REWRITE_DEBOUNCE_MS = 700;

  // Bounds the full content-script -> background -> server round trip.
  // Strictly larger than background.js's own REQUEST_TIMEOUT_MS (which
  // only bounds the inner fetch()), so a normal server timeout always
  // wins and reports its specific "server did not respond" message. This
  // is a last-resort backstop for the outer chrome.runtime.sendMessage
  // callback never firing at all -- e.g. the service worker was killed or
  // the extension context invalidated mid-request -- which otherwise has
  // no timeout of its own and would leave the awaiting button spinning
  // and disabled forever (see setLoading/setRememberLoading callers).
  const SEND_MESSAGE_TIMEOUT_MS = REQUEST_TIMEOUT_MS + 5000;

  const HavenClient = {
    send(type, payload) {
      return new Promise((resolve) => {
        let settled = false;
        const timer = setTimeout(() => {
          if (settled) return;
          settled = true;
          resolve({ ok: false, error: "Haven extension messaging timed out." });
        }, SEND_MESSAGE_TIMEOUT_MS);
        try {
          chrome.runtime.sendMessage({ type, payload }, (response) => {
            if (settled) return;
            settled = true;
            clearTimeout(timer);
            if (chrome.runtime.lastError) {
              console.error("Haven extension messaging error:", chrome.runtime.lastError.message);
            }
            resolve(
              chrome.runtime.lastError
                ? { ok: false, error: "Haven extension needs a page refresh." }
                : response
            );
          });
        } catch (sendError) {
          // chrome.runtime.sendMessage throws synchronously (rather than
          // going through the callback's lastError) when this content
          // script's extension context has been invalidated -- e.g. the
          // extension was reloaded from chrome://extensions after this tab
          // was last refreshed, so this still-running old content-script
          // instance is talking to a service worker that no longer exists.
          console.error("Haven extension messaging error (context invalidated):", sendError);
          if (!settled) {
            settled = true;
            clearTimeout(timer);
            resolve({ ok: false, error: "Haven extension needs a page refresh." });
          }
        }
      });
    },
    checkHealth() {
      return this.send("HAVEN_HEALTH_CHECK");
    },
    retrieveContext(query) {
      return this.send("HAVEN_RETRIEVE_CONTEXT", { query });
    },
    retrieveWorkingContext(query) {
      return this.send("HAVEN_RETRIEVE_WORKING_CONTEXT", { query });
    },
    previewMemory(payload) {
      return this.send("HAVEN_MEMORY_PREVIEW", payload);
    },
    commitMemory(payload) {
      return this.send("HAVEN_MEMORY_COMMIT", payload);
    },
    cancelMemory(reviewId) {
      return this.send("HAVEN_MEMORY_CANCEL", { review_id: reviewId });
    },
    rewriteQuery(query) {
      return this.send("HAVEN_QUERY_REWRITE", { query });
    },
  };

  // Auto-preview/auto-remember, set from popup/popup.js via
  // chrome.storage.local — see loadSettings() (config.js)/the onChanged
  // listener below.
  let settings = { ...DEFAULT_SETTINGS };

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes[SETTINGS_STORAGE_KEY]) {
      settings = { ...DEFAULT_SETTINGS, ...changes[SETTINGS_STORAGE_KEY].newValue };
    }
  });

  const state = {
    composeBox: null,
    uiComposeBox: null, // the compose box mount()/buildUI() last ran for -- see sync()'s comment
    container: null,
    statusDot: null,
    statusText: null,
    button: null,
    rememberButton: null,
    errorEl: null,
    errorTimer: null,
    statusTimer: null,
    connected: null, // null = unknown/checking, true = connected, false = offline
    rewriteCard: null,
    rewriteTextEl: null,
    rewriteDebounceTimer: null,
    pendingRewrite: null, // the suggested text "Use Rewrite" would insert, or null when no suggestion is showing
    lastAcceptedRewrite: null, // the text most recently inserted by "Use Rewrite", or null -- see isAcceptedRewriteEcho
  };

  function buildUI() {
    const container = document.createElement("div");
    container.className = "haven-container";

    const statusDot = document.createElement("span");
    statusDot.className = "haven-status-dot haven-status-idle";

    const statusText = document.createElement("span");
    statusText.className = "haven-status-text";

    const button = document.createElement("button");
    button.type = "button";
    button.className = "haven-button";
    button.textContent = "Use Haven";
    button.addEventListener("click", onButtonClick);

    const rememberButton = document.createElement("button");
    rememberButton.type = "button";
    rememberButton.className = "haven-button";
    rememberButton.textContent = "Remember";
    rememberButton.hidden = true; // shown once a context has been inserted
    rememberButton.addEventListener("click", onRememberClick);

    const errorEl = document.createElement("span");
    errorEl.className = "haven-message";
    errorEl.hidden = true;
    // Screen readers announce every text change here (success/error/save
    // summary all funnel through this one element) without needing a
    // dedicated live region per message type.
    errorEl.setAttribute("role", "status");
    errorEl.setAttribute("aria-live", "polite");

    container.append(statusDot, statusText, button, rememberButton, errorEl);
    document.body.append(container);

    state.container = container;
    state.statusDot = statusDot;
    state.statusText = statusText;
    state.button = button;
    state.rememberButton = rememberButton;
    state.errorEl = errorEl;
  }

  // Query Rewrite Assistant's suggestion card -- a separate floating
  // element from state.container (which sits above the compose box) since
  // this one is anchored below it (see positionRewriteCard). Built lazily,
  // the first time a suggestion actually has something to show, rather
  // than unconditionally in buildUI() above.
  function buildRewriteSuggestionUI() {
    const card = document.createElement("div");
    card.className = "haven-rewrite-card";
    card.hidden = true;

    const heading = document.createElement("div");
    heading.className = "haven-rewrite-heading";
    heading.textContent = "✨ Haven suggests a better retrieval query";

    const textEl = document.createElement("p");
    textEl.className = "haven-rewrite-text";

    const useButton = document.createElement("button");
    useButton.type = "button";
    useButton.className = "haven-rewrite-use";
    useButton.textContent = "Use Rewrite";
    useButton.addEventListener("click", onUseRewriteClick);

    const dismissButton = document.createElement("button");
    dismissButton.type = "button";
    dismissButton.className = "haven-rewrite-dismiss";
    dismissButton.textContent = "Dismiss";
    dismissButton.addEventListener("click", () => hideRewriteSuggestion());

    const actions = document.createElement("div");
    actions.className = "haven-rewrite-actions";
    actions.append(useButton, dismissButton);

    card.append(heading, textEl, actions);
    document.body.append(card);

    state.rewriteCard = card;
    state.rewriteTextEl = textEl;
  }

  function positionRewriteCard() {
    if (!state.composeBox || !state.rewriteCard) return;
    const rect = adapter.getAnchorForButton(state.composeBox).getBoundingClientRect();
    state.rewriteCard.style.top = `${window.scrollY + rect.bottom + 8}px`;
    state.rewriteCard.style.left = `${window.scrollX + rect.left}px`;
  }

  function showRewriteSuggestion(original, rewritten) {
    if (!state.rewriteCard) buildRewriteSuggestionUI();
    state.pendingRewrite = rewritten;
    state.rewriteTextEl.textContent = rewritten;
    state.rewriteCard.hidden = false;
    positionRewriteCard();
  }

  function hideRewriteSuggestion() {
    clearTimeout(state.rewriteDebounceTimer);
    state.pendingRewrite = null;
    if (state.rewriteCard) state.rewriteCard.hidden = true;
  }

  function onUseRewriteClick() {
    const composeBox = state.composeBox;
    const rewritten = state.pendingRewrite;
    hideRewriteSuggestion();
    if (!composeBox || !rewritten) return;
    // setComposeText's execCommand("insertText", ...) fires a native "input"
    // event for the text it's about to insert -- record it here, before that
    // event reaches onComposeInput, so that handler can recognize its own
    // echo and not immediately re-arm a rewrite check against it.
    state.lastAcceptedRewrite = rewritten.trim();
    adapter.setComposeText(composeBox, rewritten);
  }

  async function requestRewriteSuggestion(query) {
    const response = await HavenClient.rewriteQuery(query);
    const currentText = state.composeBox ? adapter.getComposeText(state.composeBox) : "";
    if (!isSuggestionStillRelevant(response, currentText)) return;
    showRewriteSuggestion(response.data.original, response.data.rewritten);
  }

  // Fires on every native `input` event from the compose box (see mount()'s
  // listener wiring below). Debounced here -- rather than relying on the
  // MutationObserver/sync() debounce elsewhere in this file, which is
  // shared by unrelated DOM churn and can be starved for long stretches
  // (see updateRememberVisibility's comment) -- so a rewrite request only
  // ever fires after the user actually pauses typing.
  function onComposeInput() {
    clearTimeout(state.rewriteDebounceTimer);
    const composeBox = state.composeBox;
    if (!composeBox) return;
    // Swallow every native "input" event a bootstrap mutation (e.g. "Use
    // Haven" injecting the structured Working Context prompt) fires -- there
    // can be more than one, see createRewriteSuppression's comment -- before
    // any of the normal eligibility checks below, since none of them carry
    // anything the user actually typed.
    if (rewriteSuppression.isSuppressed()) {
      hideRewriteSuggestion();
      return;
    }
    const text = adapter.getComposeText(composeBox);
    // A meaningful manual edit permanently clears the echo guard (until the
    // next "Use Rewrite") -- any text other than the exact accepted rewrite
    // means the user has typed something, so normal eligibility checks
    // resume from here on.
    if (isAcceptedRewriteEcho(text, state.lastAcceptedRewrite)) return;
    state.lastAcceptedRewrite = null;
    if (!isEligibleForRewrite(text)) {
      hideRewriteSuggestion();
      return;
    }
    const armedOnComposeBox = composeBox;
    state.rewriteDebounceTimer = setTimeout(() => {
      // The debounce can outlive the compose box it was armed on if
      // ChatGPT swapped the DOM node in the intervening 700ms -- drop the
      // stale request rather than send it against a node that may no
      // longer be the live one.
      if (armedOnComposeBox !== state.composeBox || !armedOnComposeBox.isConnected) return;
      requestRewriteSuggestion(text.trim());
    }, REWRITE_DEBOUNCE_MS);
  }

  // Six states: idle (resting/connected -- labeled "Connected" below, same
  // as popup.html's #status-text, since this is a connectivity state, not
  // an activity state), loading (a request is in flight), retrieved
  // (context fetch just succeeded), saved (a memory was just remembered),
  // error (the last action failed), offline (server unreachable).
  // retrieved/saved/error are transient — they auto-revert to idle (or
  // offline, if connectivity dropped) after a few seconds, the same pattern
  // showMessage() already uses for its own timer.
  const STATUS_LABELS = {
    idle: "Haven: connected",
    loading: "Haven: loading…",
    retrieved: "Haven: context retrieved",
    saved: "Haven: memory saved",
    error: "Haven: error",
    offline: "Haven: offline",
  };
  const TRANSIENT_STATUSES = new Set(["retrieved", "saved", "error"]);

  // Short always-visible text next to the dot -- the idle/offline dot colors
  // used to both be desaturated greys that were hard to tell apart at a
  // glance (see haven-status-idle in haven.css, now green to match
  // popup.html's "connected" dot), so the text label is the primary signal.
  const STATUS_TEXT_LABELS = {
    idle: "Connected",
    loading: "Loading…",
    retrieved: "Retrieved",
    saved: "Saved",
    error: "Error",
    offline: "Offline",
  };

  function setStatus(status) {
    clearTimeout(state.statusTimer);
    state.statusDot.className = `haven-status-dot haven-status-${status}`;
    state.statusDot.title = STATUS_LABELS[status] ?? status;
    state.statusText.textContent = STATUS_TEXT_LABELS[status] ?? status;
    if (status === "offline") state.connected = false;
    else if (status !== "loading") state.connected = true;

    if (TRANSIENT_STATUSES.has(status)) {
      state.statusTimer = setTimeout(() => {
        setStatus(state.connected === false ? "offline" : "idle");
      }, 3000);
    }
  }

  // Distinguishes "the Haven server responded, but with an error" (a real
  // HTTP status, e.g. a 422/404 the server chose to send) from "the request
  // never got a response at all" (network error, timeout, unreachable
  // server, or a chrome.runtime messaging failure) -- see background.js's
  // two failure branches: only the latter omits `status` from the message.
  // Callers use this to decide between a transient "error" status (server
  // is fine, this one call failed) and "offline" (stop trusting the
  // connection until refreshHealth() confirms it's back).
  function isConnectivityFailure(response) {
    return !response.ok && response.status === undefined;
  }

  // A slow-but-alive server (e.g. /memory/preview's multi-call LLM chain
  // outrunning REQUEST_TIMEOUT_MS) is also a connectivity failure by the
  // check above -- background.js's fetch() never got a response either.
  // This distinguishes that case (flagged via `timeout`, set on the
  // AbortError branch of background.js's catch) so callers can say "still
  // working, try again" instead of the misleading "server is offline".
  function isTimeoutFailure(response) {
    return !response.ok && response.timeout === true;
  }

  async function refreshHealth() {
    setStatus("loading");
    const response = await HavenClient.checkHealth();
    setStatus(response.ok ? "idle" : "offline");
    return response.ok;
  }

  // Renders a small spinner + label in place of the button's plain text
  // while a request is in flight (idempotent no-op when already idle).
  function renderBusyLabel(button, label) {
    button.replaceChildren();
    const spinner = document.createElement("span");
    spinner.className = "haven-spinner";
    spinner.setAttribute("aria-hidden", "true");
    button.append(spinner, document.createTextNode(label));
  }

  // "Use Haven" and "Remember" are two independent async flows that share
  // the same floating widget -- disabling only the button that was clicked
  // would still let the other one fire a second, overlapping request. Each
  // setter here also disables the *other* button for the duration, so
  // onButtonClick/onRememberClick's own disabled-check guards (see below)
  // catch either direction of overlap.
  function setLoading(isLoading) {
    state.button.disabled = isLoading;
    if (isLoading) renderBusyLabel(state.button, "Loading…");
    else state.button.textContent = "Use Haven";
    state.rememberButton.disabled = isLoading;
  }

  function setRememberLoading(isLoading, label = "Remembering…") {
    state.rememberButton.disabled = isLoading;
    if (isLoading) renderBusyLabel(state.rememberButton, label);
    else state.rememberButton.textContent = "Remember";
    state.button.disabled = isLoading;
  }

  function syncRememberButtonVisibility() {
    state.rememberButton.hidden = !rememberVisibility.isVisible();
  }

  function showMessage(message, isError) {
    state.errorEl.textContent = message;
    state.errorEl.classList.toggle("haven-error", isError);
    state.errorEl.classList.toggle("haven-success", !isError);
    state.errorEl.classList.remove("haven-save-summary-active");
    state.errorEl.hidden = false;
    clearTimeout(state.errorTimer);
    state.errorTimer = setTimeout(() => {
      state.errorEl.hidden = true;
    }, 4000);
  }

  function showError(message) {
    showMessage(message, true);
  }

  function showSuccess(message) {
    showMessage(message, false);
  }

  function clearError() {
    state.errorEl.hidden = true;
    clearTimeout(state.errorTimer);
  }

  function showPreviewDialog(contextText) {
    return new Promise((resolve) => {
      const overlay = document.createElement("div");
      overlay.className = "haven-dialog-overlay";

      const dialog = document.createElement("div");
      dialog.className = "haven-dialog";

      const heading = document.createElement("div");
      heading.className = "haven-dialog-heading";
      heading.textContent = "Context";

      const body = document.createElement("pre");
      body.className = "haven-dialog-body";
      body.textContent = contextText;

      const cancelButton = document.createElement("button");
      cancelButton.type = "button";
      cancelButton.className = "haven-dialog-cancel";
      cancelButton.textContent = "Cancel";

      const insertButton = document.createElement("button");
      insertButton.type = "button";
      insertButton.className = "haven-dialog-insert";
      insertButton.textContent = "Insert";

      const actions = document.createElement("div");
      actions.className = "haven-dialog-actions";
      actions.append(cancelButton, insertButton);

      dialog.append(heading, body, actions);
      overlay.append(dialog);
      document.body.append(overlay);

      function close(result) {
        document.removeEventListener("keydown", onKeyDown);
        overlay.remove();
        resolve(result);
      }

      function onKeyDown(event) {
        if (event.key === "Escape") close(false);
      }

      cancelButton.addEventListener("click", () => close(false));
      insertButton.addEventListener("click", () => close(true));
      overlay.addEventListener("click", (event) => {
        if (event.target === overlay) close(false);
      });
      document.addEventListener("keydown", onKeyDown);
    });
  }

  const WORKING_CONTEXT_STATUS_LABELS = {
    active: "Active",
    decided: "Decided",
    reference: "Reference",
  };

  // Working Context Preview: replaces the flat-text preview with one card
  // per WorkingContext (title/status/goal/focus/decisions/tasks), each
  // collapsible via its header. Built with textContent throughout (never
  // innerHTML) since every field is user-authored memory text landing in a
  // host page's DOM. Same Cancel/Insert contract as showPreviewDialog: the
  // returned promise resolves to whether Insert was clicked.
  function showWorkingContextDialog(contexts) {
    return new Promise((resolve) => {
      const overlay = document.createElement("div");
      overlay.className = "haven-dialog-overlay";

      const dialog = document.createElement("div");
      dialog.className = "haven-dialog haven-dialog-large";

      const heading = document.createElement("div");
      heading.className = "haven-dialog-heading";
      heading.textContent = "Working context";

      const totalMemories = contexts.reduce((sum, ctx) => sum + ctx.memory_count, 0);
      const summary = document.createElement("div");
      summary.className = "haven-dialog-summary";
      summary.textContent = `${totalMemories} memor${totalMemories === 1 ? "y" : "ies"} retrieved across ${contexts.length} context${contexts.length === 1 ? "" : "s"}`;

      const list = document.createElement("div");
      list.className = "haven-context-list";
      for (const context of contexts) {
        list.append(buildContextCard(context));
      }

      const cancelButton = document.createElement("button");
      cancelButton.type = "button";
      cancelButton.className = "haven-dialog-cancel";
      cancelButton.textContent = "Cancel";

      const insertButton = document.createElement("button");
      insertButton.type = "button";
      insertButton.className = "haven-dialog-insert";
      insertButton.textContent = "Insert";

      const actions = document.createElement("div");
      actions.className = "haven-dialog-actions";
      actions.append(cancelButton, insertButton);

      dialog.append(heading, summary, list, actions);
      overlay.append(dialog);
      document.body.append(overlay);

      function close(result) {
        document.removeEventListener("keydown", onKeyDown);
        overlay.remove();
        resolve(result);
      }

      function onKeyDown(event) {
        if (event.key === "Escape") close(false);
      }

      cancelButton.addEventListener("click", () => close(false));
      insertButton.addEventListener("click", () => {
        close(true);
      });
      overlay.addEventListener("click", (event) => {
        if (event.target === overlay) close(false);
      });
      document.addEventListener("keydown", onKeyDown);
    });
  }

  function buildContextCard(context) {
    const card = document.createElement("div");
    card.className = "haven-context-card";

    const header = document.createElement("button");
    header.type = "button";
    header.className = "haven-context-header";
    header.addEventListener("click", () => {
      card.classList.toggle("haven-context-collapsed");
    });

    const title = document.createElement("span");
    title.className = "haven-context-title";
    title.textContent = context.title;

    const status = document.createElement("span");
    status.className = `haven-context-status haven-context-status-${context.status}`;
    status.textContent = WORKING_CONTEXT_STATUS_LABELS[context.status] ?? context.status;

    const count = document.createElement("span");
    count.className = "haven-context-count";
    count.textContent = `${context.memory_count} memor${context.memory_count === 1 ? "y" : "ies"}`;

    const toggle = document.createElement("span");
    toggle.className = "haven-context-toggle";
    toggle.textContent = "▾"; // ▾, rotated via CSS when collapsed

    header.append(title, status, count, toggle);

    const body = document.createElement("div");
    body.className = "haven-context-body";
    if (context.current_goal) body.append(buildMemoryItem("Goal", context.current_goal));
    if (context.current_focus) body.append(buildMemoryItem("Focus", context.current_focus));
    appendMemoryItems(body, "Decision", context.recent_decisions);
    appendMemoryItems(body, "Task", context.pending_tasks);
    appendMemoryItems(body, "Open question", context.open_questions);
    if (!body.childElementCount) {
      const empty = document.createElement("p");
      empty.className = "haven-context-empty";
      empty.textContent = "Nothing to resume here yet.";
      body.append(empty);
    }

    card.append(header, body);
    return card;
  }

  // One retrieved memory, shown as a type badge + a 2-line preview
  // (haven-memory-text's CSS line-clamp) so the user can tell what it's
  // about without opening it. Clicking toggles this single item's own
  // expanded state via haven-memory-expanded -- independent of every
  // other item and of the context card's own collapse/expand.
  function buildMemoryItem(typeLabel, text) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "haven-memory-item";

    const badge = document.createElement("span");
    badge.className = `haven-memory-type haven-memory-type-${typeLabel.toLowerCase().replace(/\s+/g, "-")}`;
    badge.textContent = typeLabel;

    const textEl = document.createElement("span");
    textEl.className = "haven-memory-text";
    textEl.textContent = text;

    const chevron = document.createElement("span");
    chevron.className = "haven-memory-chevron";
    chevron.textContent = "▾";

    item.append(badge, textEl, chevron);
    item.addEventListener("click", () => {
      item.classList.toggle("haven-memory-expanded");
    });
    return item;
  }

  function appendMemoryItems(container, typeLabel, values) {
    if (!values || !values.length) return;
    for (const value of values) {
      container.append(buildMemoryItem(typeLabel, value));
    }
  }

  // Memory Review dialog (Remember -> Review -> Save). Deliberately flatter
  // than showWorkingContextDialog above -- one flow, no collapsible
  // sections, no status badges. Each card exposes exactly two editable
  // fields (text, memory type) plus a read-only "Detected from: ..."
  // evidence line; a card's fact_index (null for a user-added memory) is
  // read back out of card._havenReview by the Save button.
  const MEMORY_TYPES = ["fact", "preference", "decision", "goal", "project"];

  // MEMORY_TYPE_LABELS (config.js) supplies the save summary's type/decision
  // breakdown (see showSaveSummary) -- singular form; pluralized with a
  // trailing "s" by pluralizeType() below since every value pluralizes
  // regularly.
  function pluralizeType(type, count) {
    const label = MEMORY_TYPE_LABELS[type] ?? type;
    return count === 1 ? label : `${label}s`;
  }

  // Labels for obsidian/manager_ai/models.py's KnowledgeDecision values, as
  // returned in commitMemory's response.data.decision_counts (e.g.
  // {"new": 2, "confirm": 1}) -- unmapped values fall back to the raw
  // string so a future decision type still renders something.
  const DECISION_LABELS = {
    new: "added",
    confirm: "confirmed",
    update: "updated",
    supersede: "superseded",
  };

  function createMemoryTypeSelect(selected) {
    const select = document.createElement("select");
    select.className = "haven-review-select";
    for (const type of MEMORY_TYPES) {
      const option = document.createElement("option");
      option.value = type;
      option.textContent = MEMORY_TYPE_LABELS[type] ?? type;
      if (type === selected) option.selected = true;
      select.append(option);
    }
    return select;
  }

  function buildReviewCard(factIndex, text, memoryType, evidence, onRemove) {
    const card = document.createElement("div");
    card.className = "haven-review-card";

    const textarea = document.createElement("textarea");
    textarea.className = "haven-review-textarea";
    textarea.value = text;
    card.append(textarea);

    const row = document.createElement("div");
    row.className = "haven-review-row";
    const select = createMemoryTypeSelect(memoryType);
    row.append(select);

    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.className = "haven-review-remove";
    removeButton.textContent = "Remove";
    removeButton.addEventListener("click", () => {
      card.remove();
      onRemove?.();
    });
    row.append(removeButton);
    card.append(row);

    if (evidence) {
      const evidenceEl = document.createElement("p");
      evidenceEl.className = "haven-review-evidence";
      evidenceEl.textContent = `Detected from: ${evidence}`;
      card.append(evidenceEl);
    }

    card._havenReview = { factIndex, textarea, select };
    return card;
  }

  // Resolves to the array of {fact_index, text, memory_type} the user
  // wants saved, or null if the dialog was dismissed without saving
  // (Cancel, clicking the backdrop, or Escape) -- every one of those paths
  // also cancels reviewId server-side so it doesn't linger.
  function showReviewDialog(reviewId, items) {
    return new Promise((resolve) => {
      const overlay = document.createElement("div");
      overlay.className = "haven-dialog-overlay";

      const dialog = document.createElement("div");
      dialog.className = "haven-dialog";

      const heading = document.createElement("div");
      heading.className = "haven-dialog-heading";
      heading.textContent = "Review memories";

      // Omitted entirely for the zero-items case -- the list's own
      // "Nothing extracted. Add a memory below." empty state (see
      // refreshEmptyState) already says this, with its call to action.
      const subheading = document.createElement("div");
      subheading.className = "haven-dialog-subheading";
      if (items.length) {
        subheading.textContent = `Found ${items.length} ${items.length === 1 ? "memory" : "memories"} to review`;
      } else {
        subheading.hidden = true;
      }

      const list = document.createElement("div");
      list.className = "haven-review-list";

      // Declared before refreshEmptyState (which disables it) rather than
      // alongside cancelButton below, since refreshEmptyState runs once
      // synchronously for the initial items before that point.
      const saveButton = document.createElement("button");
      saveButton.type = "button";
      saveButton.className = "haven-dialog-insert";
      saveButton.textContent = "Save";

      function refreshEmptyState() {
        const hasCards = Boolean(list.querySelector(".haven-review-card"));
        let empty = list.querySelector(".haven-review-empty");
        if (!hasCards && !empty) {
          empty = document.createElement("p");
          empty.className = "haven-review-empty";
          empty.textContent = "Nothing extracted. Add a memory below.";
          list.prepend(empty);
        } else if (hasCards && empty) {
          empty.remove();
        }
        // A Save with zero cards would 200 with "Saved 0 memories" -- looks
        // broken to a demo viewer, so disable rather than let it happen.
        saveButton.disabled = !hasCards;
      }

      for (const item of items) {
        list.append(
          buildReviewCard(
            item.fact_index,
            item.text,
            item.memory_type,
            item.evidence,
            refreshEmptyState
          )
        );
      }
      refreshEmptyState();

      // Add memory (two-step): "Add memory" reveals a type picker + Create
      // button; only once a type is chosen does a full card get created,
      // preselected to that type -- rather than immediately inserting a
      // blank "fact" card.
      const addRow = document.createElement("div");
      addRow.className = "haven-review-add-row";

      const addButton = document.createElement("button");
      addButton.type = "button";
      addButton.className = "haven-dialog-cancel";
      addButton.textContent = "Add memory";

      function resetAddRow() {
        addRow.replaceChildren(addButton);
      }

      addButton.addEventListener("click", () => {
        const typeSelect = createMemoryTypeSelect("fact");
        typeSelect.className = "haven-review-add-select";
        const createButton = document.createElement("button");
        createButton.type = "button";
        createButton.className = "haven-dialog-insert";
        createButton.textContent = "Create";
        createButton.addEventListener("click", () => {
          const card = buildReviewCard(null, "", typeSelect.value, "", refreshEmptyState);
          list.append(card);
          refreshEmptyState();
          card._havenReview.textarea.focus();
          resetAddRow();
        });
        addRow.replaceChildren(typeSelect, createButton);
      });
      resetAddRow();

      const cancelButton = document.createElement("button");
      cancelButton.type = "button";
      cancelButton.className = "haven-dialog-cancel";
      cancelButton.textContent = "Cancel";

      const actions = document.createElement("div");
      actions.className = "haven-dialog-actions";
      actions.append(cancelButton, saveButton);

      const headingGroup = document.createElement("div");
      headingGroup.className = "haven-dialog-heading-group";
      headingGroup.append(heading, subheading);

      dialog.append(headingGroup, list, addRow, actions);
      overlay.append(dialog);
      document.body.append(overlay);

      function close(result) {
        document.removeEventListener("keydown", onKeyDown);
        overlay.remove();
        resolve(result);
      }

      function closeWithoutSaving() {
        HavenClient.cancelMemory(reviewId); // fire-and-forget
        close(null);
      }

      function onKeyDown(event) {
        if (event.key === "Escape") closeWithoutSaving();
      }

      cancelButton.addEventListener("click", closeWithoutSaving);
      saveButton.addEventListener("click", () => {
        const submitted = [...list.querySelectorAll(".haven-review-card")].map(
          (card) => {
            const { factIndex, textarea, select } = card._havenReview;
            return {
              fact_index: factIndex,
              text: textarea.value,
              memory_type: select.value,
            };
          }
        );
        close(submitted);
      });
      overlay.addEventListener("click", (event) => {
        if (event.target === overlay) closeWithoutSaving();
      });
      document.addEventListener("keydown", onKeyDown);
    });
  }

  function appendSummaryLine(container, count, label) {
    if (!count) return;
    const line = document.createElement("div");
    line.className = "haven-save-summary-line";
    line.textContent = `${count} ${label}`;
    container.append(line);
  }

  // Replaces the plain "Remembered." success text with a structured
  // breakdown plus shortcuts to the Write Inspector/Dashboard. Still lives
  // in state.errorEl (same auto-hide convention as showMessage), just with
  // richer content and a longer timeout since there's more to read.
  //
  // typeCounts (memory_type -> count, computed client-side from the items
  // actually sent to commitMemory) and decisionCounts
  // (response.data.decision_counts, e.g. {"new": 2, "confirm": 1}) both
  // reuse data this call already has -- no new server field, just
  // previously-unread parts of the existing commitMemory response plus one
  // client-side tally -- to make "Saved" specific instead of a bare count,
  // and to surface the Canonical Matcher's new/confirmed/updated/superseded
  // decision that was otherwise invisible in the UI.
  function showSaveSummary(summary, traceId, typeCounts, decisionCounts) {
    clearTimeout(state.errorTimer);
    state.errorEl.replaceChildren();
    state.errorEl.classList.remove("haven-error");
    state.errorEl.classList.add("haven-success", "haven-save-summary-active");
    state.errorEl.hidden = false;

    const wrapper = document.createElement("div");
    wrapper.className = "haven-save-summary";

    const headingEl = document.createElement("div");
    headingEl.className = "haven-save-summary-heading";
    const savedCount = summary?.saved ?? 0;
    headingEl.textContent = `Saved ${savedCount} ${savedCount === 1 ? "memory" : "memories"}`;
    wrapper.append(headingEl);

    const typeEntries = typeCounts ? Object.entries(typeCounts).filter(([, c]) => c > 0) : [];
    if (typeEntries.length) {
      const typeLine = document.createElement("div");
      typeLine.className = "haven-save-summary-line";
      typeLine.textContent = `(${typeEntries
        .map(([type, count]) => `${count} ${pluralizeType(type, count)}`)
        .join(", ")})`;
      wrapper.append(typeLine);
    }

    if (summary) {
      appendSummaryLine(wrapper, summary.edited, "edited");
      appendSummaryLine(wrapper, summary.added, "added");
      appendSummaryLine(wrapper, summary.removed, "removed");
    }

    const decisionEntries = decisionCounts
      ? Object.entries(decisionCounts).filter(([, c]) => c > 0)
      : [];
    if (decisionEntries.length) {
      const decisionLine = document.createElement("div");
      decisionLine.className = "haven-save-summary-line";
      decisionLine.textContent = decisionEntries
        .map(([decision, count]) => `${count} ${DECISION_LABELS[decision] ?? decision}`)
        .join(", ");
      wrapper.append(decisionLine);
    }

    const actions = document.createElement("div");
    actions.className = "haven-save-summary-actions";

    const dashboardButton = document.createElement("button");
    dashboardButton.type = "button";
    dashboardButton.className = "haven-save-summary-button";
    dashboardButton.textContent = "Dashboard";
    dashboardButton.addEventListener("click", () => {
      window.open(`${settings.serverUrl || HAVEN_BASE_URL}/dashboard`, "_blank");
    });
    actions.append(dashboardButton);

    if (traceId) {
      const inspectorButton = document.createElement("button");
      inspectorButton.type = "button";
      inspectorButton.className = "haven-save-summary-button";
      inspectorButton.textContent = "Write Inspector";
      inspectorButton.addEventListener("click", () => {
        const base = settings.serverUrl || HAVEN_BASE_URL;
        window.open(
          `${base}/dashboard?trace_id=${encodeURIComponent(traceId)}`,
          "_blank"
        );
      });
      actions.append(inspectorButton);
    }
    wrapper.append(actions);
    state.errorEl.append(wrapper);

    state.errorTimer = setTimeout(() => {
      state.errorEl.hidden = true;
      state.errorEl.classList.remove("haven-save-summary-active");
    }, 8000);
  }

  async function onButtonClick() {
    // state.button.disabled is also set while a Remember flow is running
    // (see setRememberLoading) -- this one check covers both directions of
    // overlap between the two independent async flows this widget drives.
    if (state.button.disabled) return;
    clearError();
    hideRewriteSuggestion();
    rememberVisibility.retrievalStarted();
    syncRememberButtonVisibility();

    if (state.connected === false && !(await refreshHealth())) {
      setStatus("offline");
      showError("Haven server is offline.");
      return;
    }

    const composeBox = state.composeBox;
    if (!composeBox) return;

    // Prefer the in-progress draft; once it's been sent the box is empty,
    // so fall back to the most recent user turn from the conversation (see
    // resolve-query.js) rather than erroring just because nothing is typed.
    const conversationTurns = adapter.getConversationTurns ? adapter.getConversationTurns() : [];
    const query = resolveQuery(adapter.getComposeText(composeBox), conversationTurns);
    if (!query) {
      showError("Type a prompt first.");
      return;
    }

    setLoading(true);
    setStatus("loading");
    // Try the Working Context preview first; only fetch the legacy flat
    // context if it turns out unavailable (older server, or the best-effort
    // call failed) — see the "graceful fallback" branch below. This keeps
    // the common case to a single request, same as before this feature.
    const preview = await HavenClient.retrieveWorkingContext(query);
    const workingContextAvailable = preview.ok && preview.data.available;
    let legacy = null;
    if (!workingContextAvailable) {
      legacy = await HavenClient.retrieveContext(query);
    }
    setLoading(false);

    let contentToInsert;
    if (workingContextAvailable) {
      const { contexts, structured_prompt: structuredPrompt } = preview.data;
      const totalMemories = contexts.reduce((sum, ctx) => sum + ctx.memory_count, 0);
      if (!totalMemories) {
        setStatus("error");
        showError("No relevant context found.");
        return;
      }

      setStatus("retrieved");
      rememberVisibility.retrievalSucceeded();
      syncRememberButtonVisibility();

      // Same autoPreview contract as before: on by default, the user
      // confirms via Insert/Cancel; turning it off skips the dialog and
      // inserts immediately. This is unchanged — only what the dialog
      // shows, and what gets inserted, is new. Remember's visibility
      // (above) does not depend on this choice -- Insert and Remember are
      // independent actions.
      let shouldInsert;
      if (settings.autoPreview) {
        shouldInsert = await showWorkingContextDialog(contexts);
      } else {
        shouldInsert = true;
      }
      if (!shouldInsert) return;

      contentToInsert = structuredPrompt;
    } else {
      // Graceful fallback: the exact pre-existing flow, untouched, for when
      // Working Context APIs are unavailable.
      if (!legacy.ok) {
        if (isTimeoutFailure(legacy)) {
          setStatus("error");
          showError(
            "Haven is taking longer than expected to process this conversation. Please wait a moment and try again."
          );
        } else if (isConnectivityFailure(legacy)) {
          setStatus("offline");
          showError("Haven server is offline.");
        } else {
          setStatus("error");
          showError(legacy.error ?? "Failed to retrieve context.");
        }
        return;
      }

      const context = legacy.data.context;
      if (!context) {
        setStatus("error");
        showError("No relevant context found.");
        return;
      }

      setStatus("retrieved");
      rememberVisibility.retrievalSucceeded();
      syncRememberButtonVisibility();

      const shouldInsert = settings.autoPreview ? await showPreviewDialog(context) : true;
      if (!shouldInsert) return;

      contentToInsert = context;
    }

    // structuredPrompt already embeds the resolved query verbatim inside its
    // own <UserRequest> element (see obsidian/memory_engine/engine.py's
    // query_structured, which renders StructuredPromptBuilder with exactly
    // the same `query` this call sent as retrieveWorkingContext's request
    // body). Prepending it above the still-present compose box draft --
    // insertContextAbove's contract, correct for the legacy flat `context`
    // below, which never contains the request -- would leave that same
    // request sitting a second time, raw, after </System>. setComposeText
    // replaces the draft outright so the request appears exactly once.
    // Both branches below are an internal bootstrap mutation, not
    // user-authored input -- mark the conversation as entering the "Use
    // Haven" bootstrap phase (also retracts the Remember visibility this
    // same click's own retrieval just earned, see bootstrapStarted's
    // comment) before running the mutation itself wrapped in
    // runSuppressed(), which brackets every native "input" event it fires.
    rememberVisibility.bootstrapStarted();
    rewriteSuppression.runSuppressed(() => {
      if (workingContextAvailable) {
        adapter.setComposeText(composeBox, contentToInsert);
      } else {
        adapter.insertContextAbove(composeBox, contentToInsert);
      }
    });

    // settings.autoRemember ("Auto-remember after insert") used to run
    // Remember unconditionally right here, immediately after inserting --
    // against the pre-bootstrap conversation, before the injected prompt
    // had even been sent, let alone replied to. There was nothing new to
    // remember yet at this point; the Working Context just inserted already
    // exists in Haven and must never be re-extracted (see
    // bootstrapStarted()'s call above). The trigger has moved to
    // updateRememberVisibility() below, which fires it once
    // rememberVisibility.assistantMessageObserved() reports the reply to
    // the first genuine *post*-bootstrap user message -- i.e. once this
    // conversation actually contains newly-created knowledge. Manual
    // Remember clicks (the rememberButton listener above) are unaffected.
  }

  // Remember now runs Remember -> Review -> Save: it stops after
  // extraction (previewMemory) and lets the user edit/delete/add memories
  // before anything is written, then commits the (possibly edited) set via
  // commitMemory -- which never re-runs the Extractor/Classifier/
  // ImportanceScorer, only the deterministic matching stage. See
  // obsidian/server/main.py's preview_memory/commit_memory docstrings.
  async function onRememberClick() {
    if (state.rememberButton.disabled) return;
    clearError();

    if (state.connected === false && !(await refreshHealth())) {
      setStatus("offline");
      showError("Haven server is offline.");
      return;
    }

    const composeBox = state.composeBox;
    if (!composeBox) return;

    // Prefer the full visible conversation (every rendered user/assistant
    // turn, chronological) so ManagerPipeline sees the whole dialogue, not
    // just one message — see chatgpt.js's getConversationTurns. Falls back
    // to the legacy single-message shape (the compose box's current
    // contents) when the adapter has no conversation to scrape, e.g. a
    // fresh page with no messages rendered yet, or a future site adapter
    // that hasn't implemented getConversationTurns.
    const conversationTurns = adapter.getConversationTurns
      ? adapter.getConversationTurns()
      : [];

    let payload;
    if (conversationTurns.length > 0) {
      payload = { conversation: conversationTurns };
    } else {
      const fact = adapter.getComposeText(composeBox).trim();
      if (!fact) {
        setStatus("error");
        showError("Nothing to remember yet.");
        return;
      }
      payload = { canonical_fact: fact };
    }

    setRememberLoading(true, "Analyzing…");
    setStatus("loading");
    const preview = await HavenClient.previewMemory(payload);
    setRememberLoading(false);

    if (!preview.ok) {
      if (isTimeoutFailure(preview)) {
        setStatus("error");
        showError(
          "Haven is taking longer than expected to process this conversation. Please wait a moment and try again."
        );
      } else if (isConnectivityFailure(preview)) {
        setStatus("offline");
        showError("Haven server is offline.");
      } else {
        setStatus("error");
        showError(preview.error ?? "Failed to prepare memories for review.");
      }
      return;
    }

    if (preview.data.status === "duplicate") {
      setStatus("saved");
      showSuccess("Already remembered — nothing changed.");
      return;
    }

    const { review_id: reviewId, items } = preview.data;
    const submittedItems = await showReviewDialog(reviewId, items);
    if (submittedItems === null) return; // dismissed without saving

    // Drop any card left blank (e.g. an "Add memory" card the user never
    // filled in) rather than sending it to commit, where a blank `text`
    // would just fail validation -- an omitted fact_index already means
    // "deleted" server-side, so this is consistent with that contract.
    const cleanedItems = submittedItems
      .map((item) => ({ ...item, text: item.text.trim() }))
      .filter((item) => item.text.length > 0);

    setRememberLoading(true, "Saving…");
    setStatus("loading");
    const response = await HavenClient.commitMemory({
      review_id: reviewId,
      items: cleanedItems,
    });
    setRememberLoading(false);

    if (!response.ok) {
      if (isTimeoutFailure(response)) {
        setStatus("error");
        showError(
          "Haven is taking longer than expected to process this conversation. Please wait a moment and try again."
        );
      } else if (isConnectivityFailure(response)) {
        setStatus("offline");
        showError("Haven server is offline.");
      } else {
        setStatus("error");
        showError(response.error ?? "Failed to save memory.");
      }
      return;
    }

    setStatus("saved");
    const typeCounts = cleanedItems.reduce((acc, item) => {
      acc[item.memory_type] = (acc[item.memory_type] || 0) + 1;
      return acc;
    }, {});
    showSaveSummary(
      response.data.review_summary,
      response.data.trace_id,
      typeCounts,
      response.data.decision_counts
    );
  }

  function positionContainer() {
    if (!state.composeBox || !state.container) return;
    const rect = adapter.getAnchorForButton(state.composeBox).getBoundingClientRect();
    state.container.style.top = `${window.scrollY + rect.top - state.container.offsetHeight - 8}px`;
    state.container.style.left = `${window.scrollX + rect.left}px`;
  }

  // Attaches/moves the "input" listener to whatever compose element is
  // currently live, independent of sync()'s heavier mount() work below.
  // Called both from mount() (the normal, debounced path) and directly,
  // undebounced, from the raw MutationObserver callback (see
  // ensureComposeListenerAttached below) -- the same fix already applied to
  // updateRememberVisibility for the identical starvation problem: sync()'s
  // shared 150ms debounce timer gets reset by every mutation anywhere in
  // <body>, including the ones the user's own typing causes, so waiting for
  // it to settle before moving the listener can leave it attached to a
  // detached/abandoned compose node for a while if ChatGPT swaps that node
  // out mid-typing-session -- every keystroke on the real, current node
  // would then fire zero "input" events with no error, no exception, and no
  // sign anything is wrong short of the suggestion just never appearing.
  function attachComposeInputListener(composeBox) {
    const previousComposeBox = state.composeBox;
    if (previousComposeBox === composeBox) return;
    previousComposeBox?.removeEventListener("input", onComposeInput);
    state.composeBox = composeBox;
    composeBox.addEventListener("input", onComposeInput);
  }

  // Undebounced counterpart to sync()'s mount()/unmount() -- runs on every
  // raw MutationObserver tick so a compose-node swap is caught within one
  // mutation instead of waiting out sync()'s starvable debounce (see
  // attachComposeInputListener's comment above). Only moves the listener;
  // buildUI/positioning/refreshHealth still happen through the normal
  // debounced sync() -> mount() path below, since none of that is
  // time-sensitive the way the input listener is, and it's idempotent
  // (buildUI/refreshHealth both no-op once already done) so running it a
  // tick later than the listener move is harmless. Crucially, this updates
  // state.composeBox but NOT state.uiComposeBox -- sync() below keys its own
  // mount() decision off the latter specifically so this function running
  // first on every tick can never make sync() believe UI-mounting has
  // already happened when it hasn't.
  function ensureComposeListenerAttached() {
    const composeBox = adapter.findComposeBox();
    if (!composeBox || composeBox === state.composeBox) return;
    attachComposeInputListener(composeBox);
  }

  function mount(composeBox) {
    attachComposeInputListener(composeBox);
    if (!state.container) buildUI();
    if (!state.container.isConnected) document.body.append(state.container);
    requestAnimationFrame(positionContainer);
    if (state.connected === null) refreshHealth();
    state.uiComposeBox = composeBox;
  }

  function unmount() {
    state.composeBox?.removeEventListener("input", onComposeInput);
    state.composeBox = null;
    state.uiComposeBox = null;
    state.container?.remove();
    hideRewriteSuggestion();
  }

  // Compares against state.uiComposeBox (last box mount() actually ran for),
  // NOT state.composeBox -- ensureComposeListenerAttached() (see its comment
  // above) can update state.composeBox undebounced, on the very next
  // MutationObserver tick after a compose-box swap, well before this
  // debounced sync() ever runs. Comparing against state.composeBox here
  // would then find them already equal and skip mount() forever, which is
  // exactly the bug this separate tracker exists to avoid: buildUI() would
  // never run for a freshly (re)loaded page, and the floating "Use
  // Haven"/"Remember" buttons would never appear even though the input
  // listener and rewrite suggestion card work fine.
  function sync() {
    const composeBox = adapter.findComposeBox();
    if (composeBox && composeBox !== state.uiComposeBox) {
      mount(composeBox);
    } else if (!composeBox && state.composeBox) {
      unmount();
    } else if (composeBox) {
      positionContainer();
      positionRewriteCard();
    }
  }

  // Remember's visibility is keyed off which conversation is on screen (see
  // remember-visibility.js) and the content of its latest assistant/user
  // turns -- independent of the compose box's DOM-node identity above
  // (ChatGPT can swap that node out from under us within the same
  // conversation). The retrieval-based visibility reason is independent of
  // "Use Haven" entirely, as before; the assistant/user-turn reason now also
  // tracks "Use Haven"'s bootstrap phase (see bootstrapStarted()) so its own
  // injected prompt and the reply to it don't count as new eligibility.
  //
  // This runs directly from the raw MutationObserver callback below, NOT
  // from the debounced sync() -- unlike positioning/mount (expensive layout
  // work, fine to defer), this check is cheap and time-sensitive. sync()'s
  // 150ms debounce timer is shared by *every* mutation anywhere in
  // <body>'s subtree, so on a page as continuously busy as ChatGPT's (its
  // own streaming text, sidebar/conversation-list re-renders, live UI
  // chrome) that timer can be reset faster than it ever fires, starving
  // sync() for long stretches -- including past the point a response
  // actually finishes rendering. "Use Haven" never had this problem because
  // its click handler calls syncRememberButtonVisibility() directly,
  // bypassing the observer/debounce path entirely; this makes the
  // assistant-message path just as immediate.
  function updateRememberVisibility() {
    if (!state.rememberButton) return;

    const conversationKey = adapter.getConversationKey
      ? adapter.getConversationKey()
      : null;
    rememberVisibility.conversationObserved(conversationKey);

    // Keyed off the turn's content (not just its position/count) so a
    // regeneration -- which replaces the last assistant message in place
    // rather than appending a new one -- is still detected. See
    // remember-visibility.js's assistantMessageObserved() for why. Reads
    // only the last assistant message (not the whole conversation, unlike
    // getConversationTurns()) since this runs on every raw mutation below.
    const lastAssistantContent = adapter.getLastAssistantMessage
      ? adapter.getLastAssistantMessage()
      : null;
    // True exactly once per "Use Haven" bootstrap: the reply to the first
    // genuine post-bootstrap user message, i.e. the conversation crossing
    // back over the boundary "Use Haven" drew into newly-created knowledge
    // -- see assistantMessageObserved's own comment. False for the
    // bootstrap's own reply, and false for every plain conversation that
    // never called bootstrapStarted() in the first place.
    let crossedIntoNewConversation = false;
    if (lastAssistantContent) {
      crossedIntoNewConversation = rememberVisibility.assistantMessageObserved(lastAssistantContent);
    }

    // Same rationale, needed to advance the bootstrap phase machine (see
    // remember-visibility.js's userMessageObserved) -- telling "the user
    // sent a genuine follow-up" apart from "Use Haven"'s own injected
    // prompt being sent requires seeing the user side of the conversation
    // too, not just the assistant side above.
    const lastUserContent = adapter.getLastUserMessage
      ? adapter.getLastUserMessage()
      : null;
    if (lastUserContent) {
      rememberVisibility.userMessageObserved(lastUserContent);
    }

    syncRememberButtonVisibility();

    // The redesigned Auto Remember trigger (see onButtonClick's comment
    // above): only once the conversation has crossed back over "Use
    // Haven"'s boundary into genuinely new information, and only if the
    // setting is on. onRememberClick() has its own
    // state.rememberButton.disabled guard, so this is a no-op if some other
    // Remember/Use Haven flow is already in flight.
    if (crossedIntoNewConversation && settings.autoRemember) {
      onRememberClick();
    }
  }

  let debounceTimer = null;
  const observer = new MutationObserver(() => {
    updateRememberVisibility();
    ensureComposeListenerAttached();
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(sync, 150);
  });
  observer.observe(document.body, { childList: true, subtree: true });

  function repositionAll() {
    positionContainer();
    positionRewriteCard();
  }
  window.addEventListener("resize", repositionAll);
  window.addEventListener("scroll", repositionAll, true);

  settings = await loadSettings();
  sync();
  updateRememberVisibility();
})();
