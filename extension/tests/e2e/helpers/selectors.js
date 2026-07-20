// Every selector this suite uses, in one place, so a DOM change on either
// side (Haven's own UI or ChatGPT's) only ever needs an update here.
//
// Two distinct trust levels live in this file:
// - haven.*   -- class names controller.js itself assigns (content/controller.js,
//   grep for `className =`). These are ours; they only change when we change
//   controller.js, and this file should be updated in the same commit as
//   that change.
// - chatgpt.* -- ChatGPT's own DOM, which the extension has no control over.
//   composeBox/message are imported directly from
//   content/adapters/chatgpt.js -- the extension's own single source of
//   truth for those two -- rather than copied, so a future ChatGPT-DOM fix
//   there can't silently drift out of sync with what this suite drives.
//   Node can import that file directly (it's a plain ES module with no
//   chrome.* calls at module-evaluation time; content/adapters/chatgpt.test.js
//   already does exactly this). The rest (send/stop buttons) have no
//   equivalent in the extension at all -- it never clicks ChatGPT's own send
//   button or watches its streaming state -- so they're this suite's own
//   best-known-good selectors, and the most likely thing to need updating
//   after a ChatGPT redesign.

export const haven = {
  // Floating widget (buildUI in controller.js)
  useHavenButton: ".haven-container button.haven-button >> text=Use Haven",
  rememberButton: ".haven-container button.haven-button >> text=Remember",
  statusText: ".haven-status-text",

  // Query Rewrite Assistant card (buildRewriteSuggestionUI)
  rewriteCard: ".haven-rewrite-card",
  rewriteUseButton: ".haven-rewrite-use",
  rewriteDismissButton: ".haven-rewrite-dismiss",

  // Generic dialog shell shared by Working Context / plain preview / Review
  // dialogs (showWorkingContextDialog / showPreviewDialog / showReviewDialog)
  dialogOverlay: ".haven-dialog-overlay",
  dialogHeading: ".haven-dialog-heading",
  dialogInsertButton: ".haven-dialog-insert",
  dialogCancelButton: ".haven-dialog-cancel",

  // Working Context dialog specifically (bootstrap preview)
  workingContextDialog: ".haven-dialog-overlay .haven-dialog-large",

  // Review Memories dialog specifically (onRememberClick's Remember -> Review -> Save)
  reviewCard: ".haven-review-card",
  reviewTextarea: ".haven-review-textarea",
  reviewEvidence: ".haven-review-evidence",
  reviewSaveButton: ".haven-dialog-insert", // scoped by dialogHeading text "Review memories" in helpers/chatgpt.js
};

import { COMPOSE_BOX_SELECTORS, MESSAGE_SELECTOR } from "../../../content/adapters/chatgpt.js";

export const chatgpt = {
  // COMPOSE_BOX_SELECTORS is an *ordered* fallback list for
  // findComposeBox()'s querySelector loop (first match wins); joined into a
  // CSS selector list here instead, which is a union, not a priority order
  // -- Playwright's `.first()` picks whichever matches first in DOM order,
  // not list order. In practice this doesn't matter: the list's first
  // entry, `#prompt-textarea`, is an ID selector, so at most one of these
  // ever matches on a real ChatGPT page. Worth knowing if that ever stops
  // being true.
  composeBox: COMPOSE_BOX_SELECTORS.join(", "),

  message: MESSAGE_SELECTOR,
  userMessage: '[data-message-author-role="user"]',
  assistantMessage: '[data-message-author-role="assistant"]',

  // Not used by the extension itself -- this suite's own hooks into
  // ChatGPT's native compose UI, needed to actually drive a conversation.
  // Most likely selectors to go stale after a ChatGPT redesign.
  sendButton: "[data-testid='send-button']",
  stopStreamingButton: "[data-testid='stop-button']",
};
