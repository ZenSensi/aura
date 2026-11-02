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
  const h = $("dockForm").offsetHeight + card + $("home").offsetHeight
          + (results.childElementCount ? results.scrollHeight : 0);
  return Math.max(MIN_H, Math.min(MAX_H, Math.ceil(h)));
}
// Sizes are the body's; Python's cut-out also includes the ring around it
// seq: Python handles these calls on separate threads, so they can arrive out of order; it ignores older ones
let shapeSeq = 0;
const shapeTo = (w, h) => api().dock_shape(w + 2 * RING, h + 2 * RING, RADIUS + RING, shapeSeq = Math.max(shapeSeq + 1, Date.now()));
// The notch is a little bigger than the pill (dock.css, NOTCH in dock.py); resting (just the eyes) it is narrow
const isNotch = () => document.documentElement.dataset.style === "notch";
const isRest = () => !["media", "busy", "timing", "hud"].some((c) => document.body.classList.contains(c));
const pillSize = () => isNotch() ? [isRest() ? 200 : 360, 46] : [PILL_W, PILL_H];
// After media / busy / timer / HUD change: the closed notch grows or shrinks to fit (body.rest in dock.css)
function refit() {
  const body = document.body;
  body.classList.toggle("rest", isRest());
  if (isNotch() && !body.classList.contains("open") && !body.classList.contains("has-banner")) shapeTo(...pillSize());
}

