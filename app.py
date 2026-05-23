import os
import re
import json
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import Flask, jsonify, redirect, render_template, request

YOUTUBE_HOSTS = {"youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}
URL_RE = re.compile(r"https?://\S+")
WORKER_TIMEOUT_SEC = int((os.getenv("WORKER_TIMEOUT_SEC") or "300").strip())

app = Flask(__name__)


def extract_url(text: str) -> str | None:
    match = URL_RE.search(text or "")
    return match.group(0) if match else None


def is_youtube_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host in YOUTUBE_HOSTS or host.endswith(".youtube.com")


def _worker_url() -> str:
    raw = (os.getenv("WORKER_API_URL") or "").strip()
    if not raw:
        raise RuntimeError("WORKER_API_URL is not configured.")
    return raw.rstrip("/")


def _worker_key() -> str:
    return (os.getenv("WORKER_API_KEY") or "").strip()


def _error_response(message: str, status: int, details: str | None = None):
    payload = {"ok": False, "error": message}
    if details:
        payload["details"] = details
    return jsonify(payload), status


@app.get("/")
def index():
    max_file_mb = int((os.getenv("MAX_FILE_MB") or "1900").strip())
    return render_template("index.html", max_file_mb=max_file_mb)


@app.post("/api/download")
def api_download():
    wants_html = "text/html" in (request.headers.get("Accept") or "")
    url = extract_url((request.form.get("url") or "").strip())
    if not url:
        return _error_response("Please provide a valid YouTube URL.", 400)
    if not is_youtube_url(url):
        return _error_response("Only YouTube URLs are supported.", 400)

    try:
        endpoint = f"{_worker_url()}/api/worker/download"
    except Exception as exc:  # noqa: BLE001
        return _error_response("Worker backend is not configured.", 500, str(exc))

    headers = {"Content-Type": "application/json"}
    worker_key = _worker_key()
    if worker_key:
        headers["X-Worker-Key"] = worker_key

    payload_bytes = json.dumps({"url": url}).encode("utf-8")
    req = Request(endpoint, data=payload_bytes, headers=headers, method="POST")
    status_code = 200
    body_text = ""
    try:
        with urlopen(req, timeout=WORKER_TIMEOUT_SEC) as resp:
            status_code = getattr(resp, "status", 200) or 200
            body_text = resp.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        status_code = exc.code
        body_text = exc.read().decode("utf-8", errors="replace")
    except URLError as exc:
        return _error_response("Worker backend is unreachable.", 502, str(exc))
    except TimeoutError as exc:
        return _error_response("Worker backend request timed out.", 504, str(exc))

    try:
        payload = json.loads(body_text or "{}")
    except ValueError:
        return _error_response("Worker backend returned non-JSON response.", 502, body_text[:1000])

    if status_code >= 400 or not payload.get("ok"):
        return _error_response(
            payload.get("error", "Worker failed to process the request."),
            status_code if status_code >= 400 else 502,
            payload.get("details"),
        )

    download_url = payload.get("download_url") or (payload.get("blob") or {}).get("downloadUrl")
    if wants_html and download_url:
        return redirect(download_url)
    return jsonify(payload)


@app.get("/healthz")
def healthz():
    return {"ok": True, "service": "frontend"}


if __name__ == "__main__":
    host = (os.getenv("APP_HOST") or "0.0.0.0").strip()
    port_raw = (os.getenv("PORT") or os.getenv("APP_PORT") or "5000").strip()
    port = int(port_raw) if port_raw.isdigit() else 5000
    debug = (os.getenv("APP_DEBUG") or "0").strip() == "1"
    app.run(host=host, port=port, debug=debug)
