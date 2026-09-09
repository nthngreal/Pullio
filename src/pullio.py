import json
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from io import BytesIO
from pathlib import Path

import customtkinter as ctk
from tkinter import filedialog, messagebox

APP_NAME = "Pullio"
APP_VERSION = "1.2.2"
HISTORY_LIMIT = 50

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# Pullio v1.1 UI palette
BG = "#121214"
SURFACE = "#1A1A1E"
SURFACE_2 = "#232328"
HOVER = "#2A2A30"
TEXT = "#F2F2F4"
TEXT_SECONDARY = "#9A9AA3"
TEXT_MUTED = "#686870"
ACCENT = "#2F8CFF"
ACCENT_HOVER = "#2376D8"
SUCCESS = "#22C55E"
DANGER = "#EF4444"
RADIUS = 8

def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        # Public/portable build: dependencies live next to Pullio.exe.
        return Path(sys.executable).resolve().parent

    # Development build: pullio.py lives in /src, while yt-dlp and FFmpeg
    # intentionally live in the project root next to run_dev.bat.
    source_dir = Path(__file__).resolve().parent
    project_root = source_dir.parent
    if (project_root / "yt-dlp.exe").exists() or (project_root / "ffmpeg.exe").exists():
        return project_root
    return source_dir

BASE_DIR = app_dir()
YTDLP = BASE_DIR / "yt-dlp.exe"
FFMPEG = BASE_DIR / "ffmpeg.exe"
DEFAULT_DOWNLOADS = BASE_DIR / "Downloads"
SETTINGS_FILE = BASE_DIR / "pullio_settings.json"
HISTORY_FILE = BASE_DIR / "pullio_history.json"

VIDEO_QUALITIES = ["MAX", "4K", "1440p", "1080p", "720p"]
AUDIO_QUALITIES = ["Best", "320 kbps", "256 kbps", "192 kbps", "128 kbps"]

def seconds_to_hms(sec):
    try:
        sec = int(sec)
    except Exception:
        return "—"
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

def looks_like_youtube_url(value: str) -> bool:
    try:
        u = urllib.parse.urlparse(value.strip())
        host = (u.netloc or "").lower()
        return u.scheme in ("http", "https") and ("youtube.com" in host or "youtu.be" in host)
    except Exception:
        return False


def has_playlist_param(value: str) -> bool:
    """Return True when a YouTube URL contains a playlist/list parameter."""
    try:
        u = urllib.parse.urlparse(value.strip())
        q = urllib.parse.parse_qs(u.query)
        return bool(q.get("list"))
    except Exception:
        return False

def fmt_res(data):
    w, h, fps = data.get("width"), data.get("height"), data.get("fps")
    bits = []
    if w and h:
        bits.append(f"{w}×{h}")
    elif h:
        bits.append(f"{h}p")
    if fps:
        try:
            bits.append(f"{int(round(float(fps)))} FPS")
        except Exception:
            pass
    return " • ".join(bits) if bits else "—"

def pick_codec_summary(data):
    vcodec = data.get("vcodec")
    acodec = data.get("acodec")
    bits = []
    if vcodec and vcodec != "none":
        bits.append(str(vcodec).split(".")[0].upper())
    if acodec and acodec != "none":
        bits.append(str(acodec).split(".")[0].upper())
    return " + ".join(bits) if bits else "—"


def apply_window_icon(window):
    """Use pullio.ico if present. Safe to ignore when developing without an icon."""
    icon_path = BASE_DIR / "pullio.ico"
    if icon_path.exists():
        try:
            window.iconbitmap(str(icon_path))
        except Exception:
            pass


class ToolTip:
    """Tiny hover tooltip for compact secondary explanations."""
    def __init__(self, widget, text, delay=450):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.job = None
        self.tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self.job = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self.job:
            try:
                self.widget.after_cancel(self.job)
            except Exception:
                pass
            self.job = None

    def _show(self):
        self.job = None
        if self.tip and self.tip.winfo_exists():
            return
        self.tip = ctk.CTkToplevel(self.widget)
        self.tip.overrideredirect(True)
        self.tip.attributes("-topmost", True)
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        self.tip.geometry(f"+{x}+{y}")
        frame = ctk.CTkFrame(self.tip, fg_color="#232328", corner_radius=8)
        frame.pack()
        ctk.CTkLabel(
            frame,
            text=self.text,
            text_color="#F2F2F4",
            justify="left",
            font=ctk.CTkFont(size=11),
        ).pack(padx=10, pady=7)

    def _hide(self, _event=None):
        self._cancel()
        if self.tip and self.tip.winfo_exists():
            self.tip.destroy()
        self.tip = None


class DownloaderApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        apply_window_icon(self)
        self.update_idletasks()
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()

        # Fit inside the usable desktop area on common 768p/900p/1080p screens.
        # Leave room for the Windows taskbar and title bar so the bottom controls
        # are visible immediately without manually resizing/maximizing.
        window_w = min(920, max(820, screen_w - 80))
        window_h = min(720, max(640, screen_h - 90))
        self.geometry(f"{window_w}x{window_h}")
        self.minsize(min(820, window_w), min(640, window_h))

        self.msg_queue = queue.Queue()
        self.worker = None
        self.proc = None
        self.metadata = {}
        self.thumbnail_img = None
        self.thumbnail_generation = 0
        self.last_file = None
        self.fetch_generation = 0
        self.auto_fetch_job = None
        self.details_open = False
        self.more_menu = None
        self.updating_ytdlp = False
        self.duplicate_modal = None
        self.modal_backdrop = None
        self.last_clipboard_url = ""

        settings = self.load_settings()

        self.url_var = ctk.StringVar()
        self.mode_var = ctk.StringVar(value=settings.get("mode", "Video"))
        self.video_quality_var = ctk.StringVar(value=settings.get("video_quality", "MAX"))
        self.video_container_var = ctk.StringVar(value=settings.get("video_container", "mp4"))
        self.capcut_var = ctk.BooleanVar(value=settings.get("capcut", False))
        self.audio_quality_var = ctk.StringVar(value=settings.get("audio_quality", "Best"))
        self.output_var = ctk.StringVar(value=settings.get("folder", str(DEFAULT_DOWNLOADS)))
        self.folder_name_var = ctk.StringVar(value=Path(self.output_var.get()).name or self.output_var.get())

        self.build_ui()
        self.apply_mode()
        self.check_dependencies()

        self.url_var.trace_add("write", self.on_url_changed)
        self.after(350, self.check_clipboard_on_start)
        self.bind("<FocusIn>", self._on_app_focus, add="+")
        self.after(100, self.process_queue)
        self.after_idle(self.fit_window_to_screen)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def build_ui(self):
        self.configure(fg_color=BG)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        root = ctk.CTkFrame(self, fg_color="transparent")
        root.grid(row=0, column=0, sticky="nsew", padx=22, pady=18)
        root.grid_columnconfigure(0, weight=1)

        # Header — quiet branding, utilities behind one compact menu.
        header = ctk.CTkFrame(root, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header, text="Pullio", text_color=TEXT,
            font=ctk.CTkFont(size=27, weight="bold")
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header, text="Video & Audio Downloader", text_color=TEXT_SECONDARY
        ).grid(row=1, column=0, sticky="w", pady=(1, 0))

        self.more_btn = ctk.CTkButton(
            header, text="⋯", width=38, height=34, corner_radius=RADIUS,
            fg_color="transparent", hover_color=HOVER, text_color=TEXT,
            font=ctk.CTkFont(size=20, weight="bold"),
            command=self.show_more_menu
        )
        self.more_btn.grid(row=0, column=1, rowspan=2, sticky="e")

        # URL input — one visual object, Paste lives inside the field shell.
        url_block = ctk.CTkFrame(root, fg_color="transparent")
        url_block.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        url_block.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            url_block, text="YouTube URL", text_color=TEXT_SECONDARY,
            font=ctk.CTkFont(size=12, weight="bold")
        ).grid(row=0, column=0, sticky="w", pady=(0, 6))

        input_shell = ctk.CTkFrame(url_block, fg_color=SURFACE_2, corner_radius=RADIUS)
        input_shell.grid(row=1, column=0, sticky="ew")
        input_shell.grid_columnconfigure(0, weight=1)

        self.url_entry = ctk.CTkEntry(
            input_shell, textvariable=self.url_var,
            placeholder_text="Paste a YouTube link…",
            height=42, border_width=0, corner_radius=RADIUS,
            fg_color="transparent", text_color=TEXT, placeholder_text_color=TEXT_MUTED
        )
        self.url_entry.grid(row=0, column=0, sticky="ew", padx=(8, 2))
        self.url_entry.bind("<Control-KeyPress>", self._url_ctrl_keypress, add="+")

        self.clear_btn = ctk.CTkButton(
            input_shell, text="×", width=30, height=30, corner_radius=RADIUS,
            fg_color="transparent", hover_color=HOVER, text_color=TEXT_SECONDARY,
            font=ctk.CTkFont(size=18), command=self.clear_url
        )
        self.clear_btn.grid(row=0, column=1, padx=(2, 0), pady=6)
        self.clear_btn.grid_remove()

        self.paste_btn = ctk.CTkButton(
            input_shell, text="Paste", width=66, height=30, corner_radius=RADIUS,
            fg_color="transparent", hover_color=HOVER, text_color=TEXT_SECONDARY,
            command=self.paste_url
        )
        self.paste_btn.grid(row=0, column=2, padx=(0, 6), pady=6)

        # Compact video info surface.
        meta = ctk.CTkFrame(root, fg_color=SURFACE, corner_radius=RADIUS)
        meta.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        meta.grid_columnconfigure(1, weight=1)

        self.thumb_label = ctk.CTkLabel(
            meta, text="▶", width=200, height=112, corner_radius=RADIUS,
            fg_color=SURFACE_2, text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=26, weight="bold")
        )
        self.thumb_label.grid(row=0, column=0, rowspan=4, padx=12, pady=12, sticky="w")

        self.title_label = ctk.CTkLabel(
            meta, text="Paste a YouTube link", text_color=TEXT,
            font=ctk.CTkFont(size=16, weight="bold"),
            anchor="w", justify="left", wraplength=560
        )
        self.title_label.grid(row=0, column=1, sticky="ew", padx=(0, 14), pady=(16, 2))

        self.channel_label = ctk.CTkLabel(
            meta, text="Video information will load automatically",
            text_color=TEXT_SECONDARY, anchor="w"
        )
        self.channel_label.grid(row=1, column=1, sticky="ew", padx=(0, 14))

        self.source_label = ctk.CTkLabel(meta, text="", text_color=TEXT_SECONDARY, anchor="w")
        self.source_label.grid(row=2, column=1, sticky="ew", padx=(0, 14), pady=(1, 0))

        self.fetch_status = ctk.CTkLabel(meta, text="Waiting for a link", text_color=TEXT_MUTED, anchor="w")
        self.fetch_status.grid(row=3, column=1, sticky="ew", padx=(0, 14), pady=(5, 14))

        # Settings surface.
        options = ctk.CTkFrame(root, fg_color=SURFACE, corner_radius=RADIUS)
        options.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        options.grid_columnconfigure(0, weight=1)

        tabs = ctk.CTkFrame(options, fg_color="transparent")
        tabs.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 8))
        tabs.grid_columnconfigure(2, weight=1)

        self.video_tab_btn = ctk.CTkButton(
            tabs, text="Video", width=64, height=28, corner_radius=RADIUS,
            fg_color="transparent", hover_color=HOVER, text_color=TEXT,
            command=lambda: self.set_mode("Video")
        )
        self.video_tab_btn.grid(row=0, column=0, sticky="w")
        self.audio_tab_btn = ctk.CTkButton(
            tabs, text="Audio", width=64, height=28, corner_radius=RADIUS,
            fg_color="transparent", hover_color=HOVER, text_color=TEXT_SECONDARY,
            command=lambda: self.set_mode("Audio")
        )
        self.audio_tab_btn.grid(row=0, column=1, sticky="w", padx=(4, 0))

        self.mode_help = ctk.CTkLabel(tabs, text="", text_color=TEXT_MUTED)
        self.mode_help.grid(row=0, column=2, sticky="e")

        self.video_frame = ctk.CTkFrame(options, fg_color="transparent")
        self.video_frame.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 8))
        self.video_frame.grid_columnconfigure(6, weight=1)

        ctk.CTkLabel(self.video_frame, text="Quality", text_color=TEXT_SECONDARY).grid(row=0, column=0, sticky="w")
        self.video_quality = self.neutral_option_menu(
            self.video_frame, VIDEO_QUALITIES, self.video_quality_var, 112
        )
        self.video_quality.grid(row=0, column=1, padx=(8, 20))

        ctk.CTkLabel(self.video_frame, text="Container", text_color=TEXT_SECONDARY).grid(row=0, column=2, sticky="w")
        self.video_container = self.neutral_option_menu(
            self.video_frame, ["mp4", "mkv"], self.video_container_var, 92
        )
        self.video_container.grid(row=0, column=3, padx=(8, 20))

        self.capcut_switch = ctk.CTkCheckBox(
            self.video_frame, text="Editor ready", variable=self.capcut_var,
            width=120, checkbox_width=19, checkbox_height=19, corner_radius=5,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, border_color=TEXT_MUTED,
            text_color=TEXT
        )
        self.capcut_switch.grid(row=0, column=4, sticky="w")
        self.editor_ready_tooltip = ToolTip(
            self.capcut_switch,
            "Editor ready forces an MP4 output compatible with common editors\n"
            "using H.264 video and AAC audio when available."
        )

        self.audio_frame = ctk.CTkFrame(options, fg_color="transparent")
        self.audio_frame.grid_columnconfigure(4, weight=1)
        ctk.CTkLabel(self.audio_frame, text="Format", text_color=TEXT_SECONDARY).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(self.audio_frame, text="MP3", text_color=TEXT, font=ctk.CTkFont(weight="bold")).grid(row=0, column=1, padx=(8, 22))
        ctk.CTkLabel(self.audio_frame, text="Quality", text_color=TEXT_SECONDARY).grid(row=0, column=2, sticky="w")
        self.audio_quality = self.neutral_option_menu(
            self.audio_frame, AUDIO_QUALITIES, self.audio_quality_var, 124
        )
        self.audio_quality.grid(row=0, column=3, padx=(8, 0))

        folder = ctk.CTkFrame(options, fg_color="transparent")
        folder.grid(row=2, column=0, sticky="ew", padx=14, pady=(2, 13))
        folder.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(folder, text="Save to", text_color=TEXT_SECONDARY).grid(row=0, column=0, sticky="w")
        folder_shell = ctk.CTkFrame(folder, fg_color=SURFACE_2, corner_radius=RADIUS, height=36)
        folder_shell.grid(row=0, column=1, sticky="ew", padx=(10, 8))
        folder_shell.grid_columnconfigure(0, weight=1)
        self.folder_name_label = ctk.CTkLabel(
            folder_shell, textvariable=self.folder_name_var, text_color=TEXT, anchor="w"
        )
        self.folder_name_label.grid(row=0, column=0, sticky="ew", padx=10, pady=7)

        ctk.CTkButton(
            folder, text="Change", width=70, height=30, corner_radius=RADIUS,
            fg_color="transparent", hover_color=HOVER, text_color=TEXT_SECONDARY,
            command=self.choose_folder
        ).grid(row=0, column=2, padx=(0, 4))
        ctk.CTkButton(
            folder, text="Open", width=54, height=30, corner_radius=RADIUS,
            fg_color="transparent", hover_color=HOVER, text_color=TEXT_SECONDARY,
            command=self.open_folder
        ).grid(row=0, column=3)

        # Compact download surface. State and percentage live in the primary CTA.
        dl = ctk.CTkFrame(root, fg_color="transparent", corner_radius=0)
        self.download_card = dl
        dl.grid(row=4, column=0, sticky="ew", pady=(2, 0))
        dl.grid_columnconfigure(0, weight=1)

        # Keep these widgets for state compatibility, but remove their old visual row.
        status_row = ctk.CTkFrame(dl, fg_color="transparent")
        self.status_label = ctk.CTkLabel(status_row, text="Waiting")
        self.percent_label = ctk.CTkLabel(status_row, text="")

        # Keep the progress object as internal state; it is no longer rendered separately.
        self.progress = ctk.CTkProgressBar(dl)
        self.progress.set(0)

        action = ctk.CTkFrame(dl, fg_color="transparent")
        action.grid(row=0, column=0, sticky="ew")
        action.grid_columnconfigure(0, weight=1)

        self.download_btn = ctk.CTkButton(
            action, text="Download", height=46, corner_radius=RADIUS,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#FFFFFF",
            command=self.start_download, state="disabled"
        )
        self.download_btn.grid(row=0, column=0, sticky="ew")

        self.cancel_btn = ctk.CTkButton(
            action, text="Cancel", width=86, height=46, corner_radius=RADIUS,
            fg_color=SURFACE_2, hover_color=HOVER, text_color=TEXT_SECONDARY,
            command=self.cancel_download, state="disabled"
        )
        self.cancel_btn.grid(row=0, column=1, padx=(8, 0))
        self.cancel_btn.grid_remove()

        self.download_detail = ctk.CTkLabel(
            dl, text="Paste a YouTube link to begin",
            text_color=TEXT_MUTED, anchor="center",
            font=ctk.CTkFont(size=11)
        )
        self.download_detail.grid(row=1, column=0, sticky="ew", padx=6, pady=(6, 0))

        bottom = ctk.CTkFrame(root, fg_color="transparent")
        bottom.grid(row=5, column=0, sticky="ew", pady=(9, 0))
        bottom.grid_columnconfigure(1, weight=1)

        self.details_btn = ctk.CTkButton(
            bottom, text="Details", width=74, height=30, corner_radius=RADIUS,
            fg_color="transparent", hover_color=HOVER, text_color=TEXT_SECONDARY,
            command=self.toggle_details
        )
        self.details_btn.grid(row=0, column=0, sticky="w")

        self.done_text = ctk.CTkLabel(bottom, text="", text_color=TEXT_MUTED)
        self.done_text.grid(row=0, column=1)
        self.done_text.grid_remove()

        self.open_file_btn = ctk.CTkButton(
            bottom, text="Show in folder", width=108, height=30, corner_radius=RADIUS,
            fg_color=SURFACE_2, hover_color=HOVER, text_color=TEXT_SECONDARY,
            command=self.show_last_file_in_folder
        )
        self.open_file_btn.grid(row=0, column=2, padx=(8, 0))
        self.open_file_btn.grid_remove()

        self.details_frame = ctk.CTkFrame(root, corner_radius=RADIUS, fg_color=SURFACE)
        self.details_frame.grid(row=6, column=0, sticky="nsew", pady=(8, 0))
        self.log = ctk.CTkTextbox(
            self.details_frame, height=120, font=("Consolas", 11),
            fg_color=SURFACE_2, text_color=TEXT
        )
        self.log.pack(fill="both", expand=True, padx=8, pady=8)
        self.details_frame.grid_remove()

    def neutral_option_menu(self, parent, values, variable, width):
        return ctk.CTkOptionMenu(
            parent, values=values, variable=variable, width=width, height=32,
            corner_radius=RADIUS, fg_color=SURFACE_2, button_color=SURFACE_2,
            button_hover_color=HOVER, dropdown_fg_color=SURFACE_2,
            dropdown_hover_color=HOVER, text_color=TEXT
        )

    def set_mode(self, mode):
        self.mode_var.set(mode)
        self.apply_mode()

    def show_more_menu(self):
        # Header utilities are independent from download state.
        # Always clean up a stale menu reference before creating a new one.
        if self.more_menu is not None:
            try:
                if self.more_menu.winfo_exists():
                    self.more_menu.destroy()
                    self.more_menu = None
                    return
            except Exception:
                pass
            self.more_menu = None

        menu = ctk.CTkToplevel(self)
        self.more_menu = menu
        menu.overrideredirect(True)
        menu.configure(fg_color=SURFACE)
        menu.attributes("-topmost", True)
        menu.geometry("176x116")

        self.update_idletasks()
        x = self.more_btn.winfo_rootx() + self.more_btn.winfo_width() - 176
        y = self.more_btn.winfo_rooty() + self.more_btn.winfo_height() + 5
        menu.geometry(f"176x116+{x}+{y}")

        def add_item(text, command):
            def run():
                try:
                    if menu.winfo_exists():
                        menu.destroy()
                except Exception:
                    pass
                self.more_menu = None
                command()
            ctk.CTkButton(
                menu, text=text, anchor="w", height=34, corner_radius=6,
                fg_color="transparent", hover_color=HOVER, text_color=TEXT,
                command=run
            ).pack(fill="x", padx=6, pady=(6 if not menu.winfo_children() else 0, 0))

        add_item("About Pullio", self.show_about)
        add_item("Update yt-dlp", self.confirm_update_ytdlp)
        add_item("History", self.show_history)

        # Do not use FocusOut to destroy the popup: focus transitions between
        # CTk child widgets can generate FocusOut and leave stale state.
        menu.bind("<Escape>", lambda _e: self._close_more_menu())
        menu.focus_force()

    def _close_more_menu(self):
        menu = self.more_menu
        self.more_menu = None
        if menu is not None:
            try:
                if menu.winfo_exists():
                    menu.destroy()
            except Exception:
                pass

    def on_url_changed(self, *_):
        if self.auto_fetch_job:
            self.after_cancel(self.auto_fetch_job)
            self.auto_fetch_job = None

        value = self.url_var.get().strip()
        if value:
            self.clear_btn.grid()
        else:
            self.clear_btn.grid_remove()

        if not value:
            self.reset_metadata()
            return

        self.metadata = {}
        self.download_btn.configure(state="disabled", text="Download")
        self.status_label.configure(text="Checking link…")
        self.download_detail.configure(text="Loading video information")

        if looks_like_youtube_url(value):
            if has_playlist_param(value):
                self.set_fetch_status("Playlist link detected • this video only")
            self.auto_fetch_job = self.after(650, self.fetch_metadata)
        else:
            self.set_fetch_status("Paste a valid YouTube URL")
            self.status_label.configure(text="Waiting")
            self.download_detail.configure(text="Paste a valid YouTube link")

    def check_clipboard_on_start(self):
        """Use a valid YouTube URL from the clipboard when Pullio opens."""
        if self.url_var.get().strip():
            return
        text = self._clipboard_text().strip()
        if looks_like_youtube_url(text):
            self.last_clipboard_url = text
            self.url_var.set(text)
            self.url_entry.icursor("end")
            self.set_fetch_status("YouTube link detected from clipboard")

    def _on_app_focus(self, _event=None):
        """Pick up a newly copied YouTube URL when the user returns to Pullio."""
        if self.worker and self.worker.is_alive():
            return
        if self.duplicate_modal and self.duplicate_modal.winfo_exists():
            return

        text = self._clipboard_text().strip()
        if not looks_like_youtube_url(text):
            return

        current = self.url_var.get().strip()
        if text == current or text == self.last_clipboard_url:
            return

        self.last_clipboard_url = text
        self.url_var.set(text)
        self.url_entry.icursor("end")
        self.set_fetch_status("YouTube link detected from clipboard")

    def _clipboard_text(self):
        try:
            text = self.clipboard_get()
            if isinstance(text, str):
                return text
        except Exception:
            pass

        try:
            cp = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if cp.returncode == 0:
                return cp.stdout
        except Exception:
            pass

        return ""

    def _paste_into_url(self):
        text = self._clipboard_text()
        if not text:
            self.set_fetch_status("Clipboard is empty or unavailable")
            return

        text = text.strip()
        entry = self.url_entry

        # UX rule for this app:
        # if the clipboard contains a complete YouTube URL, treat it as a new job
        # and replace the entire previous URL instead of appending to it.
        if looks_like_youtube_url(text):
            # Replace the URL atomically through the StringVar instead of
            # delete+insert. This avoids firing the URL trace twice and
            # prevents metadata/thumbnail generation races between old/new URLs.
            if self.auto_fetch_job:
                try:
                    self.after_cancel(self.auto_fetch_job)
                except Exception:
                    pass
                self.auto_fetch_job = None

            self.fetch_generation += 1
            self.metadata = {}
            self.thumbnail_img = None
            self.thumb_label.configure(image="", text="▶")
            self.title_label.configure(text="Loading video info…")
            self.channel_label.configure(text="Checking the link")
            self.source_label.configure(text="")
            self.fetch_status.configure(text="Loading video info…")

            self.url_var.set(text)
            entry.icursor("end")

            # url_var.set() triggers on_url_changed(), which schedules the normal
            # debounce fetch. Replace it with one fast fetch for an explicit paste.
            if self.auto_fetch_job:
                try:
                    self.after_cancel(self.auto_fetch_job)
                except Exception:
                    pass
            self.auto_fetch_job = self.after(150, self.fetch_metadata)
            return

        # For ordinary text, keep normal editor-like behavior:
        # replace selection if present, otherwise insert at cursor.
        try:
            if entry.selection_present():
                first = entry.index("sel.first")
                last = entry.index("sel.last")
                entry.delete(first, last)
                entry.icursor(first)
        except Exception:
            pass

        entry.insert("insert", text)

    def _url_ctrl_keypress(self, event):
        # Physical Windows key codes: A=65, C=67, V=86, X=88.
        # This keeps shortcuts working even with Ukrainian keyboard layout.
        if not (event.state & 0x0004):
            return None

        key = event.keycode
        entry = self.url_entry

        if key == 65:  # Ctrl+A
            entry.select_range(0, "end")
            entry.icursor("end")
            return "break"

        if key == 67:  # Ctrl+C
            try:
                text = entry.selection_get()
            except Exception:
                return "break"
            self.clipboard_clear()
            self.clipboard_append(text)
            return "break"

        if key == 88:  # Ctrl+X
            try:
                first = entry.index("sel.first")
                last = entry.index("sel.last")
                text = entry.get()[first:last]
            except Exception:
                return "break"
            self.clipboard_clear()
            self.clipboard_append(text)
            entry.delete(first, last)
            return "break"

        if key == 86:  # Ctrl+V
            self._paste_into_url()
            return "break"

        return None

    def show_about(self):
        text = (
            f"Pullio {APP_VERSION}\n\n"
            "A lightweight Windows app for downloading video and audio using yt-dlp + FFmpeg.\n\n"
            "Features:\n"
            "• Video downloads up to the best available quality\n"
            "• MP3 audio extraction\n"
            "• Editor-ready H.264/MP4 mode\n"
            "• Download history\n"
            "• Local yt-dlp updater\n\n"
            "Pullio does not grant rights to download or reuse copyrighted content. "
            "Use it only for content you are allowed to download."
        )
        messagebox.showinfo("About Pullio", text)


    def paste_url(self):
        self.url_entry.focus_set()
        self.url_entry.select_range(0, "end")
        self._paste_into_url()

    def clear_url(self):
        self.url_var.set("")
        self.url_entry.focus_set()

    def _recreate_thumbnail_label_if_needed(self):
        """Rebuild the thumbnail label if its underlying Tk image handle went stale."""
        try:
            # A harmless text-only configure will fail if CTk tries to reuse a dead pyimage.
            self.thumb_label.configure(text=self.thumb_label.cget("text"))
            return
        except Exception:
            pass

        try:
            old = self.thumb_label
            parent = old.master
            old.grid_forget()
            old.destroy()

            self.thumb_label = ctk.CTkLabel(
                parent, text="▶", width=200, height=112, corner_radius=RADIUS,
                fg_color=SURFACE_2, text_color=TEXT_MUTED,
                font=ctk.CTkFont(size=26, weight="bold")
            )
            self.thumb_label.grid(row=0, column=0, rowspan=4, padx=12, pady=12, sticky="w")
        except Exception:
            pass

    def reset_metadata(self):
        self._recreate_thumbnail_label_if_needed()
        # Safe thumbnail reset: never touch the Tk image option here.
        # CustomTkinter can retain a stale Tcl "pyimage" handle after a previous
        # CTkImage is destroyed. Reconfiguring image=None/image="" can then crash.
        self.thumbnail_img = None
        try:
            self.thumb_label.configure(text="▶")
        except Exception:
            pass
        self.thumb_image = None
        self.fetch_generation += 1
        self.metadata = {}
        self.title_label.configure(text="Paste a YouTube link")
        self.channel_label.configure(text="Video information will load automatically")
        self.source_label.configure(text="")
        self.fetch_status.configure(text="Waiting for a link")
        self.done_text.configure(text="")
        self.open_file_btn.grid_remove()
        self.cancel_btn.grid_remove()
        self.progress.set(0)
        self.percent_label.configure(text="")
        self.status_label.configure(text="Waiting")
        self.download_detail.configure(text="Paste a YouTube link to begin")
        self.download_btn.configure(text="Download", state="disabled")

    def start_new_download(self):
        """Reset Pullio to a clean one-job state and immediately check clipboard."""
        self.last_file = None
        self.url_var.set("")
        self.reset_metadata()
        self.url_entry.focus_set()
        self.after(80, self._on_app_focus)

    def set_fetch_status(self, text):
        self.fetch_status.configure(text=text)

    def apply_mode(self):
        is_audio = self.mode_var.get() == "Audio"
        if is_audio:
            self.video_frame.grid_remove()
            self.audio_frame.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 8))
            self.mode_help.configure(text="MP3 • Best = highest available source quality")
            self.video_tab_btn.configure(text_color=TEXT_SECONDARY, fg_color="transparent")
            self.audio_tab_btn.configure(text_color=TEXT, fg_color=SURFACE_2)
        else:
            self.audio_frame.grid_remove()
            self.video_frame.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 8))
            self.mode_help.configure(text="")
            self.video_tab_btn.configure(text_color=TEXT, fg_color=SURFACE_2)
            self.audio_tab_btn.configure(text_color=TEXT_SECONDARY, fg_color="transparent")

    def choose_folder(self):
        p = filedialog.askdirectory(initialdir=self.output_var.get() or str(DEFAULT_DOWNLOADS))
        if p:
            self.output_var.set(p)
            self.folder_name_var.set(Path(p).name or p)
            self.save_settings()

    def open_folder(self):
        p = Path(self.output_var.get())
        p.mkdir(parents=True, exist_ok=True)
        os.startfile(str(p))

    def open_last_file(self):
        if self.last_file and Path(self.last_file).exists():
            os.startfile(str(self.last_file))

    def show_last_file_in_folder(self):
        if self.last_file and Path(self.last_file).exists():
            try:
                subprocess.Popen(["explorer.exe", "/select,", str(Path(self.last_file))])
            except Exception:
                os.startfile(str(Path(self.last_file).parent))
        else:
            self.open_folder()

    def fit_window_to_screen(self):
        """Keep the whole app visible without requiring manual resizing."""
        self.update_idletasks()
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        req_w = self.winfo_reqwidth()
        req_h = self.winfo_reqheight()

        max_w = max(820, screen_w - 80)
        max_h = max(640, screen_h - 90)
        target_w = min(max(req_w, 820), max_w)
        target_h = min(max(req_h, 640), max_h)

        self.geometry(f"{target_w}x{target_h}")

    def toggle_details(self):
        self.details_open = not self.details_open
        if self.details_open:
            self.details_frame.grid()
            self.details_btn.configure(text="Hide details")
            self.geometry("920x820")
        else:
            self.details_frame.grid_remove()
            self.details_btn.configure(text="Details")
            self.geometry("920x690")
        self.after_idle(self.fit_window_to_screen)


    def append_log(self, text):
        self.log.insert("end", text + "\n")
        self.log.see("end")

    def check_dependencies(self):
        missing = [p.name for p in (YTDLP, FFMPEG) if not p.exists()]
        if missing:
            self.status_label.configure(text="Missing: " + ", ".join(missing))
            self.download_btn.configure(state="disabled")
            self.append_log("Missing files: " + ", ".join(missing))
            return

        try:
            cp = subprocess.run(
                [str(YTDLP), "--version"],
                capture_output=True, text=True, timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
            ver = (cp.stdout or cp.stderr).strip()
            if ver:
                self.append_log(f"yt-dlp: {ver}")
        except Exception as e:
            self.append_log(f"Could not check yt-dlp: {e}")

    def fetch_metadata(self):
        url = self.url_var.get().strip()
        if not looks_like_youtube_url(url):
            self.set_fetch_status("Paste a valid YouTube URL")
            return

        if not YTDLP.exists():
            return

        self.fetch_generation += 1
        generation = self.fetch_generation
        self.set_fetch_status("Loading video info…")

        threading.Thread(target=self._metadata_worker, args=(url, generation), daemon=True).start()

    def _metadata_worker(self, url, generation):
        try:
            cp = subprocess.run(
                [str(YTDLP), "--dump-single-json", "--skip-download", "--no-playlist", url],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                cwd=str(BASE_DIR), timeout=60,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
            if cp.returncode != 0:
                self.msg_queue.put(("meta_error", (generation, (cp.stderr or cp.stdout).strip())))
                return
            self.msg_queue.put(("metadata", (generation, json.loads(cp.stdout))))
        except Exception as e:
            self.msg_queue.put(("meta_error", (generation, str(e))))

    def load_thumbnail(self, url, generation):
        try:
            from PIL import Image

            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=12) as r:
                raw = r.read()

            img = Image.open(BytesIO(raw)).convert("RGB")
            tw, th = 200, 112
            ratio = max(tw / img.width, th / img.height)
            img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)
            left = max(0, (img.width - tw) // 2)
            top = max(0, (img.height - th) // 2)
            img = img.crop((left, top, left + tw, top + th))

            image = ctk.CTkImage(light_image=img, dark_image=img, size=(200, 112))
            self.msg_queue.put(("thumbnail", (generation, image)))
        except Exception as e:
            self.msg_queue.put(("thumbnail_error", (generation, str(e))))

    def _find_existing_target(self):
        """Return an existing downloaded file for the current video, if known."""
        outdir = Path(self.output_var.get())
        if not outdir.exists():
            return None

        video_id = self.metadata.get("id")
        title = self.metadata.get("title")

        # Prefer internal history. The video ID stays internal and is not shown
        # in the public filename.
        if HISTORY_FILE.exists():
            try:
                history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            except Exception:
                history = []

            for item in history:
                same_id = video_id and item.get("video_id") == video_id
                same_url = item.get("url") == self.url_var.get().strip()
                if same_id or same_url:
                    p = item.get("file")
                    if p and Path(p).exists():
                        return Path(p)

        # Fallback for existing files that predate video_id history.
        if title:
            candidates = []
            for p in outdir.glob("*"):
                if not p.is_file():
                    continue
                if p.stem == title or p.stem.startswith(title + " ("):
                    candidates.append(p)
            if candidates:
                return max(candidates, key=lambda p: p.stat().st_mtime)

        return None

    def _destroy_duplicate_modal(self):
        modal = self.duplicate_modal
        backdrop = self.modal_backdrop
        self.duplicate_modal = None
        self.modal_backdrop = None

        if modal is not None:
            try:
                modal.grab_release()
            except Exception:
                pass
            try:
                if modal.winfo_exists():
                    modal.destroy()
            except Exception:
                pass

        if backdrop is not None:
            try:
                if backdrop.winfo_exists():
                    backdrop.destroy()
            except Exception:
                pass

        try:
            self.attributes("-disabled", False)
        except Exception:
            pass
        try:
            self.lift()
            self.focus_force()
        except Exception:
            pass

    def _show_duplicate_modal(self, existing):
        """Centered Pullio duplicate dialog with a dimmed, blocked backdrop."""
        try:
            if self.duplicate_modal is not None and self.duplicate_modal.winfo_exists():
                self.duplicate_modal.lift()
                self.duplicate_modal.focus_force()
                return
        except Exception:
            self.duplicate_modal = None

        self._close_more_menu()
        self.update_idletasks()

        # Dim the complete app, including the bright Download CTA.
        # Tk/CustomTkinter cannot apply CSS backdrop-filter blur, so a separate
        # translucent top-level gives the correct visual hierarchy without
        # introducing a second GUI framework.
        backdrop = ctk.CTkToplevel(self)
        self.modal_backdrop = backdrop
        backdrop.overrideredirect(True)
        backdrop.configure(fg_color="#000000")
        backdrop.attributes("-alpha", 0.62)
        backdrop.attributes("-topmost", True)

        app_x = self.winfo_rootx()
        app_y = self.winfo_rooty()
        app_w = self.winfo_width()
        app_h = self.winfo_height()
        backdrop.geometry(f"{app_w}x{app_h}+{app_x}+{app_y}")
        backdrop.lift()

        modal_w, modal_h = 500, 258
        modal = ctk.CTkToplevel(self)
        self.duplicate_modal = modal
        modal.title("")
        modal.overrideredirect(True)
        modal.resizable(False, False)
        modal.configure(fg_color=BG)
        modal.transient(self)
        modal.attributes("-topmost", True)

        # Exact center of the Pullio client window.
        x = app_x + max(0, (app_w - modal_w) // 2)
        y = app_y + max(0, (app_h - modal_h) // 2)
        modal.geometry(f"{modal_w}x{modal_h}+{x}+{y}")

        card = ctk.CTkFrame(
            modal, fg_color=SURFACE, corner_radius=12,
            border_width=1, border_color="#2E2E34"
        )
        card.pack(fill="both", expand=True, padx=1, pady=1)

        ctk.CTkLabel(
            card, text="Already downloaded",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=TEXT, anchor="w"
        ).pack(fill="x", padx=24, pady=(23, 8))

        ctk.CTkLabel(
            card,
            text="This video already exists in the selected folder.",
            text_color=TEXT_SECONDARY, anchor="w"
        ).pack(fill="x", padx=24)

        ctk.CTkLabel(
            card, text=existing.name,
            text_color=TEXT, anchor="w",
            wraplength=450, justify="left"
        ).pack(fill="x", padx=24, pady=(12, 20))

        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.pack(fill="x", padx=24, pady=(0, 22))
        actions.grid_columnconfigure(0, weight=1)

        def cancel():
            self._destroy_duplicate_modal()

        def replace():
            self._destroy_duplicate_modal()
            self._begin_download(force_rename=False, replace_path=existing)

        def keep_both():
            self._destroy_duplicate_modal()
            self._begin_download(force_rename=True)

        cancel_btn = ctk.CTkButton(
            actions, text="Cancel", width=88, height=36, corner_radius=RADIUS,
            fg_color="transparent", hover_color=HOVER, text_color=TEXT_SECONDARY,
            command=cancel
        )
        cancel_btn.grid(row=0, column=1, padx=(0, 8))

        replace_btn = ctk.CTkButton(
            actions, text="Replace", width=96, height=36, corner_radius=RADIUS,
            fg_color=SURFACE_2, hover_color=HOVER, text_color=TEXT,
            command=replace
        )
        replace_btn.grid(row=0, column=2, padx=(0, 8))

        keep_btn = ctk.CTkButton(
            actions, text="Keep both", width=116, height=36, corner_radius=RADIUS,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#FFFFFF",
            command=keep_both
        )
        keep_btn.grid(row=0, column=3)

        modal_actions = [cancel_btn, replace_btn, keep_btn]
        focus_index = {"value": 2}

        def focus_action(index):
            focus_index["value"] = index % len(modal_actions)
            modal_actions[focus_index["value"]].focus_set()

        def cycle_action(event):
            step = -1 if (event.state & 0x0001) else 1
            focus_action(focus_index["value"] + step)
            return "break"

        def activate_action(_event=None):
            idx = focus_index["value"]
            if idx == 0:
                cancel()
            elif idx == 1:
                replace()
            else:
                keep_both()
            return "break"

        for idx, btn in enumerate(modal_actions):
            btn.bind("<FocusIn>", lambda _e, i=idx: focus_index.__setitem__("value", i), add="+")
            btn.bind("<Tab>", cycle_action, add="+")
            btn.bind("<Return>", activate_action, add="+")
            btn.bind("<KP_Enter>", activate_action, add="+")

        modal.bind("<Escape>", lambda _e: cancel())
        modal.bind("<Tab>", cycle_action)
        modal.bind("<Return>", activate_action)
        modal.bind("<KP_Enter>", activate_action)
        modal.protocol("WM_DELETE_WINDOW", cancel)
        modal.lift()
        modal.grab_set()
        self.after(40, lambda: focus_action(2))

    def _next_duplicate_number(self, outdir):
        """Find the first free Windows-style suffix: (1), (2), (3), ..."""
        title = self.metadata.get("title")
        if not title:
            return 1

        n = 1
        while True:
            expected_stem = f"{title} ({n})"
            if not any(p.is_file() and p.stem == expected_stem for p in outdir.glob("*")):
                return n
            n += 1

    def build_download_cmd(self, force_rename=False):
        outdir = Path(self.output_var.get())
        outdir.mkdir(parents=True, exist_ok=True)
        before = {p.resolve() for p in outdir.glob("*") if p.is_file()}
        duplicate_number = self._next_duplicate_number(outdir) if force_rename else None

        cmd = [
            str(YTDLP),
            "--newline",
            "--no-playlist",
            "--ffmpeg-location", str(BASE_DIR),
            "--progress-template",
            "download:PROGRESS|%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s|%(progress.total_bytes_estimate_str)s",
            "-o", str(outdir / (f"%(title)s ({duplicate_number}).%(ext)s" if force_rename else "%(title)s.%(ext)s")),
        ]

        if self.mode_var.get() == "Audio":
            aq = self.audio_quality_var.get()
            quality_arg = {
                "Best": "0",
                "320 kbps": "320K",
                "256 kbps": "256K",
                "192 kbps": "192K",
                "128 kbps": "128K",
            }.get(aq, "0")
            cmd += ["-f", "bestaudio/best", "-x", "--audio-format", "mp3", "--audio-quality", quality_arg]
        else:
            if self.capcut_var.get():
                cmd += [
                    "-f", "bv*[vcodec^=avc1]+ba[acodec^=mp4a]/b[ext=mp4]/bestvideo+bestaudio/best",
                    "--merge-output-format", "mp4",
                    "--recode-video", "mp4"
                ]
            else:
                q = self.video_quality_var.get()
                height = {"4K": 2160, "1440p": 1440, "1080p": 1080, "720p": 720}.get(q)
                fmt = f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best" if height else "bestvideo+bestaudio/best"
                cmd += ["-f", fmt, "--merge-output-format", self.video_container_var.get().lower()]

        cmd.append(self.url_var.get().strip())
        return cmd, outdir, before

    def start_download(self):
        # When completion CTA says "Download another", it starts a truly clean job.
        if self.download_btn.cget("text") == "Download another":
            self.start_new_download()
            return

        url = self.url_var.get().strip()
        if not looks_like_youtube_url(url):
            self.set_fetch_status("Paste a valid YouTube URL")
            return
        if not YTDLP.exists() or not FFMPEG.exists():
            return
        if self.worker and self.worker.is_alive():
            return

        existing = self._find_existing_target()
        if existing:
            self._show_duplicate_modal(existing)
            return

        self._begin_download(force_rename=False)

    def _begin_download(self, force_rename=False, replace_path=None):
        self.save_settings()

        if replace_path is not None:
            try:
                replace_path = Path(replace_path)
                if replace_path.exists() and replace_path.is_file():
                    replace_path.unlink()
                    self.append_log(f"Replacing existing file: {replace_path.name}")
            except Exception as e:
                self.status_label.configure(text="Could not replace file")
                self.download_detail.configure(text=str(e))
                self.download_btn.configure(text="Try again", state="normal")
                return
        self.last_file = None
        self.done_text.configure(text="")
        self.done_text.grid_remove()
        self.open_file_btn.grid_remove()
        self.progress.set(0)
        self.percent_label.configure(text="")
        self.status_label.configure(text="Preparing…")
        self.download_detail.configure(text="Checking formats and starting download")
        self.download_btn.configure(text="Preparing…", state="disabled")
        self.cancel_btn.configure(state="normal")
        self.cancel_btn.grid()

        cmd, outdir, before = self.build_download_cmd(force_rename=force_rename)
        self.append_log("Starting download…")

        self.worker = threading.Thread(target=self._download_worker, args=(cmd, outdir, before), daemon=True)
        self.worker.start()

    def _download_worker(self, cmd, outdir, before):
        try:
            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(BASE_DIR),
                bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )

            for raw in self.proc.stdout:
                line = raw.replace("\r", "").strip()
                if not line:
                    continue
                self.msg_queue.put(("log", line))

                if line.startswith("PROGRESS|"):
                    parts = line.split("|", 4)
                    pct = parts[1].strip().replace("%", "")
                    speed = parts[2].strip() if len(parts) > 2 else ""
                    eta = parts[3].strip() if len(parts) > 3 else ""
                    total = parts[4].strip() if len(parts) > 4 else ""
                    try:
                        pctv = float(pct)
                    except Exception:
                        pctv = 0.0
                    self.msg_queue.put(("progress", (pctv, speed, eta, total)))
                elif "[Merger]" in line or "Merging formats" in line:
                    self.msg_queue.put(("status", "Merging video and audio…"))
                elif "[VideoConvertor]" in line or "[VideoRemuxer]" in line:
                    self.msg_queue.put(("status", "Preparing editor-ready MP4…"))
                elif "[ExtractAudio]" in line:
                    self.msg_queue.put(("status", "Creating MP3…"))
                elif "[download]" in line:
                    self.msg_queue.put(("status", "Downloading…"))

            code = self.proc.wait()

            if code == 0:
                after = [p.resolve() for p in outdir.glob("*") if p.is_file()]
                new_files = [p for p in after if p not in before]
                if new_files:
                    newest = max(new_files, key=lambda p: p.stat().st_mtime)
                    self.msg_queue.put(("last_file", str(newest)))
                self.msg_queue.put(("done", None))
            else:
                self.msg_queue.put(("failed", code))

        except Exception as e:
            self.msg_queue.put(("error", str(e)))
        finally:
            self.proc = None

    def cancel_download(self):
        if not self.proc or self.proc.poll() is not None:
            return
        if not messagebox.askyesno(APP_NAME, "Cancel the current download?"):
            return
        try:
            self.proc.terminate()
            self.status_label.configure(text="Cancelled")
        except Exception as e:
            messagebox.showerror(APP_NAME, str(e))

    def confirm_update_ytdlp(self):
        if self.updating_ytdlp:
            messagebox.showinfo(APP_NAME, "yt-dlp update is already running.")
            return
        if not YTDLP.exists():
            messagebox.showerror(APP_NAME, "yt-dlp.exe was not found.")
            return

        ok = messagebox.askyesno(
            APP_NAME,
            "Update yt-dlp?\n\nThis will modify yt-dlp.exe in this folder. Other app files will not be changed."
        )
        if not ok:
            return

        self.updating_ytdlp = True
        threading.Thread(target=self._update_ytdlp_worker, daemon=True).start()

    def _update_ytdlp_worker(self):
        try:
            cp = subprocess.run(
                [str(YTDLP), "-U"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                cwd=str(BASE_DIR), timeout=120,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
            self.msg_queue.put(("update_result", (cp.returncode, (cp.stdout or cp.stderr).strip())))
        except Exception as e:
            self.msg_queue.put(("update_result", (1, str(e))))

    def save_history(self):
        item = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "title": self.metadata.get("title") or self.url_var.get().strip(),
            "video_id": self.metadata.get("id"),
            "url": self.url_var.get().strip(),
            "mode": self.mode_var.get(),
            "video_quality": self.video_quality_var.get(),
            "video_container": self.video_container_var.get(),
            "capcut": bool(self.capcut_var.get()),
            "audio_quality": self.audio_quality_var.get(),
            "folder": self.output_var.get(),
            "file": self.last_file,
        }

        data = []
        if HISTORY_FILE.exists():
            try:
                data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            except Exception:
                data = []

        data.insert(0, item)
        HISTORY_FILE.write_text(json.dumps(data[:HISTORY_LIMIT], ensure_ascii=False, indent=2), encoding="utf-8")

    def show_history(self):
        win = ctk.CTkToplevel(self)
        win.title("History")
        win.geometry("760x460")
        win.transient(self)

        ctk.CTkLabel(
            win, text="Recent downloads",
            font=ctk.CTkFont(size=21, weight="bold")
        ).pack(anchor="w", padx=16, pady=(16, 9))

        scroll = ctk.CTkScrollableFrame(win)
        scroll.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        data = []
        if HISTORY_FILE.exists():
            try:
                data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            except Exception:
                data = []

        if not data:
            ctk.CTkLabel(scroll, text="No downloads yet.").pack(pady=30)
            return

        for item in data:
            row = ctk.CTkFrame(scroll, corner_radius=8)
            row.pack(fill="x", pady=(0, 7))
            row.grid_columnconfigure(0, weight=1)

            mode = item.get("mode", "")
            if mode == "Audio":
                mode_text = f"MP3 • {item.get('audio_quality', 'Best')}"
            else:
                mode_text = item.get("video_quality", "MAX")
                if item.get("capcut"):
                    mode_text += " • Editor ready"

            ctk.CTkLabel(
                row, text=item.get("title", "Untitled"),
                anchor="w", justify="left",
                font=ctk.CTkFont(weight="bold"), wraplength=500
            ).grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 1))

            ctk.CTkLabel(
                row, text=f"{item.get('time', '')} • {mode_text}",
                anchor="w", text_color=("gray45", "gray65")
            ).grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))

            def reuse(i=item):
                self.url_var.set(i.get("url", ""))
                self.mode_var.set(i.get("mode", "Video"))
                self.video_quality_var.set(i.get("video_quality", "MAX"))
                self.video_container_var.set(i.get("video_container", "mp4"))
                self.capcut_var.set(bool(i.get("capcut", False)))
                self.audio_quality_var.set(i.get("audio_quality", "Best"))
                self.output_var.set(i.get("folder", str(DEFAULT_DOWNLOADS)))
                self.apply_mode()
                win.destroy()

            def open_file(i=item):
                p = i.get("file")
                if p and Path(p).exists():
                    os.startfile(p)

            ctk.CTkButton(row, text="Use again", width=90, height=30, command=reuse).grid(row=0, column=1, rowspan=2, padx=(6, 4), pady=8)
            ctk.CTkButton(
                row, text="File", width=65, height=30,
                fg_color=("gray75", "gray25"), hover_color=("gray65", "gray30"),
                command=open_file
            ).grid(row=0, column=2, rowspan=2, padx=(4, 8), pady=8)

    def load_settings(self):
        if SETTINGS_FILE.exists():
            try:
                return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def save_settings(self):
        try:
            SETTINGS_FILE.write_text(
                json.dumps({
                    "mode": self.mode_var.get(),
                    "video_quality": self.video_quality_var.get(),
                    "video_container": self.video_container_var.get(),
                    "capcut": bool(self.capcut_var.get()),
                    "audio_quality": self.audio_quality_var.get(),
                    "folder": self.output_var.get(),
                }, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception:
            pass

    def on_close(self):
        self._destroy_duplicate_modal()
        self.save_settings()
        if self.proc and self.proc.poll() is None:
            if not messagebox.askyesno(APP_NAME, "A download is in progress. Close the app and stop it?"):
                return
            try:
                self.proc.terminate()
            except Exception:
                pass
        self.destroy()

    def process_queue(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()

                if kind == "log":
                    self.append_log(payload)

                elif kind == "metadata":
                    generation, data = payload
                    if generation != self.fetch_generation:
                        continue

                    self.metadata = data
                    self.title_label.configure(text=data.get("title") or "Untitled")
                    self.channel_label.configure(text=data.get("channel") or data.get("uploader") or "Unknown channel")
                    self.source_label.configure(
                        text=f"{fmt_res(data)} • {pick_codec_summary(data)} • {seconds_to_hms(data.get('duration'))}"
                    )
                    if has_playlist_param(self.url_var.get()):
                        self.set_fetch_status("Playlist detected • Pullio will download this video only")
                    else:
                        self.set_fetch_status("")
                    self.status_label.configure(text="Ready")
                    actual_h = data.get("height")
                    if actual_h:
                        self.download_detail.configure(
                            text=f"Source up to {actual_h}p • choose settings and download"
                        )
                    else:
                        self.download_detail.configure(text="Choose your settings and download")
                    self.percent_label.configure(text="")
                    self.progress.set(0)
                    self.download_btn.configure(text="Download", state="normal")

                    thumb = None
                    thumbs = data.get("thumbnails") or []
                    if thumbs:
                        thumbs = [t for t in thumbs if t.get("url")]
                        if thumbs:
                            thumbs.sort(key=lambda t: ((t.get("width") or 0) * (t.get("height") or 0)))
                            thumb = thumbs[-1].get("url")
                    if not thumb:
                        thumb = data.get("thumbnail")

                    if thumb:
                        self.thumbnail_generation = generation
                        threading.Thread(
                            target=self.load_thumbnail,
                            args=(thumb, generation),
                            daemon=True
                        ).start()

                elif kind == "meta_error":
                    generation, err = payload
                    if generation == self.fetch_generation:
                        self.set_fetch_status("Could not load video info")
                        self.status_label.configure(text="Cannot download yet")
                        self.download_detail.configure(text="Check the YouTube link and try again")
                        self.percent_label.configure(text="")
                        self.progress.set(0)
                        self.download_btn.configure(state="disabled", text="Download")
                        self.append_log("Metadata error: " + err)

                elif kind == "thumbnail":
                    generation, image = payload
                    if generation == self.fetch_generation:
                        self.thumbnail_img = image
                        self.thumb_label.configure(image=image, text="")

                elif kind == "thumbnail_error":
                    generation, err = payload
                    if generation == self.fetch_generation:
                        self.append_log("Thumbnail error: " + err)
                        self.thumb_label.configure(image="", text="Thumbnail unavailable")

                elif kind == "progress":
                    pct, speed, eta, total = payload
                    self.progress.set(max(0, min(1, pct / 100)))

                    bits = []
                    if speed and speed.upper() not in ("NA", "N/A"):
                        bits.append(speed)
                    if eta and eta.upper() not in ("NA", "N/A"):
                        bits.append(f"{eta} remaining")
                    if total and total.upper() not in ("NA", "N/A", "UNKNOWN"):
                        bits.append(total)

                    self.percent_label.configure(text="")
                    self.download_detail.configure(text=" • ".join(bits) if bits else "Downloading file…")
                    self.status_label.configure(text="Downloading…")
                    self.download_btn.configure(text=f"Downloading {pct:.0f}%")

                elif kind == "status":
                    self.status_label.configure(text=payload)
                    if "Merging" in payload:
                        self.download_detail.configure(text="Finalizing your video")
                        self.download_btn.configure(text="Merging…")
                    elif "editor-ready" in payload.lower():
                        self.download_detail.configure(text="Converting for editing compatibility")
                        self.download_btn.configure(text="Converting…")
                    elif "MP3" in payload:
                        self.download_detail.configure(text="Extracting audio")
                        self.download_btn.configure(text="Creating MP3…")

                elif kind == "last_file":
                    self.last_file = payload

                elif kind == "done":
                    self.progress.set(1)
                    self.percent_label.configure(text="")
                    self.status_label.configure(text="Download complete")
                    self.download_detail.configure(
                        text=Path(self.last_file).name if self.last_file else "File saved successfully"
                    )
                    self.download_btn.configure(text="Download another", state="normal")
                    self.cancel_btn.configure(state="disabled")
                    self.cancel_btn.grid_remove()
                    self.save_history()
                    self.done_text.configure(text="")
                    self.done_text.grid_remove()
                    if self.last_file:
                        self.open_file_btn.grid()

                elif kind == "failed":
                    self.status_label.configure(text=f"Error, code {payload}")
                    self.download_detail.configure(text="Open Details for more information")
                    self.download_btn.configure(text="Try again", state="normal")
                    self.cancel_btn.configure(state="disabled")
                    self.cancel_btn.grid_remove()
                    if not self.details_open:
                        self.toggle_details()

                elif kind == "error":
                    self.status_label.configure(text="Error")
                    self.download_detail.configure(text="Open Details for more information")
                    self.download_btn.configure(text="Try again", state="normal")
                    self.cancel_btn.configure(state="disabled")
                    self.cancel_btn.grid_remove()
                    self.append_log("ERROR: " + payload)
                    if not self.details_open:
                        self.toggle_details()

                elif kind == "update_result":
                    code, text = payload
                    self.updating_ytdlp = False
                    self.append_log("yt-dlp update: " + text)
                    if code == 0:
                        messagebox.showinfo(APP_NAME, "yt-dlp update completed.")
                    else:
                        messagebox.showerror(APP_NAME, "Could not update yt-dlp. Open Details for more information.")

        except queue.Empty:
            pass

        self.after(100, self.process_queue)

if __name__ == "__main__":
    DownloaderApp().mainloop()
