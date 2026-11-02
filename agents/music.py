"""
agents/music.py - The Music agent.

"play kesariya on spotify"           -> finds the song and plays it in the Spotify app
"play shape of you on youtube music" -> finds the song and plays it on YouTube Music (in the browser)
"pause the music", "resume", "next song", "previous song"

NO AI MODEL: the song and the platform are read from the words.
  - Spotify: YouTube Music's search gives the song's real title and artist, then a web
    search (Tavily) finds its open.spotify.com/track/<id> page. Opening "spotify:track:<id>"
    makes the Spotify app play it.
  - YouTube Music: its own search finds the song's video id, then
    https://music.youtube.com/watch?v=<id> is opened in the normal browser.

Windows' media sessions (the media controls in the volume pop-up) tell Aura what each
app is playing, and can press play / pause / next on one app. That is how Aura:
  - checks the song really started,
  - starts YouTube Music when the browser blocked autoplay (no keys are typed, the
    window you are using keeps the focus),
  - pauses only what is playing ("pause" can never start music by mistake).

SAFETY:
  - The ids from the searches are checked against their exact formats, and Python
    builds the only links that are ever opened (Spotify and YouTube Music pages).
  - The PowerShell script that talks to the media sessions is FIXED text; only an
    action word from a fixed list and a session number are put into it.
"""

import base64
import difflib
import os
import re
import subprocess
import time
import webbrowser

import requests

import config
from agents.system import installed_apps

PLATFORMS = {"spotify": "Spotify", "youtube": "YouTube Music"}
DEFAULT_PLATFORM = "spotify"
SPOTIFY_ID = re.compile(r"open\.spotify\.com/(?:intl-[a-z-]+/)?track/([A-Za-z0-9]{22})")
YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
# Versions of a song that are only picked when the user asks for them
VARIANTS = ("lofi", "lo-fi", "slowed", "reverb", "remix", "cover", "karaoke", "instrumental", "sped up",
            "8d", "mix", "mashup", "reprise", "unplugged", "live")
START_WAIT = 15   # seconds to wait for the song to show up in Windows' media sessions

# YouTube Music's own web search (the same one music.youtube.com uses; no key needed)
YTM_SEARCH = "https://music.youtube.com/youtubei/v1/search?prettyPrint=false"
YTM_SONGS_ONLY = "EgWKAQIIAWoQEAMQBBAJEAoQBRAREBAQFQ%3D%3D"  # the "Songs" filter
YTM_CLIENT = {"clientName": "WEB_REMIX", "clientVersion": "1.20241023.01.00", "hl": "en", "gl": "IN"}


def read_task(task):
    """'play kesariya by arijit on spotify' -> ('play', 'kesariya by arijit', 'spotify')."""
    text = " ".join(task.lower().replace("’", "'").split())
    # "play <a song>" first, so songs like "Back in Black" or "Don't Stop Me Now" are not controls
    asked = re.match(r"^(?:please\s+)?(?:can you\s+)?(?:play|put on|listen to)\s+(?:the\s+)?(.+)$", text)
    if not asked or re.fullmatch(r"(?:the\s+)?(?:music|song|it|again|(?:next|previous|last) (?:song|track))", asked.group(1)):
        if re.search(r"\b(next|skip)\b", text):
            return "next", "", None
        if re.search(r"\b(previous|last|back)\b", text):
            return "previous", "", None
        if re.search(r"\b(pause|stop)\b", text):
            return "pause", "", None
        return "resume", "", None

    platform = None
    if re.search(r"\b(youtube|yt)\s*music\b|\byoutube\b|\byt\b", text):
        platform = "youtube"
    elif "spotify" in text:
        platform = "spotify"
    song = re.sub(r"\b(?:on|in|using|from|with)\s+(?:the\s+)?(?:spotify|(?:youtube|yt)\s*music|youtube|yt)(?:\s+app)?\b", " ", text)
    song = re.sub(r"\b(spotify|youtube music|yt music)\b", " ", song)
    song = re.sub(r"^\s*(?:please\s+)?(?:can you\s+)?(?:play|put on|start|listen to)\s+", "", song)
    song = re.sub(r"^(?:the\s+)?(?:song|track|music)\s+", "", song.strip())
    song = re.sub(r"\s+(?:song|track|for me|please)\s*$", "", song.strip())
    return "play", song.strip(" .\"'"), platform