// instant=true (Python, when the dock appears): no animation
async function setDockMode(mode, instant = false) {
  const body = document.body;
  body.classList.toggle("instant", instant);
  body.classList.toggle("rest", isRest());
  if (mode === "open") {
    if (document.documentElement.dataset.style === "notch") refreshHome();
    openHeight = contentHeight();
    body.style.setProperty("--open-h", openHeight + "px");
    await shapeTo(CARD_W, openHeight);
  } else {
    shapeTo(...pillSize());  // Python waits for the shrink animation before cutting
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
// Python: the dock's shortcut was pressed (dock.py shortcut)
function expandDock() { if (!document.body.classList.contains("open")) $("pill").click(); }
// Voice shortcut (dock.py _push_to_talk). While the keys are held: a wave in the closed notch / pill
// (true), then a slower wave while the speech turns into text ("working"), then off (false).
// Open notch / dock: the mic button turns red (listening) and then blinks (working) instead of the wave.
let voiceHint = null;  // the box's own placeholder while the shortcut shows its hints there
function setListening(state) {
  const body = document.body, open = body.classList.contains("open"), mic = $("dockMic");
  body.classList.toggle("listening", !!state);
  body.classList.toggle("transcribing", state === "working");
  mic.classList.toggle("listening", open && state === true);
  mic.classList.toggle("working", open && state === "working");
  if (open && state) {
    if (voiceHint === null) voiceHint = input.placeholder;
    input.placeholder = state === true ? "Listening… let go of the keys when you're done" : "Turning speech into text…";
  } else if (voiceHint !== null) {
    input.placeholder = voiceHint;
    voiceHint = null;
  }
}
// Then the dock opens with what was said in its box (or why not), for you to check and press Enter
async function voiceDone(text, error) {
  setListening(false);
  await api().dock_expand();  // brings the dock to the front, so Enter reaches it
  if (!document.body.classList.contains("open")) await setDockMode("open");
  if (error) {
    const before = input.placeholder;
    input.placeholder = error;
    setTimeout(() => { if (input.placeholder === error) input.placeholder = before; }, 4000);
    return;
  }
  input.value = ((input.value && !/\s$/.test(input.value) ? input.value + " " : input.value) + text).slice(0, input.maxLength > 0 ? input.maxLength : 1000);
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
}
function collapseDock() { if (document.body.classList.contains("open")) $("dockClose").click(); }
// Python: which corner / edge of the screen the dock is in, e.g. "bottom-left", and its style
// ("dock" or "notch"); dock.css lines it up
function setDockPosition(pos, style = "dock") {
  document.documentElement.dataset.pos = pos;
  document.documentElement.dataset.style = style;
}
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
      // Live activity: the closed pill / notch says which agent is working on what
      $("pillText").textContent = `${(AGENTS[ev.agent] || AGENTS.system).name} · ${ev.task || "working…"}`;
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
  refit();
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
    refit();
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

/* =========================================================
   Notch extras (Settings > Dock > Style: Notch; dock.css hides them otherwise)
   - Home: quick toggles, calendar (+ Google events), weather, up next, focus timer.
     Data comes from api.notch_home() (notch.py) each time the notch opens.
   - Focus timer: Python owns it (it chimes even while the notch is hidden);
     the page shows setTimer({end, total}) in the closed notch and in Home.
   - HUD: Python calls setHud(kind, percent, muted) when the volume or the
     brightness changes, and setHud(null) a moment later.
   ========================================================= */
const home = { events: [], month: null };
let timer = null, timerTick = 0;

function sameDay(a, b) { return a.toDateString() === b.toDateString(); }
// "2026-11-02" (all-day) is a local date; "2026-11-02T15:00:00+05:30" a moment
function eventDate(e) { return e.all_day ? new Date(e.start + "T00:00:00") : new Date(e.start); }

function drawCalendar() {
  const today = new Date();
  const first = home.month || new Date(today.getFullYear(), today.getMonth(), 1);
  home.month = first;
  $("calTitle").textContent = first.toLocaleDateString(undefined, { month: "long", year: "numeric" });
  const grid = $("calGrid");
  grid.replaceChildren(...["S", "M", "T", "W", "T", "F", "S"].map((d) => el("span", "dow", d)));
  const start = new Date(first);
  start.setDate(1 - first.getDay());  // back to Sunday
  for (let i = 0; i < 42; i++) {
    const day = new Date(start);
    day.setDate(start.getDate() + i);
    const cell = el("span", "", String(day.getDate()));
    if (day.getMonth() !== first.getMonth()) cell.classList.add("other");
    if (sameDay(day, today)) cell.classList.add("today");
    if (home.events.some((e) => sameDay(eventDate(e), day))) cell.classList.add("busy");
    grid.appendChild(cell);
  }
}
$("calPrev").onclick = (e) => { e.stopPropagation(); home.month = new Date(home.month.getFullYear(), home.month.getMonth() - 1, 1); drawCalendar(); };
$("calNext").onclick = (e) => { e.stopPropagation(); home.month = new Date(home.month.getFullYear(), home.month.getMonth() + 1, 1); drawCalendar(); };

function drawWeather(w) {
  const box = $("weather");
  if (!w || w.error) { box.replaceChildren(el("span", "muted", "Weather unavailable")); return; }
  const now = el("div", "now");
  now.append(icon(w.code <= 1 ? (w.day ? "sun" : "moon") : "cloud", 22), el("span", "", `${w.temp}°`));
  box.replaceChildren(now, el("div", "text", w.text),
                      el("div", "where", `${w.city ? w.city + " · " : ""}H ${w.high}° L ${w.low}°`));
}

function drawUpNext(cal) {
  const box = $("upNext");
  if (!cal.signed_in) {
    const connect = button("Connect Google", "open", async (e) => {
      e.stopPropagation();
      connect.disabled = true;
      const r = await api().notch_google();
      if (r && !r.error) { home.events = r.events; drawCalendar(); drawUpNext(r); fit(); }
      else connect.disabled = false;
    });
    box.replaceChildren(el("div", "muted", "See your Google Calendar here"), connect);
    return;
  }
  const now = new Date();
  const next = home.events.filter((e) => e.all_day ? eventDate(e) >= new Date(now.toDateString()) : eventDate(e) >= now).slice(0, 3);
  if (!next.length) { box.replaceChildren(el("div", "muted", "Nothing coming up")); return; }
  box.replaceChildren(...next.map((e) => {
    const d = eventDate(e), row = el("div", "event");
    const day = sameDay(d, now) ? "Today" : d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
    const time = e.all_day ? "All day" : d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
    row.append(el("b", "", e.title), el("span", "", `${day} · ${time}`));
    return row;
  }));
}

function drawToggles(h) {
  const b = h.battery || {};
  const chip = $("homeBattery");
  chip.replaceChildren();
  if (b.percent != null) chip.append(icon(b.charging ? "zap" : "battery", 14), el("span", "", `${b.percent}%`));
  setToggle("homeMute", h.muted, h.muted ? "volumeOff" : "volume");
  setToggle("homeAwake", h.awake);
}
function setToggle(id, on, iconName) {
  const btn = $(id);
  btn.classList.toggle("on", !!on);
  if (iconName && btn.dataset.shows !== iconName) { btn.replaceChildren(icon(iconName, 15)); btn.dataset.shows = iconName; }
}
$("homeMute").onclick = async (e) => { e.stopPropagation(); const m = await api().notch_toggle("mute"); setToggle("homeMute", m, m ? "volumeOff" : "volume"); };
$("homeAwake").onclick = async (e) => { e.stopPropagation(); setToggle("homeAwake", await api().notch_toggle("awake")); };
$("homeLock").onclick = (e) => { e.stopPropagation(); api().notch_toggle("lock"); };

async function refreshHome() {
  drawCalendar();
  api().get_automations().then((a) => { workflows = a.workflows || []; }).catch(() => {});
  const h = await api().notch_home();
  home.events = (h.calendar && h.calendar.events) || [];
  drawCalendar();
  drawWeather(h.weather);
  drawUpNext(h.calendar || {});
  drawToggles(h);
  setTimer(h.timer);
  fit();
}

/* ---------- Focus timer ---------- */
[5, 15, 25, 50].forEach((m) => {
  const b = el("button", "", `${m} min`);
  b.onclick = async (e) => { e.stopPropagation(); setTimer(await api().notch_timer(m)); };
  $("timerPresets").appendChild(b);
});
const stopBtn = el("button", "stop", "Stop");
stopBtn.onclick = async (e) => { e.stopPropagation(); await api().notch_timer(0); setTimer(null); };
$("timerPresets").appendChild(stopBtn);

function setTimer(t) {
  timer = t && t.end > Date.now() ? t : null;
  document.body.classList.toggle("timing", !!timer);
  refit();
  stopBtn.hidden = !timer;
  clearInterval(timerTick);
  drawTimer();
  if (timer) timerTick = setInterval(drawTimer, 250);
}
function drawTimer() {
  const big = $("timerBig");
  big.classList.toggle("running", !!timer);
  if (!timer) { big.textContent = "--:--"; return; }
  const left = Math.max(0, timer.end - Date.now());
  const text = clock(left + 999);  // 24:59.4 shows as 25:00, and 0:00 only at the end
  $("pillTime").textContent = text;
  $("pillChip").textContent = text;
  big.textContent = text;
  $("timerLine").style.width = `${(100 * (1 - left / timer.total)).toFixed(2)}%`;
  if (!left) setTimer(null);
}

/* ---------- Volume / brightness HUD ---------- */
function setHud(kind, percent, muted) {
  if (!kind) { document.body.classList.remove("hud"); refit(); return; }
  const name = kind === "brightness" ? "sun" : muted || !percent ? "volumeOff" : "volume";
  const box = $("hudIcon");
  if (box.dataset.shows !== name) { box.replaceChildren(icon(name, 16)); box.dataset.shows = name; }
  $("hudBar").style.width = `${muted ? 0 : percent}%`;
  $("hudPct").textContent = muted ? "Muted" : `${percent}%`;
  if (!document.body.classList.contains("hud")) { document.body.classList.add("hud"); refit(); }
}

/* ---------- Banner: notifications in the closed notch (Python: dock.banner) ---------- */
const BANNER = [440, 64];  // body size (dock.css)
const BANNER_LOOK = {
  ok: ["check", "var(--ok)"], error: ["error", "var(--err)"], warn: ["warn", "var(--warn)"],
  info: ["info", "var(--accent-2)"], remind: ["bell", "#f5a524"], charger: ["zap", "var(--ok)"],
  bluetooth: ["bluetooth", "#4c9aff"], clip: ["clipboard", "#c084fc"], ai: ["sparkles", "#f5a524"],
};
let bannerInfo = null;

async function showBanner(info) {
  const body = document.body;
  if (!info) {
    if (!body.classList.contains("has-banner")) return;
    body.classList.remove("has-banner");
    bannerInfo = null;
    if (!body.classList.contains("open")) shapeTo(...pillSize());  // Python waits for the shrink
    return;
  }
  if (body.classList.contains("open")) return;  // you are using the notch: no banner
  bannerInfo = info;
  const [name, color] = BANNER_LOOK[info.kind] || BANNER_LOOK.info;
  const box = $("bannerIcon");
  box.style.setProperty("--c", color);
  box.replaceChildren(icon(name, 18));
  $("bannerTitle").textContent = info.title;
  $("bannerText").textContent = info.text || "";
  $("bannerSnooze").hidden = !info.snooze;
  await shapeTo(...BANNER);  // cut big enough first, then grow
  body.classList.add("has-banner");
}
$("bannerSnooze").onclick = (e) => {
  e.stopPropagation();
  if (bannerInfo && bannerInfo.snooze) api().notify_snooze(bannerInfo.snooze);
  showBanner(null);
};
$("banner").onclick = () => {
  const kind = bannerInfo && bannerInfo.kind;
  showBanner(null);
  if (kind === "clip") { $("pill").click(); showTab("shelf"); }
  else if (kind === "ai") { $("pill").click(); showTab("usage"); }
  else if (["ok", "error", "warn", "info", "remind"].includes(kind)) api().notify_open();
};

/* ---------- Tabs: Home / Clipboard / AI usage ---------- */
function showTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("on", t.dataset.tab === name));
  $("homePane").hidden = name !== "home";
  $("shelfPane").hidden = name !== "shelf";
  $("usagePane").hidden = name !== "usage";
  if (name === "shelf") api().notch_shelf().then((items) => { setShelf(items); fit(); });
  else if (name === "usage") api().notch_usage().then((u) => { drawUsage(u); fit(); });
  else fit();
}
document.querySelectorAll(".tab").forEach((t) => {
  t.onclick = (e) => { e.stopPropagation(); showTab(t.dataset.tab); };
});

