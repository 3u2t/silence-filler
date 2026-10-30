from __future__ import annotations

import argparse
import ctypes
import logging
import sys
from logging.handlers import RotatingFileHandler

from silence_filler import __version__
from silence_filler.config import LOG_PATH, Config
from silence_filler.filler import Filler


def setup_logging(console: bool, debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    root = logging.getLogger()
    root.setLevel(level)

    # comtypes is extremely chatty on DEBUG and drowns out our own messages.
    logging.getLogger("comtypes").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    file_handler = RotatingFileHandler(
        LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    if console:
        stream = logging.StreamHandler()
        stream.setFormatter(fmt)
        root.addHandler(stream)


def enable_dpi_awareness() -> None:
    """Keep tray icon and settings window sharp on scaled displays."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # system DPI aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Start Spotify automatically when no other audio is playing."
    )
    parser.add_argument(
        "--no-tray", action="store_true", help="run in the console without a tray icon"
    )
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args()

    setup_logging(console=args.no_tray or args.debug, debug=args.debug)
    enable_dpi_awareness()
    log = logging.getLogger("silence-filler")
    log.info("Starting Silence Filler %s", __version__)

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    mutex = kernel32.CreateMutexW(None, False, "Local\\SilenceFiller")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        log.info("Another instance is already running")
        if args.no_tray:
            print("Silence Filler is already running.", file=sys.stderr)
        else:
            ctypes.windll.user32.MessageBoxW(
                None, "Silence Filler läuft bereits.", "Silence Filler", 0x40
            )
        return 0

    cfg = Config.load()
    filler = Filler(cfg, log)

    if args.no_tray:
        filler.run()
        return 0

    try:
        from silence_filler.tray import run_tray
    except Exception as exc:
        log.warning("Tray icon not available (%s); running in console mode", exc)
        filler.run()
        return 0

    filler.start_in_thread()
    try:
        run_tray(filler)
    except Exception:
        log.exception("Tray failed; shutting down")
        filler.stop()
        filler.wait(2)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