def similarity(asked, title):
    """How many of the asked words are in the title (0..1), with a little fuzzy matching."""
    words = re.findall(r"[a-z0-9]+", asked.lower())
    have = re.findall(r"[a-z0-9]+", title.lower())
    if not words:
        return 0.0
    hits = sum(1 for w in words if w in have or difflib.get_close_matches(w, have, n=1, cutoff=0.85))
    return hits / len(words)


def clean_title(title):
    """'Kesariya (From "Brahmastra")' -> 'Kesariya' (film/album notes in brackets are left out)."""
    return re.sub(r"\s*[(\[][^)\]]*[)\]]", "", title).strip() or title


# ---------------------------------------------------------------
# Windows media sessions (Windows 10/11)
# ---------------------------------------------------------------
MEDIA_SCRIPT = r"""
$Action = '__ACTION__'; $Which = '__WHICH__'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and
  $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
function Await($op, $type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(5000) | Out-Null; $t.Result }
$M = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime]
$mgr = Await ($M::RequestAsync()) ($M)
$P = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties]
$list = @($mgr.GetSessions()); $cur = $mgr.GetCurrentSession()
for ($i = 0; $i -lt $list.Count; $i++) {
  $s = $list[$i]; $state = $s.GetPlaybackInfo().PlaybackStatus
  $hit = ($Which -eq "$i") -or ($Which -eq 'playing' -and "$state" -eq 'Playing') -or
         ($Which -eq 'current' -and $cur -and $s.SourceAppUserModelId -eq $cur.SourceAppUserModelId)
  if ($hit) {
    if ($Action -eq 'play') { Await ($s.TryPlayAsync()) ([bool]) | Out-Null }
    if ($Action -eq 'pause') { Await ($s.TryPauseAsync()) ([bool]) | Out-Null }
    if ($Action -eq 'next') { Await ($s.TrySkipNextAsync()) ([bool]) | Out-Null }
    if ($Action -eq 'previous') { Await ($s.TrySkipPreviousAsync()) ([bool]) | Out-Null }
  }
  $p = Await ($s.TryGetMediaPropertiesAsync()) ($P)
  "{0}`t{1}`t{2}`t{3}`t{4}`t{5}" -f $i, $s.SourceAppUserModelId, $state, $p.Title, $p.Artist, [int]$hit
}
"""
MEDIA_ACTIONS = ("status", "play", "pause", "next", "previous")


def media(action="status", which="none"):
    """
    Run one action on Windows' media sessions and list them all:
    [{"n", "app", "playing", "title", "artist", "hit"}]. which = a session number,
    "playing" (every session that is playing) or "current" (the one Windows shows).
    """
    if action not in MEDIA_ACTIONS or not re.fullmatch(r"\d+|playing|current|none", str(which)):
        raise ValueError("Unknown media action.")
    script = MEDIA_SCRIPT.replace("__ACTION__", action).replace("__WHICH__", str(which))
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                         capture_output=True, encoding="utf-8", errors="replace", timeout=20,
                         creationflags=subprocess.CREATE_NO_WINDOW).stdout
    sessions = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 6 and parts[0].isdigit():
            sessions.append({"n": int(parts[0]), "app": parts[1], "playing": parts[2] == "Playing",
                             "title": parts[3], "artist": parts[4], "hit": parts[5] == "1"})
    return sessions


def describe(session):
    """'Believer by Imagine Dragons on Spotify' (browsers don't always say the song: 'the music in your browser')"""
    app = "Spotify" if "spotify" in session["app"].lower() else "your browser"
    if not session["title"]:
        return "Spotify" if app == "Spotify" else "the music in your browser"
    who = f" by {session['artist']}" if session["artist"] else ""
    return f"{session['title']}{who} on {app}"


def wait_for_song(title, in_spotify):
    """
    Wait until the song shows up in the media sessions of Spotify (in_spotify=True) or of a
    browser (False; never Spotify, which may have the same song paused). If it is there but
    paused (the browser blocked autoplay), press play on that one session. Returns it, or None.
    Browsers don't always say what a tab plays, so a browser player with no title counts
    as ours after a few seconds.
    """
    name = clean_title(title)
    pressed = False
    for second in range(START_WAIT):
        time.sleep(1)
        try:
            sessions = [s for s in media() if ("spotify" in s["app"].lower()) == in_spotify]
        except (OSError, subprocess.SubprocessError):
            return None
        mine = [s for s in sessions if similarity(name, s["title"]) >= 0.6]
        if not mine and not in_spotify and second >= 3:
            mine = [s for s in sessions if not s["title"]]
        playing = [s for s in mine if s["playing"]]
        if playing:
            return playing[0]
        if mine and not pressed:
            time.sleep(1.5)  # let the page finish loading the player first
            media("play", mine[0]["n"])
            pressed = True
    return None


