# YouTube Downloader Web

Web app: paste a YouTube URL -> get downloadable MP4.

## Local run

```powershell
cd "C:\Users\mrkpr\OneDrive\Документы\youtube-telegram-bot"
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python .\app.py
```

Open: `http://127.0.0.1:5000`

## Deploy to Render (recommended via Docker)

This repo is ready for Render:
- `Dockerfile`
- `render.yaml`

### Steps

1. Upload project to GitHub.
2. In Render: **New +** -> **Blueprint**.
3. Connect your GitHub repo and select it.
4. Render will detect `render.yaml` and create service `youtube-downloader-web`.
5. Click **Apply** / **Create Resources**.
6. Wait for build and deploy to finish.
7. Open your Render URL and test with a YouTube link.

### Important

- `cookies.txt` is excluded from Docker image (`.dockerignore`), so deploy does not leak your local cookies.
- If some videos fail, YouTube may block streams for that video/region/network.
- `MAX_FILE_MB` can be changed in Render environment variables.
- For protected videos on Render, set `YOUTUBE_COOKIES` environment variable with full Netscape cookies.txt content.

## Environment variables

- `MAX_FILE_MB` default `1900`
- `APP_DEBUG` default `0`
- `APP_HOST` default `0.0.0.0`
- `PORT` provided by Render automatically
- `YOUTUBE_COOKIES` optional, full cookies.txt content (multiline)
