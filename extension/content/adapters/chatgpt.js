// ChatGPT-specific DOM knowledge only. Anything selector- or
// contenteditable-shaped lives here, never in controller.js, so a future
// site adapter can implement these same four functions however that site's
// input actually works without touching the generic controller.

// Ordered fallback list — ChatGPT's DOM has changed these before and will
// again; this is the one part of the extension expected to need upkeep.
const COMPOSE_BOX_SELECTORS = [
  "#prompt-textarea",
  '[data-testid="chat-input-textbox"]',
  'form div[contenteditable="true"]',
  'div[contenteditable="true"]',
];

function findComposeBox() {
  for (const selector of COMPOSE_BOX_SELECTORS) {
    const el = document.querySelector(selector);
    if (el) return el;
  }
  return null;
}

function getComposeText(el) {
  return el.innerText;
}

function insertContextAbove(el, contextText) {
  // el is a React-controlled contenteditable: writing el.textContent
  // directly desyncs React's internal state (the send button can stay
  // disabled, or ChatGPT can revert the DOM on next render). Placing the
  // cursor at the start and using execCommand("insertText", ...) goes
  // through the native beforeinput/input event pipeline React listens to.
  el.focus();
  const selection = window.getSelection();
  const range = document.createRange();
  range.selectNodeContents(el);
  range.collapse(true);
  selection.removeAllRanges();
  selection.addRange(range);
  document.execCommand("insertText", false, `${contextText}\n\n`);
}

// Replaces the compose box's entire contents with `text`, used by the Query
// Rewrite Assistant's "Use Rewrite" action. Same React-controlled-
// contenteditable concern as insertContextAbove above -- selecting the
// existing content and going through execCommand("insertText", ...) (rather
// than writing el.textContent directly) is what keeps React's internal
// state in sync, since that's the native beforeinput/input event pipeline
// React listens to. Unlike insertContextAbove, the selection spans the
// *whole* node (not collapsed to its start) so the insert replaces
// everything instead of prepending.
function setComposeText(el, text) {
  el.focus();
  const selection = window.getSelection();
  const range = document.createRange();
  range.selectNodeContents(el);
  selection.removeAllRanges();
  selection.addRange(range);
  document.execCommand("insertText", false, text);
}

function getAnchorForButton(el) {
  return el.closest("form") ?? el.parentElement ?? el;
}

// Every rendered ChatGPT message (user and assistant alike) carries this
// attribute on the element holding that turn's text. Distinct from
// COMPOSE_BOX_SELECTORS above -- this walks the message history, never the
// compose box -- so there's no overlap with getComposeText's DOM reads.
const MESSAGE_SELECTOR = "[data-message-author-role]";

// Returns every visible user/assistant turn, in chronological (DOM) order,
// for the "Remember Conversation" flow (see controller.js's
// onRememberClick). System/tool turns, if ChatGPT ever renders any with
// this attribute, are skipped -- only user/assistant turns are meaningful
// input to ManagerPipeline's extraction prompt.
function getConversationTurns() {
  const turns = [];
  for (const node of document.querySelectorAll(MESSAGE_SELECTOR)) {
    const role = node.getAttribute("data-message-author-role");
    if (role !== "user" && role !== "assistant") continue;
    const content = getMessageMarkdown(node);
    if (!content) continue;
    turns.push({ role, content });
  }
  return turns;
}

// Same "last assistant turn" this file's caller (controller.js's
// updateRememberVisibility) would get from the last element of
// getConversationTurns() above -- but that walks and re-serializes *every*
// message in the conversation just to discard all but the last one.
// updateRememberVisibility runs on every raw MutationObserver callback
// (deliberately undebounced -- see its comment in controller.js), which on
// a long conversation during a streaming reply fires many times per
// second, so re-serializing the whole conversation on every tick is
// O(mutations x conversation size). This walks message nodes newest-first
// and serializes only the one it needs.
function getLastMessageByRole(role) {
  const nodes = document.querySelectorAll(MESSAGE_SELECTOR);
  for (let i = nodes.length - 1; i >= 0; i--) {
    const node = nodes[i];
    if (node.getAttribute("data-message-author-role") !== role) continue;
    const content = getMessageMarkdown(node);
    if (content) return content;
  }
  return null;
}

