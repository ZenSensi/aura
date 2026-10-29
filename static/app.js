/* =========================================================
   Aura - browser code (plain JavaScript, no framework)

   This page runs inside the desktop window opened by desktop.py.
   It calls Python directly through  window.pywebview.api.<function>().

   Flow:
   1. User sends a request  -> api.run(text)
   2. While Python runs the agents, it calls onAgentEvent(event) for every
      event (planning, plan, step_start, step_done, step_failed, error, done)
   3. handleEvent() draws each event the moment it arrives,
      so the user watches every agent work live.
   ========================================================= */

// desc = the short line shown in the sidebar's Agents list
const AGENTS = {
  orchestrator: { icon: "orchestrator", name: "Orchestrator", color: "var(--orchestrator)", desc: "Plans steps" },
  writer:       { icon: "writer",     name: "Writer",   color: "var(--writer)",   desc: "Letters & emails" },
  system:       { icon: "system",     name: "System",   color: "var(--system)",   desc: "Opens apps" },
  mailer:       { icon: "mail",       name: "Mailer",   color: "var(--mailer)",   desc: "Sends via Gmail" },
  inbox:        { icon: "inbox",      name: "Inbox",    color: "var(--inbox)",    desc: "Reads Gmail" },
  files:        { icon: "search",     name: "Files",    color: "var(--files)",    desc: "Finds files" },
  news:         { icon: "news",       name: "News",     color: "var(--news)",     desc: "News & markets" },
  calc:         { icon: "calculator", name: "Calc",     color: "var(--calc)",     desc: "Exact maths" },
  whatsapp:     { icon: "chat",       name: "WhatsApp", color: "var(--whatsapp)", desc: "Saved contacts" },
  web:          { icon: "globe",      name: "Web",      color: "var(--web)",      desc: "Web + sources" },
  reader:       { icon: "paperclip",  name: "Reader",   color: "var(--reader)",   desc: "Files & images" },
  chat:         { icon: "sparkles",   name: "Chat",     color: "var(--chat)",     desc: "Talks with you" },
  tutor:        { icon: "cap",        name: "Tutor",    color: "var(--tutor)",    desc: "Maths, science, code" },
  automate:     { icon: "clock",      name: "Automate", color: "var(--automate)", desc: "Routines & reminders" },
  organiser:    { icon: "tidy",       name: "Organiser", color: "var(--organiser)", desc: "Tidies folders" },
  music:        { icon: "music",      name: "Music",    color: "var(--music)",    desc: "Spotify & YT Music" },
  notion:       { icon: "file",       name: "Notion",   color: "var(--notion)",   desc: "Your Notion pages" },
};

document.getElementById("agentCount").textContent = Object.keys(AGENTS).length;  // header pill
document.getElementById("agentTotal").textContent = "· " + Object.keys(AGENTS).length;  // sidebar heading

// The Python functions from desktop.py (class Api)
const api = () => window.pywebview.api;

// Shortcuts to page elements
const $ = (id) => document.getElementById(id);
const chat = $("chat"), messages = $("messages"), empty = $("empty");
const input = $("input"), sendBtn = $("send");

let busy = false; // true while a request is running
let attached = null;  // + menu: {name, kind, size} of the picked file (Python keeps the real path)
let webOn = false;    // + menu: "Web search" turned on for the next message
let pinned = null;    // Agents list: the agent clicked to talk to directly (stays until removed)
let agentsOff = new Set();  // Agents list: agents switched off with their light

/* ---------- Small helpers ---------- */

// Create an element. Text is always set with textContent (never innerHTML),
// so nothing a model writes can inject HTML into the page.
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// Element with an icon in front of its text, e.g. a button "[copy icon] Copy"
function withIcon(tag, className, iconName, text, size) {
  const node = el(tag, className);
  node.append(icon(iconName, size), el("span", "", text));
  return node;
}

// True when Windows' "Animation effects" is off: skip the JavaScript animations too
const calmMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function scrollDown() {
  chat.scrollTop = chat.scrollHeight;
}

function toast(text) {
  let t = document.querySelector(".toast");
  if (!t) { t = el("div", "toast"); document.body.appendChild(t); }
  t.textContent = text;
  t.classList.add("show");
  clearTimeout(t.timer);
  t.timer = setTimeout(() => t.classList.remove("show"), 1800);
}

// "1.2s · openai/gpt-oss-120b" (+ a warning if the backup model was used)
function modelNote(info) {
  const meta = el("div", "step-meta", `${info.seconds}s · ${info.model}`);
  if (info.fallback_used) meta.appendChild(withIcon("span", "fallback-tag", "warn", "backup model used", 12));
  return meta;
}

/* ---------- Building blocks of an assistant message ---------- */

// One card in the timeline: icon, agent name, title, status pill, task, body
function makeStep(agent, title, task) {
  const a = AGENTS[agent];
  const root = el("div", "step running");
  root.style.setProperty("--c", a.color);

  const icon = el("div", "step-icon");
  icon.appendChild(window.icon(a.icon, 17));
  const main = el("div", "step-main");
  const head = el("div", "step-head");
  const titleEl = el("span", "step-title", title);
  const status = el("span", "step-status", "Working…");
  head.append(el("span", "step-agent", a.name), titleEl, status);
  main.appendChild(head);
  if (task) main.appendChild(el("div", "step-task", task));
  const body = el("div", "step-body");
  main.appendChild(body);

  root.append(icon, main);
  return { root, titleEl, status, body, main };
}

// Light up the sidebar card of the agent that is working (null = none),
// and show it in the top bar pill: "Writer working… 4s" / "11 agents ready"
let runStart = 0, pillTimer = null, pillAgent = null;
function setWorking(agent) {
  document.querySelectorAll(".agent-row").forEach((row) => {
    const on = row.dataset.agent === agent;
    row.classList.toggle("working", on);
    row.querySelector(".agent-light")?.setAttribute("aria-label",
      on ? "Working" : agentsOff.has(row.dataset.agent) ? "Off" : "Standby");
  });
  if (agent) pillAgent = agent;   // between steps, keep showing the last agent until the request ends
  drawPill();
}

function drawPill() {
  const pill = $("statusPill"), text = $("statusText");
  if (!busy) {
    clearInterval(pillTimer);
    pillTimer = null;
    pillAgent = null;
    pill.classList.remove("busy");
    pill.style.removeProperty("--c");
    text.textContent = `${Object.keys(AGENTS).length} agents ready`;
    return;
  }
  if (!pillTimer) pillTimer = setInterval(drawPill, 1000);
  const a = AGENTS[pillAgent || "orchestrator"];
  pill.classList.add("busy");
  pill.style.setProperty("--c", a.color);
  const secs = Math.floor((Date.now() - runStart) / 1000);
  text.replaceChildren(el("b", "", a.name), ` working… ${secs}s`);
}

// Top bar title: the first request of this chat, or "New chat"
function setTitle(text) {
  const t = $("chatTitle");
  t.textContent = text || "New chat";
  t.title = text || "";
}

function setStatus(step, state, label) {
  step.root.classList.remove("running");
  step.root.classList.add(state);       // done | refused | error
  step.status.textContent = label;
}

function resultLine(iconName, text, color) {
  const line = withIcon("div", "result-line", iconName, text, 15);
  line.style.setProperty("--rc", color);
  return line;
}

