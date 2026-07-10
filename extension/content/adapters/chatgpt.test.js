import { test } from "node:test";
import assert from "node:assert/strict";

// getLastAssistantMessage() (used below) reads document.querySelectorAll --
// stub it before importing, same approach background.test.js uses for the
// chrome global. A plain array supports the .length/index access the
// function needs, so it stands in for a NodeList without jsdom.
let queryResult = [];
globalThis.document = { querySelectorAll: () => queryResult };

const { getMessageMarkdown, adapter } = await import("./chatgpt.js");

// Minimal fake DOM -- no jsdom dependency. getMessageMarkdown() only ever
// touches nodeType/tagName/childNodes/getAttribute/textContent/innerText,
// so plain objects implementing that shape exercise the real code path
// exactly as a live chatgpt.com DOM node would.

function text(content) {
  return { nodeType: 3, textContent: content };
}

function el(tagName, attrs, children) {
  const node = {
    nodeType: 1,
    tagName,
    childNodes: children ?? [],
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(attrs ?? {}, name) ? attrs[name] : null;
    },
  };
  Object.defineProperty(node, "textContent", {
    get() {
      return node.childNodes.map((child) => child.textContent).join("");
    },
  });
  Object.defineProperty(node, "innerText", {
    get() {
      return node.childNodes.map((child) => child.innerText ?? child.textContent).join("");
    },
  });
  return node;
}

function message(children) {
  return el("div", {}, children);
}

test("a plain single-paragraph reply extracts unchanged", () => {
  const node = message([el("p", {}, [text("Hello world.")])]);
  assert.equal(getMessageMarkdown(node), "Hello world.");
});

test("regression: multi-paragraph plain-text chats are unchanged", () => {
  const node = message([
    el("p", {}, [text("First paragraph.")]),
    el("p", {}, [text("Second paragraph.")]),
  ]);
  assert.equal(getMessageMarkdown(node), "First paragraph.\n\nSecond paragraph.");
});

test("headings and paragraphs preserve readable text with no markdown decoration", () => {
  const node = message([
    el("h2", {}, [text("Section title")]),
    el("p", {}, [text("Body text.")]),
  ]);
  assert.equal(getMessageMarkdown(node), "Section title\n\nBody text.");
});

test("emphasis: bold and italic render as markdown", () => {
  const node = message([
    el("p", {}, [
      text("This is "),
      el("strong", {}, [text("bold")]),
      text(" and "),
      el("em", {}, [text("italic")]),
      text("."),
    ]),
  ]);
  assert.equal(getMessageMarkdown(node), "This is **bold** and _italic_.");
});

test("inline code renders with backticks, escaping an embedded backtick", () => {
  const plain = message([el("p", {}, [text("Run "), el("code", {}, [text("npm test")]), text(".")])]);
  assert.equal(getMessageMarkdown(plain), "Run `npm test`.");

  const withBacktick = message([el("p", {}, [el("code", {}, [text("a`b")])])]);
  assert.equal(getMessageMarkdown(withBacktick), "`` a`b ``");
});

test("citation and ordinary links both preserve their href", () => {
  const node = message([
    el("p", {}, [
      text("See "),
      el("a", { href: "https://example.com/paper" }, [text("[1]")]),
      text(" and "),
      el("a", { href: "https://example.com/docs" }, [text("the docs")]),
      text("."),
    ]),
  ]);
  assert.equal(
    getMessageMarkdown(node),
    "See [[1]](https://example.com/paper) and [the docs](https://example.com/docs)."
  );
});

test("a link with no visible text falls back to a bare href", () => {
  const node = message([el("p", {}, [el("a", { href: "https://example.com" }, [])])]);
  assert.equal(getMessageMarkdown(node), "<https://example.com>");
});

test("blockquotes are line-prefixed, including nested block content", () => {
  const node = message([
    el("blockquote", {}, [
      el("p", {}, [text("Quoted line one.")]),
      el("p", {}, [text("Quoted line two.")]),
    ]),
  ]);
  assert.equal(getMessageMarkdown(node), "> Quoted line one.\n>\n> Quoted line two.");
});