function ago(ms) {
  const s = Math.round((Date.now() - ms) / 1000);
  return s < 60 ? "now" : s < 3600 ? `${Math.floor(s / 60)} min` : `${Math.floor(s / 3600)} h`;
}

// Python sends the shelf after every copy; it is also asked for when the tab opens
function setShelf(items) {
  const pane = $("shelfPane");
  if (!items || !items.length) {
    pane.replaceChildren(el("div", "pane-empty", "Copy some text and it shows up here. Click an item to copy it again."));
    return;
  }
  pane.replaceChildren(...items.map((item, i) => {
    const row = el("button", "clip");
    row.type = "button";
    row.title = item.text.slice(0, 400);
    row.append(icon("clipboard", 14), el("span", "grow", item.text.replace(/\s+/g, " ")), el("span", "when", ago(item.at)));
    row.onclick = async (e) => {
      e.stopPropagation();
      if (await api().notch_copy(i)) {
        row.classList.add("copied");
        row.querySelector(".when").textContent = "Copied";
      }
    };
    return row;
  }));
}

function drawUsage(u) {
  const pane = $("usagePane");
  const names = Object.keys(u.providers || {}).sort((a, b) => u.providers[b].ok - u.providers[a].ok);
  const engine = u.mode === "cli" ? "agents" : "API keys";
  const head = el("div", "pane-empty", `Today's AI requests (engine: ${engine}). A backup takes over when one fails or hits its limit.`);
  if (!names.length) { pane.replaceChildren(head, el("div", "pane-empty", "No AI requests yet today.")); return; }
  const most = Math.max(...names.map((n) => u.providers[n].ok + u.providers[n].failed));
  pane.replaceChildren(head, ...names.map((n) => {
    const p = u.providers[n], row = el("div", "prov");
    const bar = el("span", "prov-bar"), ok = el("span", "ok"), bad = el("span", "bad");
    ok.style.width = `${100 * p.ok / most}%`;
    bad.style.width = `${100 * p.failed / most}%`;
    bar.append(ok, bad);
    row.append(el("b", "", n), bar, el("span", "count", p.failed ? `${p.ok} ok · ${p.failed} failed` : `${p.ok} ok`));
    if (p.failed && p.last_error) row.appendChild(el("span", "prov-err", p.last_error));
    return row;
  }));
}

