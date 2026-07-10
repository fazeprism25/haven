// Pure query-resolution logic for "Use Haven", pulled out of controller.js
// so it's unit-testable without a DOM/chrome extension harness.
//
// The compose box holds the draft the user is about to send -- that's the
// preferred query. But once a message has been sent, the box is empty even
// though there's clearly something to search for: the message that was just
// sent. In that case, fall back to the most recent user turn from the
// scraped conversation (see chatgpt.js's getConversationTurns) rather than
// the assistant's reply, since the reply isn't the user's question.
export function resolveQuery(composeText, conversationTurns) {
  const trimmed = (composeText ?? "").trim();
  if (trimmed) return trimmed;

  for (let i = conversationTurns.length - 1; i >= 0; i--) {
    if (conversationTurns[i].role === "user") return conversationTurns[i].content;
  }
  return "";
}
