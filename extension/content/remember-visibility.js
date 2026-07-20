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
  let lastUserMessage = null;

  // Bootstrap lifecycle for "Use Haven": normal operation is "active", where
  // every new assistant turn earns Remember visibility (see
  // assistantMessageObserved below). bootstrapStarted() moves this to
  // "bootstrap" the moment Use Haven injects the structured Working Context
  // prompt into the compose box -- before it's even sent. From there:
  //   "bootstrap" -- the next new *user* turn observed is the injected
  //     prompt itself, once sent. Expected, not user-authored conversation;
  //     consumed with no state change (still waiting on the reply to it).
  //   "bootstrap" -- the next new *assistant* turn observed is the reply to
  //     that injected prompt, not new knowledge. Consumed, advancing to
  //     "awaitingUserMessage".
  //   "awaitingUserMessage" -- assistant turns are still consumed with no
  //     effect (e.g. a regenerated bootstrap reply); only a genuine new user
  //     turn -- one the user typed themselves -- advances this to
  //     "awaitingGenuineReply".
  //   "awaitingGenuineReply" -- behaves like "active" for visibility (a new
  //     assistant turn earns Remember same as always), but the *first* one
  //     is also the conversation crossing back over "Use Haven"'s boundary
  //     from historical context into newly-created knowledge -- see
  //     assistantMessageObserved's return value below. Consumed back to
  //     "active" the moment that happens; every assistant turn after that
  //     one is normal "active" behavior with no further signal, exactly as
  //     if "Use Haven" had never been clicked.
  // This is driven entirely by *when* bootstrapStarted() is called and by
  // turn-identity change detection (already used below for regeneration),
  // never by inspecting message text -- there is no XML/keyword check
  // anywhere in this file.
  let phase = "active";

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
        lastUserMessage = null;
        phase = "active";
      }
    },

    // Called once, synchronously, by "Use Haven"'s click handler at the
    // moment it injects the structured Working Context prompt into the
    // compose box -- an internal system mutation, not conversation content,
    // so this is driven by *when* the caller runs it, never by inspecting
    // what got typed or sent.
    //
    // Also clears visibleFromRetrieval: onButtonClick calls
    // retrievalSucceeded() as soon as context is found, *before* this --
    // still within the same click, well before the injection actually
    // happens -- so without this, every "Use Haven" click would leave
    // Remember visible for its own retrieval regardless of bootstrap phase,
    // and the guards in assistantMessageObserved below would have nothing
    // left to protect. Never clears visibleFromAssistant here: that reason
    // can predate this click entirely (a real exchange the user had before
    // ever clicking "Use Haven" for a follow-up query), and un-earning it
    // just because a new bootstrap started would be a regression of its
    // own -- see retrievalStarted()'s comment for the identical concern.
    bootstrapStarted() {
      phase = "bootstrap";
      visibleFromRetrieval = false;
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
    //
    // Return value: true exactly once per "Use Haven" bootstrap -- on the
    // turn that is the reply to the first *genuine* post-bootstrap user
    // message (phase "awaitingGenuineReply"; see the phase comment above).
    // That is precisely "conversation now contains genuinely new
    // information" from the redesigned Auto Remember lifecycle: the
    // injected Working Context prompt and its own bootstrap reply never
    // return true (they're consumed while phase is "bootstrap"/
    // "awaitingUserMessage"), and false thereafter once back to plain
    // "active", same as a conversation that never called bootstrapStarted()
    // at all. controller.js's caller uses this, gated on settings
    // .autoRemember, to decide whether to run previewMemory() automatically
    // -- this module has no knowledge of that setting, only of the
    // conversation-boundary state machine itself.
    assistantMessageObserved(content) {
      if (!content || content === lastAssistantMessage) return false;
      lastAssistantMessage = content;
      if (phase === "bootstrap") {
        // The bootstrap's own reply -- consume it, wait for a genuine user
        // turn before Remember can become newly eligible again.
        phase = "awaitingUserMessage";
        return false;
      }
      if (phase === "awaitingUserMessage") return false; // e.g. a regenerated bootstrap reply
      visibleFromAssistant = true;
      if (phase === "awaitingGenuineReply") {
        phase = "active";
        return true;
      }
      return false;
    },

    // controller.js's sync() observed the *content* of the most recent user
    // turn -- same change-detection shape as assistantMessageObserved above,
    // called every tick regardless of bootstrap phase. Only meaningful while
    // "awaitingUserMessage": that's the one phase where a new user turn
    // means "the user just sent a genuine follow-up", which advances to
    // "awaitingGenuineReply" (see the phase comment above) so the reply to
    // *this* turn is recognized as newly-created knowledge. During
    // "bootstrap" itself, the next new user turn is expected to be the
    // injected prompt being sent -- consumed with no phase change, since
    // bootstrap is still waiting on the *reply* to that turn.
    userMessageObserved(content) {
      if (!content || content === lastUserMessage) return;
      lastUserMessage = content;
      if (phase === "awaitingUserMessage") phase = "awaitingGenuineReply";
    },
  };
}