/* ---------- Command palette: commands that match what you type ----------
   Every typed word must start a word of the command (e.g. "tim 25", "lock").
   Enter runs the highlighted one; the last row always sends the text to Aura. */
let workflows = [];
const startTimer = async (m) => setTimer(await api().notch_timer(m));
function commands() {
  const list = [25, 5, 15, 50].map((m) => ({ label: `Start a ${m} min focus timer`, keys: "timer focus pomodoro", run: () => startTimer(m) }));
  list.push(
    { label: "Stop the focus timer", keys: "timer", run: async () => { await api().notch_timer(0); setTimer(null); } },
    { label: "Mute / unmute", keys: "sound volume", run: () => $("homeMute").click() },
    { label: "Keep awake on / off", keys: "caffeine sleep", run: () => $("homeAwake").click() },
    { label: "Lock the PC", keys: "screen", run: () => api().notch_toggle("lock") },
    { label: "Clipboard shelf", keys: "copy paste history", run: () => showTab("shelf") },
    { label: "AI usage", keys: "limits providers", run: () => showTab("usage") },
    { label: "Open Aura", keys: "window main app", run: () => api().dock_open_main() },
    { label: "Settings", keys: "preferences options", run: () => api().tray_settings("") },
    { label: "Dock settings", keys: "notch style shortcut", run: () => api().tray_settings("dock") },
  );
  workflows.forEach((w) => list.push({ label: `Run workflow: ${w.name}`, keys: "workflow", run: () => runTask(w.name) }));
  return list;
}
const palette = { items: [], sel: 0 };