// Card showing the Writer's document with Copy / Download / Open buttons
function docCard(result) {
  const card = el("div", "doc");

  const head = el("div", "doc-head");
  const tag = el("span", "tag", result.type);
  tag.style.setProperty("--c", "var(--writer)");
  head.append(tag, withIcon("span", "doc-file", "file", result.file, 13));

  const text = el("pre", "doc-text", result.content);

  const actions = el("div", "doc-actions");
  const copy = withIcon("button", "btn primary", "copy", "Copy", 14);
  copy.onclick = () => copyText(result.content);
  const save = withIcon("button", "btn", "save", "Save as…", 14);
  save.onclick = async () => {
    if (await api().save_file(result.file)) toast("Saved");
  };
  const open = withIcon("button", "btn", "open", "Open file", 14);
  open.onclick = async () => {
    toast((await api().open_file(result.file)) ? "Opened" : "Could not open file");
  };
  const expand = withIcon("button", "btn", "expand", "Expand", 14);
  expand.onclick = () => {
    card.classList.toggle("expanded");
    expand.lastChild.textContent = card.classList.contains("expanded") ? "Collapse" : "Expand";
  };

  actions.append(copy, save, open, expand);
  card.append(head, text, actions);
  return card;
}

// Inbox agent result: a short summary, then one row per email (newest first)
function emailList(result) {
  const box = el("div", "mail-box");
  if (result.summary) box.appendChild(withIcon("div", "mail-summary", "info", result.summary, 15));
  const list = el("div", "mail-list");
  result.emails.forEach((m) => {
    const row = el("div", "mail-row" + (m.unread ? " unread" : ""));
    const top = el("div", "mail-top");
    top.append(el("span", "mail-from", m.from_name), el("span", "mail-date", m.date));
    row.append(top, el("div", "mail-subject", m.subject), el("div", "mail-snippet", m.snippet));
    row.title = m.from_address;
    list.appendChild(row);
  });
  box.appendChild(list);
  return box;
}

// ---------- News agent: market tiles with charts + headline sections ----------

// A small line chart drawn as SVG from a list of prices (no chart library)
function sparkline(points, up) {
  const W = 220, H = 56, P = 3;
  const min = Math.min(...points), max = Math.max(...points), span = max - min || 1;
  const xy = points.map((v, i) => [
    P + (i / (points.length - 1)) * (W - 2 * P),
    P + (1 - (v - min) / span) * (H - 2 * P),
  ]);
  const line = xy.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
  const id = "g" + Math.random().toString(36).slice(2, 8);  // unique gradient id
  const color = up ? "var(--ok)" : "var(--err)";
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.classList.add("spark");
  // Built only from numbers computed above, so innerHTML is safe here
  svg.innerHTML =
    `<defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1">` +
    `<stop offset="0" stop-color="${color}" stop-opacity=".35"/><stop offset="1" stop-color="${color}" stop-opacity="0"/>` +
    `</linearGradient></defs>` +
    `<path d="${line} L${W - P} ${H} L${P} ${H} Z" fill="url(#${id})"/>` +
    `<path d="${line}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>`;
  return svg;
}

function marketTile(m) {
  const up = m.change >= 0;
  const tile = el("div", "market " + (up ? "up" : "down"));
  const top = el("div", "market-top");
  top.append(el("span", "market-name", m.name),
             el("span", "market-change", `${up ? "▲" : "▼"} ${Math.abs(m.change_pct).toFixed(2)}%`));
  const price = el("div", "market-price", m.price.toLocaleString("en-IN", { maximumFractionDigits: 2 }));
  price.appendChild(el("span", "market-cur", " " + m.currency));
  // Badge = today's change. Chart colour = the whole month's change (shown under the chart).
  const first = m.points[0], last = m.points[m.points.length - 1];
  const month = ((last - first) / first) * 100;
  tile.append(top, price, sparkline(m.points, last >= first),
              el("div", "market-foot", `1 month ${month >= 0 ? "+" : ""}${month.toFixed(1)}%`));
  tile.title = m.symbol;
  return tile;
}

function newsView(r) {
  const box = el("div", "news");
  if (r.markets.length) {
    const grid = el("div", "markets");
    r.markets.forEach((m) => grid.appendChild(marketTile(m)));
    box.appendChild(grid);
    if (r.market_summary) box.appendChild(withIcon("div", "news-brief", "chart", r.market_summary, 15));
  }
  r.sections.forEach((s) => {
    const sec = el("div", "news-section");
    sec.appendChild(el("div", "news-title", s.title));
    if (s.summary) sec.appendChild(el("div", "news-summary", s.summary));
    const list = el("div", "headlines");
    s.items.forEach((it) => {
      const row = el("button", "headline");
      row.append(el("span", "headline-text", it.title),
                 el("span", "headline-meta", [it.source, it.time].filter(Boolean).join(" · ")));
      row.onclick = async () => { if (!(await api().open_link(it.link))) toast("Could not open link"); };
      list.appendChild(row);
    });
    sec.appendChild(list);
    box.appendChild(sec);
  });
  return box;
}

// Files agent result: one row per file with Open / Show in folder buttons
function fileList(result) {
  const list = el("div", "file-list");
  result.files.forEach((f) => {
    const row = el("div", "file-row");
    const text = el("div", "file-text");
    text.append(el("div", "file-name", f.name), el("div", "file-meta", `${f.folder} · ${f.size} · ${f.modified}`));
    text.title = f.path;
    const actions = el("div", "file-actions");
    if (!f.runnable) {  // programs and scripts are never opened from here
      const open = withIcon("button", "btn", "open", "Open", 13);
      open.onclick = async () => toast((await api().open_found(f.path)) ? "Opened" : "Could not open");
      actions.appendChild(open);
    }
    const show = withIcon("button", "btn", "folder", "Show in folder", 13);
    show.onclick = async () => { if (!(await api().show_found(f.path))) toast("File not found"); };
    actions.appendChild(show);
    row.append(icon("file", 18), text, actions);
    list.appendChild(row);
  });
  return list;
}

// Web and Reader agents: the answer with a Copy button, and (web) numbered source links
function answerView(r, agent) {
  const box = el("div", "answer");
  box.style.setProperty("--c", AGENTS[agent].color);
  box.appendChild(agent === "tutor" ? richAnswer(r.answer) : el("p", "answer-text", r.answer));
  if (r.sources && r.sources.length) {
    const list = el("div", "sources");
    r.sources.forEach((s, i) => {
      const row = el("button", "source");
      const text = el("span", "source-text");
      text.append(el("span", "source-title", s.title), el("span", "source-site", s.site));
      row.append(el("span", "source-num", String(i + 1)), text);
      row.title = s.url;
      row.onclick = async () => { if (!(await api().open_link(s.url))) toast("Could not open link"); };
      list.appendChild(row);
    });
    box.appendChild(list);
  }
  const actions = el("div", "answer-actions");
  const copy = withIcon("button", "btn", "copy", "Copy answer", 14);
  copy.onclick = () => copyText(r.answer);
  actions.appendChild(copy);
  box.appendChild(actions);
  return box;
}

// Chat or Tutor agent on its own: the answer as a chat message, with a small Copy link and the model under it.
// The Tutor's answer is Markdown with maths and code (rich.js draws it).
function chatReply(r) {
  const box = el("div", "chat-msg");
  box.appendChild(r.agent === "tutor" ? richAnswer(r.answer) : el("p", "chat-reply", r.answer));
  const foot = el("div", "chat-foot");
  const copy = withIcon("button", "chat-copy", "copy", "Copy", 12);
  copy.onclick = () => copyText(r.answer);
  foot.append(copy, modelNote(r));
  box.appendChild(foot);
  return box;
}

// Organiser result: one small chip per folder the files went into ("PDFs 12")
function groupChips(groups) {
  const row = el("div", "group-chips");
  groups.forEach((g) => {
    const chip = withIcon("span", "group-chip", "folder", g.folder, 12);
    chip.appendChild(el("b", "", String(g.count)));
    row.appendChild(chip);
  });
  return row;
}

// "Every day at 8:00 AM · next tomorrow at 8:00 AM" (a one-time routine: just its time)
function routineWhen(r) {
  if (r.paused) return `${r.when} · paused`;
  return r.schedule.repeat === "once" ? r.when : `${r.when} · next ${r.next}`;
}