# ---------------------------------------------------------------
# Spotify
# ---------------------------------------------------------------
def find_on_spotify(song):
    """
    The best open.spotify.com track for the song: (id, title) or (None, None).
    YouTube Music's search first gives the song's real title and artist (it understands
    "kesariya" = "Kesariya (From Brahmastra)" by Arijit Singh); then the web search finds
    Spotify pages, and the one whose song name AND artist match wins. Covers, remixes and
    lofi versions lose points unless the user asked for them.
    """
    if not config.TAVILY_API_KEY:
        return None, None
    try:
        songs = youtube_music_songs(song)
    except requests.RequestException:
        songs = []
    name, artist = (clean_title(songs[0][1]), songs[0][2]) if songs else (song, "")
    response = requests.post("https://api.tavily.com/search", timeout=config.API_TIMEOUT_SECONDS, json={
        "api_key": config.TAVILY_API_KEY, "query": f"{name} {artist} site:open.spotify.com/track", "max_results": 8})
    response.raise_for_status()
    best, best_score = (None, None), 0.0
    for result in response.json().get("results", []):
        match = SPOTIFY_ID.search(result.get("url", ""))
        if not match:
            continue
        # Spotify page titles look like "Kesariya - song and lyrics by Pritam, Arijit Singh | Spotify"
        title = re.sub(r"\s*\|\s*Spotify\s*$", "", result.get("title", ""))
        track, _, by = title.partition(" - song and lyrics by ")
        track = clean_title(track)
        score = 2 * similarity(name, track) + (similarity(artist, by) if artist else 0)
        extra = len(re.findall(r"[a-z0-9]+", track.lower())) - len(re.findall(r"[a-z0-9]+", name.lower()))
        score -= 0.3 * max(0, extra)  # "Kesariya - Synthwave" is not "Kesariya"
        if any(v in title.lower() and v not in song for v in VARIANTS):
            score -= 1.0
        if score > best_score:
            best, best_score = (match.group(1), (track, by)), score
    return best if best_score >= 1.4 else (None, None)


def play_on_spotify(song, dry_run):
    track_id, found = find_on_spotify(song)
    has_app = any("spotify" in name for name in installed_apps())
    if not track_id:
        if dry_run:
            return "search", f"[dry run] Would search Spotify for: {song}"
        # Not found by the web search: show Spotify's own search, so one click plays it
        if has_app:
            os.startfile("spotify:search:" + requests.utils.quote(song))
        else:
            webbrowser.open("https://open.spotify.com/search/" + requests.utils.quote(song))
        return "search", f"I couldn't pick the exact song, so I opened Spotify's search for \"{song}\". Click the song to play it."
    track, by = found
    label = f"{track} by {by}" if by else track
    if dry_run:
        return "play", f"[dry run] Would play on Spotify: {label}"
    if not has_app:  # the web player in the browser
        webbrowser.open(f"https://open.spotify.com/track/{track_id}")
        playing = wait_for_song(track, in_spotify=False)
        return "play", f"Now playing {label} in the Spotify web player" if playing else \
            f"Opened {label} in the Spotify web player. Press play if it hasn't started."
    os.startfile(f"spotify:track:{track_id}")  # the Spotify app opens the song and plays it
    playing = wait_for_song(track, in_spotify=True)
    return "play", f"Now playing {describe(playing)}" if playing else \
        f"Opened {label} in Spotify. If it hasn't started, press play."


# ---------------------------------------------------------------
# YouTube Music
# ---------------------------------------------------------------
def youtube_music_songs(song):
    """YouTube Music's song results for the words: [(video id, title, artist), ...], best first."""
    response = requests.post(YTM_SEARCH, timeout=config.API_TIMEOUT_SECONDS,
                             json={"context": {"client": YTM_CLIENT}, "query": song, "params": YTM_SONGS_ONLY},
                             headers={"User-Agent": "Mozilla/5.0", "Origin": "https://music.youtube.com",
                                      "Referer": "https://music.youtube.com/"})
    response.raise_for_status()
    found = []

    def walk(node):  # the reply is deeply nested: collect every song row in order
        if isinstance(node, dict):
            row = node.get("musicResponsiveListItemRenderer")
            if row:
                video = (row.get("playlistItemData") or {}).get("videoId", "")
                columns = ["".join(run.get("text", "") for run in
                                   (c.get("musicResponsiveListItemFlexColumnRenderer", {}).get("text", {}).get("runs") or []))
                           for c in row.get("flexColumns", [])]
                if YOUTUBE_ID.match(video) and columns:
                    artist = columns[1].split(" • ")[0] if len(columns) > 1 else ""
                    found.append((video, columns[0], artist))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(response.json())
    return found


