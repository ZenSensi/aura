"""
nowplaying.py - Tells the dock what is playing on the PC (Spotify, YouTube in a browser, ...).

Windows keeps a "media session" for every app that plays sound and shows it in
the volume pop-up: the song, the artist, the cover art, how far it has played,
and play / pause / next buttons. A small PowerShell script (FIXED text, like the
Music agent's, see agents/music.py) keeps running in the background while the
dock is on screen and prints what Windows shows twice a second, one JSON line each:

  {"app": "Spotify.exe", "status": "Playing", "title": "...", "artist": "...",
   "pos": 61000, "dur": 215000, "thumb": "<base64 picture>" | null}

"thumb" is only sent when the song changes ("" = no picture). Aura sends it
"toggle", "next" or "previous" on its input to press those buttons; closing
its input stops it.

Nothing here can run anything else: the script never changes, and only one
of those three words is ever written to it.
"""

import base64
import json
import subprocess
import threading

WATCH_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
# NOTE: PowerShell names ignore case, so the types are $T... ($p would overwrite $P)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and
  $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
function Await($op, $type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(3000) | Out-Null; $t.Result }
$TManager = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime]
$TProps = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties]
$TStream = [Windows.Storage.Streams.IRandomAccessStreamWithContentType, Windows.Storage.Streams, ContentType = WindowsRuntime]
$TInput = [Windows.Storage.Streams.IInputStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
# The picture stream comes as a raw COM object PowerShell can't read; reflection casts it to a .NET stream
$asStream = [System.IO.WindowsRuntimeStreamExtensions].GetMethod('AsStreamForRead', [type[]]@($TInput))
$mgr = Await ($TManager::RequestAsync()) ($TManager)
$in = New-Object System.IO.StreamReader([Console]::OpenStandardInput())  # Console.In would block
$cmd = $in.ReadLineAsync(); $last = $null; $tick = 0
while ($true) {
  $pressed = $false
  if ($cmd.IsCompleted) {
    $c = $cmd.Result
    if ($c -eq $null) { break }            # Aura closed our input: stop
    $s = $mgr.GetCurrentSession()
    if ($s -and $c -eq 'toggle') { Await ($s.TryTogglePlayPauseAsync()) ([bool]) | Out-Null }
    if ($s -and $c -eq 'next') { Await ($s.TrySkipNextAsync()) ([bool]) | Out-Null }
    if ($s -and $c -eq 'previous') { Await ($s.TrySkipPreviousAsync()) ([bool]) | Out-Null }
    $cmd = $in.ReadLineAsync(); $pressed = $true
  }
  if ($pressed -or $tick % 5 -eq 0) {      # every 0.5 s, and right after a button
    $o = [ordered]@{ app = ''; status = 'None'; title = ''; artist = ''; pos = 0; dur = 0; thumb = $null }
    $s = $mgr.GetCurrentSession()
    if ($s) {
      $o.app = $s.SourceAppUserModelId
      $o.status = "$($s.GetPlaybackInfo().PlaybackStatus)"
      $p = Await ($s.TryGetMediaPropertiesAsync()) ($TProps)
      if ($p) { $o.title = $p.Title; $o.artist = $p.Artist }
      $t = $s.GetTimelineProperties()
      $o.dur = [int64]($t.EndTime - $t.StartTime).TotalMilliseconds
      $o.pos = [int64]$t.Position.TotalMilliseconds
      if ($o.status -eq 'Playing' -and $t.LastUpdatedTime.Year -gt 2000) {
        $o.pos += [int64]([DateTimeOffset]::Now - $t.LastUpdatedTime).TotalMilliseconds
      }
      $key = "$($o.app)|$($o.title)|$($o.artist)"
      if ($key -ne $last) {
        $last = $key; $o.thumb = ''
        if ($p -and $p.Thumbnail) {
          $st = Await ($p.Thumbnail.OpenReadAsync()) ($TStream)
          if ($st) {
            $net = $asStream.Invoke($null, @($st)); $mem = New-Object System.IO.MemoryStream
            $net.CopyTo($mem); $net.Dispose(); $b = $mem.ToArray()
            if ($b.Length -gt 0 -and $b.Length -lt 2000000) { $o.thumb = [Convert]::ToBase64String($b) }
          }
        }
      }
    } elseif ($last -ne '') { $last = '' }
    [Console]::Out.WriteLine(($o | ConvertTo-Json -Compress)); [Console]::Out.Flush()
  }
  $tick++; Start-Sleep -Milliseconds 100
}
"""
COMMANDS = ("toggle", "next", "previous")


class NowPlaying:
    """Runs the watcher while someone listens (the dock), and calls on_change(info) for every update."""

    def __init__(self, on_change):
        self.on_change = on_change
        self._process = None
        self._lock = threading.Lock()

    def start(self):
        with self._lock:
            if self._process and self._process.poll() is None:
                return
            encoded = base64.b64encode(WATCH_SCRIPT.encode("utf-16-le")).decode()
            try:
                self._process = subprocess.Popen(
                    ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    encoding="utf-8", errors="replace", bufsize=1, creationflags=subprocess.CREATE_NO_WINDOW)
            except OSError:
                self._process = None
                return
            threading.Thread(target=self._read, args=(self._process,), daemon=True).start()

    def _read(self, process):
        for line in process.stdout:
            try:
                info = json.loads(line)
            except ValueError:
                continue  # PowerShell noise, not one of our lines
            if isinstance(info, dict):
                try:
                    self.on_change(info)
                except Exception:
                    pass  # e.g. the dock window closed meanwhile

    def press(self, command):
        """'toggle' | 'next' | 'previous' on what Windows shows as playing."""
        if command not in COMMANDS:
            return False
        with self._lock:
            if not self._process or self._process.poll() is not None:
                return False
            try:
                self._process.stdin.write(command + "\n")
                self._process.stdin.flush()
                return True
            except OSError:
                return False

    def stop(self):
        with self._lock:
            process, self._process = self._process, None
        if process and process.poll() is None:
            try:
                process.stdin.close()  # the script stops by itself
                process.wait(2)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
