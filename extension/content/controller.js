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

  const { HAVEN_BASE_URL, SETTINGS_STORAGE_KEY, DEFAULT_SETTINGS, REQUEST_TIMEOUT_MS } =
    await import(chrome.runtime.getURL("config.js"));
  const { resolveQuery } = await import(
    chrome.runtime.getURL("content/resolve-query.js")
  );
  const { createRememberVisibility } = await import(
    chrome.runtime.getURL("content/remember-visibility.js")
  );
  const rememberVisibility = createRememberVisibility();

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
  };

  // Auto-preview/auto-remember, set from popup/popup.js via
  // chrome.storage.local — see loadSettings()/the onChanged listener below.
  let settings = { ...DEFAULT_SETTINGS };

  async function loadSettings() {
    const stored = await chrome.storage.local.get(SETTINGS_STORAGE_KEY);
    settings = { ...DEFAULT_SETTINGS, ...(stored[SETTINGS_STORAGE_KEY] || {}) };
  }

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes[SETTINGS_STORAGE_KEY]) {
      settings = { ...DEFAULT_SETTINGS, ...changes[SETTINGS_STORAGE_KEY].newValue };
    }
  });

  const state = {
    composeBox: null,
    container: null,
    statusDot: null,
    statusText: null,
    button: null,
    rememberButton: null,
    errorEl: null,
    errorTimer: null,
    statusTimer: null,
    connected: null, // null = unknown/checking, true = connected, false = offline
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

  // Six states: idle (resting/connected), loading (a request is in
  // flight), retrieved (context fetch just succeeded), saved (a memory was
  // just remembered), error (the last action failed), offline (server
  // unreachable). retrieved/saved/error are transient — they auto-revert to
  // idle (or offline, if connectivity dropped) after a few seconds, the
  // same pattern showMessage() already uses for its own timer.
  const STATUS_LABELS = {
    idle: "Haven: idle",
    loading: "Haven: loading…",
    retrieved: "Haven: context retrieved",
    saved: "Haven: memory saved",
    error: "Haven: error",
    offline: "Haven: offline",
  };
  const TRANSIENT_STATUSES = new Set(["retrieved", "saved", "error"]);

  // Short always-visible text next to the dot -- the idle/offline dot colors
  // are both desaturated greys that are hard to tell apart at a glance, so
  // the dot alone isn't a reliable signal (mirrors popup.html's #status-text).
  const STATUS_TEXT_LABELS = {
    idle: "Idle",
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
      dialog.className = "haven-dialog";

      const heading = document.createElement("div");
      heading.className = "haven-dialog-heading";
      heading.textContent = "Working context";

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

      dialog.append(heading, list, actions);
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
    appendField(body, "Current goal", context.current_goal);
    appendField(body, "Current focus", context.current_focus);
    appendFieldList(body, "Recent decisions", context.recent_decisions);
    appendFieldList(body, "Pending tasks", context.pending_tasks);
    if (!body.childElementCount) {
      const empty = document.createElement("p");
      empty.className = "haven-context-empty";
      empty.textContent = "Nothing to resume here yet.";
      body.append(empty);
    }

    card.append(header, body);
    return card;
  }

  function appendField(container, label, value) {
    if (!value) return;
    const field = document.createElement("div");
    field.className = "haven-context-field";
    const labelEl = document.createElement("div");
    labelEl.className = "haven-context-field-label";
    labelEl.textContent = label;
    const valueEl = document.createElement("div");
    valueEl.className = "haven-context-field-value";
    valueEl.textContent = value;
    field.append(labelEl, valueEl);
    container.append(field);
  }

  function appendFieldList(container, label, values) {
    if (!values || !values.length) return;
    const field = document.createElement("div");
    field.className = "haven-context-field";
    const labelEl = document.createElement("div");
    labelEl.className = "haven-context-field-label";
    labelEl.textContent = label;
    const list = document.createElement("ul");
    for (const value of values) {
      const item = document.createElement("li");
      item.textContent = value;
      list.append(item);
    }
    field.append(labelEl, list);
    container.append(field);
  }

  // Memory Review dialog (Remember -> Review -> Save). Deliberately flatter
  // than showWorkingContextDialog above -- one flow, no collapsible
  // sections, no status badges. Each card exposes exactly two editable
  // fields (text, memory type) plus a read-only "Detected from: ..."
  // evidence line; a card's fact_index (null for a user-added memory) is
  // read back out of card._havenReview by the Save button.
  const MEMORY_TYPES = ["fact", "preference", "decision", "goal", "project"];

  // Presentation-only labels for the save summary's type/decision breakdown
  // (see showSaveSummary) -- singular form; pluralized with a trailing "s"
  // by pluralizeType() below since every value here pluralizes regularly.
  const MEMORY_TYPE_LABELS = {
    fact: "Fact",
    preference: "Preference",
    decision: "Decision",
    goal: "Goal",
    project: "Project",
  };

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
        if (isConnectivityFailure(legacy)) {
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

    adapter.insertContextAbove(composeBox, contentToInsert);

    if (settings.autoRemember) {
      await onRememberClick();
    }
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
      if (isConnectivityFailure(preview)) {
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
      if (isConnectivityFailure(response)) {
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

  function mount(composeBox) {
    state.composeBox = composeBox;
    if (!state.container) buildUI();
    if (!state.container.isConnected) document.body.append(state.container);
    requestAnimationFrame(positionContainer);
    if (state.connected === null) refreshHealth();
  }

  function unmount() {
    state.composeBox = null;
    state.container?.remove();
  }

  function sync() {
    const composeBox = adapter.findComposeBox();
    if (composeBox && composeBox !== state.composeBox) {
      mount(composeBox);
    } else if (!composeBox && state.composeBox) {
      unmount();
    } else if (composeBox) {
      positionContainer();
    }
  }

  // Remember's visibility is keyed off which conversation is on screen (see
  // remember-visibility.js) and the content of its latest assistant turn --
  // independent of retrieval/"Use Haven" entirely, and independent of the
  // compose box's DOM-node identity above (ChatGPT can swap that node out
  // from under us within the same conversation).
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
    if (lastAssistantContent) {
      rememberVisibility.assistantMessageObserved(lastAssistantContent);
    }

    syncRememberButtonVisibility();
  }

  let debounceTimer = null;
  const observer = new MutationObserver(() => {
    updateRememberVisibility();
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(sync, 150);
  });
  observer.observe(document.body, { childList: true, subtree: true });

  window.addEventListener("resize", positionContainer);
  window.addEventListener("scroll", positionContainer, true);

  await loadSettings();
  sync();
  updateRememberVisibility();
})();