def find_on_youtube_music(song):
    """The top song result on YouTube Music: (video id, title, artist) or (None, None, None)."""
    found = youtube_music_songs(song)
    for video, title, artist in found[:5]:  # the first one that isn't an unasked-for remix/lofi version
        if not any(v in title.lower() and v not in song for v in VARIANTS):
            return video, title, artist
    return found[0] if found else (None, None, None)


def play_on_youtube_music(song, dry_run):
    video, title, artist = find_on_youtube_music(song)
    if not video:
        if not dry_run:
            webbrowser.open("https://music.youtube.com/search?q=" + requests.utils.quote(song))
        return "search", f"I couldn't find that song, so I opened YouTube Music's search for \"{song}\"."
    label = f"{title} by {artist}" if artist else title
    if dry_run:
        return "play", f"[dry run] Would play on YouTube Music: {label}"
    webbrowser.open(f"https://music.youtube.com/watch?v={video}")
    # Browsers often block sound until you click the page: then Aura presses play on it
    playing = wait_for_song(title, in_spotify=False)
    return "play", f"Now playing {label} on YouTube Music" if playing else \
        f"Opened {label} on YouTube Music. If it hasn't started, press play in the browser."


# ---------------------------------------------------------------
# Pause / resume / next / previous
# ---------------------------------------------------------------
_paused_apps = []  # the players Aura paused last, so "resume" brings back the same one


def control(action):
    """Pause what is playing, resume what Aura paused (else the last player), or skip on the player in use."""
    sessions = media()
    playing = [s for s in sessions if s["playing"]]
    if action == "pause":
        if not playing:
            return "Nothing is playing right now."
        media("pause", "playing")
        _paused_apps[:] = [s["app"] for s in playing]
        return "Paused " + describe(playing[0]) + "."
    if action == "resume":
        if playing:
            return "Already playing " + describe(playing[0]) + "."
        if not sessions:
            return "There's no music to resume. Try \"play <song> on Spotify\"."
        paused_by_aura = [s for s in sessions if s["app"] in _paused_apps]
        after = media("play", paused_by_aura[0]["n"] if paused_by_aura else "current")
        hit = next((s for s in after if s["hit"]), None)
        return f"Resumed {describe(hit)}." if hit else "Resumed the music."
    # next / previous: on the player that is playing (else the one Windows shows)
    which = playing[0]["n"] if playing else "current"
    media(action, which)
    time.sleep(1.5)
    now = [s for s in media() if s["playing"]]
    word = "Skipped to" if action == "next" else "Went back to"
    return f"{word} {describe(now[0])}." if now else f"{word} the {action} song."


def run_music(task, state):
    """Run the Music agent on one task, e.g. "play kesariya on spotify"."""
    start = time.time()
    action, song, platform = read_task(task)
    target = song

    if action != "play":
        if state.get("dry_run"):
            message = f"[dry run] Would {action} the music"
        else:
            try:
                message = control(action)
            except (OSError, subprocess.SubprocessError):
                raise ValueError("I couldn't reach Windows' media controls. Use the player's own buttons for now.")
    elif not song:
        action, message = "refuse", "Which song should I play? For example: \"play Kesariya on Spotify\"."
    else:
        platform = platform or DEFAULT_PLATFORM
        target = f"{song} ({PLATFORMS[platform]})"
        try:
            if platform == "spotify":
                action, message = play_on_spotify(song, state.get("dry_run"))
            else:
                action, message = play_on_youtube_music(song, state.get("dry_run"))
        except requests.RequestException as e:
            raise ValueError(f"I couldn't search {PLATFORMS[platform]} right now ({type(e).__name__}). "
                             "Check the internet connection and try again.")

    return {
        "agent": "music", "model": "none (rules)", "seconds": round(time.time() - start, 2),
        "fallback_used": False, "action": {"action": action, "target": target, "reason": message},
        "message": message,
    }