test("unordered and ordered lists render with markdown markers", () => {
  const node = message([
    el("ul", {}, [
      el("li", {}, [text("first")]),
      el("li", {}, [text("second")]),
    ]),
    el("ol", {}, [
      el("li", {}, [text("one")]),
      el("li", {}, [text("two")]),
    ]),
  ]);
  assert.equal(getMessageMarkdown(node), "- first\n- second\n\n1. one\n2. two");
});

test("nested lists are indented under their parent item", () => {
  const node = message([
    el("ul", {}, [
      el("li", {}, [
        text("parent"),
        el("ul", {}, [el("li", {}, [text("child")])]),
      ]),
    ]),
  ]);
  assert.equal(getMessageMarkdown(node), "- parent\n  - child");
});

test("tables render as GitHub-flavored markdown, escaping pipes and collapsing newlines in cells", () => {
  const node = message([
    el("table", {}, [
      el("thead", {}, [
        el("tr", {}, [el("th", {}, [text("Name")]), el("th", {}, [text("Value")])]),
      ]),
      el("tbody", {}, [
        el("tr", {}, [el("td", {}, [text("a | b")]), el("td", {}, [text("1")])]),
        el("tr", {}, [el("td", {}, [text("multi\nline")]), el("td", {}, [text("2")])]),
      ]),
    ]),
  ]);
  assert.equal(
    getMessageMarkdown(node),
    "| Name | Value |\n| --- | --- |\n| a \\| b | 1 |\n| multi line | 2 |"
  );
});

test("a table missing a <tbody> wrapper still extracts (rows directly under <table>)", () => {
  const node = message([
    el("table", {}, [
      el("tr", {}, [el("th", {}, [text("A")])]),
      el("tr", {}, [el("td", {}, [text("1")])]),
    ]),
  ]);
  assert.equal(getMessageMarkdown(node), "| A |\n| --- |\n| 1 |");
});

test("fenced code blocks preserve source text and detect a language class", () => {
  const node = message([
    el("pre", {}, [el("code", { class: "language-python" }, [text("print('hi')\n")])]),
  ]);
  assert.equal(getMessageMarkdown(node), "```python\nprint('hi')\n```");
});

test("a code block with no language class still fences cleanly", () => {
  const node = message([el("pre", {}, [el("code", {}, [text("plain text")])])]);
  assert.equal(getMessageMarkdown(node), "```\nplain text\n```");
});

test("a code block containing a triple-backtick widens its own fence", () => {
  const node = message([el("pre", {}, [el("code", {}, [text("```nested```")])])]);
  assert.equal(getMessageMarkdown(node), "````\n```nested```\n````");
});

test("a horizontal rule renders as its own block", () => {
  const node = message([
    el("p", {}, [text("Before.")]),
    el("hr", {}, []),
    el("p", {}, [text("After.")]),
  ]);
  assert.equal(getMessageMarkdown(node), "Before.\n\n---\n\nAfter.");
});

test("a <br> inside a paragraph becomes a single line break, not a new block", () => {
  const node = message([el("p", {}, [text("Line one"), el("br", {}, []), text("Line two")])]);
  assert.equal(getMessageMarkdown(node), "Line one\nLine two");
});

test("mixed inline formatting composes correctly in one paragraph", () => {
  const node = message([
    el("p", {}, [
      text("Start "),
      el("strong", {}, [text("bold "), el("em", {}, [text("and italic")])]),
      text(" then "),
      el("code", {}, [text("code()")]),
      text(" and "),
      el("a", { href: "https://x.test" }, [text("a link")]),
      text("."),
    ]),
  ]);
  assert.equal(
    getMessageMarkdown(node),
    "Start **bold _and italic_** then `code()` and [a link](https://x.test)."
  );
});

test("deeply nested wrapper divs are transparent -- content survives regardless of depth", () => {
  let node = el("p", {}, [text("Deep content.")]);
  for (let i = 0; i < 8; i++) {
    node = el("div", { class: `wrapper-${i}` }, [node]);
  }
  assert.equal(getMessageMarkdown(message([node])), "Deep content.");
});

