"""
notch.py - What the notch's Home panel shows (Settings > Dock > Style: Notch).

  - weather():   now / high / low for where you are (Open-Meteo, no key; place from your IP).
  - calendar():  your Google Calendar events for this month and the next few weeks
                 (only if you already signed in to Google in Aura; never opens the browser).
  - battery():   battery % and charging.
  - Toggles:     mute, keep awake (no sleep / screen off), lock the PC.
  - Hud:         watches the volume and the screen brightness and calls back when they
                 change, so the notch can show a bar for a moment (like a phone).
"""

import ctypes
import json
import subprocess
import threading
import time
from datetime import datetime, timedelta

import requests

import google_sync

WEATHER_KEEP = 20 * 60   # seconds a weather reading is reused
CALENDAR_KEEP = 5 * 60   # seconds a calendar reading is reused
WEATHER_TEXT = {0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Cloudy", 45: "Fog", 48: "Fog",
                51: "Drizzle", 53: "Drizzle", 55: "Drizzle", 61: "Light rain", 63: "Rain", 65: "Heavy rain",
                71: "Light snow", 73: "Snow", 75: "Heavy snow", 80: "Showers", 81: "Showers", 82: "Heavy showers",
                95: "Thunderstorm", 96: "Thunderstorm", 99: "Thunderstorm"}

_cache = {}


def _cached(name, keep, make):
    """make() at most once every `keep` seconds; a failure is remembered for a minute too."""
    saved = _cache.get(name)
    if saved and time.time() - saved[0] < (keep if "error" not in saved[1] else 60):
        return saved[1]
    try:
        value = make()
    except Exception as e:
        value = {"error": str(e)[:200]}
    _cache[name] = (time.time(), value)
    return value


def _place():
    """Rough location from the internet address (city level). Two free services, in case one is down."""
    for url in ("https://ipwho.is/", "https://ipapi.co/json/"):
        try:
            place = requests.get(url, timeout=8).json()
            if place.get("latitude") is not None:
                return place
        except (requests.RequestException, ValueError):
            pass
    raise RuntimeError("Couldn't find where you are.")


def _weather():
    place = _place()
    lat, lon = place["latitude"], place["longitude"]
    data = requests.get("https://api.open-meteo.com/v1/forecast", timeout=8, params={
        "latitude": lat, "longitude": lon, "current": "temperature_2m,weather_code,is_day",
        "daily": "temperature_2m_max,temperature_2m_min", "timezone": "auto", "forecast_days": 1}).json()
    now, day = data["current"], data["daily"]
    return {"temp": round(now["temperature_2m"]), "code": now["weather_code"], "day": bool(now.get("is_day", 1)),
            "text": WEATHER_TEXT.get(now["weather_code"], "—"), "city": place.get("city", ""),
            "high": round(day["temperature_2m_max"][0]), "low": round(day["temperature_2m_min"][0])}


def weather():
    return _cached("weather", WEATHER_KEEP, _weather)


def _calendar():
    if not google_sync.ready():
        return {"events": [], "signed_in": False}
    now = datetime.now().astimezone()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    try:
        events = google_sync.upcoming_events(start, now + timedelta(days=45))
    except google_sync.GoogleError:
        return {"events": [], "signed_in": False}
    return {"events": events, "signed_in": True}


def calendar():
    return _cached("calendar", CALENDAR_KEEP, _calendar)


class SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_byte), ("BatteryFlag", ctypes.c_byte),
                ("BatteryLifePercent", ctypes.c_byte), ("SystemStatusFlag", ctypes.c_byte),
                ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]


def battery():
    """{"percent": 0-100 or None (no battery), "charging": bool}"""
    status = SYSTEM_POWER_STATUS()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        return {"percent": None, "charging": False}
    percent = status.BatteryLifePercent & 0xFF
    return {"percent": None if percent == 255 or status.BatteryFlag & 0x80 else percent,
            "charging": status.ACLineStatus == 1}


# ---------- Toggles ----------
def _speakers():
    from pycaw.pycaw import AudioUtilities
    return AudioUtilities.GetSpeakers().EndpointVolume


def mute(toggle=False):
    """Is the sound muted? toggle=True flips it first. None if there is no speaker."""
    import comtypes
    comtypes.CoInitialize()
    try:
        volume = _speakers()
        if toggle:
            volume.SetMute(0 if volume.GetMute() else 1, None)
        return bool(volume.GetMute())
    except Exception:
        return None
    finally:
        comtypes.CoUninitialize()


