import atexit
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from flask import Flask, after_this_request, jsonify, redirect, render_template, request, send_file

YOUTUBE_HOSTS = {"youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}
URL_RE = re.compile(r"https?://\S+")
MAX_FILE_MB = int((os.getenv("MAX_FILE_MB") or "1900").strip())
HOST = (os.getenv("APP_HOST") or "0.0.0.0").strip()
_port_raw = (os.getenv("PORT") or os.getenv("APP_PORT") or "5000").strip()
PORT = int(_port_raw) if _port_raw.isdigit() else 5000
DEBUG = (os.getenv("APP_DEBUG") or "0").strip() == "1"

app = Flask(__name__)
WORK_ROOT = Path(tempfile.gettempdir()) / "yt_web_downloads"
WORK_ROOT.mkdir(parents=True, exist_ok=True)


@dataclass
class DownloadResult:
    path: Path
    title: str
    size_bytes: int


def extract_url(text: str) -> str | None:
    match = URL_RE.search(text or "")
    return match.group(0) if match else None


def is_youtube_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host in YOUTUBE_HOSTS or host.endswith(".youtube.com")


def _find_downloaded_file(info: dict, ydl: yt_dlp.YoutubeDL) -> Path:
    requested = info.get("requested_downloads") or []
    for item in requested:
        filepath = (item or {}).get("filepath")
        if filepath:
            file_path = Path(filepath)
            if file_path.exists():
                return file_path

    file_path = Path(ydl.prepare_filename(info))
    if file_path.exists():
        return file_path

    mp4_candidate = file_path.with_suffix(".mp4")
    if mp4_candidate.exists():
        return mp4_candidate

    raise RuntimeError("Could not detect resulting media file.")


def download_video(youtube_url: str, output_dir: Path) -> DownloadResult:
    base_opts = {
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "outtmpl": str(output_dir / "%(title).180B [%(id)s].%(ext)s"),
        "postprocessor_args": {"Merger": ["-movflags", "+faststart"]},
        "extractor_args": {"youtube": {"player_client": ["android_vr"]}},
    }
    env_cookies = os.getenv("YOUTUBE_COOKIES", "").strip()
    if env_cookies:
        cookies_file = output_dir / "cookies.txt"
        cookies_file.write_text(env_cookies, encoding="utf-8")
        base_opts["cookiefile"] = str(cookies_file)
    else:
        cookies_path = Path(__file__).with_name("cookies.txt")
        if cookies_path.exists():
            base_opts["cookiefile"] = str(cookies_path)

    format_candidates = [
        "bv*[height<=1080]+ba/b[height<=1080]/b[height<=1080]",
        "bv*+ba/b",
        "best",
    ]

    info = None
    last_error = None
    for fmt in format_candidates:
        ydl_opts = {**base_opts, "format": fmt}
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                current = ydl.extract_info(youtube_url, download=True)
                req = current.get("requested_formats") or []
                one = current.get("requested_downloads") or []
                has_storyboard = any((x or {}).get("ext") == "mhtml" for x in req) or any(
                    (x or {}).get("ext") == "mhtml" for x in one
                )
                if has_storyboard or current.get("ext") == "mhtml":
                    raise yt_dlp.utils.DownloadError(
                        "YouTube returned storyboard/images instead of video."
                    )
                info = current
                break
        except yt_dlp.utils.DownloadError as exc:
            last_error = exc
            continue

    if info is None:
        raise RuntimeError(f"Failed to download video: {last_error}")

    with yt_dlp.YoutubeDL(base_opts) as ydl:
        file_path = _find_downloaded_file(info, ydl)

    return DownloadResult(
        path=file_path,
        title=info.get("title", "video"),
        size_bytes=file_path.stat().st_size,
    )


def cleanup_work_root() -> None:
    if WORK_ROOT.exists():
        shutil.rmtree(WORK_ROOT, ignore_errors=True)


atexit.register(cleanup_work_root)


@app.get("/")
def index():
    return render_template("index.html", max_file_mb=MAX_FILE_MB)


@app.post("/api/download")
def api_download():
    wants_html = "text/html" in (request.headers.get("Accept") or "")

    def error_response(message: str, status: int, details: str | None = None):
        if wants_html:
            body = message if not details else f"{message}\n\n{details}"
            return body, status, {"Content-Type": "text/plain; charset=utf-8"}
        payload = {"ok": False, "error": message}
        if details:
            payload["details"] = details
        return jsonify(payload), status

    url = extract_url((request.form.get("url") or "").strip())
    if not url:
        return error_response("Please provide a valid YouTube URL.", 400)
    if not is_youtube_url(url):
        return error_response("Only YouTube URLs are supported.", 400)

    job_dir = WORK_ROOT / uuid.uuid4().hex
    job_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = download_video(url, job_dir)
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(job_dir, ignore_errors=True)
        return error_response(
            "Could not download this video. YouTube may be returning only storyboard/images or blocking formats.",
            400,
            str(exc),
        )

    size_mb = result.size_bytes / (1024 * 1024)
    if size_mb > MAX_FILE_MB:
        shutil.rmtree(job_dir, ignore_errors=True)
        return error_response(
            f"File is too large ({size_mb:.1f} MB). Limit is {MAX_FILE_MB} MB.",
            400,
        )

    @after_this_request
    def remove_temp_dir(response):
        shutil.rmtree(job_dir, ignore_errors=True)
        return response

    download_name = result.path.name
    if not download_name.lower().endswith(".mp4"):
        download_name = f"{result.title}.mp4"

    return send_file(
        result.path,
        as_attachment=True,
        download_name=download_name,
        mimetype="video/mp4",
        max_age=0,
    )


@app.get("/api/download")
def api_download_get():
    return redirect("/")


if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=DEBUG)
