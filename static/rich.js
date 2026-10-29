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

function richAnswer(text) {
  const box = el("div", "rich");
  const maths = [];
  const marked_text = text.replace(MATH_OR_CODE, (all, code, display1, display2, inline1, inline2) => {
    if (code) return all;
    const display = display1 !== undefined || display2 !== undefined;
    const tex = display1 ?? display2 ?? inline1 ?? inline2;
    maths.push(katex.renderToString(tex.trim(), { displayMode: display, throwOnError: false, output: "html" }));
    return `@@MATH${maths.length - 1}@@`;
  });

  const html = DOMPurify.sanitize(marked.parse(marked_text, { gfm: true, breaks: true }));
  box.innerHTML = html.replace(/@@MATH(\d+)@@/g, (_, i) => maths[+i]);

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
