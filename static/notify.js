/* =========================================================
   Aura "task done" notification - page code (notify.py shows the window).

   notify({kind, title, text}) -> fill the card, tell Python its height
   (api.notify_shape: the window is cut to it), slide it in. A thin bar runs
   out over LIFE seconds (paused while the mouse is on the card); then the
   card slides out and Python hides the window (api.notify_hide).
   Click the card = open Aura (api.notify_open).
   Reminders ({snooze: id}) get Snooze / Done buttons and stay on screen longer.
   ========================================================= */

const api = () => window.pywebview.api;
const $ = (id) => document.getElementById(id);
const card = $("card");
const LIFE = 6;           // seconds on screen
const REMINDER_LIFE = 25;  // reminders wait longer for you to see them
const KIND_ICON = { ok: "check", warn: "warn", error: "error", info: "info", remind: "bell" };
let snoozeId = null;
const calm = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
let timer = null;

/* Glass: blurred + sharp pictures of the screen behind the card (see notify.css) */
function setGlass(blurred, sharp) {
  const root = document.documentElement.style;
  root.setProperty("--glass", blurred ? `url("${blurred}")` : "none");
  root.setProperty("--behind", sharp ? `url("${sharp}")` : "none");
}

async function notify(n) {
  clearTimeout(timer);
  card.className = "card " + (KIND_ICON[n.kind] ? n.kind : "info");  // hidden, off to the right
  $("icon").replaceChildren(icon(KIND_ICON[n.kind] || "info", 17));
  $("title").textContent = n.title;   // text only, never HTML
  $("msg").textContent = n.text;
  snoozeId = n.snooze || null;
  $("actions").hidden = !snoozeId;
  const life = snoozeId ? REMINDER_LIFE : LIFE;
  card.style.setProperty("--life", life + "s");
  await api().notify_shape(card.offsetHeight + 4);  // + the 2px ring above and below
  void card.offsetWidth;  // start from the hidden position, then slide in
  card.classList.add("show");
  if (calm()) timer = setTimeout(hide, life * 1000);  // no timer bar animation then
}

function hide() {
  if (!card.classList.contains("show")) return;
  card.classList.replace("show", "leaving");
  timer = setTimeout(() => api().notify_hide(), 420);  // after the slide-out
}

$("timer").addEventListener("animationend", hide);  // the bar ran out
card.addEventListener("click", (e) => {
  if (e.target.closest("#snooze")) api().notify_snooze(snoozeId);
  else if (!e.target.closest(".card-close, #done")) api().notify_open();
  hide();
});