class KeepAwake:
    """Stops Windows sleeping or turning the screen off. The flag belongs to the thread
    that set it, so one thread holds it for as long as it is on."""
    def __init__(self):
        self.on = False
        self._stop = threading.Event()

    def toggle(self):
        if self.on:
            self._stop.set()
        else:
            self._stop = threading.Event()
            threading.Thread(target=self._hold, args=(self._stop,), daemon=True).start()
        self.on = not self.on
        return self.on

    @staticmethod
    def _hold(stop):
        set_state = ctypes.windll.kernel32.SetThreadExecutionState
        set_state(0x80000000 | 0x00000001 | 0x00000002)  # CONTINUOUS | SYSTEM_REQUIRED | DISPLAY_REQUIRED
        stop.wait()
        set_state(0x80000000)  # back to normal


def lock_pc():
    ctypes.windll.user32.LockWorkStation()


# ---------- Volume / brightness HUD ----------
BRIGHTNESS_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
Register-CimIndicationEvent -Namespace root/wmi -ClassName WmiMonitorBrightnessEvent -SourceIdentifier b | Out-Null
while ($true) {
  $e = Wait-Event -SourceIdentifier b
  [Console]::WriteLine($e.SourceEventArgs.NewEvent.Brightness)
  [Console]::Out.Flush()
  Remove-Event -EventIdentifier $e.EventIdentifier
}
"""


class Hud:
    """Calls on_change(kind, percent, muted) when the volume ("volume") or the screen
    brightness ("brightness") changes. Volume is read 6 times a second (one cheap COM call);
    brightness comes from a small PowerShell that waits for Windows' brightness events."""
    def __init__(self, on_change):
        self.on_change = on_change
        self._running = False
        self._process = None

    def start(self):
        if self._running:
            return
        self._running = True
        threading.Thread(target=self._volume_loop, daemon=True).start()
        threading.Thread(target=self._brightness_loop, daemon=True).start()

    def stop(self):
        self._running = False
        if self._process:
            try:
                self._process.kill()
            except OSError:
                pass

    def _volume_loop(self):
        import comtypes
        comtypes.CoInitialize()
        try:
            volume, last = _speakers(), None
            while self._running:
                now = (round(volume.GetMasterVolumeLevelScalar() * 100), bool(volume.GetMute()))
                if last is not None and now != last:
                    self.on_change("volume", now[0], now[1])
                last = now
                time.sleep(1 / 6)
        except Exception as e:
            print(f"  [notch] volume watch stopped: {e}")
        finally:
            comtypes.CoUninitialize()

    def _brightness_loop(self):
        try:
            self._process = subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", BRIGHTNESS_SCRIPT],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                creationflags=0x08000000)  # CREATE_NO_WINDOW
            for line in self._process.stdout:
                if not self._running:
                    break
                if line.strip().isdigit():
                    self.on_change("brightness", int(line), False)
        except Exception as e:
            print(f"  [notch] brightness watch stopped: {e}")