test("an unrecognized custom element is treated as a transparent wrapper", () => {
  const node = message([el("chatgpt-weird-widget-v7", {}, [el("p", {}, [text("Still readable.")])])]);
  assert.equal(getMessageMarkdown(node), "Still readable.");
});

test("malformed HTML (stray text in a list, an empty table, an unwrapped item) does not throw and keeps what it can", () => {
  const node = message([
    el("ul", {}, [text("   "), el("li", {}, [text("real item")]), text("\n")]),
    el("table", {}, []),
    el("li", {}, [text("orphan item outside any list")]),
  ]);
  assert.doesNotThrow(() => getMessageMarkdown(node));
  const result = getMessageMarkdown(node);
  assert.match(result, /real item/);
  assert.match(result, /orphan item outside any list/);
});

test("an empty message produces empty content", () => {
  const node = message([]);
  assert.equal(getMessageMarkdown(node), "");
});

test("a whitespace-only message produces empty content", () => {
  const node = message([text("   \n  ")]);
  assert.equal(getMessageMarkdown(node), "");
});

test("falls back to innerText when the walk finds no recognized blocks at all", () => {
  // A table with only a <caption> and no rows: serializeTable() finds zero
  // <tr>s and yields nothing, so collectBlocks() has nothing to report even
  // though the message clearly has visible text.
  const node = message([el("table", {}, [el("caption", {}, [text("Important note")])])]);
  assert.equal(getMessageMarkdown(node), "Important note");
});

test("a long mixed-content article preserves every block type in visual order", () => {
  const node = message([
    el("h1", {}, [text("Title")]),
    el("p", {}, [text("Intro paragraph.")]),
    el("ul", {}, [el("li", {}, [text("point one")]), el("li", {}, [text("point two")])]),
    el("pre", {}, [el("code", { class: "language-js" }, [text("const x = 1;")])]),
    el("table", {}, [
      el("tr", {}, [el("th", {}, [text("Col")])]),
      el("tr", {}, [el("td", {}, [text("Val")])]),
    ]),
    el("blockquote", {}, [el("p", {}, [text("A quote.")])]),
    el("p", {}, [text("Conclusion.")]),
  ]);
  const result = getMessageMarkdown(node);
  const order = [
    "Title",
    "Intro paragraph.",
    "- point one",
    "```js",
    "| Col |",
    "> A quote.",
    "Conclusion.",
  ];
  let lastIndex = -1;
  for (const fragment of order) {
    const index = result.indexOf(fragment);
    assert.ok(index !== -1, `expected to find ${JSON.stringify(fragment)}`);
    assert.ok(index > lastIndex, `expected ${JSON.stringify(fragment)} to come after the previous block`);
    lastIndex = index;
  }
});

// --- getLastAssistantMessage() ----------------------------------------------
// Used by controller.js's updateRememberVisibility() on every raw DOM
// mutation to decide whether "Remember" should appear -- it must read only
// the last assistant turn, not walk/serialize the whole conversation (see
// this function's own comment in chatgpt.js for the performance reason).

function turnNode(role, children) {
  return el("div", { "data-message-author-role": role }, children);
}

test("getLastAssistantMessage returns the most recent assistant turn's content", () => {
  queryResult = [
    turnNode("user", [text("first question")]),
    turnNode("assistant", [text("first answer")]),
    turnNode("user", [text("second question")]),
    turnNode("assistant", [text("second answer")]),
  ];
  assert.equal(adapter.getLastAssistantMessage(), "second answer");
});

test("getLastAssistantMessage returns null when there is no assistant turn", () => {
  queryResult = [turnNode("user", [text("only a question")])];
  assert.equal(adapter.getLastAssistantMessage(), null);
});

test("getLastAssistantMessage returns null for an empty conversation", () => {
  queryResult = [];
  assert.equal(adapter.getLastAssistantMessage(), null);
});

test("getLastAssistantMessage skips a trailing empty assistant turn and falls back to an earlier one", () => {
  queryResult = [
    turnNode("assistant", [text("earlier real answer")]),
    turnNode("user", [text("a question")]),
    turnNode("assistant", [text("   ")]), // still streaming / whitespace-only
  ];
  assert.equal(adapter.getLastAssistantMessage(), "earlier real answer");
});
