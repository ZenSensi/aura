/* =========================================================
   Aura dock - page code (plain JavaScript).

   Python (dock.py) shows this small window when Aura is minimised.
   - Click the pill  -> api.dock_expand(), the input appears.
   - Type a task     -> api.dock_run(text): the MAIN window runs it,
                        so it is also saved in the main chat.
   - Every agent event is also sent here as onDockEvent(event),
     and drawn as a compact list of steps with short results.
   - The window grows to fit the results (api.dock_fit(height)).
   - Now Playing: Python sends setMedia(info) twice a second while the dock
     shows (nowplaying.py), and setMediaArt(url) when the song changes.
   ========================================================= */

const AGENTS = {
  orchestrator: { icon: "orchestrator", name: "Orchestrator", color: "var(--orchestrator)" },
  writer:       { icon: "writer", name: "Writer", color: "var(--writer)" },
  system:       { icon: "system", name: "System", color: "var(--system)" },
  mailer:       { icon: "mail",   name: "Mailer", color: "var(--mailer)" },
  inbox:        { icon: "inbox",  name: "Inbox",  color: "var(--inbox)" },
  files:        { icon: "search", name: "Files",  color: "var(--files)" },
  news:         { icon: "news",   name: "News",   color: "var(--news)" },
  calc:         { icon: "calculator", name: "Calc", color: "var(--calc)" },
  whatsapp:     { icon: "chat",   name: "WhatsApp", color: "var(--whatsapp)" },
  web:          { icon: "globe",  name: "Web",    color: "var(--web)" },
  reader:       { icon: "paperclip", name: "Reader", color: "var(--reader)" },
  chat:         { icon: "sparkles", name: "Chat",   color: "var(--chat)" },
  tutor:        { icon: "cap",      name: "Tutor",  color: "var(--tutor)" },
  automate:     { icon: "clock",    name: "Automate", color: "var(--automate)" },
  organiser:    { icon: "tidy",     name: "Organiser", color: "var(--organiser)" },
  music:        { icon: "music",    name: "Music",  color: "var(--music)" },
  notion:       { icon: "file",     name: "Notion", color: "var(--notion)" },
};

const api = () => window.pywebview.api;
const $ = (id) => document.getElementById(id);
const results = $("dockResults"), input = $("dockInput");
let current = null;  // the run being drawn: { box, rows: {stepNumber: row}, plan }

// Text is always set with textContent, never innerHTML
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function button(label, iconName, onClick) {
  const b = el("button", "btn");
  b.append(icon(iconName, 12), el("span", "", label));
  b.onclick = onClick;
  return b;
}

/* ---------- Glass: Python sends a blurred picture of the screen behind the dock,
   plus a sharp one for the thin ring outside the rounded edge (see dock.css) ---------- */
function setGlass(blurred, sharp) {
  const root = document.documentElement.style;
  root.setProperty("--glass", blurred ? `url("${blurred}")` : "none");
  root.setProperty("--behind", sharp ? `url("${sharp}")` : "none");
}

/* ---------- Modes: pill <-> open panel ----------
   CSS animates the body between the two sizes (dock.css). Python cuts the
   window to fit (dock.py set_shape): it is told FIRST, so when the dock grows
   the cut-out is already big enough for every frame of the animation. */
const PILL_W = 296, PILL_H = 40, MIN_H = 72, RING = 2, RADIUS = 20;  // body sizes (dock.css)
// The card fills the window's real width/height (minus the ring), up to 636 x 596
const CARD_W = Math.min(636, innerWidth - 2 * RING), MAX_H = Math.min(596, innerHeight - 2 * RING);
document.body.style.setProperty("--card-w", CARD_W + "px");
let openHeight = MIN_H;

// Height the open card needs for its content (limited to the window)
function contentHeight() {
  const card = $("nowCard").offsetHeight ? $("nowCard").offsetHeight + 12 : 0;  // + its bottom margin
  const h = $("dockForm").offsetHeight + card + (results.childElementCount ? results.scrollHeight : 0);
  return Math.max(MIN_H, Math.min(MAX_H, Math.ceil(h)));
}
// Sizes are the body's; Python's cut-out also includes the ring around it
const shapeTo = (w, h) => api().dock_shape(w + 2 * RING, h + 2 * RING, RADIUS + RING);

