from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

import comtypes
import psutil
from pycaw.api.audiopolicy import IAudioSessionControl2, IAudioSessionManager2
from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
from pycaw.constants import CLSID_MMDeviceEnumerator, EDataFlow, ERole
from pycaw.pycaw import IAudioMeterInformation


@dataclass
class AudioSample:
    active: bool
    peak: float
    sources: list[str]


def _process_name(pid: int) -> str:
    if pid == 0:
        return "<system>"
    try:
        return psutil.Process(pid).name()
    except Exception:
        return f"pid:{pid}"


class _DeviceSessionManager:
    """Caches the default endpoint's session manager.

    Calling pycaw's AudioUtilities.GetAllSessions() opens the property store of
    the device on every call (~150-200 ms here). That is far too expensive for a
    background monitor, so we activate IAudioSessionManager2 directly and only
    refresh it when the default device might have changed.
    """

    def __init__(self, flow, log: logging.Logger):
        self._flow = flow
        self._log = log
        self._enumerator = None
        self._manager = None
        self._device_id = None
        self._checked = 0.0

    def get(self):
        now = time.monotonic()
        if self._manager is not None and now - self._checked < 30.0:
            return self._manager
        try:
            if self._enumerator is None:
                self._enumerator = comtypes.CoCreateInstance(
                    CLSID_MMDeviceEnumerator,
                    IMMDeviceEnumerator,
                    comtypes.CLSCTX_INPROC_SERVER,
                )
            device = self._enumerator.GetDefaultAudioEndpoint(
                self._flow, ERole.eMultimedia.value
            )
            device_id = device.GetId()
            self._checked = now
            if self._manager is not None and device_id == self._device_id:
                return self._manager
            iface = device.Activate(IAudioSessionManager2._iid_, comtypes.CLSCTX_ALL, None)
            self._manager = iface.QueryInterface(IAudioSessionManager2)
            self._device_id = device_id
        except Exception as exc:
            self._log.debug("Could not open audio session manager: %s", exc)
            self._manager = None
        return self._manager


class AudioMonitor:
    """Checks all render sessions on the default output device for sound."""

    def __init__(self, cfg, log: logging.Logger):
        self.cfg = cfg
        self.log = log
        self._own_pid = os.getpid()
        self._manager = _DeviceSessionManager(EDataFlow.eRender.value, log)
        self._names: dict[int, tuple[str, float]] = {}

    def _name_for(self, pid: int, now: float) -> str:
        cached = self._names.get(pid)
        if cached is not None and now - cached[1] < 10.0:
            return cached[0]
        name = _process_name(pid)
        self._names[pid] = (name, now)
        return name

    def sample(self) -> AudioSample:
        threshold = float(self.cfg.audio_threshold)
        excluded = {p.lower() for p in self.cfg.exception_processes}
        best = 0.0
        sources: list[str] = []
        now = time.monotonic()

        manager = self._manager.get()
        if manager is None:
            return AudioSample(False, 0.0, [])

        try:
            enumerator = manager.GetSessionEnumerator()
            count = enumerator.GetCount()
        except Exception as exc:
            self.log.debug("Could not enumerate audio sessions: %s", exc)
            return AudioSample(False, 0.0, [])

        for i in range(count):
            try:
                control = enumerator.GetSession(i)
                if control is None:
                    continue
                control2 = control.QueryInterface(IAudioSessionControl2)
                # 1 == AudioSessionStateActive
                if control2.GetState() != 1:
                    continue

                pid = control2.GetProcessId()
                if pid == self._own_pid:
                    continue
                # PID 0 is the "System Sounds" session: Windows error beeps,
                # notification dings, etc. They must not pause Spotify.
                if pid == 0 and self.cfg.ignore_system_sounds:
                    continue
                name = self._name_for(pid, now)
                if name.lower() in excluded:
                    continue

                meter = control2.QueryInterface(IAudioMeterInformation)
                peak = float(meter.GetPeakValue())
            except Exception:
                # Sessions can vanish between enumeration and querying.
                continue

            if peak >= threshold:
                best = max(best, peak)
                if name not in sources:
                    sources.append(name)

        return AudioSample(bool(sources), best, sources)


class MicrophoneMonitor:
    """Detects whether an app is actively capturing the default microphone."""

    def __init__(self, cfg, log: logging.Logger):
        self.cfg = cfg
        self.log = log
        self._own_pid = os.getpid()
        self._manager = _DeviceSessionManager(EDataFlow.eCapture.value, log)
        self._names: dict[int, tuple[str, float]] = {}

    def _name_for(self, pid: int, now: float) -> str:
        cached = self._names.get(pid)
        if cached is not None and now - cached[1] < 10.0:
            return cached[0]
        name = _process_name(pid)
        self._names[pid] = (name, now)
        return name

    def in_use(self) -> tuple[bool, list[str]]:
        if not self.cfg.pause_on_microphone:
            return False, []

        ignored = {p.lower() for p in self.cfg.exception_processes}
        apps: list[str] = []
        now = time.monotonic()

        manager = self._manager.get()
        if manager is None:
            return False, []

        try:
            enumerator = manager.GetSessionEnumerator()
            count = enumerator.GetCount()
        except Exception as exc:
            self.log.debug("Could not enumerate microphone sessions: %s", exc)
            return False, []

        for i in range(count):
            try:
                control = enumerator.GetSession(i)
                if control is None:
                    continue
                control2 = control.QueryInterface(IAudioSessionControl2)
                # 1 == AudioSessionStateActive
                if control2.GetState() != 1:
                    continue
                pid = control2.GetProcessId()
                if pid == 0 or pid == self._own_pid:
                    continue
                name = self._name_for(pid, now)
                if name.lower() in ignored:
                    continue
                if name not in apps:
                    apps.append(name)
            except Exception:
                continue

        return bool(apps), apps