function getLastAssistantMessage() {
  return getLastMessageByRole("assistant");
}

// Same "last turn, not the whole conversation" rationale as
// getLastAssistantMessage above -- controller.js's bootstrap-lifecycle
// tracking (see remember-visibility.js's userMessageObserved) needs to tell
// "the user sent a new message" apart from unrelated DOM churn just as
// cheaply, and on the same every-mutation cadence.
function getLastUserMessage() {
  return getLastMessageByRole("user");
}

// ---------------------------------------------------------------------------
// Structure-preserving message extraction
//
// getMessageMarkdown() replaces a plain `.innerText` read (which flattens a
// table into ambiguous space-separated text, drops a code block's
// fence/language, and keeps only a citation link's visible chip text while
// silently discarding its href) with a recursive walk that classifies nodes
// by tag name only -- never by class name or data-testid. ChatGPT's
// markdown renderer must emit real semantic tags (h1-h6, p, ul/ol, table,
// pre/code, a, ...) to be markdown at all; those tags are what the content
// *means*, not how ChatGPT chooses to style or wrap it. Any tag this file
// doesn't special-case -- div, span, section, a future custom element, a
// Canvas/artifact card's wrapper -- is treated as transparent: its children
// are walked in DOM order as if the wrapper weren't there. That single rule
// is what survives ChatGPT restyling or re-wrapping content it already
// renders; only a change to *which semantic tag* carries a given content
// type would ever require an update here.
//
// Deliberately not handled -- left as explicit future work rather than
// guessed at, since neither can be verified without a real ChatGPT DOM
// sample:
// - Auto-expanding collapsed "Thought for Ns" / "Show more" sections. This
//   walk stays a pure, synchronous read of the DOM exactly as it is right
//   now: obsidian/server/main.py hashes this same content for checkpoint
//   dedup (turn_hash/transcript_hash), so a click-to-expand step would risk
//   the same turn hashing differently across calls, on top of mutating a
//   page this content script doesn't own.
// - Reading full Canvas/artifact panel content. That content, if present in
//   the DOM at all while the panel is closed, most likely lives in a
//   detached subtree outside this message's own node, which no amount of
//   per-message tag classification can see. An inline card's visible text
//   (a title, a snippet) still comes through via the generic transparent-
//   wrapper fallback below, same as any other unrecognized element -- there
//   is no Canvas-specific code path.

function nodeTag(node) {
  return typeof node.tagName === "string" ? node.tagName.toLowerCase() : null;
}

function childNodesOf(node) {
  return node.childNodes ? Array.from(node.childNodes) : [];
}

// --- Inline content (runs inside a paragraph, heading, list item, or table
// cell -- never split across block boundaries) ------------------------------

function serializeInline(node) {
  if (node.nodeType === 3) return node.textContent ?? "";
  if (node.nodeType !== 1) return ""; // comments and anything else, ignored

  switch (nodeTag(node)) {
    case "br":
      return "\n";
    case "strong":
    case "b":
      return wrapNonEmpty(serializeInlineChildren(node), "**", "**");
    case "em":
    case "i":
      return wrapNonEmpty(serializeInlineChildren(node), "_", "_");
    case "code":
      return serializeInlineCode(node.textContent ?? "");
    case "a":
      return serializeLink(node);
    default:
      // Transparent: span, and anything else with no inline meaning of its
      // own, just passes its children through.
      return serializeInlineChildren(node);
  }
}

function serializeInlineChildren(node) {
  return childNodesOf(node).map(serializeInline).join("");
}

function wrapNonEmpty(text, open, close) {
  const trimmed = text.trim();
  return trimmed ? `${open}${trimmed}${close}` : "";
}

function serializeInlineCode(text) {
  const fence = text.includes("`") ? "``" : "`";
  const pad = fence.length > 1 ? " " : "";
  return `${fence}${pad}${text}${pad}${fence}`;
}