// The icon tile in front of a routine / reminder / workflow row: only the icon inside, so it sits
// exactly in the middle, tinted in that kind's colour (style.css .auto-icon.<kind>)
const AUTO_KIND = { repeat: "routine", bell: "reminder", zap: "workflow" };
function autoTile(iconName) {
  const tile = el("span", "auto-icon " + (AUTO_KIND[iconName] || ""));
  tile.appendChild(icon(iconName, 16));
  return tile;
}

// Automate agent's "show my routines": the same rows as the Automations panel
function savedView(saved) {
  const box = el("div", "saved-view");
  const rows = [
    ...saved.routines.map((r) => ["repeat", r.name, routineWhen(r)]),
    ...saved.reminders.map((r) => ["bell", r.text, r.when]),
    ...saved.workflows.map((w) => ["zap", w.name, w.task]),
  ];
  if (!rows.length) box.appendChild(el("div", "auto-empty", "Nothing saved yet."));
  rows.forEach(([iconName, title, sub]) => {
    const row = el("div", "saved-row");
    const text = el("div", "auto-text");
    text.append(el("div", "auto-name", title), el("div", "auto-sub", sub));
    row.append(autoTile(iconName), text);
    box.appendChild(row);
  });
  const open = withIcon("button", "btn", "clock", "Open Automations", 13);
  open.onclick = openAutomations;
  box.appendChild(open);
  return box;
}

// Copy text; falls back to the older method if the clipboard API is blocked
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = el("textarea");
    area.value = text;
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
  toast("Copied to clipboard");
}

function finalNote(body, icon, text, color) {
  const note = el("div", "final");
  note.style.setProperty("--fc", color);
  note.append(window.icon(icon, 17), el("span", "", text));
  body.appendChild(note);
}

/* ---------- Draw one event from the server ---------- */

function handleEvent(ev, ctx) {
  // Which agent is busy after this event? (step_start -> that agent, planning -> orchestrator)
  setWorking(ev.type === "planning" ? "orchestrator" : ev.type === "step_start" ? ev.agent : null);

  switch (ev.type) {
    case "planning": {
      ctx.plan = makeStep("orchestrator", "is planning your request");
      ctx.plan.status.textContent = "Thinking…";
      ctx.timeline.appendChild(ctx.plan.root);
      break;
    }

    case "plan": {
      const n = ev.steps.length;
      if (n === 0 && ev.reply) {  // greeting / small talk / "can't do that, but…": the Orchestrator answers itself
        setStatus(ctx.plan, "done", "Replied");
        ctx.plan.titleEl.textContent = "says";
        ctx.plan.body.appendChild(el("p", "chat-reply", ev.reply));
      } else if (n === 0) {
        setStatus(ctx.plan, "done", "No steps");
        ctx.plan.titleEl.textContent = "found nothing I can do";
      } else if (n === 1 && (ev.steps[0].agent === "chat" || ev.steps[0].agent === "tutor")) {
        // Just a conversation or one problem: no plan card, Aura answers like a normal chat message
        ctx.chatOnly = ev.steps[0].agent;
        ctx.plan.root.remove();
        break;
      } else {
        setStatus(ctx.plan, "done", "Planned");
        ctx.plan.titleEl.textContent = ev.workflow ? `ran your workflow "${ev.workflow}" · ${n} step${n === 1 ? "" : "s"}`
                                                   : `made a ${n}-step plan`;
        const list = el("div", "plan");
        ev.steps.forEach((s, i) => {
          const row = el("div", "plan-row");
          const tag = withIcon("span", "tag", AGENTS[s.agent].icon, AGENTS[s.agent].name, 12);
          tag.style.setProperty("--c", AGENTS[s.agent].color);
          row.append(el("span", "plan-num", String(i + 1)), tag, el("span", "", s.task));
          list.appendChild(row);
        });
        ctx.plan.body.appendChild(list);
      }
      ctx.plan.main.appendChild(modelNote(ev));
      break;
    }

    case "step_start": {
      if (ctx.chatOnly) {  // "Aura is typing" dots instead of a step card
        ctx.typing = el("div", "typing");
        ctx.typing.append(el("span"), el("span"), el("span"));
        if (ctx.chatOnly === "tutor") {  // thinking takes a few seconds: say what is happening
          ctx.typing.classList.add("solving");
          ctx.typing.appendChild(el("em", "", "Tutor is working it out…"));
        }
        ctx.timeline.appendChild(ctx.typing);
        break;
      }
      ctx.step = makeStep(ev.agent, `· step ${ev.number} of ${ev.total}`, ev.task);
      ctx.timeline.appendChild(ctx.step.root);
      break;
    }

    case "step_done": {
      const r = ev.result;
      if (ctx.chatOnly) {
        ctx.typing.replaceWith(chatReply(r));
        break;
      }
      if (ev.agent === "writer") {
        setStatus(ctx.step, "done", "Done");
        ctx.step.titleEl.textContent = `wrote ${r.type === "email" ? "an" : "a"} ${r.type}`;
        ctx.step.body.appendChild(docCard(r));
      } else if (ev.agent === "news") {
        setStatus(ctx.step, "done", "Done");
        ctx.step.titleEl.textContent = r.message.replace("Briefing ready: ", "briefing: ").replace(/\.$/, "");
        ctx.step.body.appendChild(newsView(r));
      } else if (ev.agent === "files" && r.files.length) {
        setStatus(ctx.step, "done", "Done");
        ctx.step.titleEl.textContent = r.message.split(" (")[0].replace(/\.$/, "").replace("Found", "found");
        ctx.step.body.appendChild(fileList(r));
      } else if (ev.agent === "chat") {  // chat as one step of a bigger plan
        setStatus(ctx.step, "done", "Done");
        ctx.step.titleEl.textContent = "replied";
        ctx.step.body.appendChild(answerView(r, "chat"));
      } else if (ev.agent === "tutor") {  // tutor as one step of a bigger plan
        setStatus(ctx.step, "done", "Done");
        ctx.step.titleEl.textContent = "solved it";
        ctx.step.body.appendChild(answerView(r, "tutor"));
      } else if (ev.agent === "web" || ev.agent === "reader") {
        setStatus(ctx.step, "done", "Done");
        ctx.step.titleEl.textContent = ev.agent === "web"
          ? `searched the web · ${r.sources.length} source${r.sources.length === 1 ? "" : "s"}`
          : `read ${r.file}`;
        ctx.step.body.appendChild(answerView(r, ev.agent));
      } else if (ev.agent === "notion") {
        setStatus(ctx.step, "done", "Done");
        ctx.step.titleEl.textContent = r.action.action.replace("notion-", "") + " · Notion";
        ctx.step.body.appendChild(answerView(r, "notion"));
      } else if (ev.agent === "inbox" && r.emails.length) {
        setStatus(ctx.step, "done", "Done");
        ctx.step.titleEl.textContent = `read ${r.emails.length} email${r.emails.length === 1 ? "" : "s"}`;
        ctx.step.body.appendChild(emailList(r));
      } else {
        const refused = r.action.action === "refuse";
        setStatus(ctx.step, refused ? "refused" : "done", refused ? "Refused" : "Done");
        ctx.step.body.appendChild(resultLine(refused ? "ban" : "check", r.message,
                                             refused ? "var(--warn)" : "var(--ok)"));
        if (r.groups && r.groups.length) ctx.step.body.appendChild(groupChips(r.groups));
        if (r.saved) ctx.step.body.appendChild(savedView(r.saved));
      }
      if (ev.agent === "automate" || ev.agent === "organiser") loadAutomations();  // the sidebar count
      ctx.step.main.appendChild(modelNote(r));
      break;
    }

    case "step_failed": {
      if (ctx.chatOnly) {
        ctx.typing.replaceWith(resultLine("error", ev.message, "var(--err)"));
        break;
      }
      setStatus(ctx.step, "error", "Failed");
      ctx.step.body.appendChild(resultLine("error", ev.message, "var(--err)"));
      break;
    }

    case "error": {
      setStatus(ctx.plan, "error", "Failed");
      ctx.plan.body.appendChild(resultLine("error", ev.message, "var(--err)"));
      break;
    }

    case "done": {
      if (ctx.chatOnly && ev.ok) {
        // a conversation reply needs no "All done!"
      } else if (ev.nothing_to_do && ev.reply) {
        // the reply in the Orchestrator's card is the whole answer: no extra note
      } else if (ev.nothing_to_do) {
        finalNote(ctx.body, "info", "I can write letters and emails, open apps and sites, read your Gmail, " +
                  "find files, get the news, solve maths, science and coding problems, search the web, read attached files, send WhatsApp messages, " +
                  "set reminders and routines, tidy your folders and play music, but not that one. Try one of the suggestions!", "var(--accent)");
      } else if (ev.ok) {
        finalNote(ctx.body, "check", "All done!", "var(--ok)");
      } else {
        finalNote(ctx.body, "warn", "I stopped because a step failed. Please try again.", "var(--warn)");
      }
      break;
    }
  }
  scrollDown();
}

