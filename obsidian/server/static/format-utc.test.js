import { test } from "node:test";
import assert from "node:assert/strict";

import { toUtcDate, formatUtcTimestamp } from "./format-utc.js";

test("a timezone-less ISO string (Python's naive-UTC isoformat()) parses as the correct UTC instant", () => {
  const ms = toUtcDate("2026-07-05T00:13:26.693").getTime();
  assert.equal(ms, Date.UTC(2026, 6, 5, 0, 13, 26, 693));
});

test("a string that already carries a timezone designator is not double-converted", () => {
  const ms = toUtcDate("2026-07-05T00:13:26.693Z").getTime();
  assert.equal(ms, Date.UTC(2026, 6, 5, 0, 13, 26, 693));
});

test("formatUtcTimestamp renders the correct UTC instant in the host's local time", () => {
  // Timezone-independent regression guard for the actual bug: before the
  // fix, a designator-less string was misread as already-local, so the
  // displayed value was byte-identical to the raw UTC digits regardless
  // of the host's timezone. Comparing against Date(iso + "Z") -- the
  // correct interpretation -- catches that regression in any timezone.
  const iso = "2026-07-05T00:13:26.693";
  assert.equal(formatUtcTimestamp(iso), new Date(`${iso}Z`).toLocaleString());
});
