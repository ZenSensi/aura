"""
voice.py - Voice typing: record the microphone, turn speech into text.

  start()  -> starts recording the default microphone
  stop()   -> stops, sends the audio to a Whisper speech-to-text model,
              and returns the text (the page puts it in the input box)

Recording uses Windows' built-in recorder (MCI, in winmm.dll) through
ctypes, so no extra library and no browser microphone permission is needed.
MCI only works in the thread that opened the recording, so every
recording runs in its own small thread, which waits for stop().

The audio goes to the Whisper model in config.VOICE_MODEL (Groq, free).
It is only turned into text there; the recording is deleted afterwards.
"""

import ctypes
import os
import sys
import tempfile
import threading
import time
import wave
from array import array

import requests

import config

# A short file name: MCI rejects long paths
AUDIO_FILE = os.path.join(tempfile.gettempdir(), "aura_voice.wav")
ALIAS = "aura_voice"
QUIET = 60          # average loudness below this = silence (e.g. a muted mic)
MIN_CONFIDENCE = -0.8  # Whisper's avg_logprob per piece of text; below this it is usually guessing
# (Whisper "hears" words in background noise, e.g. "What is the point?". In tests, real
#  speech scored about -0.35 and noise -0.8 or lower, so unsure pieces are dropped.)

_lock = threading.Lock()
_recording = None  # the current recording: {"stop": Event, "done": Event, "error": str|None, "started": time}


class VoiceError(Exception):
    """Something went wrong with recording or speech-to-text (shown to the user)."""


def _mci(command):
    """Send one command to the Windows recorder; raise VoiceError with its message on failure."""
    error = ctypes.windll.winmm.mciSendStringW(command, None, 0, None)
    if error:
        text = ctypes.create_unicode_buffer(256)
        ctypes.windll.winmm.mciGetErrorStringW(error, text, 256)
        raise VoiceError(text.value or f"Recorder error {error}")


def _record(rec):
    """Runs in its own thread: open -> record -> wait for stop() (or the time limit) -> save."""
    try:
        _mci(f"open new type waveaudio alias {ALIAS}")
        # 16 kHz, 16-bit, mono: what speech models expect, and small to upload
        _mci(f"set {ALIAS} time format ms bitspersample 16 channels 1 "
             "samplespersec 16000 bytespersec 32000 alignment 2")
        _mci(f"record {ALIAS}")
        rec["stop"].wait(config.MAX_VOICE_SECONDS)  # stops by itself after the limit
        _mci(f"stop {ALIAS}")
        _mci(f'save {ALIAS} "{AUDIO_FILE}"')
    except VoiceError as e:
        rec["error"] = str(e)
    finally:
        try:
            _mci(f"close {ALIAS}")
        except VoiceError:
            pass
        rec["done"].set()


def start():
    """Start recording. Raises VoiceError if it can't."""
    global _recording
    if sys.platform != "win32":
        raise VoiceError("Voice typing works on Windows only.")
    if ctypes.windll.winmm.waveInGetNumDevs() == 0:
        raise VoiceError("No microphone found.")
    with _lock:
        if _recording and not _recording["done"].is_set():
            return  # already recording
        _recording = {"stop": threading.Event(), "done": threading.Event(), "error": None, "started": time.time()}
        threading.Thread(target=_record, args=(_recording,), daemon=True).start()


def cancel():
    """Stop recording and throw the audio away."""
    with _lock:
        rec = _recording
    if rec:
        rec["stop"].set()
        rec["done"].wait(5)
    _delete_audio()


def _delete_audio():
    try:
        os.remove(AUDIO_FILE)
    except OSError:
        pass


def _loudness(path):
    """Average loudness of a 16-bit WAV file (0 = total silence)."""
    with wave.open(path, "rb") as f:
        samples = array("h", f.readframes(f.getnframes()))
    if not samples:
        return 0
    return sum(abs(s) for s in samples) / len(samples)


def stop():
    """Stop recording and return the spoken text ("" if nothing was said). Raises VoiceError."""
    with _lock:
        rec = _recording
    if not rec:
        raise VoiceError("Not recording.")
    rec["stop"].set()
    if not rec["done"].wait(5):
        raise VoiceError("The recorder did not stop.")
    if rec["error"]:
        raise VoiceError(f"Couldn't record: {rec['error']}")
    try:
        if time.time() - rec["started"] < 0.6 or _loudness(AUDIO_FILE) < QUIET:
            return ""  # too short or silent: don't let Whisper guess words
        return transcribe(AUDIO_FILE)
    finally:
        _delete_audio()  # the recording is never kept


def transcribe(path):
    """Send a WAV file to the Whisper model and return the text."""
    choice = config.VOICE_MODEL
    provider = config.PROVIDERS[choice["provider"]]
    api_key = choice.get("api_key") or provider["api_key"]
    if not api_key:
        raise VoiceError(f"Missing API key for '{choice['provider']}'. Add it to your .env file.")
    try:
        with open(path, "rb") as audio:
            response = requests.post(
                provider["base_url"] + "/audio/transcriptions",
                headers={"Authorization": "Bearer " + api_key},
                files={"file": ("voice.wav", audio, "audio/wav")},
                # verbose_json = the text in pieces ("segments"), each with a confidence score
                data={"model": choice["model"], "response_format": "verbose_json", "temperature": "0",
                      "language": config.VOICE_LANGUAGE},
                timeout=config.API_TIMEOUT_SECONDS,
            )
    except requests.RequestException as e:
        raise VoiceError(f"Network error: {e}")
    if response.status_code != 200:
        raise VoiceError(f"Speech-to-text error {response.status_code}: {' '.join(response.text.split())[:150]}")
    try:
        reply = response.json()
        segments = reply.get("segments")
        if segments is None:  # no scores given: use the whole text
            return str(reply["text"]).strip()
        sure = [s["text"].strip() for s in segments if s.get("avg_logprob", 0) >= MIN_CONFIDENCE]
        return " ".join(t for t in sure if t)
    except (ValueError, KeyError, TypeError):
        raise VoiceError("Unexpected reply from the speech-to-text model.")
