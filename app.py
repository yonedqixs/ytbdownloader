import atexit
import base64
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

try:
    from vercel.blob import BlobClient
except Exception:  # noqa: BLE001
    BlobClient = None

YOUTUBE_HOSTS = {"youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}
URL_RE = re.compile(r"https?://\S+")
MAX_FILE_MB = int((os.getenv("MAX_FILE_MB") or "1900").strip())
HOST = (os.getenv("APP_HOST") or "0.0.0.0").strip()
_port_raw = (os.getenv("PORT") or os.getenv("APP_PORT") or "5000").strip()
PORT = int(_port_raw) if _port_raw.isdigit() else 5000
DEBUG = (os.getenv("APP_DEBUG") or "0").strip() == "1"
BLOB_ACCESS = (os.getenv("BLOB_ACCESS") or "public").strip().lower()

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


def _safe_blob_pathname(source_file: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", source_file.stem).strip("._-")
    if not stem:
        stem = "video"
    return f"downloads/{stem}.mp4"


def _upload_to_blob(source_file: Path) -> dict | None:
    if BlobClient is None:
        return None

    token = os.getenv("BLOB_READ_WRITE_TOKEN", "").strip()
    if not token:
        return None

    if BLOB_ACCESS not in {"public", "private"}:
        raise RuntimeError("BLOB_ACCESS must be 'public' or 'private'.")

    with source_file.open("rb") as f:
        payload = f.read()

    client = BlobClient(token=token)
    uploaded = client.put(
        _safe_blob_pathname(source_file),
        payload,
        access=BLOB_ACCESS,
        content_type="video/mp4",
        add_random_suffix=True,
        multipart=True,
    )
    return dict(uploaded)


def _resolve_path(value: str) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate
    return (Path.cwd() / candidate).resolve()


def _normalize_cookie_text(raw: str) -> str:
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()

    if text.startswith("base64:"):
        payload = text[len("base64:") :].strip()
        decoded = base64.b64decode(payload).decode("utf-8", errors="replace")
        text = decoded.strip()

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if "\\n" in text:
        text = text.replace("\\n", "\n")

    if not text.startswith("# HTTP Cookie File") and not text.startswith("# Netscape HTTP Cookie File"):
        text = "# Netscape HTTP Cookie File\n" + text

    if not text.endswith("\n"):
        text += "\n"
    return text


def _resolve_cookiefile(output_dir: Path) -> Path | None:
    env_cookies_file = os.getenv("YOUTUBE_COOKIES_FILE", "").strip()
    env_cookies = os.getenv("YOUTUBE_COOKIES", "").strip()
    local_cookies_path = Path(__file__).with_name("cookies.txt")

    if env_cookies_file:
        candidate = _resolve_path(env_cookies_file)
        if not candidate.exists():
            raise RuntimeError(
                f"YOUTUBE_COOKIES_FILE is set but file does not exist: {candidate}"
            )
        return candidate

    if env_cookies:
        # Support passing path via YOUTUBE_COOKIES too.
        if "\n" not in env_cookies and "\t" not in env_cookies:
            candidate = _resolve_path(env_cookies)
            if candidate.exists():
                return candidate

        cookiefile_path = output_dir / "cookies.txt"
        cookiefile_path.write_text(_normalize_cookie_text(env_cookies), encoding="utf-8")
        return cookiefile_path

    if local_cookies_path.exists():
        return local_cookies_path

    return None


def _parse_cookiesfrombrowser_spec() -> tuple[str, str | None, str | None, str | None] | None:
    raw = os.getenv("YOUTUBE_COOKIES_FROM_BROWSER", "").strip()
    if not raw:
        return None
    if raw.lower() in {"0", "false", "none", "null", "off"}:
        return None

    match = re.fullmatch(
        r"""(?x)
        (?P<name>[^+:]+)
        (?:\s*\+\s*(?P<keyring>[^:]+))?
        (?:\s*:\s*(?!:)(?P<profile>.+?))?
        (?:\s*::\s*(?P<container>.+))?
    """,
        raw,
    )
    if not match:
        raise RuntimeError(
            "Invalid YOUTUBE_COOKIES_FROM_BROWSER format. "
            "Use BROWSER[+KEYRING][:PROFILE][::CONTAINER], e.g. chrome or firefox:default"
        )

    browser_name, keyring, profile, container = match.group("name", "keyring", "profile", "container")
    return browser_name.lower(), profile, (keyring.upper() if keyring else None), container


def download_video(youtube_url: str, output_dir: Path) -> DownloadResult:
    base_opts = {
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "outtmpl": str(output_dir / "%(title).180B [%(id)s].%(ext)s"),
        "postprocessor_args": {"Merger": ["-movflags", "+faststart"]},
        "extractor_retries": 2,
    }

    cookiefile_path = _resolve_cookiefile(output_dir)
    browser_cookie_spec = _parse_cookiesfrombrowser_spec()

    format_candidates = [
        "bestvideo+bestaudio/best",
        "best",
    ]

    def try_download(opts: dict):
        info_local = None
        last_error_local = None
        used_opts = opts

        for fmt in format_candidates:
            ydl_opts = {**opts, "format": fmt}
            used_opts = ydl_opts
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
                    info_local = current
                    break
            except yt_dlp.utils.DownloadError as exc:
                last_error_local = exc
                continue

        return info_local, last_error_local, used_opts

    attempts: list[tuple[str, dict]] = []
    if cookiefile_path:
        opts = dict(base_opts)
        opts["cookiefile"] = str(cookiefile_path)
        attempts.append(("cookiefile", opts))

    if browser_cookie_spec:
        opts = dict(base_opts)
        opts["cookiesfrombrowser"] = browser_cookie_spec
        attempts.append(("cookiesfrombrowser", opts))

    attempts.append(("no_cookies", dict(base_opts)))

    info = None
    last_error = None
    used_opts = dict(base_opts)
    attempt_errors: list[str] = []
    for attempt_name, opts in attempts:
        info, last_error, used_opts = try_download(opts)
        if info is not None:
            break
        attempt_errors.append(f"{attempt_name}: {last_error}")

    if info is None:
        details = " | ".join(attempt_errors) if attempt_errors else str(last_error)
        raise RuntimeError(f"Failed to download video: {details}")

    with yt_dlp.YoutubeDL(used_opts) as ydl:
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
        details = str(exc)
        if 'unsupported browser: "0"' in details or "unsupported browser" in details:
            user_msg = (
                "Invalid YOUTUBE_COOKIES_FROM_BROWSER value. "
                "Use empty value on Vercel or valid values like chrome/firefox for local runs."
            )
        elif "Sign in to confirm you" in details:
            user_msg = (
                "YouTube requires authorization for this video. "
                "Provide fresh cookies via YOUTUBE_COOKIES_FILE or YOUTUBE_COOKIES. "
                "You can also set YOUTUBE_COOKIES_FROM_BROWSER for local runs."
            )
        else:
            user_msg = (
                "Could not download this video. YouTube may be returning only storyboard/images "
                "or blocking formats."
            )
        return error_response(
            user_msg,
            400,
            details,
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

    try:
        blob = _upload_to_blob(result.path)
    except Exception as exc:  # noqa: BLE001
        return error_response("Downloaded video but failed to upload to Vercel Blob.", 500, str(exc))

    if blob:
        download_url = blob.get("downloadUrl") or blob.get("url")
        if wants_html and download_url:
            return redirect(download_url)
        return jsonify(
            {
                "ok": True,
                "title": result.title,
                "size_mb": round(size_mb, 1),
                "blob": blob,
                "download_url": download_url,
            }
        )
    if os.getenv("VERCEL", "").strip() == "1":
        if BlobClient is None:
            return error_response(
                "Python package 'vercel' is missing. Install dependencies and redeploy.",
                500,
            )
        return error_response(
            "BLOB_READ_WRITE_TOKEN is not configured. "
            "On Vercel, direct video response is not supported for large files. "
            "Connect Vercel Blob and redeploy.",
            500,
        )

    # Local fallback when Vercel Blob is not configured
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
