/* =========================================================
   voice.js - the mic button (voice typing), used by the main
   window and the dock.

   Click once  -> Python starts recording (api.voice_start)
   Click again -> Python stops, a Whisper model turns the speech
                  into text (api.voice_stop), and the text is
                  added to the input box, so you can check it
                  before sending.
   Esc while recording -> cancel (nothing is kept).
   ========================================================= */

function setupVoice(button, field) {
  const voiceApi = () => window.pywebview.api;
  let state = "idle";  // idle | listening | working
  let placeholder = field.placeholder;

  function setState(next, hint) {
    state = next;
    button.classList.toggle("listening", next === "listening");
    button.classList.toggle("working", next === "working");
    button.title = { idle: "Voice typing", listening: "Stop and type what I said (Esc to cancel)",
                     working: "Turning speech into text…" }[next];
    if (next === "idle") field.placeholder = hint || placeholder;
    else field.placeholder = hint;
  }

  // Show a short message in the input box for a few seconds
  function flash(message) {
    setState("idle", message);
    setTimeout(() => { if (state === "idle") field.placeholder = placeholder; }, 4000);
  }

  function insert(text) {
    const before = field.value && !/\s$/.test(field.value) ? field.value + " " : field.value;
    field.value = (before + text).slice(0, field.maxLength > 0 ? field.maxLength : 1000);
    field.dispatchEvent(new Event("input"));  // e.g. the main window resizes the box
    field.focus();
    field.setSelectionRange(field.value.length, field.value.length);
  }

  button.addEventListener("click", async () => {
    if (state === "working") return;
    // A failed call must still show a message and free the mic button
    const call = (name) => voiceApi()[name]().catch(() => ({ ok: false, error: "Voice typing stopped working. Try again." }));
    if (state === "idle") {
      placeholder = field.placeholder;
      const r = await call("voice_start");
      if (!r.ok) return flash(r.error);
      setState("listening", "Listening… click the mic again when you're done");
      return;
    }
    setState("working", "Turning speech into text…");
    const r = await call("voice_stop");
    if (!r.ok) return flash(r.error);
    if (!r.text) return flash("I didn't hear anything. Try again a bit closer to the mic.");
    setState("idle");
    insert(r.text);
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && state === "listening") {
      e.stopPropagation();
      voiceApi().voice_cancel();
      flash("Voice typing cancelled.");
    }
  }, true);
}
