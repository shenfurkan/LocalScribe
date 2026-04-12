# 🎙 LocalScribe

**LocalScribe** is an elegant, entirely offline, and privacy-focused application for highly accurate audio transcription, translation, and document generation. Built on **PySide6** and powered by the cutting-edge **faster-whisper** engine, it brings studio-quality subtitle and transcript generation completely to your local hardware without relying on any cloud APIs or third-party telemetries.

No internet connection required. No subscriptions. No tracking. Complete privacy—all audio strictly stays on your local machine.

---

## ✨ Comprehensive Feature Set

### 🧠 Advanced Audio Inference
* **100% Offline AI Processing**: Transcribe media directly on your PC. Uses CTranslate2 under the hood (via `faster-whisper`) delivering vastly increased performance over standard Whisper.
* **Hardware Acceleration**: Automatic environment detection natively supports inference on compatible NVIDIA GPUs via CUDA/cuDNN out of the box, falling back naturally to optimized CPU inference when necessary.
* **Instant Background Initialization**: Core transcription models are intelligently preloaded by a background worker instantly upon opening the application, making startup smooth and non-blocking. 
* **Live Streaming Transcripts**: Don't just stare at a progress bar. LocalScribe features a real-time live view of your transcription as it streams word-by-word into the UI, updating on the fly!

### 🌍 Native Offline Translation
* **ArgosTranslate Integration**: Integrated local-only machine translation engine bridging dozens of language pairs.  
* **Translation Manager**: Translations work seamlessly—translate entire lengthy transcripts asynchronously. All language models are stored locally ensuring privacy.

### 📄 Rich Document Generation & Export
* **SRT Subtitles Generation**: Generate standardized `.srt` subtitle files. Granularly customize subtitle breaks, max line lengths, and max sentences perfectly customized for YouTube videos or social media.
* **Microsoft Word Export**: Advanced `python-docx` integration correctly outputs deeply formatted transcripts including automatic conversational breakdown blocks. 
* **Direct PDF Generation**: Features `fpdf2` logic for directly rendering transcripts into standardized portable documents.

### 🎨 Beautiful, Modern UI Elements
* **Native GUI**: Developed with PySide6 for snappy, native cross-platform performance.
* **Intelligent Dashboard**: Features an elegant drag-and-drop landing interface cleanly organizing historical file states and visual statuses.
* **Dual Theme Architecture**: Built from the ground up to support stunning modern Light and Dark Modes driven by bespoke customizable `QSS` (Qt Style Sheets).
* **Interactive Editor**: Jump into completed transcript cards to edit timestamps, correct generated words, and replay segments gracefully handled by the LocalScribe playback module.

---

## 🏗️ Project Architecture & Layout

Understanding how LocalScribe works under the hood is straightforward:

```text
LocalScribe/
├── main.py                  # The standard entrypoint initializing PySide6
├── run.py                   # Convenience launcher (auto-activates the Virtual Env!)
├── build.py                 # PyInstaller wrapper logic to compile to standalone .exe
├── assets/
│   ├── dark_theme.qss       # Detailed styling engine for Dark Mode
│   └── light_theme.qss      # Detailed styling engine for Light Mode
├── image/
│   └── LocalScribe.ico      # The application branding and icons
├── core/
│   ├── transcriber.py       # Interfaces with faster-whisper and hardware bridging
│   ├── translator.py        # Logic pipeline for argostranslate text transformation
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

## 🚀 Installation & Developer Setup

### 1. Prerequisites
- **Python 3.10+**: Ensure Python is correctly added to your System PATH environment variables.  
- **FFmpeg**: Required. `faster-whisper` depends on **FFmpeg** to decode and ingest audio files accurately.  
  - *Windows*: Install via `winget install ffmpeg` or download a release and place in your system PATH.
  - *Mac*: `brew install ffmpeg`
  - *Linux*: `sudo apt install ffmpeg`

### 2. Initialization 
Pull down the project code:
```bash
git clone https://github.com/shenfurkan/LocalScribe.git
cd LocalScribe
```

### 3. Virtual Environment (Recommended Workflow)
LocalScribe behaves best isolated in its own virtual environment (to prevent package collisions with CUDA variables). 
```bash
python -m venv venv
```
Activate it:  
- **Windows**: `venv\Scripts\activate`  
- **macOS/Linux**: `source venv/bin/activate`

### 4. Install Dependencies
```bash
pip install -r requirements.txt
```

### 5. Start the Application
You can fire up the program utilizing the clever helper:
```bash
python run.py
```
*(Notice: If you execute `run.py` from the global PATH accidentally but a `/venv` is present, `run.py` automatically terminates itself, discovers the venv, and restarts `main.py` properly within the isolated path!)*

---

## 📦 Building a Standalone Executable Application

Tired of using Python to launch? Want to share LocalScribe with a friend who has zero technical background? We've designed a specialized zero-setup packaging infrastructure to convert the entire application suite into a portable standalone software file.

Just execute the provided build framework from the Root Environment:
```bash
python build.py
```

### How the build pipeline works:
1. **Validates Installations**: Automatically imports `PyInstaller` if missing.
2. **Flags Configured**: Disables the development console, integrates `image/LocalScribe.ico`, configures `--onedir` execution path.
3. **Appends Static Assets**: Traverses the source, injecting `/assets`, `/image`, `/core`, `/ui` ensuring styling sheets, icons, and nested views resolve exactly correctly in a production format.
4. **Scans GPU Bridges**: Programmatically discovers any present NVIDIA cuDNN or CUDA (`cublas`) `.dll` files in your running environment mapping them explicitly into the packaged app natively, guaranteeing standalone hardware acceleration without needing local CUDA installation configurations.
5. **Generates Software**: Your completely portable application will drop directly inside `/dist/LocalScribe` ready to be zipped and shared!

---

## 📝 Setup Details & Data Privacy Policies 

The privacy focus of LocalScribe dictates how it manages large AI data:
* **The Transcriber Model**: Before the engine runs offline for the first time, LocalScribe downloads a massive open-source dictionary weight model from Hugging Face (`~1 to ~3 GB` depending on settings) to process speech. You will see an indicator mapping this transfer. **Once cached into `/models/`, LocalScribe will never ping the internet again.**
* **Translation Logic**: Translation models are locally cached upon a language pack request. After the download triggers once per language pair (e.g., English ➡️ Spanish), it operates 100% offline. 
* **Your Files**: The host audio/video and consequent transcribed items are permanently tethered implicitly to your own hard drives configuration (within `/transcripts`). Usage telemetry, tracking, and remote calls **do not exist** anywhere in the source logic. 

Enjoy your studio quality private subtitles!