// instant=true (Python, when the dock appears): no animation
async function setDockMode(mode, instant = false) {
  const body = document.body;
  body.classList.toggle("instant", instant);
  if (mode === "open") {
    openHeight = contentHeight();
    body.style.setProperty("--open-h", openHeight + "px");
    await shapeTo(CARD_W, openHeight);
  } else {
    shapeTo(PILL_W, PILL_H);  // Python waits for the shrink animation before cutting
  }
  body.classList.toggle("pill", mode === "pill");
  body.classList.toggle("open", mode === "open");
  if (instant) {
    void body.offsetWidth;  // apply the new size now (no animation), then switch animations back on
    body.classList.remove("instant");
  }
  if (mode === "open") setTimeout(() => input.focus(), 60);
}

// Results came in: grow (or shrink) the open card to fit them, animated
async function fit() {
  if (!document.body.classList.contains("open")) return;
  const h = contentHeight();
  if (Math.abs(h - openHeight) < 2) return;
  openHeight = h;
  await shapeTo(CARD_W, h);
  document.body.style.setProperty("--open-h", h + "px");
}
$("pill").onclick = async () => { await api().dock_expand(); setDockMode("open"); };
$("dockClose").onclick = async () => { await api().dock_collapse(); setDockMode("pill"); };
$("dockOpen").onclick = () => api().dock_open_main();
setupVoice($("dockMic"), input);  // voice typing (voice.js)
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("dockClose").click(); });
// A click anywhere outside the dock takes focus away from its window: go back to the pill
window.addEventListener("blur", () => { if (document.body.classList.contains("open")) $("dockClose").click(); });

$("dockForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  startRun(text);
  input.value = "";
  if (!(await api().dock_run(text))) {
    current.box.appendChild(final("warn", "Aura is busy with another task. Try again in a moment.", "var(--warn)"));
    current = null;
    fit();
  }
});

/* ---------- Drawing one run ---------- */
function startRun(text) {
  const box = el("div", "run");
  box.appendChild(el("div", "run-ask", text));
  results.appendChild(box);
  current = { box, rows: {}, plan: null };
  // keep only the last 4 runs so the dock stays small
  while (results.childElementCount > 4) results.firstChild.remove();
  setBusy(true);
}

function makeRow(agent, task) {
  const a = AGENTS[agent] || AGENTS.system;
  const row = el("div", "drow running");
  row.style.setProperty("--c", a.color);
  const ic = el("div", "drow-icon");
  ic.appendChild(icon(a.icon, 15));
  const main = el("div", "drow-main");
  const head = el("div", "drow-head");
  const status = el("span", "drow-status", "Working…");
  head.append(el("span", "drow-agent", a.name), el("span", "drow-task", task || ""), status);
  const body = el("div", "drow-body");
  main.append(head, body);
  row.append(ic, main);
  current.box.appendChild(row);
  return { row, status, body };
}

function finish(r, state, label) {
  r.row.classList.remove("running");
  r.row.classList.add(state);
  r.status.textContent = label;
}

function final(iconName, text, color) {
  const f = el("div", "run-final");
  f.style.setProperty("--fc", color);
  f.append(icon(iconName, 14), el("span", "", text));
  return f;
}