// Citations and ordinary links are both just <a href> as far as the DOM is
// concerned -- ChatGPT can restyle a citation marker into a pill, a
// superscript, or plain inline text without ever changing the fact that
// it's an anchor with an href, so keying off the tag (not the chip's
// styling) is what survives a citation UI redesign.
function serializeLink(node) {
  const href = typeof node.getAttribute === "function" ? node.getAttribute("href") : null;
  const text = serializeInlineChildren(node).trim();
  if (href && text) return `[${text}](${href})`;
  if (href) return `<${href}>`;
  return text;
}

// --- Block-level leaf serializers -------------------------------------------

function serializeBlockquote(node) {
  const inner = collectBlocks(node).join("\n\n");
  return inner
    .split("\n")
    .map((line) => (line ? `> ${line}` : ">"))
    .join("\n");
}

function serializeList(listNode, depth) {
  const ordered = nodeTag(listNode) === "ol";
  const indent = "  ".repeat(depth);
  let index = 1;
  const lines = [];
  for (const item of childNodesOf(listNode)) {
    if (nodeTag(item) !== "li") continue; // stray whitespace between <li>s
    const { text, nestedLists } = splitListItem(item, depth);
    lines.push(`${indent}${ordered ? `${index++}.` : "-"} ${text}`.trimEnd());
    lines.push(...nestedLists);
  }
  return lines.join("\n");
}

function splitListItem(itemNode, depth) {
  const inline = [];
  const nestedLists = [];
  for (const child of childNodesOf(itemNode)) {
    const tag = nodeTag(child);
    if (tag === "ul" || tag === "ol") {
      nestedLists.push(serializeList(child, depth + 1));
    } else {
      inline.push(serializeInline(child));
    }
  }
  return { text: inline.join("").trim(), nestedLists };
}

function serializeTable(tableNode) {
  const rows = childNodesOf(tableNode)
    .flatMap((child) =>
      ["thead", "tbody", "tfoot"].includes(nodeTag(child)) ? childNodesOf(child) : [child]
    )
    .filter((child) => nodeTag(child) === "tr");
  if (!rows.length) return "";

  const cellsPerRow = rows.map((row) =>
    childNodesOf(row)
      .filter((cell) => nodeTag(cell) === "th" || nodeTag(cell) === "td")
      .map((cell) =>
        serializeInlineChildren(cell).trim().replace(/\|/g, "\\|").replace(/\s*\n\s*/g, " ")
      )
  );
  const columnCount = Math.max(...cellsPerRow.map((row) => row.length));
  const pad = (row) => {
    const padded = row.slice(0, columnCount);
    while (padded.length < columnCount) padded.push("");
    return padded;
  };

  const [header, ...body] = cellsPerRow.map(pad);
  return [
    `| ${header.join(" | ")} |`,
    `| ${header.map(() => "---").join(" | ")} |`,
    ...body.map((row) => `| ${row.join(" | ")} |`),
  ].join("\n");
}

function serializeCodeBlock(preNode) {
  const codeNode = childNodesOf(preNode).find((child) => nodeTag(child) === "code");
  const source = codeNode ?? preNode;
  const text = (source.textContent ?? "").replace(/\n$/, "");
  const language = extractLanguage(source, preNode);
  const fence = "`".repeat(Math.max(3, longestBacktickRun(text) + 1));
  return `${fence}${language}\n${text}\n${fence}`;
}

function extractLanguage(source, preNode) {
  return languageFromClass(source.getAttribute?.("class")) ?? languageFromClass(preNode.getAttribute?.("class")) ?? "";
}

function languageFromClass(className) {
  if (!className) return null;
  const match = /(?:^|\s)(?:language|lang)-(\S+)/.exec(className);
  return match ? match[1] : null;
}

