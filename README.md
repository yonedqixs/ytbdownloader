# YouTube Downloader (Vercel Frontend + Worker Backend)

This repo is split into two services:

- `app.py`: lightweight frontend/proxy (deploy on Vercel)
- `worker_app.py`: heavy downloader (deploy on VPS/Render/Railway/etc.)

The frontend sends URL requests to worker.  
Worker downloads with `yt-dlp`, uploads MP4 to Vercel Blob, and returns a download URL.

## Why this architecture

YouTube often blocks datacenter IPs (`Sign in to confirm you're not a bot`) even with cookies.  
Vercel is great for frontend, but download reliability is better on a dedicated worker host.

## Install

```powershell
cd "C:\Users\mrkpr\OneDrive\Документы\youtube-telegram-bot"
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

## Frontend env (Vercel)

- `WORKER_API_URL` = `https://your-worker-domain`
- `WORKER_API_KEY` = shared secret (same on worker)
- `WORKER_TIMEOUT_SEC` = `300` (or more)
- `MAX_FILE_MB` = `1900` (UI hint only)

## Worker env

- `WORKER_API_KEY` = shared secret
- `BLOB_READ_WRITE_TOKEN` = token from Vercel Blob
- `BLOB_ACCESS` = `public` or `private`
- `MAX_FILE_MB` = `1900`
- `YOUTUBE_COOKIES` or `YOUTUBE_COOKIES_FILE` (Netscape format)
- Optional local only: `YOUTUBE_COOKIES_FROM_BROWSER`

## Run locally

Worker:

```powershell
$env:APP_PORT="5001"
$env:WORKER_API_KEY="change-me"
.\.venv\Scripts\python .\worker_app.py
```

Frontend (new terminal):

```powershell
$env:WORKER_API_URL="http://127.0.0.1:5001"
$env:WORKER_API_KEY="change-me"
.\.venv\Scripts\python .\app.py
```

Open: [http://127.0.0.1:5000](http://127.0.0.1:5000)

## API flow

1. Browser POST `/api/download` to frontend.
2. Frontend POSTs JSON to worker: `/api/worker/download` + `X-Worker-Key`.
3. Worker downloads video and uploads to Blob.
4. Frontend redirects user to `download_url`.

## Vercel notes

- Keep `api/index.py` and `vercel.json` as committed in repo.
- Frontend does not stream MP4 bytes directly.
- Worker must handle downloading.
- `Dockerfile` in this repo is configured for worker (`worker_app:app`).