# ---------- Device events: charger and Bluetooth (notifications in the notch) ----------
BLUETOOTH_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$connected = '{83DA6326-97A6-4088-9453-A1923F573B29} 15'   # DEVPKEY_Device_IsConnected (Bluetooth)
$battery = '{104EA319-6EE2-4701-BD47-8DDBF425BBE5} 2'      # DEVPKEY_Bluetooth_Battery
$last = ''
while ($true) {
  $devices = Get-PnpDevice -Class Bluetooth | Where-Object { $_.InstanceId -like 'BTHENUM\DEV_*' -or $_.InstanceId -like 'BTHLE\DEV_*' }
  $on = @()
  if ($devices) {
    $props = Get-PnpDeviceProperty -InstanceId $devices.InstanceId -KeyName $connected, $battery
    foreach ($d in $devices) {
      $mine = $props | Where-Object { $_.InstanceId -eq $d.InstanceId }
      if (($mine | Where-Object { $_.KeyName -eq $connected }).Data -eq $true) {
        $on += @{ name = $d.FriendlyName; battery = ($mine | Where-Object { $_.KeyName -eq $battery }).Data }
      }
    }
  }
  $json = ConvertTo-Json -Compress -InputObject @($on)
  if ($json -ne $last) { [Console]::WriteLine($json); [Console]::Out.Flush(); $last = $json }
  Start-Sleep -Seconds 6
}
"""


class Devices:
    """Calls on_event(kind, info) when the charger is plugged in / out ("charger",
    {"charging", "percent"}) or a Bluetooth device connects ("bluetooth", {"name", "battery"}).
    The charger is checked every 2 s (one cheap call); Bluetooth by a small PowerShell
    every 6 s (Windows' own device properties: connected + battery %)."""
    def __init__(self, on_event):
        self.on_event = on_event
        self._running = False
        self._process = None

    def start(self):
        if self._running:
            return
        self._running = True
        threading.Thread(target=self._charger_loop, daemon=True).start()
        threading.Thread(target=self._bluetooth_loop, daemon=True).start()

    def stop(self):
        self._running = False
        if self._process:
            try:
                self._process.kill()
            except OSError:
                pass

    def _charger_loop(self):
        last = battery()
        while self._running:
            time.sleep(2)
            now = battery()
            if now["percent"] is not None and now["charging"] != last["charging"]:
                self.on_event("charger", now)
            last = now

    def _bluetooth_loop(self):
        seen = None  # names connected at the last check (None: the first check, which says nothing)
        try:
            self._process = subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", BLUETOOTH_SCRIPT],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                creationflags=0x08000000)  # CREATE_NO_WINDOW
            for line in self._process.stdout:
                if not self._running:
                    break
                try:
                    devices = [d for d in json.loads(line) if isinstance(d, dict) and d.get("name")]
                except ValueError:
                    continue
                if seen is not None:
                    for d in devices:
                        if d["name"] not in seen:
                            self.on_event("bluetooth", {"name": str(d["name"])[:60],
                                                        "battery": d.get("battery") if isinstance(d.get("battery"), int) else None})
                seen = {d["name"] for d in devices}
        except Exception as e:
            print(f"  [notch] Bluetooth watch stopped: {e}")


# ---------- Clipboard shelf ----------
CF_UNICODETEXT = 13
SHELF_SIZE = 20


class Clipboard:
    """Keeps the last SHELF_SIZE texts you copied (in memory only, never saved) and calls
    on_copy(text, count_today) for each new one. Skips what password managers mark as
    private (Windows' "don't put this in clipboard history" formats)."""
    def __init__(self, on_copy):
        self.on_copy = on_copy
        self.items = []          # newest first: {"text", "at"}
        self.today = (time.strftime("%Y-%m-%d"), 0)
        self._running = False
        self._ignore = 0         # our own copy (restore from the shelf) isn't a new item
        user32 = ctypes.windll.user32
        user32.RegisterClipboardFormatW.restype = ctypes.c_uint
        self._private = [user32.RegisterClipboardFormatW(name) for name in
                         ("ExcludeClipboardContentFromMonitorProcessing", "CanIncludeInClipboardHistory")]

    def start(self):
        if not self._running:
            self._running = True
            threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._running = False

    def _loop(self):
        user32 = ctypes.windll.user32
        last = user32.GetClipboardSequenceNumber()
        while self._running:
            time.sleep(0.4)
            number = user32.GetClipboardSequenceNumber()
            if number == last:
                continue
            last = number
            if self._ignore:
                self._ignore -= 1
                continue
            text = self._read()
            if not text or not text.strip() or (self.items and self.items[0]["text"] == text):
                continue
            self.items = [{"text": text, "at": int(time.time() * 1000)}] + \
                         [i for i in self.items if i["text"] != text][:SHELF_SIZE - 1]
            day = time.strftime("%Y-%m-%d")
            self.today = (day, (self.today[1] if self.today[0] == day else 0) + 1)
            self.on_copy(text, self.today[1])

    def _read(self):
        """The copied text, or None (not text, private, or another app holds the clipboard)."""
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        user32.GetClipboardData.restype = ctypes.c_void_p
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        if any(user32.IsClipboardFormatAvailable(f) for f in self._private if f):
            return None  # e.g. a password from a password manager
        for _ in range(5):
            if user32.OpenClipboard(None):
                break
            time.sleep(0.05)
        else:
            return None
        try:
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return None
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                return None
            try:
                return ctypes.wstring_at(pointer)[:5000]
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()

    def copy(self, text):
        """Put a shelf item back on the clipboard. True if it worked."""
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
        user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
        user32.SetClipboardData.restype = ctypes.c_void_p
        data = ctypes.create_unicode_buffer(str(text))
        size = ctypes.sizeof(data)
        handle = kernel32.GlobalAlloc(0x0002, size)  # GMEM_MOVEABLE
        if not handle:
            return False
        pointer = kernel32.GlobalLock(handle)
        ctypes.memmove(pointer, data, size)
        kernel32.GlobalUnlock(handle)
        if not user32.OpenClipboard(None):
            kernel32.GlobalFree(handle)
            return False
        try:
            user32.EmptyClipboard()
            if not user32.SetClipboardData(CF_UNICODETEXT, handle):
                kernel32.GlobalFree(handle)  # Windows didn't take it
                return False
            self._ignore += 1  # our own copy isn't a new shelf item
            return True
        finally:
            user32.CloseClipboard()


if __name__ == "__main__":  # quick self-check: python notch.py
    print(json.dumps({"weather": weather(), "battery": battery(),
                      "calendar": len(calendar().get("events", []))}, indent=1))
    hud = Hud(lambda *a: print("hud", a))
    hud.start()
    print("change the volume or brightness for 8 s...")
    time.sleep(8)
    hud.stop()
