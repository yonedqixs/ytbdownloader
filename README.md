# YouTube Web Downloader

Minimal web app for downloading YouTube videos as MP4.

## Install

```powershell
cd "C:\Users\mrkpr\OneDrive\Документы\youtube-telegram-bot"
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

## Environment

Copy `.env.example` and set what you need:

- `APP_HOST` (default `127.0.0.1`)
- `APP_PORT` (default `5000`)
- `APP_DEBUG` (`0` or `1`)
- `MAX_FILE_MB` (default `1900`)
- `YOUTUBE_COOKIES_FILE` (optional path to cookies file)
- `YOUTUBE_COOKIES` (optional raw cookies text or cookies file path)
- `YOUTUBE_COOKIES_FROM_BROWSER` (optional, e.g. `chrome` or `firefox:default`)
- `BLOB_READ_WRITE_TOKEN` (required on Vercel to store output files)
- `BLOB_ACCESS` (`public` or `private`, default `public`)

Priority:
1. `YOUTUBE_COOKIES_FILE`
2. `YOUTUBE_COOKIES`
3. local `cookies.txt`

`cookies.txt` must be Netscape format (`# Netscape HTTP Cookie File` header).

## Run

```powershell
.\.venv\Scripts\python .\app.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000)

## Deploy on Vercel

1. Create a Blob store in your Vercel project and keep `BLOB_READ_WRITE_TOKEN` enabled.
2. Add env vars in Vercel Project Settings:
   - `BLOB_READ_WRITE_TOKEN`
   - `BLOB_ACCESS=public`
   - one of `YOUTUBE_COOKIES_FILE` / `YOUTUBE_COOKIES`
3. Deploy.

On Vercel, `/api/download` uploads the generated MP4 to Blob and returns/redirects to blob URL.

## Notes

- `ffmpeg` must be available in `PATH`.
- Some videos may still be blocked by YouTube region/account restrictions.
- `YOUTUBE_COOKIES_FROM_BROWSER` is for local runs; cloud environments usually cannot read your local browser profile.
