/* =========================================================
   rich.js - draws the Tutor agent's answers: Markdown + maths + code.

   Libraries (in static/vendor, so it works offline):
     marked    -> Markdown to HTML
     DOMPurify -> removes anything unsafe from that HTML (the text comes from an AI model,
                  and this page can call Python, so no <script>, onclick=..., etc.)
     KaTeX     -> draws LaTeX maths: $...$, $$...$$, \(...\), \[...\]

   Maths is cut out BEFORE Markdown runs (Markdown would eat the _ and * inside it),
   replaced by a placeholder, and the KaTeX drawing is put back after DOMPurify.
   Code (```...``` and `...`) is left alone, so a $ in code stays a $.
   ========================================================= */

const MATH_OR_CODE = /(```[\s\S]*?(?:```|$)|`[^`\n]+`)|\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|\$(?!\s)([^$\n]+?)(?<!\s)\$(?!\d)/g;

// Markdown from a model may only make text formatting: no pictures, forms, buttons, SVG or styles
const MARKDOWN_PURIFY = {
  FORBID_TAGS: ["img", "picture", "video", "audio", "source", "form", "input", "button", "textarea", "select",
                "option", "svg", "math", "style", "iframe", "object", "embed", "link", "meta"],
  FORBID_ATTR: ["style", "srcset"],
};

function richAnswer(text) {
  const box = el("div", "rich");
  const maths = [];
  // A random marker per answer, so text the model writes can never look like a maths placeholder
  const mark = "MATH" + Math.random().toString(36).slice(2, 10) + "X";
  const marked_text = text.replace(MATH_OR_CODE, (all, code, display1, display2, inline1, inline2) => {
    if (code) return all;
    const display = display1 !== undefined || display2 !== undefined;
    const tex = display1 ?? display2 ?? inline1 ?? inline2;
    // KaTeX's drawing needs its inline styles, so it gets its own (default) DOMPurify pass
    maths.push(DOMPurify.sanitize(katex.renderToString(tex.trim(), { displayMode: display, throwOnError: false, output: "html" })));
    return `${mark}${maths.length - 1}${mark}`;
  });

  box.innerHTML = DOMPurify.sanitize(marked.parse(marked_text, { gfm: true, breaks: true }), MARKDOWN_PURIFY);

  // Put the maths back into TEXT only (never into an attribute such as a link's title)
  const found = new RegExp(`${mark}(\\d+)${mark}`, "g");
  const walker = document.createTreeWalker(box, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) if (walker.currentNode.nodeValue.includes(mark)) nodes.push(walker.currentNode);
  nodes.forEach((node) => {
    const parts = document.createDocumentFragment();
    let last = 0;
    for (const m of node.nodeValue.matchAll(found)) {
      parts.append(node.nodeValue.slice(last, m.index));
      if (maths[+m[1]] === undefined) parts.append(m[0]);  // not one of ours: keep it as text
      else {
        const t = document.createElement("template");
        t.innerHTML = maths[+m[1]];
        parts.append(t.content);
      }
      last = m.index + m[0].length;
    }
    parts.append(node.nodeValue.slice(last));
    node.replaceWith(parts);
  });

  // Code blocks: a small header with the language and a Copy button
  box.querySelectorAll("pre > code").forEach((code) => {
    const pre = code.parentElement;
    const lang = (code.className.match(/language-([\w+#-]+)/) || [])[1] || "code";
    const head = el("div", "code-head");
    const copy = withIcon("button", "code-copy", "copy", "Copy", 12);
    copy.onclick = () => copyText(code.textContent);
    head.append(el("span", "", lang), copy);
    const wrap = el("div", "code-block");
    pre.replaceWith(wrap);
    wrap.append(head, pre);
  });
  // Links from a model are not opened (open_link only opens links Aura found itself): show them as text
  box.querySelectorAll("a").forEach((a) => {
    const span = el("span", "rich-link", a.textContent);
    span.title = a.getAttribute("href") || "";
    a.replaceWith(span);
  });
  return box;
}
