# LocalScribe

> **Beta Notice**: LocalScribe is currently in an **early beta** stage. Features, UI behavior, and setup/packaging flows may change, and you may still encounter bugs while the project is being stabilized.

**LocalScribe** is an elegant, entirely offline, and privacy-focused application for highly accurate audio transcription, translation, and document generation. Built on **PySide6** and powered by the cutting-edge **faster-whisper** engine, it brings studio-quality subtitle and transcript generation completely to your local hardware without relying on any cloud APIs or third-party telemetries.

No internet connection required. No subscriptions. No tracking. Complete privacy—all audio strictly stays on your local machine.

---

## Comprehensive Feature Set

### Advanced Audio Inference
* **100% Offline AI Processing**: Transcribe media directly on your PC. Uses CTranslate2 under the hood (via `faster-whisper`) delivering vastly increased performance over standard Whisper.
* **Hardware Acceleration**: Automatic environment detection natively supports inference on compatible NVIDIA GPUs via CUDA/cuDNN out of the box, falling back naturally to optimized CPU inference when necessary.
* **Instant Background Initialization**: Core transcription models are intelligently preloaded by a background worker instantly upon opening the application, making startup smooth and non-blocking. 
* **Live Streaming Transcripts**: Don't just stare at a progress bar. LocalScribe features a real-time live view of your transcription as it streams word-by-word into the UI, updating on the fly!

* **SRT Subtitles Generation**: Generate standardized `.srt` subtitle files. Granularly customize subtitle breaks, max line lengths, and max sentences perfectly customized for YouTube videos or social media.
* **Microsoft Word Export**: Advanced `python-docx` integration correctly outputs deeply formatted transcripts including automatic conversational breakdown blocks. 
* **Direct PDF Generation**: Features `fpdf2` logic for directly rendering transcripts into standardized portable documents.


### Beautiful, Modern UI Elements
* **Native GUI**: Developed with PySide6 for snappy, native cross-platform performance.
* **Intelligent Dashboard**: Features an elegant drag-and-drop landing interface cleanly organizing historical file states and visual statuses.
* **Dual Theme Architecture**: Built from the ground up to support stunning modern Light and Dark Modes driven by bespoke customizable `QSS` (Qt Style Sheets).
* **Interactive Editor**: Jump into completed transcript cards to edit timestamps, correct generated words, and replay segments gracefully handled by the LocalScribe playback module.

---

## Project Architecture & Layout

Understanding how LocalScribe works under the hood is straightforward:

```text
LocalScribe/
├── main.py                  # The standard entrypoint initializing PySide6
├── run.py                   # Convenience launcher (auto-activates the Virtual Env!)
├── build.py                 # PyInstaller wrapper logic to compile to standalone .exe
├── installer.iss            # Inno Setup script to create a Windows installer
├── assets/
│   ├── dark_theme.qss       # Detailed styling engine for Dark Mode
│   └── light_theme.qss      # Detailed styling engine for Light Mode
├── image/
│   └── LocalScribe.ico      # The application branding and icons
├── core/
│   ├── paths.py             # Centralized data directory resolution (dev vs frozen)
│   ├── transcriber.py       # Interfaces with faster-whisper and hardware bridging
│   ├── exporter.py          # PDF/Word processing and string handling
│   ├── model_manager.py     # Checks/Downloads required translation & transcript models
│   └── storage.py           # Handles state mapping, JSON history, config caching
└── ui/
    ├── main_window.py       # Core UI routing, stacked widgets, and threading connections
    ├── sidebar.py           # Sidebar logic holding history and status elements
    ├── dashboard_page.py    # Drag/drop landing zone for launching jobs
    ├── transcript_page.py   # Code area serving the visual editor & live streaming updates
    ├── dialogs/             # Modal flows (Translation target selections, SRT tweaks)
    └── widgets/             # Reusable UI components (Action Buttons, SubCards, Zones)
```

---

## Quick Start (End Users)

No Python or technical setup required.

