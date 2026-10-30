from __future__ import annotations

import copy
import ctypes
import logging
import os
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

import psutil

from .config import CONFIG_PATH, LOG_PATH, Config

log = logging.getLogger(__name__)

WINDOW_TITLE = "Silence Filler – Einstellungen"
WM_CLOSE = 0x0010

_lock = threading.Lock()
_thread: threading.Thread | None = None
_user32 = None


# --------------------------------------------------------------------- helpers


def _get_user32():
    global _user32
    if _user32 is None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.FindWindowW.restype = ctypes.c_void_p
        user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        user32.PostMessageW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        _user32 = user32
    return _user32


def _format_number(value: float, decimals: int) -> str:
    return f"{value:.{decimals}f}".replace(".", ",")


def _parse_number(text: str) -> float:
    cleaned = (text or "").strip().replace(" ", "").replace(",", ".")
    if not cleaned:
        raise ValueError("leer")
    return float(cleaned)


def _running_process_names(taken: set[str]) -> list[str]:
    names: set[str] = set()
    for proc in psutil.process_iter(["name"]):
        try:
            name = (proc.info.get("name") or "").strip().lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if name and name not in taken:
            names.add(name)
    return sorted(names)


# ------------------------------------------------------------------ public API


def open_settings(filler) -> None:
    """Open the settings window, or bring the existing one to the front."""
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            _focus_window()
            return
        _thread = threading.Thread(
            target=_run_window, args=(filler,), name="settings-window", daemon=True
        )
        _thread.start()


def close_settings() -> None:
    hwnd = _get_user32().FindWindowW(None, WINDOW_TITLE)
    if hwnd:
        _get_user32().PostMessageW(hwnd, WM_CLOSE, 0, 0)


def _focus_window() -> None:
    user32 = _get_user32()
    hwnd = user32.FindWindowW(None, WINDOW_TITLE)
    if hwnd:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)


def _run_window(filler) -> None:
    try:
        window = SettingsWindow(filler)
    except Exception:
        log.exception("Could not build the settings window")
        return
    try:
        window.root.mainloop()
    except Exception:
        log.exception("Settings window crashed")
    finally:
        try:
            window.root.destroy()
        except Exception:
            pass


# --------------------------------------------------------------------- window


