# 🤖 NXT_HUB Leech Bot

A powerful Telegram leech bot with integrated FFmpeg encoding, multi-source downloading, intelligent auto-thumbnail generation, and Pyrogram Premium upload support.

---

## ✨ Features

| Category | Feature |
|---|---|
| **Download** | HTTP/HTTPS, YouTube & 1000+ sites (yt-dlp), M3U8 streams, Torrent/Magnet (aria2), qBittorrent, Mega.nz, Telegram media re-upload |
| **Direct Links** | MediaFire, PixelDrain, HubCloud, GDFlix, GoFile, BuzzHeavier, TeraBox, 1Fichier, KrakenFiles, WeTransfer, OneDrive, Yandex, Streamtape, DoodStream, FileLions/StreamWish, MP4Upload, Racaty, and 70+ more |
| **JDLeech** | Premium multi-host resolving via MyJDownloader API |
| **Encode** | H.264/H.265, custom CRF/preset/resolution/FPS, audio codec & bitrate, hardsub/softsub, ASS watermark overlay |
| **Upload** | Auto-split >2 GB files, Premium 4 GB session, dump channel with username tag, custom or auto-generated HD thumbnail |
| **Auto-Thumbnail** | Fanart.tv HD logo composite → TMDB backdrop+logo → iTunes poster → title-card fallback (4-tier waterfall) |
| **Caption Tokens** | `{name}` `{size}` `{ext}` `{quality}` `{language}` `{codec}` `{audio}` `{fps}` `{date}` |
| **Settings** | Per-user: prefix/suffix, rename regex, caption template, cookies, upload mode, dump channel, encode prefs |
| **Admin Panel** | Interactive `/admin` panel — add/remove owners & admins, list users, live task stats |
| **Subtitle Mux** | `/encsub` — attach external SRT/ASS subtitle to a video without re-encoding |

---

## 🚀 Setup

### 1. Clone & install

```bash
git clone https://github.com/mepankaja/NXTL.git
cd NXTL
pip install -r requirements.txt
apt install aria2 ffmpeg mkvtoolnix qbittorrent-nox  # system dependencies
```

### 2. Configure `config.py`

