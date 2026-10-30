from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path

import psutil
from winsdk.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as SessionManager,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
)

PLAYING = PlaybackStatus.PLAYING


class SpotifyController:
    """Controls the Spotify desktop app through the Windows media session API.

    No Spotify API key and no Premium subscription are required: this uses the
    same media session that the play/pause keys on a keyboard talk to.
    """

    def __init__(self, cfg, log: logging.Logger):
        self.cfg = cfg
        self.log = log
        self._manager = None

    async def start(self) -> None:
        try:
            self._manager = await SessionManager.request_async()
            self.log.info("Windows media session manager ready")
        except Exception as exc:
            self.log.error("Could not access Windows media sessions: %s", exc)

    async def _find_session(self):
        if self._manager is None:
            await self.start()
        if self._manager is None:
            return None
        try:
            for session in self._manager.get_sessions():
                app_id = (session.source_app_user_model_id or "").lower()
                if "spotify" in app_id:
                    return session
        except Exception as exc:
            self.log.debug("Could not list media sessions: %s", exc)
        return None

    async def playback_status(self):
        session = await self._find_session()
        if session is None:
            return None
        try:
            return session.get_playback_info().playback_status
        except Exception:
            return None

    async def play(self) -> bool:
        session = await self._find_session()

        if session is None and self.cfg.launch_spotify_if_closed:
            if not self._spotify_process_running():
                if not self._launch_spotify():
                    return False
            # Wait for Spotify to register its media session.
            for _ in range(30):
                await asyncio.sleep(0.5)
                session = await self._find_session()
                if session is not None:
                    break

        if session is None:
            self.log.warning("No Spotify media session found")
            return False

        try:
            info = session.get_playback_info()
            if info.playback_status == PLAYING:
                return True

            # A cold start sometimes ignores the first play request, so try twice.
            for attempt in range(2):
                ok = await session.try_play_async()
                self.log.info("Spotify try_play (attempt %d) -> %s", attempt + 1, ok)
                for _ in range(10):
                    await asyncio.sleep(0.25)
                    if session.get_playback_info().playback_status == PLAYING:
                        return True
                await asyncio.sleep(0.5)
        except Exception as exc:
            self.log.warning("Spotify play failed: %s", exc)
        return False

    async def pause(self) -> bool:
        session = await self._find_session()
        if session is None:
            return True  # nothing to pause
        try:
            if session.get_playback_info().playback_status != PLAYING:
                return True
            ok = await session.try_pause_async()
            self.log.info("Spotify try_pause -> %s", ok)
            for _ in range(5):
                await asyncio.sleep(0.2)
                if session.get_playback_info().playback_status != PLAYING:
                    return True
            return False
        except Exception as exc:
            self.log.warning("Spotify pause failed: %s", exc)
            return False

    def _spotify_process_running(self) -> bool:
        for proc in psutil.process_iter(["name"]):
            try:
                if (proc.info["name"] or "").lower() == "spotify.exe":
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return False

    def _launch_spotify(self) -> bool:
        path = Path(self.cfg.spotify_path or "")
        if not path.is_file():
            appdata = os.environ.get("APPDATA", "")
            path = Path(appdata) / "Spotify" / "Spotify.exe"
        if not path.is_file():
            self.log.error("Spotify.exe not found (checked %s)", path)
            return False

        args = [str(path)]
        if self.cfg.start_minimized:
            args.append("--minimized")
        try:
            subprocess.Popen(
                args,
                cwd=str(path.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.log.info("Launched Spotify: %s", path)
            return True
        except Exception as exc:
            self.log.error("Could not launch Spotify: %s", exc)
            return False