function drawPalette() {
  const text = input.value.trim(), box = $("palette");
  if (document.documentElement.dataset.style !== "notch" || !text) {
    palette.items = [];
    if (!box.hidden) { box.hidden = true; fit(); }
    return;
  }
  const words = text.toLowerCase().split(/\s+/);
  const found = commands().filter((c) => {
    const hay = `${c.label} ${c.keys}`.toLowerCase().split(/[^a-z0-9]+/);
    return words.every((w) => hay.some((h) => h.startsWith(w)));
  }).slice(0, 5);
  palette.items = [...found, { label: `Ask Aura: ${text}`, ask: true, run: () => runTask(text) }];
  palette.sel = 0;
  box.replaceChildren(...palette.items.map((c, i) => {
    const row = el("button", "cmd");
    row.type = "button";
    row.append(icon(c.ask ? "sparkles" : "zap", 14), el("span", "grow", c.label));
    row.onclick = (e) => { e.stopPropagation(); pick(i); };
    return row;
  }));
  markPalette();
  box.hidden = false;
  fit();
}
function markPalette() {
  [...$("palette").children].forEach((row, i) => {
    row.classList.toggle("sel", i === palette.sel);
    const key = row.querySelector("kbd");
    if (i === palette.sel && !key) row.appendChild(el("kbd", "", "Enter"));
    else if (i !== palette.sel && key) key.remove();
  });
}
function pick(i) {
  const c = palette.items[i];
  input.value = "";
  drawPalette();
  if (c) c.run();
}
function runTask(text) {  // send as a normal Aura task (skips the palette)
  input.value = text;
  palette.items = [];
  $("dockForm").requestSubmit();
}
input.addEventListener("input", drawPalette);
input.addEventListener("keydown", (e) => {
  if (!palette.items.length || (e.key !== "ArrowDown" && e.key !== "ArrowUp")) return;
  e.preventDefault();
  palette.sel = (palette.sel + (e.key === "ArrowDown" ? 1 : -1) + palette.items.length) % palette.items.length;
  markPalette();
});
// Runs before the normal submit (capture): a highlighted command wins over sending a task
$("dockForm").addEventListener("submit", (e) => {
  const c = palette.items[palette.sel];
  if (c && !c.ask) { e.preventDefault(); e.stopImmediatePropagation(); pick(palette.sel); return; }
  palette.items = [];
  if (!$("palette").hidden) { $("palette").hidden = true; }
}, true);