// Short version of each agent's result
function summary(agent, r, body) {
  if (agent === "writer") {
    body.appendChild(el("div", "preview", r.content));
    const actions = el("div", "mini-actions");
    actions.append(
      button("Open file", "open", () => api().open_file(r.file)),
      button("Copy", "copy", () => navigator.clipboard.writeText(r.content).catch(() => {})),
    );
    body.appendChild(actions);
  } else if (agent === "inbox" && r.emails && r.emails.length) {
    body.appendChild(el("div", "", r.summary || r.message));
    const list = el("div", "mini-list");
    r.emails.slice(0, 3).forEach((m) => {
      const item = el("div", "mini-item");
      item.append(el("span", "grow", `${m.from_name}: ${m.subject}`), el("span", "muted", m.date));
      list.appendChild(item);
    });
    body.appendChild(list);
  } else if (agent === "tutor") {  // the result line here; the full worked solution is in the main window
    body.appendChild(el("div", "", r.message));
    body.appendChild(el("div", "muted", "Full step-by-step solution in Aura's window."));
  } else if (agent === "web" && r.sources) {
    body.appendChild(el("div", "", r.answer));
    const list = el("div", "mini-list");
    r.sources.slice(0, 3).forEach((s, i) => {
      const item = el("div", "mini-item");
      item.append(el("span", "grow", `[${i + 1}] ${s.title}`), button("Open", "open", () => api().open_link(s.url)));
      list.appendChild(item);
    });
    body.appendChild(list);
  } else if (agent === "files" && r.files && r.files.length) {
    body.appendChild(el("div", "muted", r.message));
    const list = el("div", "mini-list");
    r.files.slice(0, 4).forEach((f) => {
      const item = el("div", "mini-item");
      item.appendChild(el("span", "grow", f.name));
      if (!f.runnable) item.appendChild(button("Open", "open", () => api().open_found(f.path)));
      item.appendChild(button("Show", "folder", () => api().show_found(f.path)));
      list.appendChild(item);
    });
    body.appendChild(list);
  } else if (agent === "news" && (r.markets || r.sections)) {
    const list = el("div", "mini-list");
    (r.markets || []).slice(0, 5).forEach((m) => {
      const item = el("div", "mini-item");
      const up = m.change >= 0;
      item.append(el("span", "grow", m.name),
                  el("span", "", m.price.toLocaleString("en-IN", { maximumFractionDigits: 2 })),
                  el("span", up ? "up" : "down", `${up ? "+" : ""}${m.change_pct.toFixed(2)}%`));
      list.appendChild(item);
    });
    body.appendChild(list);
    (r.sections || []).slice(0, 2).forEach((s) => {
      if (s.summary) body.appendChild(el("div", "", `${s.title}: ${s.summary}`));
    });
  } else {
    body.appendChild(el("div", "", r.message || ""));
  }
}

/* ---------- Events from Python (same events as the main window) ---------- */
function onDockEvent(ev) {
  if (!current) startRun("Task from the main window");  // started before the dock opened
  switch (ev.type) {
    case "planning":
      current.plan = makeRow("orchestrator", "planning…");
      break;
    case "plan":
      if (!current.plan) current.plan = makeRow("orchestrator", "");
      finish(current.plan, "done", ev.steps.length ? `${ev.steps.length}-step plan` : "No steps");
      current.plan.row.querySelector(".drow-task").textContent =
        ev.steps.map((s) => (AGENTS[s.agent] || {}).name).join(" → ");
      break;
    case "step_start":
      current.rows[ev.number] = makeRow(ev.agent, ev.task);
      break;
    case "step_done": {
      // The dock may have opened mid-run and missed this step's start: draw its row now
      const r = ev.result, row = current.rows[ev.number] ||= makeRow(ev.agent, ev.task);
      const refused = r.action && r.action.action === "refuse";
      finish(row, refused ? "refused" : "done", refused ? "Refused" : "Done");
      summary(ev.agent, r, row.body);
      break;
    }
    case "step_failed":
      current.rows[ev.number] ||= makeRow(ev.agent, ev.task);
      finish(current.rows[ev.number], "error", "Failed");
      current.rows[ev.number].body.appendChild(el("div", "", ev.message));
      break;
    case "error":
      if (current.plan) finish(current.plan, "error", "Failed");
      current.box.appendChild(final("error", ev.message, "var(--err)"));
      break;
    case "done":
      if (ev.nothing_to_do) current.box.appendChild(final("info", ev.reply || "Nothing I can do for that one.", "var(--accent-2)"));
      else if (ev.ok) current.box.appendChild(final("check", "All done!", "var(--ok)"));
      else current.box.appendChild(final("warn", "Stopped because a step failed.", "var(--warn)"));
      current = null;
      setBusy(false);
      break;
  }
  results.scrollTop = results.scrollHeight;
  fit();
}

