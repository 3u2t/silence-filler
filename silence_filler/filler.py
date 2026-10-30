from __future__ import annotations

import asyncio
import logging
import threading
import time

from .audio_monitor import AudioMonitor, MicrophoneMonitor
from .spotify_control import PLAYING, SpotifyController


class Filler:
    def __init__(self, cfg, log: logging.Logger):
        self.cfg = cfg
        self.log = log
        self.audio = AudioMonitor(cfg, log)
        self.mic = MicrophoneMonitor(cfg, log)
        self.spotify = SpotifyController(cfg, log)

        self.enabled = True
        self.status = "Startet …"

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

        self._auto_active = False
        self._handled_silence = False
        self._had_audio = False
        self._active_since: float | None = None
        self._silent_since: float | None = None
        self._prev_active = False
        self._prev_mic = False

        self._mic_active = False
        self._mic_apps: list[str] = []
        self._next_mic_check = 0.0
        self._next_sync_check = 0.0
        self._next_pause_attempt = 0.0
        self._last_play_ts = 0.0
        self._spotify_status = None
        self._pause_requested = False

        self._notice: str | None = None
        self._notice_until = 0.0

    # ------------------------------------------------------------------ public

    def start_in_thread(self) -> None:
        self._thread = threading.Thread(target=self.run, name="silence-filler", daemon=True)
        self._thread.start()

    def run(self) -> None:
        try:
            asyncio.run(self._main())
        except KeyboardInterrupt:
            pass
        except Exception:
            self.log.exception("Filler crashed")

    def stop(self) -> None:
        self._stop.set()

    def wait(self, timeout: float = 3.0) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self.log.info("Autofill %s", "enabled" if enabled else "disabled")
        if not enabled and self._auto_active:
            self._pause_requested = True
        if enabled:
            # Start a fresh silence window instead of acting on old timestamps.
            self._silent_since = None
            self._handled_silence = False
            self._notify("Autofill aktiv", 3)

    def reload_config(self) -> None:
        try:
            self.cfg.reload()
            self._notify("Config neu geladen", 4)
            self.log.info("Config reloaded")
        except Exception:
            self.log.exception("Could not reload config")
            self._notify("Config-Fehler (siehe Log)", 8)

    def apply_settings(self) -> None:
        """Called after the settings window wrote config.json."""
        try:
            self.cfg.reload()
        except Exception:
            self.log.exception("Could not apply settings")
            self._notify("Einstellungen: Fehler (siehe Log)", 8)
            return
        self.log.info("Settings saved and applied")
        self._notify("Einstellungen gespeichert", 3)

    def manual_play(self) -> None:
        self._submit(self._manual_play())

    def manual_pause(self) -> None:
        self._submit(self._manual_pause())

    # ----------------------------------------------------------------- private

    def _submit(self, coro) -> None:
        loop = self._loop
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, loop)
        else:
            coro.close()
            self.log.warning("Background loop is not running")

    async def _manual_play(self) -> None:
        self._notify("Starte Spotify …", 10)
        ok = await self.spotify.play()
        if ok:
            self._auto_active = True
            self._last_play_ts = time.monotonic()
            self._spotify_status = PLAYING
            self._handled_silence = True
            self._notify("Spotify läuft", 3)
        else:
            self._notify("Spotify startet nicht", 10)

    async def _manual_pause(self) -> None:
        ok = await self.spotify.pause()
        self._auto_active = False
        self._handled_silence = True
        self._notify("Spotify pausiert" if ok else "Pause fehlgeschlagen", 4)

    def _notify(self, text: str, seconds: float = 6.0) -> None:
        self._notice = text
        self._notice_until = time.monotonic() + seconds

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        await self.spotify.start()
        self._spotify_status = await self.spotify.playback_status()
        self._notify("Bereit", 3)
        self.log.info("Watching audio sessions (threshold %.4f)", self.cfg.audio_threshold)

        while not self._stop.is_set():
            started = time.monotonic()
            try:
                await self._tick()
            except Exception:
                self.log.exception("Tick failed")
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(0.05, self.cfg.poll_interval_seconds - elapsed))

        self.log.info("Stopped")

    async def _tick(self) -> None:
        now = time.monotonic()

        # Disabled: still honour a pending "pause because I turned it off".
        if not self.enabled:
            if self._pause_requested or self._auto_active:
                self._pause_requested = False
                if self._auto_active:
                    await self.spotify.pause()
                    self._auto_active = False
                    self._spotify_status = None
            self.status = "Deaktiviert"
            self._prev_active = False
            self._prev_mic = False
            return

        sample = self.audio.sample()

        if now >= self._next_mic_check:
            mic_active, mic_apps = self.mic.in_use()
            self._mic_active, self._mic_apps = mic_active, mic_apps
            self._next_mic_check = now + 1.0
        else:
            mic_active, mic_apps = self._mic_active, self._mic_apps

        blocking = sample.active or mic_active

        if sample.active:
            self._had_audio = True
            self._silent_since = None
            if self._active_since is None:
                self._active_since = now
        else:
            self._active_since = None

        if mic_active:
            # A call counts as activity and interrupts the silence window.
            self._had_audio = True
            self._silent_since = None

        if not sample.active and not mic_active and self._silent_since is None:
            self._silent_since = now

        if blocking:
            self._handled_silence = False

        if sample.active != self._prev_active:
            if sample.active:
                self.log.debug(
                    "Other audio started: %s (peak %.4f)", ", ".join(sample.sources), sample.peak
                )
            else:
                self.log.debug("Other audio stopped")
        if mic_active != self._prev_mic:
            if mic_active:
                self.log.info("Microphone in use: %s", ", ".join(mic_apps))
            else:
                self.log.info("Microphone released")

        # ---------------------------------------------------------- pause path
        if self._auto_active and now >= self._next_pause_attempt:
            reason = None
            if mic_active:
                reason = f"Mikrofon: {', '.join(mic_apps[:2])}"
            elif (
                sample.active
                and self._active_since is not None
                and now - self._active_since >= self.cfg.pause_delay_seconds
            ):
                reason = f"Audio: {', '.join(sample.sources[:2])}"
            if reason:
                if await self.spotify.pause():
                    self._auto_active = False
                    self._spotify_status = None
                    self.log.info("Paused Spotify (%s)", reason)
                    self._notify(f"Spotify pausiert – {reason}", 4)
                else:
                    self._next_pause_attempt = now + 3.0
                    self.log.warning("Could not pause Spotify (%s)", reason)

        # ---------------------------------------------------------- resume path
        if (
            not self._auto_active
            and not blocking
            and not self._handled_silence
            and self._silent_since is not None
            and now - self._silent_since >= self.cfg.silence_delay_seconds
            and (self._had_audio or self.cfg.start_playing_on_launch)
        ):
            self._handled_silence = True
            silence_for = now - self._silent_since
            self._notify("Stille erkannt – starte Spotify …", 10)
            self.log.info("Silence for %.1fs, starting Spotify", silence_for)
            if await self.spotify.play():
                self._auto_active = True
                self._last_play_ts = time.monotonic()
                self._spotify_status = PLAYING
                self.log.info("Spotify is playing")
                self._notify("Spotify läuft", 3)
            else:
                self._spotify_status = None
                self.log.warning("Spotify could not be started")
                self._notify("Spotify startet nicht (siehe Log)", 10)

        # ------------------------------------------------------ keep state sane
        if now >= self._next_sync_check:
            self._next_sync_check = now + 2.0
            status = await self.spotify.playback_status()
            self._spotify_status = status
            if (
                self._auto_active
                and status != PLAYING
                and now - self._last_play_ts > 2.5
            ):
                self._auto_active = False
                self.log.info("Spotify stopped playing on its own (manual pause/close)")
            elif status == PLAYING and not self._auto_active and not blocking:
                # The user started Spotify manually during a quiet phase: manage
                # it from now on so it pauses again when other audio starts.
                self._auto_active = True
                self.log.info("Adopted manually started Spotify playback")

        self._update_status(sample, mic_active, mic_apps, now)
        self._prev_active = sample.active
        self._prev_mic = mic_active

    def _update_status(self, sample, mic_active: bool, mic_apps: list[str], now: float) -> None:
        if now < self._notice_until and self._notice:
            self.status = self._notice
            return
        if self._auto_active or self._spotify_status == PLAYING:
            self.status = "Spotify läuft"
        elif mic_active:
            self.status = f"Mikrofon: {', '.join(mic_apps[:2])}"
        elif sample.active:
            self.status = f"Audio: {', '.join(sample.sources[:2])}"
        else:
            self.status = "Stille"