/* ---------- Send a request and read the live stream ---------- */

// options (from the chat box): { file: {name, kind, size} | null, web: true/false }
// or, when a routine runs by itself: { routine: "Morning news" }
async function send(text, options = {}) {
  text = text.trim();
  if (!text && options.file) text = "Summarise this file";  // a file on its own
  if (!text || busy) return;
  if (!messages.childElementCount) setTitle(text);  // first request names the chat
  runStart = Date.now();
  setBusy(true);
  setWorking("orchestrator");
  empty.style.display = "none";
  chat.classList.remove("is-empty");
  $("quick").hidden = false;  // show the quick actions once the chat has started

  // User bubble, with the picked agent / attached file / "Web search" above it
  const userMsg = el("div", "msg user");
  if (options.file || options.web || options.routine || options.agent) {
    const chips = el("div", "bubble-chips");
    if (options.routine) chips.appendChild(withIcon("span", "in-chip routine", "repeat", "Routine · " + options.routine, 14));
    if (options.agent) chips.appendChild(agentChip(options.agent));
    if (options.file) chips.appendChild(fileChip(options.file));
    if (options.web) chips.appendChild(webChip());
    userMsg.appendChild(chips);
  }
  userMsg.appendChild(el("div", "bubble", text));
  messages.appendChild(userMsg);

  // Assistant message with an empty timeline
  const botMsg = el("div", "msg assistant");
  const body = el("div", "msg-body");
  const timeline = el("div", "timeline");
  body.appendChild(timeline);
  const avatar = el("div", "avatar");
  avatar.appendChild(icon("bot", 18));
  botMsg.append(avatar, body);
  messages.appendChild(botMsg);
  scrollDown();

  // Python calls onAgentEvent() for every event of THIS request
  const ctx = { timeline, body, plan: null, step: null };
  window.onAgentEvent = (event) => handleEvent(event, ctx);

  try {
    // returns when all agents have finished
    await api().run(text, { web: !!options.web, attach: !!options.file, routine: options.routine || "",
                            agent: options.agent || "" });
  } catch (err) {
    finalNote(body, "error", "Something went wrong: " + err.message, "var(--err)");
  } finally {
    setBusy(false);
    setWorking(null);
    loadRecent();
    loadUsage();
    if (options.routine) loadAutomations();  // a one-time routine is finished now
    scrollDown();
  }
}

// The Scheduler (Python) starts a routine: run it like a typed request.
// Returns false while another request is running, so Python tries again a few seconds later.
window.runRoutine = (task, name) => {
  if (busy) return false;
  closeAutomations();
  send(task, { routine: name });
  return true;
};

function setBusy(value) {
  busy = value;
  sendBtn.disabled = value;
  setPlaceholder();
  updateComposer();
  if (!value) input.focus();
}

// Chat box: glow + bright send button when there is something to send,
// and a character counter once the text gets close to the 1000 limit
function updateComposer() {
  const len = input.value.length, max = input.maxLength;
  $("composer").classList.toggle("ready", !busy && (!!input.value.trim() || !!attached));
  const count = $("charCount");
  count.hidden = len < max * 0.8;
  count.textContent = `${len} / ${max}`;
  count.classList.toggle("full", len >= max);
}

// Morning / afternoon / evening greeting above the welcome title
function setGreeting(name) {
  const h = new Date().getHours();
  const part = h < 5 ? "Working late" : h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
  const day = new Date().toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long" });
  $("greeting").textContent = `${part}${name ? ", " + name : ""} · ${day}`;
}

function setPlaceholder() {
  input.placeholder = busy ? "Agents are working…"
    : attached ? "Ask about this file… (or just send to get a summary)"
    : webOn ? "Search the web…"
    : pinned ? `Message ${AGENTS[pinned].name}…`
    : "Ask me to write something or open an app…";
}

/* ---------- Sidebar: agents, allowlist, stats ---------- */

async function loadInfo() {
  try {
    const info = await api().get_info();
    agentsOff = new Set(info.off || []);
    const list = $("agentList");
    list.innerHTML = "";
    for (const [key, a] of Object.entries(AGENTS)) {
      // Click a row = talk to that agent directly (the Orchestrator's row = back to automatic)
      const row = el("div", "agent-row");
      row.dataset.agent = key;
      row.style.setProperty("--c", a.color);
      row.tabIndex = 0;
      row.setAttribute("role", "button");
      row.onclick = () => setPinned(key);
      row.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setPinned(key); } };
      const icon = el("div", "agent-icon");
      icon.appendChild(window.icon(a.icon, 15));
      // Requests done + last used ("12 · 3h"), from the logs (loadUsage)
      const use = el("span", "agent-use");
      // Status light on the right: red = on standby, green = working right now (setWorking), grey = off.
      // Clicking it switches the agent on/off (not the Orchestrator: it plans everything)
      const light = el("span", "agent-light");
      light.setAttribute("aria-label", "Standby");
      let lamp = light;
      if (key !== "orchestrator") {
        lamp = el("button", "agent-switch");
        lamp.type = "button";
        lamp.appendChild(light);
        lamp.onclick = (e) => { e.stopPropagation(); switchAgent(key, agentsOff.has(key)); };
        lamp.onkeydown = (e) => e.stopPropagation();  // Enter on the light must not also pick the agent
      }
      row.append(icon, el("span", "agent-name", a.name), use, lamp);
      // Hover: what it does + which AI model (and backup) it uses
      const m = info.agents[key];
      row.title = `${a.name}: ${a.desc}
` + (m ? `Model: ${m.model}` + (m.fallback ? `
Backup: ${m.fallback}` : "")
                                                : "No AI model")
        + (key === "orchestrator" ? "\nClick to let the Orchestrator pick agents again"
                                  : `\nClick to talk to ${a.name} directly`);
      list.appendChild(row);
    }
    drawAgentStates();
    drawUsage(info.usage || {});
    sender = info.sender || "";
    userName = info.user || "";
    setGreeting(userName);
  } catch { /* sidebar stays with placeholders */ }
}

// Each row: dimmed + grey light when switched off, highlighted when picked
function drawAgentStates() {
  document.querySelectorAll(".agent-row").forEach((row) => {
    const key = row.dataset.agent, off = agentsOff.has(key), name = AGENTS[key].name;
    row.classList.toggle("off", off);
    row.classList.toggle("pinned", key === pinned || (!pinned && key === "orchestrator"));
    row.setAttribute("aria-pressed", String(key === pinned));
    if (!row.classList.contains("working")) row.querySelector(".agent-light").setAttribute("aria-label", off ? "Off" : "Standby");
    const lamp = row.querySelector(".agent-switch");
    if (lamp) lamp.title = lamp.ariaLabel = off ? `Switch ${name} on` : `Switch ${name} off`;
  });
}

// The light: switch an agent on or off (Python remembers it in agents_off.json)
async function switchAgent(key, on) {
  try { agentsOff = new Set(await api().set_agent(key, on)); } catch { return; }
  if (!on && pinned === key) setPinned(null);
  drawAgentStates();
  toast(`${AGENTS[key].name} is ${on ? "on" : "off"}` + (on ? "" : " — Aura won't use it until you switch it back on"));
}

