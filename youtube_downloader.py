import os
import sys
import re
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import yt_dlp


# -----------------------------
# Helpers
# -----------------------------
def is_youtube_url(url: str) -> bool:
    u = (url or "").lower()
    return ("youtube.com" in u) or ("youtu.be" in u)


def app_base_dir() -> Path:
    """
    Return a base folder for locating bundled resources.

    - In normal Python run: folder containing this script.
    - In PyInstaller frozen exe: folder containing the exe.
    - In onefile: sys._MEIPASS is a temp extraction dir; we also check exe folder.
    """
    if getattr(sys, "frozen", False):  # PyInstaller
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def find_ffmpeg_folder() -> str | None:
    """
    Try to find a folder that contains ffmpeg.exe and ffprobe.exe.

    We check:
    1) PyInstaller onefile extraction dir (sys._MEIPASS)
    2) Script/exe folder
    3) Script/exe folder\ffmpeg
    If not found, return None (yt-dlp will still try PATH).
    """
    candidates: list[Path] = []

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass))

    base = app_base_dir()
    candidates.append(base)
    candidates.append(base / "ffmpeg")

    for c in candidates:
        ffmpeg = c / "ffmpeg.exe"
        ffprobe = c / "ffprobe.exe"
        if ffmpeg.exists() and ffprobe.exists():
            return str(c)

    return None


def make_ydl_opts(url: str, out_dir: str, ui_update, ui_note) -> dict:
    """
    Create yt-dlp options with a format strategy that guarantees audio.

    - If FFmpeg is available (bundled or on PATH via ffmpeg_location), we download:
      best video + best audio, then merge to mp4. Highest quality with sound.

    - If FFmpeg is NOT available, we force a progressive format that has BOTH
      audio and video (typically <= 720p). Still has sound, but may be lower res.
    """
    ffmpeg_loc = find_ffmpeg_folder()

    # Progress hooks
    def progress_hook(d):
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            pct = (downloaded / total * 100.0) if total else 0.0

            speed = d.get("speed")
            eta = d.get("eta")

            msg = f"Downloading… {pct:.1f}%"
            if speed:
                msg += f" | {speed/1024/1024:.2f} MB/s"
            if eta is not None:
                msg += f" | ETA {eta}s"

            ui_update(pct, msg)

        elif status == "finished":
            # File downloaded; merging may happen next (if FFmpeg is used)
            ui_update(100.0, "Download finished. Processing/merging…")

    def postprocessor_hook(d):
        # Called during postprocessing (merge/convert)
        status = d.get("status")
        name = d.get("postprocessor", "")
        if status == "started":
            ui_note(f"Post-processing started: {name or 'processing'}…")
        elif status == "finished":
            ui_note("Post-processing finished.")

    # --- FORMAT STRATEGY (fixes no-sound issue) ---
    if ffmpeg_loc:
        # Highest quality WITH sound (requires merge)
        # Prefer MP4 video + M4A audio when available; otherwise fallback to best.
        fmt = "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/best"
        ui_note("FFmpeg detected: Highest quality (video+audio merge) enabled.")
    else:
        # No FFmpeg: force a single file that has BOTH audio+video (guaranteed sound)
        # This avoids silent video-only streams.
        fmt = "best[vcodec!=none][acodec!=none][ext=mp4]/best[vcodec!=none][acodec!=none]/best"
        ui_note("FFmpeg not detected: using single-file format (sound guaranteed, may be <=720p).")

    ydl_opts = {
        "format": fmt,
        "outtmpl": os.path.join(out_dir, "%(title)s [%(id)s].%(ext)s"),
        "noplaylist": True,
        "quiet": True,            # keep console quiet; we show UI messages instead
        "no_warnings": True,
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [postprocessor_hook],
        "retries": 3,
        "fragment_retries": 3,
    }

    # If we have a specific FFmpeg folder, tell yt-dlp explicitly.
    # If you installed FFmpeg system-wide, leaving this unset is fine too.
    if ffmpeg_loc:
        ydl_opts["ffmpeg_location"] = ffmpeg_loc
        ydl_opts["merge_output_format"] = "mp4"

    return ydl_opts


# -----------------------------
# GUI App
# -----------------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("YouTube Downloader (Best Quality + Sound)")
        self.geometry("760x250")
        self.resizable(False, False)

        self.url_var = tk.StringVar()
        self.out_var = tk.StringVar(value=str(Path.home() / "Downloads"))
        self.status_var = tk.StringVar(value="Idle")
        self.note_var = tk.StringVar(value="")
        self.progress_var = tk.DoubleVar(value=0.0)

        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}
        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="YouTube URL:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.url_var, width=85).grid(row=0, column=1, columnspan=2, sticky="we", **pad)

        ttk.Label(frm, text="Output folder:").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.out_var, width=70).grid(row=1, column=1, sticky="we", **pad)
        ttk.Button(frm, text="Browse…", command=self.pick_folder).grid(row=1, column=2, sticky="e", **pad)

        ttk.Label(frm, textvariable=self.note_var, foreground="gray").grid(row=2, column=0, columnspan=3, sticky="w", **pad)

        ttk.Progressbar(frm, variable=self.progress_var, maximum=100).grid(
            row=3, column=0, columnspan=3, sticky="we", padx=10, pady=6
        )

        ttk.Label(frm, textvariable=self.status_var).grid(row=4, column=0, columnspan=3, sticky="w", **pad)

        self.btn_download = ttk.Button(frm, text="Download Best Quality (with sound)", command=self.start_download)
        self.btn_download.grid(row=5, column=0, columnspan=3, sticky="we", padx=10, pady=10)

        frm.columnconfigure(1, weight=1)

    def pick_folder(self):
        folder = filedialog.askdirectory(title="Select download folder")
        if folder:
            self.out_var.set(folder)

    def start_download(self):
        url = self.url_var.get().strip()
        out_dir = self.out_var.get().strip()

        if not is_youtube_url(url):
            messagebox.showerror("Invalid input", "Please paste a valid YouTube URL.")
            return

        if not out_dir:
            messagebox.showerror("Invalid input", "Please choose an output folder.")
            return

        Path(out_dir).mkdir(parents=True, exist_ok=True)

        self.btn_download.config(state="disabled")
        self.progress_var.set(0.0)
        self.status_var.set("Starting…")
        self.note_var.set("")

        t = threading.Thread(target=self._download_worker, args=(url, out_dir), daemon=True)
        t.start()

    # UI-safe update helpers
    def ui_update(self, pct: float, msg: str):
        self.after(0, lambda: (self.progress_var.set(pct), self.status_var.set(msg)))

    def ui_note(self, msg: str):
        self.after(0, lambda: self.note_var.set(msg))

    def ui_done(self, msg: str):
        def f():
            self.progress_var.set(100.0)
            self.status_var.set(msg)
            self.btn_download.config(state="normal")
        self.after(0, f)

    def ui_error(self, msg: str):
        def f():
            self.btn_download.config(state="normal")
            self.status_var.set("Failed.")
            messagebox.showerror("Download error", msg)
        self.after(0, f)

    def _download_worker(self, url: str, out_dir: str):
        try:
            ydl_opts = make_ydl_opts(url, out_dir, self.ui_update, self.ui_note)

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)

            title = (info or {}).get("title", "Done")
            self.ui_done(f"Done: {title}")

        except Exception as e:
            self.ui_error(str(e))


if __name__ == "__main__":
    App().mainloop()