function setBusy(on) {
  document.body.classList.toggle("busy", on);
  $("pillText").textContent = on ? "Aura is working…" : "Ask Aura anything…";
  $("dockSend").disabled = on;
}

/* ---------- Now Playing ----------
   Shown while something plays, and for a minute after it is paused (so the
   play button is still there). Times come in milliseconds. */
const PAUSED_KEEP = 60000;
const media = { shown: false, playing: false, pos: 0, dur: 0, at: 0, lastPlaying: 0, key: "" };

function appName(id) {
  const app = (id || "").toLowerCase();
  if (app.includes("spotify")) return "Spotify";
  for (const [word, name] of [["brave", "Brave"], ["chrome", "Chrome"], ["msedge", "Edge"], ["edge", "Edge"],
                              ["firefox", "Firefox"], ["opera", "Opera"], ["vlc", "VLC"], ["zune", "Media Player"]]) {
    if (app.includes(word)) return name;
  }
  return "";
}

function clock(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function setMedia(info) {
  const now = Date.now();
  const playing = info.status === "Playing";
  if (playing) media.lastPlaying = now;
  const has = ["Playing", "Paused", "Changing"].includes(info.status) && (info.title || info.app);
  const shown = !!has && (playing || now - media.lastPlaying < PAUSED_KEEP);
  Object.assign(media, { playing, pos: info.pos || 0, dur: info.dur || 0, at: now });

  const body = document.body;
  body.classList.toggle("paused", !playing);
  body.classList.toggle("no-time", !(media.dur > 1000));
  document.querySelectorAll(".media-btn.toggle").forEach((b) => {
    const size = b.classList.contains("big") ? 16 : 13;
    const want = playing ? "pause" : "play";
    if (b.dataset.shows !== want) { b.replaceChildren(icon(want, size)); b.dataset.shows = want; }
  });

  const app = appName(info.app);
  const title = info.title || (app ? `Playing in ${app}` : "Playing");
  const artist = info.artist || (info.title ? app : "");  // not "Playing in Brave · Brave"
  const key = `${title}|${artist}`;
  if (key !== media.key) {  // a new song: write the names (textContent only)
    media.key = key;
    const song = $("pillSong");
    song.replaceChildren(el("b", "", title));
    if (artist) song.append(el("span", "", ` · ${artist}`));
    $("nowTitle").textContent = title;
    $("nowArtist").textContent = artist;
    $("pill").title = `${title}${artist ? " · " + artist : ""} (click to ask Aura)`;
  }
  if (shown !== media.shown) {
    media.shown = shown;
    body.classList.toggle("media", shown);
    if (!shown) $("pill").title = "Ask Aura";
    fit();  // the open card grows or shrinks for the Now Playing card
    if (shown) requestAnimationFrame(tick);
  }
}

// The cover picture of the new song ("" = none: a music note on the app's colours)
function setMediaArt(url) {
  for (const id of ["pillArt", "nowArt"]) {
    const box = $(id);
    const ok = typeof url === "string" && url.startsWith("data:image/");
    box.classList.toggle("has-art", ok);
    box.style.backgroundImage = ok ? `url("${url}")` : "";
    box.replaceChildren(...(ok ? [] : [icon("music", id === "nowArt" ? 20 : 15)]));
  }
}
setMediaArt("");

// Moves the progress bars smoothly between Python's updates
function tick() {
  if (!media.shown) return;
  if (media.dur > 1000) {
    const pos = Math.min(media.dur, media.pos + (media.playing ? Date.now() - media.at : 0));
    const pct = `${(100 * pos / media.dur).toFixed(2)}%`;
    $("pillBar").style.width = pct;
    $("nowBar").style.width = pct;
    $("nowPos").textContent = clock(pos);
    $("nowDur").textContent = clock(media.dur);
  }
  requestAnimationFrame(tick);
}

document.querySelectorAll(".media-btn").forEach((b) => {
  b.addEventListener("click", (e) => {
    e.stopPropagation();  // a button on the pill must not open the dock
    api().dock_media(b.dataset.media);
  });
});