function longestBacktickRun(text) {
  const matches = text.match(/`+/g);
  return matches ? Math.max(...matches.map((run) => run.length)) : 0;
}

// --- Block-level tree walk ---------------------------------------------------

// Walks node's children in DOM order, producing an array of independently-
// serialized block strings (to be joined with blank lines by the caller).
// Consecutive inline/text runs that aren't inside a recognized block tag
// (e.g. bare text or <a>/<strong> floating directly under a wrapper <div>,
// which real markdown renderers don't produce but malformed/unexpected DOM
// might) are coalesced into an implied paragraph, exactly like a browser's
// own HTML parser would treat loose phrasing content.
function collectBlocks(node) {
  const blocks = [];
  let inlineBuffer = [];

  const flushInline = () => {
    const text = inlineBuffer.join("").trim();
    if (text) blocks.push(text);
    inlineBuffer = [];
  };

  for (const child of childNodesOf(node)) {
    if (child.nodeType === 3) {
      inlineBuffer.push(child.textContent ?? "");
      continue;
    }
    if (child.nodeType !== 1) continue; // comments, etc.

    switch (nodeTag(child)) {
      case "h1":
      case "h2":
      case "h3":
      case "h4":
      case "h5":
      case "h6":
      case "p": {
        flushInline();
        const text = serializeInlineChildren(child).trim();
        if (text) blocks.push(text);
        break;
      }
      case "blockquote":
        flushInline();
        blocks.push(serializeBlockquote(child));
        break;
      case "ul":
      case "ol":
        flushInline();
        blocks.push(serializeList(child, 0));
        break;
      case "table": {
        flushInline();
        const table = serializeTable(child);
        if (table) blocks.push(table);
        break;
      }
      case "pre":
        flushInline();
        blocks.push(serializeCodeBlock(child));
        break;
      case "hr":
        flushInline();
        blocks.push("---");
        break;
      case "br":
        inlineBuffer.push("\n");
        break;
      case "strong":
      case "b":
      case "em":
      case "i":
      case "code":
      case "a":
        inlineBuffer.push(serializeInline(child));
        break;
      default:
        // Transparent wrapper: div, span, section, a Canvas card's
        // container, any unrecognized custom element -- recurse as if this
        // node weren't here at all.
        flushInline();
        blocks.push(...collectBlocks(child));
        break;
    }
  }
  flushInline();
  return blocks;
}

// The one entry point: a structured Markdown-ish rendering of everything
// collectBlocks() found, in visual order. Falls back to the old .innerText
// behavior only if the walk found nothing at all for a message that
// visibly has content -- e.g. a future ChatGPT redesign this file hasn't
// been taught about yet -- so this change can only add structure on top of
// today's extraction, never regress to losing a message outright.
function getMessageMarkdown(node) {
  const structured = collectBlocks(node).join("\n\n").trim();
  if (structured) return structured;
  return node.innerText ? node.innerText.trim() : "";
}

// Identifies which conversation is currently on screen, independent of
// any single DOM node's identity -- ChatGPT's own re-renders can swap the
// compose box's underlying node within the same conversation (see
// insertContextAbove's comment above), so controller.js can't use that
// node's identity to tell "the same conversation, DOM churned" apart from
// "navigated to a different conversation". Conversation URLs are
// path-based (``/c/<id>``); a fresh, not-yet-sent chat has no id segment
// yet. Query string / hash are deliberately excluded so they can't cause
// a false "different conversation" read.
function getConversationKey() {
  return location.pathname;
}

export const adapter = {
  findComposeBox,
  getComposeText,
  insertContextAbove,
  setComposeText,
  getAnchorForButton,
  getConversationTurns,
  getLastAssistantMessage,
  getLastUserMessage,
  getConversationKey,
};

// Exported for chatgpt.test.js only -- controller.js and the rest of the
// extension only ever go through the adapter object above.
export { getMessageMarkdown };

// Exported for tests/e2e/helpers/selectors.js only, as the single source of
// truth for these two selectors -- the E2E suite drives the real chatgpt.com
// DOM and needs to find the same compose box / message nodes this adapter
// does, and duplicating the literal strings a second time would let them
// drift silently after a ChatGPT redesign (fixed in one file, forgotten in
// the other). Neither this file's own logic above nor controller.js reads
// these two exports -- they still only ever go through the adapter/
// getMessageMarkdown exports, same as before.
export { COMPOSE_BOX_SELECTORS, MESSAGE_SELECTOR };