// Pick the agent the next messages go to (null / the Orchestrator / the same one again = automatic)
function setPinned(key) {
  if (key && agentsOff.has(key)) return toast(`${AGENTS[key].name} is off. Click its light to switch it on.`);
  pinned = key && key !== "orchestrator" && key !== pinned ? key : null;
  drawAgentStates();
  drawChips();
  input.focus();
}

// Requests done + when last used, under each agent's name
async function loadUsage() {
  try { drawUsage(await api().get_usage()); } catch { /* keep the old numbers */ }
}
function drawUsage(usage) {
  document.querySelectorAll(".agent-row").forEach((row) => {
    const u = usage[row.dataset.agent], use = row.querySelector(".agent-use");
    use.textContent = u ? `${u.count} · ${shortAgo(u.last)}` : "";
    use.title = u ? `Used in ${u.count} request${u.count === 1 ? "" : "s"} · last ${timeAgo(u.last)}` : "";
  });
}

// "2026-10-19T08:41:48" -> "now" / "5m" / "3h" / "2d" / "12 Oct" (short, for the Agents list)
function shortAgo(stamp) {
  const then = new Date(stamp), mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return "now";
  if (mins < 60) return `${mins}m`;
  if (mins < 24 * 60) return `${Math.round(mins / 60)}h`;
  if (mins < 7 * 24 * 60) return `${Math.round(mins / 1440)}d`;
  return then.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

// "2026-10-16T22:59:08" -> "just now" / "5m ago" / "3h ago" / "yesterday" / "12 Oct"
function timeAgo(stamp) {
  const then = new Date(stamp), mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  if (mins < 24 * 60 && then.getDate() === new Date().getDate()) return `${Math.round(mins / 60)}h ago`;
  if (mins < 48 * 60) return "yesterday";
  return then.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

// Sidebar "Recent tasks": the latest requests from the logs; click one to run it again
async function loadRecent() {
  let tasks;
  try { tasks = await api().get_recent(); } catch { return; }
  const list = $("recentList");
  list.replaceChildren();
  if (!tasks.length) {
    list.appendChild(el("div", "recent-empty", "Your requests will show up here."));
    return;
  }
  const STATUS = { ok: ["check", "var(--ok)", "Done"], failed: ["error", "var(--err)", "A step failed"],
                   nothing: ["info", "var(--faint)", "Nothing to do"] };
  tasks.forEach((t) => {
    const [iconName, color, label] = STATUS[t.status];
    const row = el("button", "recent-item");
    row.style.setProperty("--rc", color);
    const agents = el("span", "recent-agents");
    t.agents.slice(0, 3).forEach((a) => {
      if (!AGENTS[a]) return;
      const dot = icon(AGENTS[a].icon, 12);
      dot.style.color = AGENTS[a].color;
      agents.appendChild(dot);
    });
    const meta = el("span", "recent-meta");
    meta.append(agents, el("span", "", timeAgo(t.time)));
    const text = el("span", "recent-text");
    text.append(el("span", "recent-title", t.text), meta);
    row.append(withIcon("span", "recent-status", iconName, "", 14), text);
    row.title = `${t.text}\n${label} · ${new Date(t.time).toLocaleString("en-IN")}\nClick to run it again`;
    row.onclick = () => { if (!busy) send(t.text); };
    list.appendChild(row);
  });
}

/* ---------- Theme (dark / light), remembered in this browser ---------- */

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $("themeLabel").textContent = theme === "dark" ? "Dark mode" : "Light mode";
  document.querySelector(".theme-icon").replaceChildren(icon(theme === "dark" ? "moon" : "sun", 16));
  if (window.pywebview && window.pywebview.api) api().set_theme(theme);  // the window's title bar too
}

function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem("ma-theme"); } catch { /* storage blocked */ }
  const prefersLight = window.matchMedia("(prefers-color-scheme: light)").matches;
  applyTheme(saved || (prefersLight ? "light" : "dark"));
  $("themeToggle").onclick = (e) => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    const change = () => {
      applyTheme(next);
      try { localStorage.setItem("ma-theme", next); } catch { /* ignore */ }
    };
    if (!document.startViewTransition || calmMotion()) return change();
    // The new theme spreads out as a growing circle from the button (motion.css)
    const r = e.currentTarget.getBoundingClientRect();
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    const radius = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
    document.startViewTransition(change).ready.then(() => {
      document.documentElement.animate(
        { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] },
        { duration: 650, easing: "cubic-bezier(.22, 1, .36, 1)", pseudoElement: "::view-transition-new(root)" },
      );
    });
  };
}

/* ---------- Wire up the page ---------- */

$("composer").addEventListener("submit", (e) => {
  e.preventDefault();
  if (busy || (!input.value.trim() && !attached)) return;
  // The "Play" example fills "Play  on ": if no platform was typed after "on ", drop it (Spotify is the default)
  const text = input.value.replace(/^(\s*play\s.*?)\s+on\s+$/i, "$1");
  const options = { file: attached, web: webOn, agent: pinned };  // the picked agent stays for the next message
  input.value = "";
  autoSize();
  attached = null;  // Python forgets the file once this message uses it
  setWeb(false);    // both chips are for one message
  send(text, options);
});

/* ---------- + button: attach a file / turn on web search ---------- */

const plusBtn = $("plus"), plusMenu = $("plusMenu");

function fileChip(file) {
  const chip = el("span", "in-chip file");
  chip.append(icon(file.kind === "image" ? "image" : "file", 14), el("span", "chip-name", file.name),
              el("span", "chip-size", file.size));
  chip.title = file.name;
  return chip;
}
function webChip() {
  const chip = el("span", "in-chip web");
  chip.append(icon("globe", 14), el("span", "chip-name", "Web search"));
  return chip;
}
function agentChip(key) {
  const a = AGENTS[key], chip = el("span", "in-chip agent");
  chip.style.setProperty("--c", a.color);
  chip.append(icon(a.icon, 14), el("span", "chip-name", a.name));
  chip.title = `Only ${a.name} works on this`;
  return chip;
}
function withRemove(chip, label, onRemove) {
  const x = el("button", "chip-x");
  x.type = "button";
  x.title = x.ariaLabel = label;
  x.appendChild(icon("x", 13));
  x.onclick = () => { onRemove(); input.focus(); };
  chip.appendChild(x);
  return chip;
}

// Redraw the chips above the text box
function drawChips() {
  const row = $("chipsRow");
  row.replaceChildren();
  if (pinned) row.appendChild(withRemove(agentChip(pinned), "Let the Orchestrator pick agents", () => setPinned(null)));
  if (attached) row.appendChild(withRemove(fileChip(attached), "Remove file", () => {
    attached = null;
    api().clear_attachment();
    drawChips();
  }));
  if (webOn) row.appendChild(withRemove(webChip(), "Turn off web search", () => setWeb(false)));
  row.hidden = !row.childElementCount;
  setPlaceholder();
  updateComposer();
}

function setWeb(on) {
  webOn = on;
  $("webBtn").setAttribute("aria-checked", String(on));
  drawChips();
}

function setMenu(open) {
  plusMenu.hidden = !open;
  plusBtn.setAttribute("aria-expanded", String(open));
  if (open) $("attachBtn").focus();
}

plusBtn.onclick = () => setMenu(plusMenu.hidden);
document.addEventListener("click", (e) => {
  if (!plusMenu.hidden && !e.target.closest(".plus-wrap")) setMenu(false);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !plusMenu.hidden) { setMenu(false); plusBtn.focus(); }
});

$("appBtn").onclick = () => { setMenu(false); openForm("app"); };

