from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, fields
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = APP_DIR / "config.json"
LOG_PATH = APP_DIR / "silence-filler.log"

log = logging.getLogger(__name__)


def _default_spotify_path() -> str:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return ""
    return str(Path(appdata) / "Spotify" / "Spotify.exe")


@dataclass
class Config:
    # How long everything has to be silent before Spotify starts.
    silence_delay_seconds: float = 4.0
    # How long other audio has to play before Spotify is paused again.
    pause_delay_seconds: float = 0.8
    # Per-session peak level (0..1) that counts as "audio is playing".
    audio_threshold: float = 0.0015
    # How often the audio sessions are sampled.
    poll_interval_seconds: float = 0.25

    # Apps that are exempt: their sound and their microphone use never pause
    # Spotify. Add e.g. discord.exe here to keep music running during a call.
    # spotify.exe  -> our own playback must not block itself
    # windhawk.exe -> a Windhawk mod mirrors the system audio peak
    # voicemod.exe, ... -> tools that keep the microphone open all the time
    exception_processes: list[str] = field(
        default_factory=lambda: [
            "spotify.exe",
            "windhawk.exe",
            "discord.exe",
            "discordptb.exe",
            "yapper.exe",
            "voicemod.exe",
            "hxn-srv-audio-engine.exe",
            "nvidia broadcast.exe",
        ]
    )

    # Start the Spotify desktop app if it is not running at all.
    launch_spotify_if_closed: bool = True
    spotify_path: str = field(default_factory=_default_spotify_path)
    start_minimized: bool = True

    # Do not start Spotify while the microphone is being captured (Discord call,
    # Teams/Zoom meeting, ...). Uses active WASAPI capture sessions.
    pause_on_microphone: bool = True

    # Windows error/notification sounds are played by the special "System
    # Sounds" session (PID 0). They are short and should never pause Spotify.
    ignore_system_sounds: bool = True

    # Also start Spotify on launch when the PC is already silent.
    start_playing_on_launch: bool = False

    def validate(self) -> None:
        self.silence_delay_seconds = max(0.5, float(self.silence_delay_seconds))
        self.pause_delay_seconds = max(0.1, float(self.pause_delay_seconds))
        self.audio_threshold = min(0.5, max(0.0, float(self.audio_threshold)))
        self.poll_interval_seconds = min(2.0, max(0.1, float(self.poll_interval_seconds)))

        cleaned: list[str] = []
        for name in self.exception_processes:
            name = str(name).strip().lower()
            if name and name not in cleaned:
                cleaned.append(name)
        if "spotify.exe" not in cleaned:
            cleaned.insert(0, "spotify.exe")
        self.exception_processes = cleaned

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    cls._migrate(data)
                    cls._apply(cfg, data)
            except Exception as exc:
                log.warning("Could not read %s (%s); using defaults", CONFIG_PATH, exc)
        else:
            try:
                cfg.save()
                log.info("Created default config at %s", CONFIG_PATH)
            except Exception as exc:
                log.warning("Could not write default config: %s", exc)
        cfg.validate()
        return cfg

    @staticmethod
    def _migrate(data: dict) -> None:
        """Older configs kept separate lists for sound and microphone."""
        if "exception_processes" in data:
            return
        merged: list[str] = []
        found = False
        for key in ("exclude_processes", "mic_ignore_processes"):
            value = data.get(key)
            if not isinstance(value, list):
                continue
            found = True
            for name in value:
                name = str(name).strip().lower()
                if name and name not in merged:
                    merged.append(name)
        if found:
            data["exception_processes"] = merged

    @staticmethod
    def _apply(cfg: "Config", data: dict) -> None:
        defaults = Config()
        for f in fields(Config):
            if f.name not in data:
                continue
            value = data[f.name]
            default = getattr(defaults, f.name)
            try:
                if isinstance(default, bool):
                    value = bool(value)
                elif isinstance(default, float):
                    value = float(value)
                elif isinstance(default, int):
                    value = int(value)
                elif isinstance(default, list):
                    if not isinstance(value, list):
                        raise TypeError("list expected")
                    value = [str(v) for v in value]
            except (TypeError, ValueError):
                log.warning("Invalid value for %s in config; keeping default", f.name)
                continue
            setattr(cfg, f.name, value)

    def save(self) -> None:
        data = {f.name: getattr(self, f.name) for f in fields(Config)}
        CONFIG_PATH.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    def reload(self) -> None:
        """Reload config.json in place so every component sees the new values."""
        fresh = Config.load()
        for f in fields(Config):
            setattr(self, f.name, getattr(fresh, f.name))