```python
# ── Telegram ──────────────────────────────────────────────────
API_ID           = your_api_id
API_HASH         = "your_api_hash"
BOT_TOKEN        = "your_bot_token"
SESSION          = ""              # Pyrogram Premium session string (enables 4 GB uploads)
OWNER_ID         = your_user_id
LOG_CHANNEL      = -100xxxxxxxxx
AUTHORIZED_CHATS = []              # e.g. [123456789, -1001234567890]

# ── Database ──────────────────────────────────────────────────
MONGO_URI = "mongodb+srv://..."
MONGO_DB  = "nxthub"

# ── Mega.nz (optional) ────────────────────────────────────────
MEGA_EMAIL    = ""
MEGA_PASSWORD = ""

# ── JDownloader (optional — enables premium host resolving) ───
JD_EMAIL    = ""     # MyJDownloader account email
JD_PASSWORD = ""     # MyJDownloader account password
JD_DEVICE   = ""     # Device name (blank = first available)

# ── Aria2 ─────────────────────────────────────────────────────
ARIA2_HOST   = "http://localhost"
ARIA2_PORT   = 6800
ARIA2_SECRET = ""    # Optional RPC secret token

# ── qBittorrent (optional) ────────────────────────────────────
QBT_HOST     = "localhost"
QBT_PORT     = 8090
QBT_USERNAME = "admin"
QBT_PASSWORD = "adminadmin"

# ── Auto-Thumbnail APIs (optional) ────────────────────────────
TMDB_API_KEY   = ""  # https://www.themoviedb.org/settings/api
FANART_API_KEY = ""  # https://fanart.tv/get-an-api-key/

# ── Upload ────────────────────────────────────────────────────
DUMP_CHANNEL     = 0        # Forward all uploads here (channel ID)
DUMP_CHANNEL_TAG = True     # Tag uploader's username in dump
AS_DOCUMENT      = False    # True = always upload as document
MAX_TASKS        = 4        # Concurrent tasks per user

# ── Bot branding ──────────────────────────────────────────────
WATERMARK      = "@NXT_HUB"
WATERMARK_FILE = "bot/encoding/extras/watermark.ass"
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
| `/d <url>` | Download any URL — auto-detects type (HTTP, yt-dlp, torrent, mega, etc.) |
| `/d` (reply to file) | Re-upload a Telegram file |
| `/leech` / `/l` | Alias for `/d` |
| `/jdleech <url>` | Multi-host premium download via MyJDownloader |

### 🎬 Encoding
| Command | Description |
|---|---|
| `/encode` | Reply to a video to download & encode it |
| `/encurl <url>` | Download a URL then encode it |
| `/encsub` | Attach an external SRT/ASS subtitle to a video (no re-encode) |
| `/encset` | Open interactive FFmpeg encoding settings |
| `/vset` | View current encoding settings as text |

### 📊 Tasks & Status
| Command | Description |
|---|---|
| `/status` | View your active tasks with live progress |
| `/cancel <id>` | Cancel a task by ID |

### ⚙️ Settings & Admin
| Command | Description |
|---|---|
| `/settings` | Open per-user settings menu |
| `/admin` | Open admin panel (owners/admins only) |
| `/addadmin <id>` | Grant admin access to a user |
| `/removeadmin <id>` | Revoke admin access |
| `/addowner <id>` | Grant owner access |
| `/removeowner <id>` | Revoke owner access |
| `/listusers` | List all authorized users |

---

## ⚙️ Settings Sections

### 📥 Download Settings
- **Cookies** — Upload a `cookies.txt` (Netscape format) for yt-dlp restricted content
- Export from browser with the *Get cookies.txt LOCALLY* extension

### 📤 Upload Settings
- **Custom Thumbnail** — Set a custom thumbnail for all uploads
- **Upload Mode** — Toggle between Media (video/audio) and Document mode
- **Dump Channel** — Forward all uploads to an additional channel

### 🎬 Encoding Settings
- Codec (H.264 / H.265), CRF, Preset, Resolution, FPS
- Audio: Codec, Bitrate, Sample Rate, Channels
- Subtitles: Hardsub (burn-in) / Softsub (copy stream)
- Watermark: ASS overlay from `bot/encoding/extras/watermark.ass`

### 🏷 Rename Settings
- **Prefix / Suffix** — Added to every uploaded filename
- **Rename Regex** — Pattern stripped from filenames before upload
- **Caption Template** — Text shown below every upload

**Available caption tokens:**

| Token | Description | Example |
|---|---|---|
| `{name}` | Clean filename stem | `Movie.Name.2024` |
| `{ext}` | File extension | `mkv` |
| `{size}` | Human-readable size | `1.23 GB` |
| `{quality}` | Resolution/quality tag | `1080p` |
| `{language}` | Audio language codes | `eng+hin` |
| `{codec}` | Video codec | `HEVC` |
| `{audio}` | Audio codec | `AAC` |
| `{fps}` | Frame rate | `24` |
| `{date}` | Today's date | `2026-06-14` |

---

## 🖼 Auto-Thumbnail (4-Tier Waterfall)

When no custom thumbnail is set, the bot auto-generates one using this priority chain:

1. **Fanart.tv** — HD movie/series logo composited over a clean background
2. **TMDB** — Official backdrop image + title logo PNG composite
3. **iTunes** — Portrait poster converted to landscape with title text overlay
4. **Title Card** — Guaranteed local fallback, always produces a result

Requires `TMDB_API_KEY` and `FANART_API_KEY` set in `config.py` for tiers 1–3.

---

## 🌐 Supported Direct-Link Hosts (70+)

PixelDrain · HubCloud · GDFlix · MediaFire · GoFile · BuzzHeavier ·
TeraBox (all domains) · 1Fichier · KrakenFiles · WeTransfer · OneDrive ·
Yandex Disk · GitHub Releases · Streamtape · DoodStream · FileLions ·
StreamWish · UploadHaven · DevUploads · Send.cm · Racaty · MP4Upload ·
AKMFiles · StreamVid · StreamHub · and 50+ more via NEO-WZML engine

---

## 📦 Large File Handling

| Scenario | Behaviour |
|---|---|
| File ≤ 2 GB, no SESSION | Upload normally via bot account |
| File ≤ 4 GB, SESSION set | Upload via Premium account session |
| File > 2 GB, no SESSION | Auto-split into numbered `.partNN.ext` documents |
| File > 4 GB, SESSION set | Auto-split into 4 GB parts |

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

## 🏗 Project Structure

```
NXTL/
├── main.py                        # Entry point
├── config.py                      # All configuration
├── health_check.py                # Koyeb health endpoint
├── bot/
│   ├── core/
│   │   ├── downloader.py          # Unified re-export hub
│   │   ├── uploader.py            # Upload engine (split, thumb, dump)
│   │   ├── extractor.py           # Archive extract / zip
│   │   └── task_manager.py        # Concurrent task tracking
│   ├── downloaders/
│   │   ├── aria2_downloader.py    # Torrent/magnet via aria2 RPC
│   │   ├── qbt_downloader.py      # qBittorrent client
│   │   ├── http_downloader.py     # HTTP/HTTPS streaming
│   │   ├── ytdlp_downloader.py    # yt-dlp (YouTube, M3U8, 1000+ sites)
│   │   ├── mega_downloader.py     # Mega.nz
│   │   ├── jd_downloader.py       # MyJDownloader premium hosts
│   │   ├── telegram_downloader.py # Telegram media
│   │   └── direct_link_generator.py  # URL resolver
│   ├── handlers/
│   │   ├── download.py            # /d /leech /l commands
│   │   ├── encode.py              # /encode /encurl /encsub
│   │   ├── settings.py            # /settings menu
│   │   ├── admin.py               # /admin panel
│   │   ├── status.py              # /status /cancel
│   │   └── callbacks.py           # Inline button handlers
│   └── utils/
│       ├── thumbnail.py           # 4-tier auto-thumbnail engine
│       ├── token_resolver.py      # Caption token engine
│       ├── rename.py              # Smart rename / prefix / suffix
│       ├── progress.py            # Progress cards & status UI
│       └── direct_links.py        # Direct link resolvers
└── web/
    └── app.py                     # FastAPI web UI (file selector)
```

---

## 🙏 Credits

- [pyrofork](https://github.com/Mayuri-Chan/pyrofork) — Telegram MTProto client
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — Video & audio downloader
- [WZML-X](https://github.com/weebzone/WZML-X) — Direct link generator patterns
- [ENCODING-BOT](https://github.com/Cantarellabots/ENCODING-BOT) — FFmpeg encoding engine
- [aria2](https://aria2.github.io/) — Torrent/magnet download daemon
- [TMDB](https://www.themoviedb.org/) / [Fanart.tv](https://fanart.tv/) — Thumbnail metadata