$("attachBtn").onclick = async () => {
  setMenu(false);
  const file = await api().pick_attachment();  // Windows' own "Open" dialog
  if (!file) return;                           // cancelled
  if (file.error) return toast(file.error);
  attached = file;
  drawChips();
  input.focus();
};
$("webBtn").onclick = () => {
  setMenu(false);
  setWeb(!webOn);
  input.focus();
};

// Enter = send, Shift+Enter = new line
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    $("composer").requestSubmit();
  }
});

// Grow the text box as the user types
function autoSize() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 160) + "px";
}
input.addEventListener("input", () => { autoSize(); updateComposer(); });
setupVoice($("mic"), input);  // voice typing (voice.js)

// Put text in the chat box for the user to finish; the cursor goes to data-caret (or the end)
function fillInput(text, caret) {
  if (busy) return;
  input.value = text;
  input.dispatchEvent(new Event("input"));
  input.focus();
  const at = caret === undefined ? text.length : Number(caret);
  input.setSelectionRange(at, at);
}

// Cards, quick buttons and examples: data-form opens the pop-up form, data-fill fills the
// chat box, data-web turns on Web search, data-text is sent directly
document.querySelectorAll(".suggestion, .quick-btn, .example").forEach((btn) =>
  btn.addEventListener("click", () => {
    const d = btn.dataset;
    if (d.form) openForm(d.form);
    else if (d.fill !== undefined) fillInput(d.fill, d.caret);
    else if (d.web !== undefined) { setWeb(true); input.focus(); }
    else send(d.text);
  })
);

/* ---------- Pop-up form: "Write & send an email" and "Open an app" ---------- */

const FORMS = {
  email: { icon: "mail", title: "Write & send an email", askTopic: true },
  app:   { icon: "app",  title: "Open an app", askApp: true },
};
let appNames = [];  // installed apps, loaded from Python for the suggestions
const EMAIL_OK = /^[^\s@]+@[^\s@]+\.[a-z]{2,}$/i;
let formKind = null, nameTouched = false, lookupTimer = null, sender = "", userName = "";

function openForm(kind) {
  if (busy) return;
  formKind = kind;
  const f = FORMS[kind];
  $("modalIcon").replaceChildren(icon(f.icon, 20));
  $("modalTitle").textContent = f.title;
  $("modalSub").textContent = f.askApp ? "Any app installed on this PC"
    : sender ? `Sent from ${sender} as soon as it's written` : "Sent as soon as it's written";
  // Show only the fields this form needs
  $("topicField").hidden = !f.askTopic;
  $("appField").hidden = !f.askApp;
  $("emailField").hidden = $("nameField").hidden = !!f.askApp;
  ["fTopic", "fEmail", "fName", "fApp"].forEach((id) => ($(id).value = ""));
  $("appList").replaceChildren();
  $("modalSubmit").replaceChildren(icon(f.askApp ? "open" : "send", 14), el("span", "", f.askApp ? "Open" : "Write & send"));
  if (f.askApp && !appNames.length) api().get_apps().then((names) => { appNames = names; showApps(); });
  setHint("", "");
  showError("");
  nameTouched = false;
  $("modalScrim").hidden = false;
  (f.askApp ? $("fApp") : f.askTopic ? $("fTopic") : $("fEmail")).focus();
  if (f.askApp) showApps();
}

// Does the typed text fit this app name? Every typed word must be the start of a word
// in the name, or of its initials: "vs code" / "vsc" -> Visual Studio Code
function appMatches(name, typed) {
  const words = name.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  const initials = words.map((w) => w[0]).join("");
  return typed.split(/\s+/).every((t) =>
    words.some((w) => w.startsWith(t)) || initials.startsWith(t));
}

// Shown first when nothing is typed (only the ones installed on this PC)
const POPULAR_APPS = ["google chrome", "visual studio code", "whatsapp", "spotify", "microsoft edge", "notepad",
  "calculator", "file explorer", "word", "excel", "powerpoint", "discord", "telegram", "paint", "vlc media player"];
// Start-menu extras that aren't really apps: hidden unless the user types that word
const JUNK_APP = /\b(help|uninstall|readme|read me|documentation|manual|release notes|license|website)\b/i;
const TILE_COLORS = ["--system", "--writer", "--orchestrator", "--mailer", "--inbox", "--files",
  "--news", "--calc", "--whatsapp", "--web", "--reader", "--chat", "--tutor", "--automate", "--organiser", "--music"];
let appIndex = -1;  // highlighted suggestion (arrow keys), -1 = none

// The same app always gets the same tile colour
function tileColor(name) {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return `var(${TILE_COLORS[h % TILE_COLORS.length]})`;
}

// "Spotify" with typed "spo" -> <mark>Spo</mark>tify
function nameWithMatch(name, typed) {
  const span = el("span", "app-name");
  const at = typed ? name.toLowerCase().indexOf(typed) : -1;
  if (at < 0) { span.textContent = name; return span; }
  span.append(name.slice(0, at), el("mark", "", name.slice(at, at + typed.length)), name.slice(at + typed.length));
  return span;
}

// Suggestions under the app box: popular apps when empty; else names starting with the
// typed text first, then other matches
function showApps() {
  const typed = $("fApp").value.trim().toLowerCase();
  const usable = appNames.filter((n) => !JUNK_APP.test(n) || (typed && JUNK_APP.test(typed)));
  let names;
  if (!typed) {
    const popular = POPULAR_APPS.map((p) => usable.find((n) => n.toLowerCase() === p)).filter(Boolean);
    names = [...popular, ...usable.filter((n) => !popular.includes(n))];
  } else {
    const starts = usable.filter((n) => n.toLowerCase().startsWith(typed));
    const contains = usable.filter((n) => !starts.includes(n) &&
      (n.toLowerCase().includes(typed) || appMatches(n, typed)));
    names = [...starts, ...contains];
  }
  $("appListLabel").textContent = !appNames.length ? "Loading apps…"
    : !typed ? `Suggested · ${usable.length} apps on this PC`
    : names.length ? `${names.length} match${names.length === 1 ? "" : "es"}` : "No app with that name. Enter tries it anyway.";
  appIndex = typed && names.length ? 0 : -1;  // Enter opens the best match
  const list = $("appList");
  list.replaceChildren();
  names.slice(0, 6).forEach((name, i) => {
    const option = el("button", "app-option");
    option.type = "button";
    option.setAttribute("role", "option");
    option.dataset.name = name;
    const tile = el("span", "app-tile", name.replace(/[^a-z0-9]/gi, "").charAt(0).toUpperCase() || "?");
    tile.style.setProperty("--c", tileColor(name));
    const go = el("span", "app-go");
    go.appendChild(icon("open", 13));
    option.append(tile, nameWithMatch(name, typed), go);
    option.onmouseenter = () => highlightApp(i);
    option.onclick = () => { $("fApp").value = name; $("modal").requestSubmit(); };
    list.appendChild(option);
  });
  highlightApp(appIndex);
}

function highlightApp(i) {
  appIndex = i;
  $("appList").querySelectorAll(".app-option").forEach((o, n) => {
    o.classList.toggle("active", n === i);
    o.setAttribute("aria-selected", String(n === i));
  });
}

$("fApp").addEventListener("input", showApps);
// Arrow keys move through the suggestions; Enter opens the highlighted one
$("fApp").addEventListener("keydown", (e) => {
  const options = $("appList").querySelectorAll(".app-option");
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    if (!options.length) return;
    const step = e.key === "ArrowDown" ? 1 : -1;
    highlightApp((appIndex + step + options.length) % options.length);
    options[appIndex].scrollIntoView({ block: "nearest" });
  } else if (e.key === "Enter" && appIndex >= 0 && options[appIndex]) {
    e.preventDefault();
    $("fApp").value = options[appIndex].dataset.name;
    $("modal").requestSubmit();
  }
});

function closeForm() {
  $("modalScrim").hidden = true;
  formKind = null;
  input.focus();
}

function setHint(text, kind) {
  const hint = $("nameHint");
  hint.textContent = text;
  hint.className = "field-hint " + kind;  // found | guess | missing | busy
}

