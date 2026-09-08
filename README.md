<div align="center">

<img src="assets/pullio-512.png" width="120" alt="Pullio logo">

# Pullio

### Video & Audio Downloader

A lightweight Windows app for downloading video and audio, powered by **yt-dlp** and **FFmpeg**.

**Windows 10 / 11 · Video · MP3 · CapCut-friendly MP4**

<br>

## Demo

![Pullio demo](assets/pullio-demo.gif)

</div>

---

## 📥 Download Pullio

### Windows 10 / 11

➡️ **[Download the latest version](https://github.com/nthngreal/Pullio/releases/latest)**

Pullio is portable. **No installation or Python required.**

### 🚀 Getting started

1. Download `Pullio-1.0.0-win64.zip`
2. Extract the ZIP file
3. Open the extracted `Pullio-1.0.0-win64` folder
4. Run **`Pullio.exe`**
5. Paste a YouTube URL
6. Choose **Video** or **Audio**
7. Click **Download**

> 💡 Everything needed to run Pullio is included in the release package.

---

## ✨ Features

| Feature | Support |
|---|---|
| 🎬 Video quality | MAX, 4K, 1440p, 1080p, 720p |
| 📦 Video formats | MP4, MKV |
| 🎵 Audio | MP3 - Best, 320, 256, 192, 128 kbps |
| ✂️ CapCut | CapCut-compatible MP4 mode |
| 🖼️ Video info | Title, channel, source info and thumbnail |
| 📊 Download status | Progress, speed and ETA |
| 📁 Duplicate files | Automatic `(1)`, `(2)`, `(3)` naming |
| 🕘 History | Download history |
| 🔄 yt-dlp | Built-in updater |
| 🌙 Interface | Modern dark UI |
| 💻 Windows | Portable release, no installation required |

---

## 🖥️ How to use

### 1. Paste a YouTube URL

Paste the link into the **YouTube URL** field.

Pullio automatically loads the video's title, channel, thumbnail and source information.

### 2. Choose Video or Audio

For **Video**, choose the desired quality and container.

For **Audio**, choose the desired MP3 quality.

### 3. Choose where to save

Use **Browse** to select your destination folder.

### 4. Download

Click **Download**.

Pullio will display the progress, download speed and estimated time remaining.

---

## ⚙️ How Pullio works

Pullio provides a graphical interface around:

- **yt-dlp** for media downloading
- **FFmpeg** for merging, conversion and audio extraction
- **FFprobe** for media information

Pullio does not hide or replace these projects. They are the engines that power the download process.

---

## 🛠️ For developers

Pullio is built with **Python + CustomTkinter** and uses **yt-dlp + FFmpeg**.

<details>
<summary><b>Development setup</b></summary>

### Requirements

- Python 3
- yt-dlp
- FFmpeg
- FFprobe

### Install Python dependencies

Run:

```bat
install_dev.bat
```

### Run Pullio

Run:

```bat
run_dev.bat
```

### Build the Windows release

Run:

```bat
build_release.bat
```

The build script creates the portable Windows release in:

```text
release/Pullio-1.0.0-win64/
```

</details>

---

## ❤️ Support

Pullio is free to use.

If you find Pullio useful, you can support its development through the **Support** button inside the app once the support page is configured.

---

## ⚖️ Legal / Responsible use

Pullio is a general-purpose media download interface.

It does not give users permission to download, redistribute or reuse copyrighted material.

Use Pullio only for content you have the right or permission to download.

Users are responsible for complying with applicable laws and the terms of service of the platforms they use.

---

## 📄 License

Pullio's own source code is released under the **MIT License**.

Third-party components have their own licenses and terms.

See:

- [LICENSE](LICENSE)
- [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)

---

<div align="center">

Made with Python, CustomTkinter, yt-dlp and FFmpeg.

**Pullio v1.0.0**

</div>
