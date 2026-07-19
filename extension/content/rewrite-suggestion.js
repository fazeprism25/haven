// Pure decision logic for the Query Rewrite Assistant, pulled out of
// controller.js so it's unit-testable without a DOM/chrome extension
// harness -- same split as resolve-query.js. controller.js owns the actual
// setTimeout/DOM/HavenClient wiring; this file only answers two questions:
// "is this draft worth sending to the rewrite endpoint at all?" and "once a
// response comes back, is it still worth showing?".

// Below this many trimmed characters, a draft is almost always an
// in-progress fragment ("h", "hey", "ok") rather than a real query -- not
// worth a network round trip. This is a cheap pre-filter, not the thing
// that decides whether a suggestion is *shown*; a short-but-complete query
// like "What is Python?" clears it easily and is still sent, it's just that
// the rewrite endpoint itself will report `changed: false` for it (see
// content/controller.js's HavenClient.rewriteQuery and
// obsidian/server/schemas.py's QueryRewriteSuggestionResponse).
export const MIN_REWRITE_LENGTH = 12;

export function isEligibleForRewrite(text) {
  return typeof text === "string" && text.trim().length >= MIN_REWRITE_LENGTH;
}

// A HAVEN_QUERY_REWRITE response is only worth displaying if the server
// actually proposed a change (`changed`) AND the compose box still holds
// the exact text the response was computed for -- the user may have kept
// typing, cleared the box, or already accepted/dismissed a different
// suggestion while the request was in flight. This one check is sufficient
// staleness protection on its own (no request-id/token bookkeeping needed):
// any edit since the request was sent changes `currentText`, which fails
// the comparison below.
export function isSuggestionStillRelevant(response, currentText) {
  return (
    Boolean(response?.ok) &&
    Boolean(response.data?.changed) &&
    response.data.original === (currentText ?? "").trim()
  );
}