1. Download **`LocalScribe_Setup.exe`** from the [Releases](https://github.com/shenfurkan/LocalScribe/releases) page.
2. Run the installer and follow the prompts.
3. Launch **LocalScribe** from the Start Menu or Desktop shortcut.
4. On first use, the Whisper speech model (~3 GB) downloads automatically — this only happens once.

### Which file should I click?

- **First time / normal users:** click **`LocalScribe_Setup.exe`**.
- **After installation:** click the **LocalScribe Start Menu/Desktop shortcut** (or `LocalScribe.exe` inside the installed folder).
- **Do not** copy only `LocalScribe.exe` by itself to another folder/Desktop. It needs its bundled files next to it.

### If you see startup errors

- `Failed to load Python DLL` usually means the app was launched without its bundled files.
- `No module named 'PySide6'` usually means the installer was built from an incomplete build environment.
- Fix: rebuild from project venv, then regenerate `LocalScribe_Setup.exe` and re-upload that installer.

### If transcription misses speech after long silence/music

- Open **Advanced Transcription Settings** before starting.
- Set **Transcription Profile** to:
  - `Pause Resilient (Long Silence/Music)` for interviews/podcasts with long pauses or music beds.
  - `No VAD (Most Permissive)` only for difficult edge cases (slower, may include more noise text).
- Keep language explicit (don’t rely on Auto-Detect) when possible for better stability.
- LocalScribe also performs an automatic **tail recovery pass** if a suspiciously large ending gap is detected.
- Recovery diagnostics are saved in transcript metadata (`transcription_diagnostics`) for troubleshooting.

> **FFmpeg** is required for audio decoding. Install it before first use:  
> *Windows*: `winget install ffmpeg` or download from [ffmpeg.org](https://ffmpeg.org/download.html)  
> *macOS*: `brew install ffmpeg`  
> *Linux*: `sudo apt install ffmpeg`

---

## Developer Setup

### 1. Prerequisites
- **Python 3.10+** on your system PATH
- **FFmpeg** installed (see above)

### 2. Clone & Environment
```bash
git clone https://github.com/shenfurkan/LocalScribe.git
cd LocalScribe
python -m venv venv
```
Activate it:  
- **Windows**: `venv\Scripts\activate`  
- **macOS/Linux**: `source venv/bin/activate`

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Run
```bash
python run.py
```
`run.py` auto-detects the local virtual environment and relaunches inside it if needed.

### 5. Environment Recovery (Windows)

If `python run.py` reports a broken or stale virtual environment, use the repair script:

```powershell
.\repair_env.ps1
```

| Flag | Purpose |
|---|---|
| `-UseDotVenv` | Create `.venv` instead of `venv` |
| `-Yes` | Skip the delete-confirmation prompt |
| `-AllowStorePython` | Allow the Windows Store Python alias |

---

## Building & Packaging

### Step 1 — Build with PyInstaller
```bash
python build.py
```
This produces a portable folder at `dist/LocalScribe/` containing the exe and all dependencies.

### Step 2 — Create the Installer (optional)
Install [Inno Setup 6](https://jrsoftware.org/isdl.php), then compile the included script:
```
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
```
This generates `dist/LocalScribe_Setup.exe` — a single installer file you can upload to GitHub Releases.

### One-command publisher workflow (recommended)

From project root:

```powershell
.\release_installer.ps1
```

This script is fail-fast and will:
- Verify local venv Python exists
- Verify Inno Setup compiler exists
- Clean previous build outputs
- Run `build.py`
- Compile `installer.iss`
- Confirm `dist\LocalScribe_Setup.exe` exists

If you already built and only want installer recompilation:

```powershell
.\release_installer.ps1 -SkipBuild
```

### Release checklist (recommended)

1. Build from the project venv:
   ```bash
   python build.py
   ```
2. Compile installer:
   ```
   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
   ```
3. Test on a clean machine (or VM):
   - Run `LocalScribe_Setup.exe`
   - Launch app from Start Menu
   - Confirm first-run model download starts
4. Upload only the tested `dist/LocalScribe_Setup.exe` to GitHub Releases.

See `docs/distribution_policy.md` for the full good/bad scenario matrix and update policy.

---

## Data Storage & Privacy

LocalScribe stores all user data in OS-standard locations — never next to the executable:

| Platform | Data Directory |
|---|---|
| **Windows** | `%LOCALAPPDATA%\LocalScribe\` |
| **macOS** | `~/Library/Application Support/LocalScribe/` |
| **Linux** | `~/.local/share/LocalScribe/` |

Inside that directory:
* **`models/`** — The Whisper speech model (~3 GB). Downloaded once on first launch from Hugging Face, then used entirely offline.
* **`transcripts/`** — Your transcript JSON files and index.

**Privacy**: No telemetry, no tracking, no cloud calls. All audio processing happens locally. Translation models (via ArgosTranslate) are also cached locally after a one-time download per language pair.

> When running from source (developer mode), data is stored in the project directory instead for convenience.

---

## Acknowledgements & Inspiration

LocalScribe is built on top of excellent open-source projects. Huge thanks to these communities and maintainers:

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (SYSTRAN) — core speech-to-text engine used by LocalScribe.
- [OpenAI Whisper](https://github.com/openai/whisper) — foundational speech recognition model architecture and research that inspired the ecosystem.
- [CTranslate2](https://github.com/OpenNMT/CTranslate2) — high-performance inference runtime used under `faster-whisper`.
- [Hugging Face Hub](https://huggingface.co/docs/huggingface_hub/) — model hosting and download SDK used during first-run setup.
- [Systran/faster-whisper-large-v3](https://huggingface.co/Systran/faster-whisper-large-v3) — Whisper model repository downloaded for local inference.
- [PySide6 / Qt for Python](https://doc.qt.io/qtforpython/) — desktop UI framework.
- [python-docx](https://github.com/python-openxml/python-docx) and [fpdf2](https://github.com/py-pdf/fpdf2) — document export support.
- [FFmpeg](https://ffmpeg.org/) — audio decoding backend used before transcription.
- [PyInstaller](https://pyinstaller.org/) and [Inno Setup](https://jrsoftware.org/isinfo.php) — Windows packaging and installer tooling.
