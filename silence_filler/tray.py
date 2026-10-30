from __future__ import annotations

import ctypes
import logging
import os
import threading
import time

import pystray
from PIL import Image
from pystray import Menu, MenuItem

from .config import LOG_PATH
from .icon import make_icon

log = logging.getLogger(__name__)


def _make_image(filler) -> Image.Image:
    return make_icon(filler.enabled, getattr(filler, "_auto_active", False))


def run_tray(filler) -> None:
    def on_toggle(icon, item):
        filler.set_enabled(not filler.enabled)
        icon.update_menu()

    def on_play(icon, item):
        filler.manual_play()

    def on_pause(icon, item):
        filler.manual_pause()

    def on_settings(icon, item):
        try:
            from .settings_ui import open_settings
        except Exception as exc:
            log.exception("Settings window unavailable")
            ctypes.windll.user32.MessageBoxW(
                None,
                f"Einstellungen konnten nicht geöffnet werden:\n{exc}",
                "Silence Filler",
                0x10,
            )
            return
        open_settings(filler)

    def on_log(icon, item):
        os.startfile(LOG_PATH)

    def on_quit(icon, item):
        try:
            from .settings_ui import close_settings

            close_settings()
        except Exception:
            pass
        filler.stop()
        icon.stop()

    menu = Menu(
        MenuItem(lambda item: f"Status: {filler.status}", None, enabled=False),
        Menu.SEPARATOR,
        MenuItem("Autofill aktiv", on_toggle, checked=lambda item: filler.enabled),
        Menu.SEPARATOR,
        MenuItem("Spotify jetzt starten", on_play),
        MenuItem("Spotify pausieren", on_pause),
        Menu.SEPARATOR,
        MenuItem("Einstellungen …", on_settings, default=True),
        MenuItem("Log öffnen", on_log),
        Menu.SEPARATOR,
        MenuItem("Beenden", on_quit),
    )

    icon = pystray.Icon("silence-filler", _make_image(filler), "Silence Filler", menu)

    def updater():
        last_icon_key = None
        while not filler._stop.is_set():
            try:
                key = (filler.enabled, getattr(filler, "_auto_active", False))
                if key != last_icon_key:
                    icon.icon = _make_image(filler)
                    last_icon_key = key
                icon.title = f"Silence Filler – {filler.status}"
                icon.update_menu()
            except Exception:
                pass
            time.sleep(2)

    def setup(icon):
        icon.visible = True
        threading.Thread(target=updater, name="tray-updater", daemon=True).start()

    icon.run(setup=setup)
    filler.wait(3)
