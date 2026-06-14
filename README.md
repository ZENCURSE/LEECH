# 🤖 NXT_HUB Leech Bot

A powerful Telegram leech bot with integrated FFmpeg encoding, multi-host downloading, and Pyrogram premium upload support.

---

## ✨ Features

| Category | Feature |
|---|---|
| **Download** | HTTP/HTTPS, YouTube, M3U8 streams, torrents/magnets, Mega.nz |
| **JDLeech** | MediaFire, PixelDrain, BuzzHeavier, GoFile, TeraBox, 1Fichier, KrakenFiles, WeTransfer, OneDrive, Yandex, Streamtape, DoodStream, FileLions, and more |
| **Encode** | H.264/H.265, custom CRF/preset/resolution, audio codec, hardsub/softsub, watermark |
| **Upload** | Auto-split >2 GB files, premium 4 GB session support, dump channel, custom thumbnail |
| **Settings** | Per-user: prefix/suffix, rename regex, caption template, cookies, encode prefs |

---

## 🚀 Setup

### 1. Clone & install

```bash
git clone <repo>
cd NXTL-main
pip install -r requirements.txt
apt install aria2 ffmpeg mkvtoolnix  # system dependencies
```

### 2. Configure `config.py`

```python
API_ID           = your_api_id
API_HASH         = "your_api_hash"
BOT_TOKEN        = "your_bot_token"
SESSION          = ""              # Pyrogram Premium session string (enables 4 GB uploads)
OWNER_ID         = your_user_id
LOG_CHANNEL      = -100xxxxxxxxx
AUTHORIZED_CHATS = []

MONGO_URI = "mongodb+srv://..."
MEGA_EMAIL    = ""                 # Optional: Mega.nz account
MEGA_PASSWORD = ""

# JDownloader2 — optional, enables premium host resolving via MyJDownloader API
# Register at: https://my.jdownloader.org  |  pip install myjdapi
JD_EMAIL    = ""                   # MyJDownloader account email
JD_PASSWORD = ""                   # MyJDownloader account password
JD_DEVICE   = ""                   # Device name (blank = first available device)
```

### 3. Run

```bash
python main.py
```

---

## 📋 Commands

### 📥 Download
| Command | Description |
|---|---|
| `/d <url>` | Download any URL (auto-detects type) |
| `/jdleech <url>` | Multi-host download (MediaFire, GoFile, TeraBox, etc.) |
| `/d` (reply to file) | Re-upload a Telegram file |
| `/leech` or `/l` | Alias for `/d` |

### 🎬 Encoding
| Command | Description |
|---|---|
| `/encode` | Reply to a video to download & encode it |
| `/encurl <url>` | Download URL and encode it |
| `/encset` | Open interactive encoding settings |
| `/vset` | View current encoding settings as text |

### 📊 Tasks
| Command | Description |
|---|---|
| `/status` | View your active tasks |
| `/cancel <id>` | Cancel a task |

### ⚙️ Settings
| Command | Description |
|---|---|
| `/settings` | Open settings menu (sections below) |

---

## ⚙️ Settings Sections

The `/settings` menu is divided into 4 sections:

### 📥 Download Settings
- **Cookies** — Upload a `cookies.txt` (Netscape format) for yt-dlp to access restricted content
- Export from browser using the *Get cookies.txt LOCALLY* extension

### 📤 Upload Settings
- **Custom Thumbnail** — Set a custom thumbnail for all uploads
- **Upload Mode** — Toggle between Media (video/audio) and Document mode
- **Dump Channel** — Forward all uploads to an additional channel

### 🎬 Encoding Settings
- Opens the FFmpeg encode settings panel
- Configure: Codec (H.264/H.265), CRF, Preset, Resolution, FPS
- Audio: Codec, Bitrate, Sample Rate, Channels
- Subtitles: Hardsub / Softsub copy
- Watermark overlay (ASS subtitle)

### 🏷 Rename Settings
- **Prefix** / **Suffix** — Added to every filename
- **Rename Regex** — Pattern stripped from filenames
- **Caption Template** — Shown below uploaded files
  - Tokens: `{name}` `{size}` `{quality}` `{language}` `{codec}` `{audio}` `{fps}`

---

## 📡 JDLeech — Supported Hosts

MediaFire · PixelDrain · BuzzHeavier · GoFile · TeraBox (all domains) ·
1Fichier · KrakenFiles · WeTransfer · OneDrive · Yandex Disk · GitHub ·
Streamtape · DoodStream · FileLions/StreamWish · UploadHaven · DevUploads ·
Send.cm · Racaty · MP4Upload · AKMFiles · StreamVid · StreamHub

---

## 📦 Large File Handling

- Files up to **2 GB** — uploaded normally (bot account)
- Files up to **4 GB** — requires `SESSION` set to a Premium account session string
- Files **>2 GB without SESSION** — automatically split into numbered `.partNN.ext` parts uploaded as documents
- Files **>4 GB with SESSION** — automatically split into 4 GB parts

---

## 🎬 Encoding Settings Reference

| Setting | Options |
|---|---|
| Codec | H.264 / H.265 |
| CRF | 0–51 (lower = better quality, larger file) |
| Preset | ultrafast / superfast / veryfast / fast / medium / slow |
| Resolution | Source / 4K / 2K / 1080p / 720p / 576p / 480p / 360p |
| FPS | Source / NTSC / PAL / Film / 23.976 / 30 / 60 |
| Audio | Source / AAC / AC3 / OPUS / VORBIS / ALAC |
| Subtitles | Hardsub (burn-in) / Softsub (copy stream) |
| Watermark | ASS overlay from `bot/encoding/extras/watermark.ass` |

---

## 🐳 Docker

```bash
docker-compose up -d
```

---

## 🙏 Credits

- [pyrofork](https://github.com/Mayuri-Chan/pyrofork) — Telegram client
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — Video downloader
- [WZML-X](https://github.com/weebzone/WZML-X) — Downloader patterns & direct link generators
- [ENCODING-BOT](https://github.com/Cantarellabots/ENCODING-BOT) — FFmpeg encoding engine
- [aria2](https://aria2.github.io/) — Torrent/magnet downloader
