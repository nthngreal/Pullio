# Pullio

**Pullio** is a lightweight Windows GUI for downloading video and audio using
yt-dlp + FFmpeg.

## Features

- Video downloads: MAX / 4K / 1440p / 1080p / 720p
- MP4 / MKV output
- CapCut-compatible MP4 mode
- MP3 extraction: Best / 320 / 256 / 192 / 128 kbps
- Automatic video info + thumbnail
- Download progress, speed and ETA
- Duplicate detection with clean `(1)`, `(2)`, `(3)` filenames
- Download history
- yt-dlp updater with confirmation
- Dark modern interface

## Development

1. Put your local dependencies in the project root:
   - `yt-dlp.exe`
   - `ffmpeg.exe`
   - `ffprobe.exe` (recommended)

2. Install Python dependencies:

   `install_dev.bat`

3. Run:

   `run_dev.bat`

## Build Windows release

Run:

`build_release.bat`

It creates:

- `dist\Pullio.exe`
- `release\Pullio-1.0.0-win64\`

The build script does **not** overwrite your existing yt-dlp or FFmpeg files.

## Icon

The Pullio brand assets are included:

- `assets\pullio.ico` - multi-size Windows icon used by `Pullio.exe`
- `assets\pullio-512.png` - square PNG for GitHub/release pages
- `assets\pullio-brand.png` - original high-resolution brand artwork

`build_release.bat` automatically embeds `assets\pullio.ico` into the EXE.

## Support link

Before publishing, edit these constants in `src\pullio.py`:

```python
PROJECT_URL = ""
SUPPORT_URL = ""
```

Use a GitHub repository URL and a GitHub Sponsors / Ko-fi page. Do not put card
numbers or payment secrets in the source code.

## Legal / responsible use

Pullio is a general-purpose media download interface. It does not give users
permission to download, redistribute, or reuse copyrighted material.

Use Pullio only for content you have the right or permission to download.

See `LICENSE` and `THIRD_PARTY_NOTICES.md`.


## Release privacy

`pullio_settings.json` and `pullio_history.json` are local runtime files.
They are intentionally excluded from the public release package.

`build_release.bat` cleans the previous release folder before packaging and
verifies that these personal JSON files are not present before creating the ZIP.