function showError(text) {
  $("modalError").textContent = text;
}

// When an email address is typed, look up the person's name (after a short pause)
$("fEmail").addEventListener("input", () => {
  clearTimeout(lookupTimer);
  const address = $("fEmail").value.trim();
  if (!EMAIL_OK.test(address)) { setHint("", ""); return; }
  setHint("looking it up…", "busy");
  lookupTimer = setTimeout(async () => {
    const found = await api().lookup_name(address);
    if ($("fEmail").value.trim() !== address) return;  // the address changed meanwhile
    if (!nameTouched) $("fName").value = found.name;
    if (found.source === "gmail") setHint("found in your Gmail", "found");
    else if (found.source === "guess") setHint("guessed from the address, please check", "guess");
    else setHint("not found, please type it", "missing");
  }, 600);
});
$("fName").addEventListener("input", () => (nameTouched = true));

$("modal").addEventListener("submit", (e) => {
  e.preventDefault();
  if (formKind === "app") {
    const app = $("fApp").value.trim();
    if (!app) return showError("Type the name of an app.");
    closeForm();
    send(`Open ${app}`);  // the System agent checks it is installed and not blocked
    return;
  }
  const topic = $("fTopic").value.trim();
  const address = $("fEmail").value.trim();
  const name = $("fName").value.trim();
  if (formKind === "email" && !topic) return showError("Please write what the email is about.");
  if (!EMAIL_OK.test(address)) return showError("Please enter a valid email address.");

  const greeting = name ? `Address them as ${name}.` : "Use a polite greeting.";
  const request = `Write an email to ${name || "the recipient"} about: ${topic}. ${greeting} Then send it to ${address}.`;
  closeForm();
  send(request);
});

$("modalCancel").onclick = closeForm;
$("modalScrim").addEventListener("click", (e) => { if (e.target === $("modalScrim")) closeForm(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && formKind) closeForm(); });

$("newChat").onclick = async () => {
  if (busy) return;
  if (messages.childElementCount && !calmMotion()) {  // fade the old chat out first
    await messages.animate([{ opacity: 1 }, { opacity: 0, transform: "translateY(-10px)", filter: "blur(4px)" }],
                           { duration: 220, easing: "ease-in" }).finished;
  }
  messages.innerHTML = "";
  api().new_chat();  // Python forgets the conversation too
  empty.style.display = "";
  chat.classList.add("is-empty");
  setTitle("");
  setGreeting(userName);
  $("quick").hidden = true;
  closeMenu();
  input.focus();
};

// Mobile sidebar
const sidebar = $("sidebar"), scrim = $("scrim");
function closeMenu() { sidebar.classList.remove("open"); scrim.classList.remove("show"); }
$("menuBtn").onclick = () => { sidebar.classList.add("open"); scrim.classList.add("show"); };
scrim.onclick = closeMenu;


/* ---------- Automations panel (automations.py): routines, reminders, workflows ---------- */

// Clicking an example fills the chat box, so the user can change it before sending
const AUTO_EXAMPLES = {
  routines: ["Every day at 8 AM give me today's tech news", "Every Monday at 9 AM summarise my unread emails",
             "Every Sunday at 7 PM organise my Downloads folder"],
  reminders: ["Remind me in 20 minutes to drink water", "Remind me tomorrow at 10 AM to submit the assignment"],
  workflows: ["Save \"open VS Code, Spotify and Gmail\" as study mode"],
};
const AUTO_SECTIONS = [
  ["routines", "repeat", "Routines", "Aura runs these requests on a schedule"],
  ["reminders", "bell", "Reminders", "A notification at the time, with Snooze"],
  ["workflows", "zap", "Workflows", "Type the name (e.g. \"study mode\") to run all its steps"],
];

async function loadAutomations() {
  let data;
  try { data = await api().get_automations(); } catch { return; }
  const total = data.routines.filter((r) => !r.paused).length + data.reminders.length;
  $("autoCount").hidden = !total;
  $("autoCount").textContent = total;
  if (!$("autoScrim").hidden) drawAutomations(data);
  return data;
}
window.loadAutomations = loadAutomations;  // Python calls it when a reminder fires

function autoButton(iconName, title, onClick, extra = "") {
  const b = el("button", "auto-btn " + extra);
  b.type = "button";
  b.title = b.ariaLabel = title;
  b.appendChild(icon(iconName, 14));
  b.onclick = onClick;
  return b;
}

function closeAutomations() { $("autoScrim").hidden = true; }

function runFromPanel(task, name) {
  closeAutomations();
  send(task, name ? { routine: name } : {});
}

function drawAutomations(data) {
  const body = $("autoBody");
  body.replaceChildren();
  AUTO_SECTIONS.forEach(([kind, iconName, title, hint]) => {
    const sec = el("div", "auto-section");
    const head = el("div", "auto-head");
    head.append(withIcon("span", "auto-title " + AUTO_KIND[iconName], iconName, title, 15), el("span", "auto-hint", hint));
    sec.appendChild(head);
    const items = data[kind];
    if (!items.length) {
      const empty = el("div", "auto-empty", "None yet. Try: ");
      AUTO_EXAMPLES[kind].forEach((text) => {
        const ex = el("button", "auto-example", text);
        ex.type = "button";
        ex.onclick = () => { closeAutomations(); fillInput(text); };
        empty.appendChild(ex);
      });
      sec.appendChild(empty);
    }
    items.forEach((item) => {
      const row = el("div", "auto-row" + (item.paused ? " paused" : ""));
      const text = el("div", "auto-text");
      const name = kind === "reminders" ? item.text : item.name;
      const sub = kind === "routines" ? routineWhen(item)
                : kind === "reminders" ? item.when : item.task;
      text.append(el("div", "auto-name", name), el("div", "auto-sub", sub));
      if (kind === "routines") text.appendChild(el("div", "auto-task", "Runs: " + item.task));
      text.title = item.task || name;
      const actions = el("div", "auto-actions");
      if (kind === "routines") {
        actions.append(
          autoButton("play", "Run now", () => runFromPanel(item.task, item.name)),
          autoButton(item.paused ? "play" : "pause", item.paused ? "Resume" : "Pause", async () => {
            await api().automation_pause(item.id, !item.paused);
            loadAutomations();
          }, "toggle"),
        );
      }
      if (kind === "workflows") actions.appendChild(autoButton("play", "Run now", () => runFromPanel(item.name)));
      actions.appendChild(autoButton("trash", "Delete", async () => {
        await api().automation_delete(item.id);
        toast("Deleted");
        loadAutomations();
      }, "danger"));
      row.append(autoTile(iconName), text, actions);
      sec.appendChild(row);
    });
    body.appendChild(sec);
  });

  // Folder tidy-up: buttons that send the request (the Organiser agent does it in the chat)
  const tidy = el("div", "auto-section");
  const head = el("div", "auto-head");
  head.append(withIcon("span", "auto-title", "tidy", "Tidy folders", 15),
              el("span", "auto-hint", "Moves loose files into folders by type. Never deletes; can be undone."));
  const buttons = el("div", "auto-tidy");
  [["tidy", "Tidy Downloads", "Organise my Downloads folder"],
   ["tidy", "Tidy Desktop", "Organise my Desktop folder"],
   ["search", "Preview Downloads", "Preview organising my Downloads folder"],
   ["undo", "Undo last tidy-up", "Undo the last tidy-up"]].forEach(([iconName, label, request]) => {
    const b = withIcon("button", "btn", iconName, label, 13);
    b.type = "button";
    b.onclick = () => runFromPanel(request);
    buttons.appendChild(b);
  });
  tidy.append(head, buttons);
  body.appendChild(tidy);

  $("startupRow").hidden = !data.can_startup;
  $("startupBox").setAttribute("aria-checked", String(!!data.startup));
}