class SettingsWindow:
    def __init__(self, filler):
        self.filler = filler
        self.cfg = filler.cfg

        self.root = tk.Tk()
        self.root.title(WINDOW_TITLE)
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._cancel)
        self.root.bind("<Escape>", lambda _event: self._cancel())
        self.root.bind("<Return>", lambda _event: self._save())
        self._icon_image = None
        self._set_window_icon()

        self._setup_style()
        self._build()
        self._fix_size()
        self._flash()

    def _set_window_icon(self) -> None:
        try:
            from PIL import ImageTk

            from .icon import make_icon

            self._icon_image = ImageTk.PhotoImage(make_icon(True, False))
            self.root.iconphoto(True, self._icon_image)
        except Exception:
            log.debug("Could not set the window icon", exc_info=True)

    # ------------------------------------------------------------------ setup

    def _setup_style(self) -> None:
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(name).configure(family="Segoe UI", size=10)
            except tk.TclError:
                pass
        style.configure("Hint.TLabel", foreground="#5f6368")
        style.configure("Title.TLabel", font=("Segoe UI", 13, "bold"))
        style.configure("Subtitle.TLabel", foreground="#5f6368")

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=(16, 14, 16, 12))
        outer.grid(row=0, column=0, sticky="nsew")

        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(header, text="Einstellungen", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Änderungen gelten sofort nach dem Speichern.",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        self.notebook = ttk.Notebook(outer)
        self.notebook.grid(row=1, column=0, sticky="nsew")

        self._build_behavior_tab()
        self._build_exceptions_tab()
        self._build_advanced_tab()

        buttons = ttk.Frame(outer)
        buttons.grid(row=2, column=0, sticky="ew", pady=(14, 0))
        ttk.Button(buttons, text="Standardwerte", command=self._reset_defaults).pack(
            side="left"
        )
        ttk.Button(
            buttons, text="Speichern", command=self._save, default="active"
        ).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="Abbrechen", command=self._cancel).pack(side="right")

    # ------------------------------------------------------------ behavior tab

    def _build_behavior_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=(16, 14))
        self.notebook.add(tab, text="Verhalten")
        tab.columnconfigure(1, weight=1)

        self.var_silence = tk.StringVar(
            value=_format_number(self.cfg.silence_delay_seconds, 1)
        )
        self.var_pause = tk.StringVar(
            value=_format_number(self.cfg.pause_delay_seconds, 1)
        )

        row = 0
        ttk.Label(tab, text="Spotify starten nach").grid(
            row=row, column=0, sticky="w", pady=(0, 2)
        )
        box = ttk.Frame(tab)
        box.grid(row=row, column=1, sticky="w", pady=(0, 2))
        self._spin(box, self.var_silence, 0.5, 600.0, 0.5, 1).pack(side="left")
        ttk.Label(box, text="Sekunden ohne Ton").pack(side="left", padx=(8, 0))

        ttk.Label(
            tab,
            text="So lange muss es still sein, bevor Spotify weiterläuft.",
            style="Hint.TLabel",
        ).grid(row=row + 1, column=0, columnspan=2, sticky="w", pady=(0, 12))

        row += 2
        ttk.Label(tab, text="Spotify pausieren nach").grid(
            row=row, column=0, sticky="w", pady=(0, 2)
        )
        box = ttk.Frame(tab)
        box.grid(row=row, column=1, sticky="w", pady=(0, 2))
        self._spin(box, self.var_pause, 0.2, 30.0, 0.1, 1).pack(side="left")
        ttk.Label(box, text="Sekunden mit anderem Ton").pack(side="left", padx=(8, 0))

        ttk.Label(
            tab,
            text="Kurz genug, damit Spotify nicht in ein Video hineinläuft.",
            style="Hint.TLabel",
        ).grid(row=row + 1, column=0, columnspan=2, sticky="w", pady=(0, 16))

        row += 2
        ttk.Separator(tab).grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 12))

        row += 1
        self.var_launch = tk.BooleanVar(value=self.cfg.launch_spotify_if_closed)
        self.var_minimized = tk.BooleanVar(value=self.cfg.start_minimized)
        self.var_start_playing = tk.BooleanVar(value=self.cfg.start_playing_on_launch)
        self.var_mic = tk.BooleanVar(value=self.cfg.pause_on_microphone)

        self.chk_launch = ttk.Checkbutton(
            tab,
            text="Spotify starten, wenn es geschlossen ist",
            variable=self.var_launch,
            command=self._sync_minimized,
        )
        self.chk_launch.grid(row=row, column=0, columnspan=2, sticky="w", pady=3)

        self.chk_minimized = ttk.Checkbutton(
            tab,
            text="Spotify dabei minimiert öffnen",
            variable=self.var_minimized,
        )
        self.chk_minimized.grid(
            row=row + 1, column=0, columnspan=2, sticky="w", padx=(22, 0), pady=3
        )
        self._sync_minimized()

        ttk.Checkbutton(
            tab,
            text="Beim Start der App sofort abspielen, wenn es still ist",
            variable=self.var_start_playing,
        ).grid(row=row + 2, column=0, columnspan=2, sticky="w", pady=(8, 3))

        ttk.Checkbutton(
            tab,
            text="Während Anrufen pausiert bleiben (Mikrofon aktiv)",
            variable=self.var_mic,
        ).grid(row=row + 3, column=0, columnspan=2, sticky="w", pady=3)

        status = ttk.LabelFrame(tab, text="Aktuell", padding=(12, 8))
        status.grid(row=row + 4, column=0, columnspan=2, sticky="ew", pady=(20, 0))
        self.var_status = tk.StringVar(value=getattr(self.filler, "status", "–"))
        ttk.Label(
            status, textvariable=self.var_status, font=("Segoe UI", 10, "bold")
        ).pack(anchor="w")
        ttk.Label(
            status,
            text="Live aus der Überwachung übernommen.",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(2, 0))
        self._poll_status()

    def _poll_status(self) -> None:
        try:
            self.var_status.set(getattr(self.filler, "status", "–"))
            self.root.after(1000, self._poll_status)
        except tk.TclError:
            pass

    # ---------------------------------------------------------- exceptions tab

    def _build_exceptions_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=(16, 14))
        self.notebook.add(tab, text="Ausnahmen")

        exceptions = [
            p for p in self.cfg.exception_processes if p.lower() != "spotify.exe"
        ]
        self.tree_exceptions = self._process_list(
            tab,
            "Diese Apps pausieren Spotify nicht",
            "Spotify läuft weiter, auch wenn diese Apps Ton machen oder das "
            "Mikrofon benutzen – zum Beispiel discord.exe. Spotify selbst ist "
            "immer ausgenommen.",
            exceptions,
            height=8,
        )

        self.var_system_sounds = tk.BooleanVar(value=self.cfg.ignore_system_sounds)
        ttk.Checkbutton(
            tab,
            text="Windows-Systemtöne ignorieren "
            "(Fehler- und Benachrichtigungssounds)",
            variable=self.var_system_sounds,
        ).pack(anchor="w", pady=(0, 10))

        info = ttk.LabelFrame(tab, text="Gut zu wissen", padding=(12, 8))
        info.pack(fill="x", pady=(0, 0))
        ttk.Label(
            info,
            text="Die Ausnahmen gelten für beides: Ton und Mikrofon. "
            "Trägt man Discord ein, läuft Spotify also auch im Discord-Call "
            "weiter. Andere Apps wie ChatGPT pausieren Spotify weiterhin, "
            "sobald sie das Mikrofon benutzen. Kurze Windows-Systemtöne "
            "pausieren Spotify ebenfalls nicht.",
            style="Hint.TLabel",
            wraplength=520,
            justify="left",
        ).pack(anchor="w")
        ttk.Label(
            info,
            text="Tipp: „Hinzufügen …“ zeigt alle gerade laufenden Programme.",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(6, 0))

    def _process_list(
        self, parent, title, hint, values, height: int = 4
    ) -> ttk.Treeview:
        frame = ttk.LabelFrame(parent, text=title, padding=(10, 8))
        frame.pack(fill="both", expand=True, pady=(0, 10))

        ttk.Label(
            frame, text=hint, style="Hint.TLabel", wraplength=520, justify="left"
        ).pack(anchor="w", pady=(0, 8))

        body = ttk.Frame(frame)
        body.pack(fill="both", expand=True)

        tree = ttk.Treeview(
            body,
            columns=("name",),
            show="headings",
            height=height,
            selectmode="extended",
        )
        tree.heading("name", text="Programm", anchor="w")
        tree.column("name", width=420, anchor="w")
        tree.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(body, orient="vertical", command=tree.yview)
        scroll.pack(side="right", fill="y")
        tree.configure(yscrollcommand=scroll.set)

        for value in values:
            tree.insert("", "end", values=(value,))

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(
            buttons, text="Hinzufügen …", command=lambda: self._add_process(tree)
        ).pack(side="left")
        ttk.Button(
            buttons, text="Entfernen", command=lambda: self._remove_process(tree)
        ).pack(side="left", padx=(6, 0))
        return tree

    # ------------------------------------------------------------ advanced tab

    def _build_advanced_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=(16, 14))
        self.notebook.add(tab, text="Erweitert")
        tab.columnconfigure(1, weight=1)

        self.var_threshold = tk.StringVar(
            value=_format_number(self.cfg.audio_threshold, 4)
        )
        self.var_poll = tk.StringVar(
            value=_format_number(self.cfg.poll_interval_seconds, 2)
        )
        self.var_spotify_path = tk.StringVar(value=self.cfg.spotify_path)

        row = 0
        ttk.Label(tab, text="Pegel-Schwelle").grid(row=row, column=0, sticky="w")
        self._spin(tab, self.var_threshold, 0.0001, 0.5, 0.0005, 4).grid(
            row=row, column=1, sticky="w", padx=(10, 0)
        )
        ttk.Label(
            tab,
            text="Standard 0,0015. Höher = unempfindlicher, niedriger = "
            "empfindlicher.",
            style="Hint.TLabel",
        ).grid(row=row + 1, column=0, columnspan=2, sticky="w", pady=(0, 12))

        row += 2
        ttk.Label(tab, text="Abtastrate").grid(row=row, column=0, sticky="w")
        box = ttk.Frame(tab)
        box.grid(row=row, column=1, sticky="w", padx=(10, 0))
        self._spin(box, self.var_poll, 0.1, 2.0, 0.05, 2).pack(side="left")
        ttk.Label(box, text="Sekunden").pack(side="left", padx=(8, 0))
        ttk.Label(
            tab,
            text="Kleiner = reagiert schneller, kostet aber mehr CPU. "
            "Standard 0,25.",
            style="Hint.TLabel",
        ).grid(row=row + 1, column=0, columnspan=2, sticky="w", pady=(0, 12))

        row += 2
        ttk.Label(tab, text="Spotify-Programm").grid(
            row=row, column=0, columnspan=2, sticky="w"
        )
        path_box = ttk.Frame(tab)
        path_box.grid(
            row=row + 1, column=0, columnspan=2, sticky="ew", pady=(4, 0)
        )
        path_box.columnconfigure(0, weight=1)
        ttk.Entry(path_box, textvariable=self.var_spotify_path).grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Button(path_box, text="Durchsuchen …", command=self._browse_spotify).grid(
            row=0, column=1, padx=(6, 0)
        )
        ttk.Label(
            tab,
            text="Nur nötig, wenn Spotify woanders installiert ist.",
            style="Hint.TLabel",
        ).grid(row=row + 2, column=0, columnspan=2, sticky="w", pady=(4, 16))

        row += 3
        ttk.Separator(tab).grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        row += 1

        quick = ttk.LabelFrame(tab, text="Schnellzugriff", padding=(12, 8))
        quick.grid(row=row, column=0, columnspan=2, sticky="ew")
        ttk.Button(
            quick, text="Spotify starten", command=lambda: self._call_filler("manual_play")
        ).pack(side="left")
        ttk.Button(
            quick, text="Spotify pausieren", command=lambda: self._call_filler("manual_pause")
        ).pack(side="left", padx=(6, 0))
        ttk.Button(quick, text="Log öffnen", command=self._open_log).pack(
            side="left", padx=(6, 0)
        )

        row += 1
        ttk.Label(
            tab,
            text="Die Einstellungen werden in config.json gespeichert. "
            "Für den Alltag brauchst du die Datei nicht.",
            style="Hint.TLabel",
            wraplength=520,
            justify="left",
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(16, 0))
        ttk.Button(tab, text="config.json öffnen", command=self._open_config).grid(
            row=row + 1, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )

    def _call_filler(self, name: str) -> None:
        action = getattr(self.filler, name, None)
        if callable(action):
            action()

    def _open_log(self) -> None:
        try:
            os.startfile(LOG_PATH)
        except Exception as exc:
            messagebox.showerror("Konnte nicht öffnen", str(exc), parent=self.root)

    # ----------------------------------------------------------------- widgets

    def _spin(
        self, parent, variable: tk.StringVar, low: float, high: float, step: float,
        decimals: int,
    ) -> ttk.Spinbox:
        spin = ttk.Spinbox(
            parent,
            from_=low,
            to=high,
            increment=step,
            width=10,
            textvariable=variable,
        )

        def normalize(*_args) -> None:
            try:
                value = _parse_number(variable.get())
            except ValueError:
                return
            variable.set(_format_number(value, decimals))

        spin.configure(command=normalize)
        spin.bind("<FocusOut>", normalize)
        return spin

    def _sync_minimized(self) -> None:
        if self.var_launch.get():
            self.chk_minimized.state(["!disabled"])
        else:
            self.chk_minimized.state(["disabled"])

    def _add_process(self, tree: ttk.Treeview) -> None:
        taken = {
            str(tree.item(item, "values")[0]).lower() for item in tree.get_children()
        }
        dialog = _ProcessDialog(self.root, "Programm hinzufügen", taken)
        self.root.wait_window(dialog)
        if dialog.result:
            tree.insert("", "end", values=(dialog.result,))

    def _remove_process(self, tree: ttk.Treeview) -> None:
        for item in tree.selection():
            tree.delete(item)

    def _browse_spotify(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Spotify.exe auswählen",
            filetypes=[("Programm", "*.exe"), ("Alle Dateien", "*.*")],
        )
        if path:
            self.var_spotify_path.set(path)

    def _open_config(self) -> None:
        try:
            os.startfile(CONFIG_PATH)
        except Exception as exc:
            messagebox.showerror(
                "Konnte nicht öffnen", str(exc), parent=self.root
            )

    # -------------------------------------------------------------------- data

    @staticmethod
    def _tree_values(tree: ttk.Treeview) -> list[str]:
        values = []
        for item in tree.get_children():
            name = str(tree.item(item, "values")[0]).strip().lower()
            if name and name not in values:
                values.append(name)
        return values

    def _read_number(
        self, variable: tk.StringVar, label: str, low: float, high: float,
        decimals: int,
    ) -> float:
        try:
            value = _parse_number(variable.get())
        except ValueError:
            raise ValueError(f"„{label}“ ist keine gültige Zahl.") from None
        if not low <= value <= high:
            raise ValueError(
                f"„{label}“ muss zwischen {_format_number(low, decimals)} und "
                f"{_format_number(high, decimals)} liegen."
            )
        return value

    def _collect(self) -> Config:
        new = copy.deepcopy(self.cfg)
        new.silence_delay_seconds = self._read_number(
            self.var_silence, "Stille-Verzögerung", 0.5, 600.0, 1
        )
        new.pause_delay_seconds = self._read_number(
            self.var_pause, "Pause-Verzögerung", 0.2, 30.0, 1
        )
        new.audio_threshold = self._read_number(
            self.var_threshold, "Pegel-Schwelle", 0.0001, 0.5, 4
        )
        new.poll_interval_seconds = self._read_number(
            self.var_poll, "Abtastrate", 0.1, 2.0, 2
        )
        new.spotify_path = self.var_spotify_path.get().strip()
        new.launch_spotify_if_closed = bool(self.var_launch.get())
        new.start_minimized = bool(self.var_minimized.get())
        new.start_playing_on_launch = bool(self.var_start_playing.get())
        new.pause_on_microphone = bool(self.var_mic.get())
        new.ignore_system_sounds = bool(self.var_system_sounds.get())
        new.exception_processes = self._tree_values(self.tree_exceptions)
        new.validate()
        return new

    # ----------------------------------------------------------------- actions

    def _save(self) -> None:
        try:
            new = self._collect()
        except ValueError as exc:
            messagebox.showerror("Eingabe prüfen", str(exc), parent=self.root)
            return

        try:
            new.save()
        except Exception as exc:
            log.exception("Could not save settings")
            messagebox.showerror(
                "Speichern fehlgeschlagen",
                f"config.json konnte nicht geschrieben werden:\n{exc}",
                parent=self.root,
            )
            return

        self.filler.apply_settings()
        self._close()

    def _reset_defaults(self) -> None:
        defaults = Config()
        self.var_silence.set(_format_number(defaults.silence_delay_seconds, 1))
        self.var_pause.set(_format_number(defaults.pause_delay_seconds, 1))
        self.var_threshold.set(_format_number(defaults.audio_threshold, 4))
        self.var_poll.set(_format_number(defaults.poll_interval_seconds, 2))
        self.var_spotify_path.set(defaults.spotify_path)
        self.var_launch.set(defaults.launch_spotify_if_closed)
        self.var_minimized.set(defaults.start_minimized)
        self.var_start_playing.set(defaults.start_playing_on_launch)
        self.var_mic.set(defaults.pause_on_microphone)
        self.var_system_sounds.set(defaults.ignore_system_sounds)

        self.tree_exceptions.delete(*self.tree_exceptions.get_children())
        for name in defaults.exception_processes:
            if name.lower() != "spotify.exe":
                self.tree_exceptions.insert("", "end", values=(name,))

        self._sync_minimized()

    def _cancel(self) -> None:
        self._close()

    def _close(self) -> None:
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ layout

    def _fix_size(self) -> None:
        self.root.update_idletasks()
        width = height = 0
        for tab_id in self.notebook.tabs():
            self.notebook.select(tab_id)
            self.root.update_idletasks()
            width = max(width, self.root.winfo_reqwidth())
            height = max(height, self.root.winfo_reqheight())
        self.notebook.select(0)
        self.root.update_idletasks()

        x = max(0, (self.root.winfo_screenwidth() - width) // 2)
        y = max(0, (self.root.winfo_screenheight() - height) // 2 - 40)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _flash(self) -> None:
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(300, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()


class _ProcessDialog(tk.Toplevel):
    def __init__(self, parent, title: str, taken: set[str]):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.result: str | None = None

        frame = ttk.Frame(self, padding=(16, 14))
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame, text="Programm auswählen oder Namen eintippen:"
        ).pack(anchor="w")
        self.var = tk.StringVar()
        combo = ttk.Combobox(
            frame,
            textvariable=self.var,
            values=_running_process_names(taken),
            width=46,
        )
        combo.pack(fill="x", pady=(6, 12))
        combo.focus_set()

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Hinzufügen", command=self._ok).pack(side="right")
        ttk.Button(buttons, text="Abbrechen", command=self._cancel).pack(
            side="right", padx=(0, 6)
        )

        self.bind("<Return>", lambda _event: self._ok())
        self.bind("<Escape>", lambda _event: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_reqwidth()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

        self.transient(parent)
        self.grab_set()

    def _ok(self) -> None:
        name = self.var.get().strip().lower()
        if not name:
            messagebox.showwarning(
                "Kein Name", "Bitte ein Programm auswählen oder eintippen.",
                parent=self,
            )
            return
        self.result = name
        self._cancel()

    def _cancel(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
