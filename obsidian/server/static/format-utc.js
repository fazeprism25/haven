// Presentation-only UTC -> browser-local timestamp formatting for the
// Memory Dashboard. Every timestamp Haven stores (valid_from,
// last_confirmed, valid_until) is UTC, serialized via Python's
// datetime.isoformat() on a naive datetime -- which omits a timezone
// designator (e.g. "2026-07-05T00:13:26.693561", no trailing "Z").
// `new Date(isoString)` on a designator-less string is parsed as LOCAL
// time per the ECMAScript spec, not UTC, so passing these strings
// straight to `.toLocaleString()` silently skips the UTC -> local
// conversion and just echoes the raw UTC digits back labelled as local.
// Appending "Z" (only when not already present) marks the string as UTC
// so the browser correctly converts it. Storage stays UTC -- this only
// changes how a timestamp is displayed.
export function toUtcDate(isoString) {
  const withZone = isoString.endsWith("Z") ? isoString : `${isoString}Z`;
  return new Date(withZone);
}

export function formatUtcTimestamp(isoString) {
  return toUtcDate(isoString).toLocaleString();
}
