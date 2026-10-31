"""
Aura's icon in the system tray (the icons next to the clock).
Click it to open Aura; right-click for Open Aura / Settings / Dock settings / Quit Aura.
When Aura starts with Windows it waits here and in the dock, with no taskbar button.
"""
import threading

import pystray
from PIL import Image


class Tray:
    def __init__(self, icon_file, open_main, open_settings, quit_app):
        menu = pystray.Menu(
            pystray.MenuItem("Open Aura", lambda: open_main(), default=True),  # default = left click
            pystray.MenuItem("Settings", lambda: open_settings()),
            pystray.MenuItem("Dock settings", lambda: open_settings("dock")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Aura", lambda: quit_app()),
        )
        self.icon = pystray.Icon("Aura", Image.open(icon_file), "Aura", menu)
        threading.Thread(target=self.icon.run, daemon=True).start()

    def stop(self):
        self.icon.stop()