async function openAutomations() {
  if (busy) return toast("Wait for the agents to finish");
  closeMenu();
  $("autoScrim").hidden = false;
  const data = await loadAutomations();
  if (data) drawAutomations(data);
  $("autoClose").focus();
}

$("openAuto").onclick = openAutomations;
$("autoClose").onclick = closeAutomations;
$("autoScrim").addEventListener("click", (e) => { if (e.target === $("autoScrim")) closeAutomations(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("autoScrim").hidden) closeAutomations(); });
$("startupBox").onclick = async (e) => {
  const box = e.currentTarget;
  const on = await api().set_startup(box.getAttribute("aria-checked") !== "true");
  box.setAttribute("aria-checked", String(on));
  toast(on ? "Aura will start with Windows" : "Aura won't start with Windows");
};

/* ---------- Settings: email, connections, Aura ----------
   Python never sends keys or passwords here, only whether each one is saved.
   An empty key box keeps the saved key. */

const SET_KEYS = [  // [.env name, label, what it's for, icon]
  ["GROQ_API_KEY", "Groq", "Main AI model for most agents", "zap"],
  ["GROQ_API_KEY_SYSTEM", "Groq (second key)", "Planner, System, Files, Inbox: their own rate limit", "zap"],
  ["GEMINI_API_KEY", "Gemini", "Backup model when Groq is busy", "zap"],
  ["OPENROUTER_API_KEY", "OpenRouter", "Last backup, and the Reader's picture model", "zap"],
  ["TAVILY_API_KEY", "Tavily", "Web search", "globe"],
  ["NOTION_TOKEN", "Notion", "Integration secret from notion.so/profile/integrations", "file"],
];
const GOOGLE_TEXT = { connected: "Connected: reminders also go to your phone",
                      signed_out: "Not connected", not_setup: "Needs google_client.json next to Aura (see google_sync.py)" };

function setInput(key, type, value, placeholder) {
  const input = el("input", "set-input");
  input.type = type;
  input.dataset.key = key;
  input.value = input.dataset.orig = value;
  input.placeholder = placeholder;
  input.spellcheck = false;
  input.maxLength = 300;
  return input;
}

function setField(label, input) {
  const field = el("label", "field");
  field.append(el("span", "", label), input);
  return field;
}

function setSection(iconName, title, hint) {
  const sec = el("div", "auto-section");
  const head = el("div", "auto-head");
  head.append(withIcon("span", "auto-title", iconName, title, 15), el("span", "auto-hint", hint));
  sec.appendChild(head);
  return sec;
}

function setRow(iconName, name, status, ok, ...extra) {
  const row = el("div", "auto-row");
  const text = el("div", "auto-text");
  text.append(el("div", "auto-name", name), el("div", "auto-sub set-status" + (ok ? " ok" : ""), status));
  row.append(autoTile(iconName), text, ...extra);
  return row;
}

function setButton(label, onClick) {
  const b = el("button", "btn", label);
  b.type = "button";
  b.onclick = onClick;
  return b;
}

function drawSettings(s) {
  const body = $("setBody");
  body.replaceChildren();
  const saved = (key) => s.set[key];

  // Email
  const mailOk = s.email && saved("GMAIL_APP_PASSWORD");
  const mail = setSection("mail", "Email", mailOk ? "Sending and reading as " + s.email : "Not set up: Mailer and Inbox need this");
  const pair = el("div", "set-pair");
  pair.append(
    setField("Gmail address", setInput("GMAIL_ADDRESS", "email", s.email, "you@gmail.com")),
    setField("App password", setInput("GMAIL_APP_PASSWORD", "password", "",
      saved("GMAIL_APP_PASSWORD") ? "Saved. Type to replace" : "Google Account › Security › App passwords")),
  );
  mail.append(pair, setField("Your name (signs emails, greets you)", setInput("USER_NAME", "text", s.name, "Your full name")));
  body.appendChild(mail);

  // Connections
  const conn = setSection("lock", "Connections", "Paste a new key to replace the saved one");
  SET_KEYS.forEach(([key, label, what, iconName]) => {
    conn.appendChild(setRow(iconName, label, saved(key) ? "Connected · " + what : "Not set · " + what, saved(key),
      setInput(key, "password", "", saved(key) ? "••••••••" : "Paste key")));
  });
  const google = setButton(s.google === "connected" ? "Disconnect" : "Connect", async () => {
    $("setError").textContent = "";
    if (s.google === "connected") return drawSettings(await api().google_disconnect());
    google.disabled = true;
    google.textContent = "Finish signing in in your browser…";
    const r = await api().google_connect();
    if (r.error) { $("setError").textContent = r.error; google.disabled = false; google.textContent = "Connect"; return; }
    toast("Connected to Google");
    drawSettings(r);
  });
  google.hidden = s.google === "not_setup";
  conn.appendChild(setRow("clock", "Google Tasks & Calendar", GOOGLE_TEXT[s.google], s.google === "connected", google));
  const people = s.contacts < 0 ? "contacts.json has a mistake: open it to fix"
               : `${s.contacts} saved contact${s.contacts === 1 ? "" : "s"} · uses WhatsApp on this PC`;
  conn.appendChild(setRow("chat", "WhatsApp", people, s.contacts > 0, setButton("Edit contacts", () => api().open_contacts())));
  body.appendChild(conn);

  // Aura
  const aura = setSection("settings", "Aura", "");
  const dark = document.documentElement.dataset.theme === "dark";
  aura.appendChild(setRow(dark ? "moon" : "sun", "Theme", dark ? "Dark" : "Light", false,
    setButton(dark ? "Use light" : "Use dark", () => { $("themeToggle").click(); setTimeout(() => drawSettings(s), 50); })));
  if (s.can_startup) {
    const sw = el("button", "switch");
    sw.type = "button";
    sw.setAttribute("role", "switch");
    sw.setAttribute("aria-checked", String(!!s.startup));
    sw.ariaLabel = "Start Aura when Windows starts";
    sw.onclick = async () => {
      s.startup = await api().set_startup(!s.startup);
      sw.setAttribute("aria-checked", String(s.startup));
      $("startupBox").setAttribute("aria-checked", String(s.startup));  // the same switch in Automations
    };
    aura.appendChild(setRow("clock", "Start with Windows", "Waits in the dock, so routines run on time", false, sw));
  }
  aura.appendChild(setRow("folder", "Outputs folder", "Letters, emails and files Aura makes", false,
    setButton("Open", () => api().open_outputs_folder())));
  body.appendChild(aura);
}

function closeSettings() { $("setScrim").hidden = true; }

async function openSettings() {
  if (busy) return toast("Wait for the agents to finish");
  closeMenu();
  $("setError").textContent = "";
  drawSettings(await api().get_settings());
  $("setScrim").hidden = false;
  $("setBody").scrollTop = 0;  // start at Email
  $("setClose").focus();
}

$("setForm").onsubmit = async (e) => {
  e.preventDefault();
  const changes = {};
  $("setBody").querySelectorAll("[data-key]").forEach((input) => {
    if (input.value.trim() !== input.dataset.orig) changes[input.dataset.key] = input.value;
  });
  if (!Object.keys(changes).length) return closeSettings();
  const r = await api().save_settings(changes);
  if (r.error) { $("setError").textContent = r.error; return; }
  closeSettings();
  toast("Settings saved");
  loadInfo();  // the greeting and the email sender may have changed
};
$("openSettings").onclick = openSettings;
$("setClose").onclick = $("setCancel").onclick = closeSettings;
$("setScrim").addEventListener("click", (e) => { if (e.target === $("setScrim")) closeSettings(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("setScrim").hidden) closeSettings(); });

// Start: the theme can be set at once, but Python functions are only
// available after pywebview says it is ready.
initTheme();
setGreeting("");
updateComposer();
window.addEventListener("pywebviewready", () => {
  api().set_theme(document.documentElement.dataset.theme);  // colour the title bar to match
  loadInfo();
  loadRecent();
  loadAutomations();
});
input.focus();
