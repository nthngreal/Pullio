import json
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from io import BytesIO
from pathlib import Path

import customtkinter as ctk
from tkinter import filedialog, messagebox

APP_NAME = "Pullio"
APP_VERSION = "1.0.0"
HISTORY_LIMIT = 50

# Public release links. Set these before publishing.
PROJECT_URL = ""
SUPPORT_URL = ""

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

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

        settings = self.load_settings()

        self.url_var = ctk.StringVar()
        self.mode_var = ctk.StringVar(value=settings.get("mode", "Video"))
        self.video_quality_var = ctk.StringVar(value=settings.get("video_quality", "MAX"))
        self.video_container_var = ctk.StringVar(value=settings.get("video_container", "mp4"))
        self.capcut_var = ctk.BooleanVar(value=settings.get("capcut", False))
        self.audio_quality_var = ctk.StringVar(value=settings.get("audio_quality", "Best"))
        self.output_var = ctk.StringVar(value=settings.get("folder", str(DEFAULT_DOWNLOADS)))

        self.build_ui()
        self.apply_mode()
        self.check_dependencies()

        self.url_var.trace_add("write", self.on_url_changed)
        self.after(100, self.process_queue)
        self.after_idle(self.fit_window_to_screen)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        root = ctk.CTkFrame(self, fg_color="transparent")
        root.grid(row=0, column=0, sticky="nsew", padx=18, pady=14)
        root.grid_columnconfigure(0, weight=1)

        # Header
        header = ctk.CTkFrame(root, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header, text="Pullio",
            font=ctk.CTkFont(size=25, weight="bold")
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header, text="Video & Audio Downloader",
            text_color=("gray45", "gray65")
        ).grid(row=1, column=0, sticky="w", pady=(0, 0))

        header_actions = ctk.CTkFrame(header, fg_color="transparent")
        header_actions.grid(row=0, column=1, rowspan=2, sticky="e")

        self.support_btn = ctk.CTkButton(
            header_actions, text="♡ Support", width=92, height=30,
            fg_color="transparent", border_width=1,
            command=self.open_support
        )
        self.support_btn.pack(side="left", padx=(0, 6))

        self.about_btn = ctk.CTkButton(
            header_actions, text="About", width=72, height=30,
            fg_color="transparent", border_width=1,
            command=self.show_about
        )
        self.about_btn.pack(side="left", padx=(0, 6))

        self.update_btn = ctk.CTkButton(
            header_actions, text="Update yt-dlp", width=122, height=30,
            fg_color="transparent", border_width=1,
            command=self.confirm_update_ytdlp
        )
        self.update_btn.pack(side="left")

        # URL card
        url_card = self.card(root, 1)
        url_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            url_card, text="YouTube URL",
            font=ctk.CTkFont(weight="bold")
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=14, pady=(11, 6))

        self.url_entry = ctk.CTkEntry(
            url_card, textvariable=self.url_var,
            placeholder_text="https://www.youtube.com/watch?v=..."
        )
        self.url_entry.grid(row=1, column=0, sticky="ew", padx=(14, 8), pady=(0, 12), ipady=2)

        # Windows-like shortcuts independent of EN/UA keyboard layout.
        # Only Ctrl+A/C/X/V are intercepted; normal typing, Backspace, Delete,
        # arrows, Home and End remain native.
        self.url_entry.bind("<Control-KeyPress>", self._url_ctrl_keypress, add="+")

        self.paste_btn = ctk.CTkButton(url_card, text="Paste", width=92, height=32, command=self.paste_url)
        self.paste_btn.grid(row=1, column=1, padx=(0, 8), pady=(0, 12))

        self.clear_btn = ctk.CTkButton(
            url_card, text="Clear", width=92, height=32,
            fg_color=("gray75", "gray25"), hover_color=("gray65", "gray30"),
            command=self.clear_url
        )
        self.clear_btn.grid(row=1, column=2, padx=(0, 14), pady=(0, 12))

        # Metadata
        meta = self.card(root, 2)
        meta.grid_columnconfigure(1, weight=1)

        self.thumb_label = ctk.CTkLabel(
            meta, text="Thumbnail", width=280, height=158,
            corner_radius=8, fg_color=("gray85", "gray14"),
            text_color=("gray45", "gray65")
        )
        self.thumb_label.grid(row=0, column=0, rowspan=4, padx=14, pady=12, sticky="w")

        self.title_label = ctk.CTkLabel(
            meta, text="Paste a YouTube link. Video info will load automatically.",
            font=ctk.CTkFont(size=17, weight="bold"),
            anchor="w", justify="left", wraplength=530
        )
        self.title_label.grid(row=0, column=1, sticky="ew", padx=(0, 14), pady=(14, 2))

        self.channel_label = ctk.CTkLabel(meta, text="Channel: —", text_color=("gray45", "gray65"), anchor="w")
        self.channel_label.grid(row=1, column=1, sticky="ew", padx=(0, 14))

        self.source_label = ctk.CTkLabel(meta, text="Source: —", text_color=("gray45", "gray65"), anchor="w")
        self.source_label.grid(row=2, column=1, sticky="ew", padx=(0, 14), pady=(1, 0))

        self.fetch_status = ctk.CTkLabel(meta, text="", text_color=("gray45", "gray65"), anchor="w")
        self.fetch_status.grid(row=3, column=1, sticky="ew", padx=(0, 14), pady=(5, 12))

        # Options
        options = self.card(root, 3)
        options.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(options, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=14, pady=(11, 7))
        top.grid_columnconfigure(2, weight=1)

        self.mode_switch = ctk.CTkSegmentedButton(
            top, values=["Video", "Audio"], variable=self.mode_var,
            command=lambda _: self.apply_mode(), width=150
        )
        self.mode_switch.grid(row=0, column=0, sticky="w")

        self.mode_help = ctk.CTkLabel(
            top, text="", text_color=("gray45", "gray65")
        )
        self.mode_help.grid(row=0, column=2, sticky="e")

        self.video_frame = ctk.CTkFrame(options, fg_color="transparent")
        self.video_frame.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 7))
        self.video_frame.grid_columnconfigure(5, weight=1)

        ctk.CTkLabel(self.video_frame, text="Quality").grid(row=0, column=0, sticky="w")
        self.video_quality = ctk.CTkOptionMenu(
            self.video_frame, values=VIDEO_QUALITIES,
            variable=self.video_quality_var, width=110, height=30
        )
        self.video_quality.grid(row=0, column=1, padx=(8, 18))

        ctk.CTkLabel(self.video_frame, text="Container").grid(row=0, column=2)
        self.video_container = ctk.CTkOptionMenu(
            self.video_frame, values=["mp4", "mkv"],
            variable=self.video_container_var, width=90, height=30
        )
        self.video_container.grid(row=0, column=3, padx=(8, 18))

        self.capcut_switch = ctk.CTkSwitch(
            self.video_frame, text="CapCut compatible",
            variable=self.capcut_var
        )
        self.capcut_switch.grid(row=0, column=4, sticky="w")

        self.audio_frame = ctk.CTkFrame(options, fg_color="transparent")
        self.audio_frame.grid_columnconfigure(4, weight=1)
        ctk.CTkLabel(self.audio_frame, text="Format").grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(self.audio_frame, text="MP3", font=ctk.CTkFont(weight="bold")).grid(row=0, column=1, padx=(8, 20))
        ctk.CTkLabel(self.audio_frame, text="Quality").grid(row=0, column=2)
        self.audio_quality = ctk.CTkOptionMenu(
            self.audio_frame, values=AUDIO_QUALITIES,
            variable=self.audio_quality_var, width=120, height=30
        )
        self.audio_quality.grid(row=0, column=3, padx=(8, 0))

        folder = ctk.CTkFrame(options, fg_color="transparent")
        folder.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 12))
        folder.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(folder, text="Save to").grid(row=0, column=0, sticky="w")
        self.folder_entry = ctk.CTkEntry(folder, textvariable=self.output_var, height=30)
        self.folder_entry.grid(row=0, column=1, sticky="ew", padx=(10, 8))

        ctk.CTkButton(folder, text="Browse", width=82, height=30, command=self.choose_folder).grid(row=0, column=2, padx=(0, 8))
        ctk.CTkButton(
            folder, text="Open", width=82, height=30,
            fg_color=("gray75", "gray25"), hover_color=("gray65", "gray30"),
            command=self.open_folder
        ).grid(row=0, column=3)


        # Download card
        dl = self.card(root, 4)
        self.download_card = dl
        dl.grid_columnconfigure(0, weight=1)

        status_row = ctk.CTkFrame(dl, fg_color="transparent")
        status_row.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 7))
        status_row.grid_columnconfigure(1, weight=1)

        self.activity_icon = ctk.CTkLabel(
            status_row, text="↓", width=34, height=34,
            corner_radius=10, fg_color=("gray80", "gray22"),
            font=ctk.CTkFont(size=20, weight="bold")
        )
        self.activity_icon.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 10))

        self.status_label = ctk.CTkLabel(
            status_row, text="Waiting",
            font=ctk.CTkFont(size=15, weight="bold"), anchor="w"
        )
        self.status_label.grid(row=0, column=1, sticky="w")

        self.download_detail = ctk.CTkLabel(
            status_row, text="Paste a YouTube link to begin",
            text_color=("gray45", "gray65"), anchor="w"
        )
        self.download_detail.grid(row=1, column=1, sticky="w")

        self.percent_label = ctk.CTkLabel(
            status_row, text="0%",
            font=ctk.CTkFont(size=24, weight="bold")
        )
        self.percent_label.grid(row=0, column=2, rowspan=2, sticky="e")

        self.progress = ctk.CTkProgressBar(dl, height=12, corner_radius=6)
        self.progress.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 11))
        self.progress.set(0)

        action = ctk.CTkFrame(dl, fg_color="transparent")
        action.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 12))
        action.grid_columnconfigure(0, weight=1)

        self.download_btn = ctk.CTkButton(
            action, text="Download", height=38,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self.start_download
        )
        self.download_btn.grid(row=0, column=0, sticky="ew")

        self.cancel_btn = ctk.CTkButton(
            action, text="Cancel", width=100, height=38,
            fg_color=("gray75", "gray25"), hover_color=("gray65", "gray30"),
            command=self.cancel_download, state="disabled"
        )
        self.cancel_btn.grid(row=0, column=1, padx=(8, 0))

        # Bottom controls always visible
        bottom = ctk.CTkFrame(root, fg_color="transparent")
        bottom.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        bottom.grid_columnconfigure(1, weight=1)

        self.details_btn = ctk.CTkButton(
            bottom, text="Show details", width=130, height=30,
            fg_color="transparent", border_width=1,
            command=self.toggle_details
        )
        self.details_btn.grid(row=0, column=0, sticky="w")

        self.done_text = ctk.CTkLabel(bottom, text="", text_color=("gray45", "gray65"))
        self.done_text.grid(row=0, column=1)

        self.open_file_btn = ctk.CTkButton(
            bottom, text="Open file", width=110, height=30,
            command=self.open_last_file
        )
        self.open_file_btn.grid(row=0, column=2, padx=(8, 0))
        self.open_file_btn.grid_remove()

        self.history_btn = ctk.CTkButton(
            bottom, text="History", width=90, height=30,
            fg_color="transparent", border_width=1,
            command=self.show_history
        )
        self.history_btn.grid(row=0, column=3, padx=(8, 0))

        # Details overlay-like block; hidden by default
        self.details_frame = ctk.CTkFrame(root, corner_radius=10)
        self.details_frame.grid(row=6, column=0, sticky="nsew", pady=(8, 0))
        self.log = ctk.CTkTextbox(self.details_frame, height=120, font=("Consolas", 11))
        self.log.pack(fill="both", expand=True, padx=8, pady=8)
        self.details_frame.grid_remove()

    def card(self, root, row):
        f = ctk.CTkFrame(root, corner_radius=10)
        f.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        return f

    def on_url_changed(self, *_):
        if self.auto_fetch_job:
            self.after_cancel(self.auto_fetch_job)
            self.auto_fetch_job = None

        value = self.url_var.get().strip()
        if not value:
            self.reset_metadata()
            return

        if looks_like_youtube_url(value):
            self.auto_fetch_job = self.after(650, self.fetch_metadata)

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
            self.thumb_label.configure(image="", text="Thumbnail")
            self.title_label.configure(text="Loading video info…")
            self.channel_label.configure(text="Channel: —")
            self.source_label.configure(text="Source: —")
            self.fetch_status.configure(text="Loading video info…")

            self.url_var.set(text)
            entry.icursor("end")

            # Start one clean metadata request for this exact URL.
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

    def open_support(self):
        if SUPPORT_URL:
            webbrowser.open(SUPPORT_URL)
        else:
            messagebox.showinfo(
                APP_NAME,
                "Support link is not configured yet.\\n\\n"
                "Before publishing, set SUPPORT_URL in app_v5.py "
                "to your GitHub Sponsors or Ko-fi page."
            )

    def show_about(self):
        text = (
            f"Pullio {APP_VERSION}\n\n"
            "A lightweight Windows app for downloading video and audio using yt-dlp + FFmpeg.\n\n"
            "Features:\n"
            "• Video downloads up to the best available quality\n"
            "• MP3 audio extraction\n"
            "• CapCut-compatible MP4 mode\n"
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

    def reset_metadata(self):
        self.fetch_generation += 1
        self.metadata = {}
        self.thumbnail_img = None
        self.thumb_label.configure(image=None, text="Thumbnail")
        self.title_label.configure(text="Paste a YouTube link. Video info will load automatically.")
        self.channel_label.configure(text="Channel: —")
        self.source_label.configure(text="Source: —")
        self.fetch_status.configure(text="")
        self.done_text.configure(text="")
        self.open_file_btn.grid_remove()
        self.progress.set(0)
        self.percent_label.configure(text="0%")
        self.status_label.configure(text="Waiting")
        self.download_detail.configure(text="Paste a YouTube link to begin")
        self.activity_icon.configure(text="↓", fg_color=("gray80", "gray22"))
        self.download_card.configure(border_width=0)
        self.download_btn.configure(text="Download", state="normal")

    def set_fetch_status(self, text):
        self.fetch_status.configure(text=text)

    def apply_mode(self):
        if self.mode_var.get() == "Audio":
            self.video_frame.grid_remove()
            self.audio_frame.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 7))
            self.mode_help.configure(text="MP3 • Best = highest available source quality")
        else:
            self.audio_frame.grid_remove()
            self.video_frame.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 7))
            self.mode_help.configure(text="MAX = best available • CapCut = H.264/MP4")

    def choose_folder(self):
        p = filedialog.askdirectory(initialdir=self.output_var.get() or str(DEFAULT_DOWNLOADS))
        if p:
            self.output_var.set(p)
            self.save_settings()

    def open_folder(self):
        p = Path(self.output_var.get())
        p.mkdir(parents=True, exist_ok=True)
        os.startfile(str(p))

    def open_last_file(self):
        if self.last_file and Path(self.last_file).exists():
            os.startfile(str(self.last_file))

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
            self.details_btn.configure(text="Show details")
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
            tw, th = 280, 158
            ratio = max(tw / img.width, th / img.height)
            img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)
            left = max(0, (img.width - tw) // 2)
            top = max(0, (img.height - th) // 2)
            img = img.crop((left, top, left + tw, top + th))

            image = ctk.CTkImage(light_image=img, dark_image=img, size=(280, 158))
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

    def _confirm_duplicate_download(self):
        existing = self._find_existing_target()
        if not existing:
            return False  # normal filename

        # Keep the UI simple: warn once. If the user continues, download again
        # automatically under a unique filename.
        continue_download = messagebox.askyesno(
            APP_NAME,
            "This video appears to be already downloaded.\n\n"
            f"{existing.name}\n\n"
            "Download it again?\n\n"
            "If you continue, the new file will be saved with a different name."
        )
        if not continue_download:
            return None  # stop
        return True  # force unique filename

    def _next_duplicate_number(self, outdir):
        """Find the first free duplicate suffix: (1), (2), (3), ..."""
        video_id = self.metadata.get("id")
        if not video_id:
            return 1

        n = 1
        while True:
            marker = f"[{video_id}] ({n})"
            if not any(marker in p.name for p in outdir.glob("*") if p.is_file()):
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
        url = self.url_var.get().strip()
        if not looks_like_youtube_url(url):
            self.set_fetch_status("Paste a valid YouTube URL")
            return
        if not YTDLP.exists() or not FFMPEG.exists():
            return
        if self.worker and self.worker.is_alive():
            return

        duplicate_result = self._confirm_duplicate_download()
        if duplicate_result is None:
            return

        force_rename = duplicate_result is True

        self.save_settings()
        self.last_file = None
        self.done_text.configure(text="")
        self.open_file_btn.grid_remove()
        self.progress.set(0)
        self.percent_label.configure(text="0%")
        self.status_label.configure(text="Preparing…")
        self.download_detail.configure(text="Checking formats and starting download")
        self.activity_icon.configure(text="↓", fg_color=("#3b82f6", "#2563eb"))
        self.download_card.configure(border_width=1, border_color=("#60a5fa", "#3b82f6"))
        self.download_btn.configure(text="Downloading…", state="disabled")
        self.cancel_btn.configure(state="normal")

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
                    self.msg_queue.put(("status", "Preparing MP4 for CapCut…"))
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
        if not YTDLP.exists():
            messagebox.showerror(APP_NAME, "yt-dlp.exe was not found.")
            return

        ok = messagebox.askyesno(
            APP_NAME,
            "Update yt-dlp?\n\nThis will modify yt-dlp.exe in this folder. Other app files will not be changed."
        )
        if not ok:
            return

        self.update_btn.configure(state="disabled", text="Updating…")
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
                    mode_text += " • CapCut"

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
                    self.channel_label.configure(text="Channel: " + (data.get("channel") or data.get("uploader") or "—"))
                    self.source_label.configure(
                        text=f"Source: {fmt_res(data)} • {pick_codec_summary(data)} • {seconds_to_hms(data.get('duration'))}"
                    )
                    self.set_fetch_status("Video info loaded")
                    self.status_label.configure(text="Ready to download")
                    self.download_detail.configure(text="Press Download to start")
                    self.percent_label.configure(text="0%")
                    self.progress.set(0)
                    self.activity_icon.configure(text="↓", fg_color=("gray80", "gray22"))
                    self.download_card.configure(border_width=0)
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
                        self.percent_label.configure(text="0%")
                        self.progress.set(0)
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

                    self.percent_label.configure(text=f"{pct:.0f}%")
                    self.download_detail.configure(text=" • ".join(bits) if bits else "Downloading file…")
                    self.status_label.configure(text="Downloading…")
                    self.download_btn.configure(text=f"Downloading {pct:.0f}%")

                elif kind == "status":
                    self.status_label.configure(text=payload)
                    if "Merging" in payload:
                        self.download_detail.configure(text="Finalizing your video")
                        self.download_btn.configure(text="Merging…")
                    elif "CapCut" in payload:
                        self.download_detail.configure(text="Converting for editing compatibility")
                        self.download_btn.configure(text="Converting…")
                    elif "MP3" in payload:
                        self.download_detail.configure(text="Extracting audio")
                        self.download_btn.configure(text="Creating MP3…")

                elif kind == "last_file":
                    self.last_file = payload

                elif kind == "done":
                    self.progress.set(1)
                    self.percent_label.configure(text="100%")
                    self.status_label.configure(text="Download complete")
                    self.download_detail.configure(
                        text=Path(self.last_file).name if self.last_file else "File saved successfully"
                    )
                    self.activity_icon.configure(text="✓", fg_color=("#22c55e", "#16a34a"))
                    self.download_card.configure(border_width=1, border_color=("#4ade80", "#22c55e"))
                    self.download_btn.configure(text="Download another", state="normal")
                    self.cancel_btn.configure(state="disabled")
                    self.save_history()
                    self.done_text.configure(text="✓ Downloaded")
                    if self.last_file:
                        self.open_file_btn.grid()

                elif kind == "failed":
                    self.status_label.configure(text=f"Error, code {payload}")
                    self.download_detail.configure(text="Open Details for more information")
                    self.activity_icon.configure(text="!", fg_color=("#ef4444", "#dc2626"))
                    self.download_card.configure(border_width=1, border_color=("#f87171", "#ef4444"))
                    self.download_btn.configure(text="Try again", state="normal")
                    self.cancel_btn.configure(state="disabled")
                    if not self.details_open:
                        self.toggle_details()

                elif kind == "error":
                    self.status_label.configure(text="Error")
                    self.download_detail.configure(text="Open Details for more information")
                    self.activity_icon.configure(text="!", fg_color=("#ef4444", "#dc2626"))
                    self.download_card.configure(border_width=1, border_color=("#f87171", "#ef4444"))
                    self.download_btn.configure(text="Try again", state="normal")
                    self.cancel_btn.configure(state="disabled")
                    self.append_log("ERROR: " + payload)
                    if not self.details_open:
                        self.toggle_details()

                elif kind == "update_result":
                    code, text = payload
                    self.update_btn.configure(state="normal", text="Update yt-dlp")
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
