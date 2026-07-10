// Decides whether "Remember" should be visible, driven by explicit
// extension state rather than compose-box DOM-node identity. ChatGPT's own
// re-renders can swap the compose box's underlying DOM node within the
// same conversation (see chatgpt.js's insertContextAbove comment), so
// tracking "has context been inserted for the conversation currently on
// screen" separately from *which DOM node* the compose box happens to be
// right now is what keeps Remember visible across that churn while still
// hiding it when the user actually navigates to a different conversation.
export function createRememberVisibility() {
  let conversationKey = null;
  // Two independent reasons Remember can be visible, tracked separately and
  // OR'd together in isVisible(). They *must* stay independent: retrieval
  // starting/failing is unrelated to whether the conversation already has an
  // assistant reply worth remembering, so clearing one must never clear the
  // other. (Bug this fixes: a "Use Haven" click that found no context, or
  // failed, called retrievalStarted() and then returned without ever
  // calling retrievalSucceeded() -- when both reasons shared one boolean,
  // that permanently hid an already-earned Remember button until an
  // unrelated new assistant message happened to arrive.)
  let visibleFromRetrieval = false;
  let visibleFromAssistant = false;
  let lastAssistantMessage = null;

  return {
    isVisible() {
      return visibleFromRetrieval || visibleFromAssistant;
    },

    // "Use Haven" successfully retrieved context for the conversation
    // currently on screen. Fires on retrieval success alone -- Insert is a
    // separate, independent action (inserting the retrieved text into the
    // compose box) and must not gate Remember's visibility. This is one of
    // two independent ways Remember can become visible -- see
    // assistantMessageObserved() below, which is the primary trigger and
    // does not require "Use Haven" to ever have been clicked.
    retrievalSucceeded() {
      visibleFromRetrieval = true;
    },

    // A new "Use Haven" request just started; hide any stale Remember
    // button from a previous retrieval until this one resolves. Only
    // clears the retrieval-based reason -- never the assistant-message one,
    // which has nothing to do with "Use Haven" and must survive a retrieval
    // that ends up failing or finding nothing.
    retrievalStarted() {
      visibleFromRetrieval = false;
    },

    // controller.js's sync() observed *a* conversation identifier --
    // called on every sync tick, regardless of whether the compose box's
    // DOM node changed. Only resets visibility when conversationKey
    // genuinely differs from the last one observed, so a repeated
    // observation of the same key (e.g. a DOM remount within the same
    // conversation) is a no-op and never hides a just-shown button.
    conversationObserved(key) {
      if (key !== conversationKey) {
        conversationKey = key;
        visibleFromRetrieval = false;
        visibleFromAssistant = false;
        lastAssistantMessage = null;
      }
    },

    // controller.js's sync() observed the *content* of the most recent
    // assistant turn for the conversation on screen -- called on every
    // sync tick, independent of "Use Haven"/retrieval entirely. Comparing
    // the full message content (rather than just the assistant turn count)
    // is what makes this robust to regeneration: ChatGPT replaces the last
    // assistant message in place rather than appending a new one, so the
    // turn count doesn't change, but the content does. Passing the
    // in-progress content while a response is still streaming also means
    // Remember can appear a little before the response fully finishes
    // rather than only exactly on completion -- an acceptable tradeoff
    // given there's no reliable "generation finished" DOM event to hook,
    // and it never fires *later* than completion. A no-op when the content
    // is unchanged (repeated ticks with a stable message, or no assistant
    // turn yet) so it never has to re-derive visibility once shown.
    assistantMessageObserved(content) {
      if (content && content !== lastAssistantMessage) {
        lastAssistantMessage = content;
        visibleFromAssistant = true;
      }
    },
  };
}
