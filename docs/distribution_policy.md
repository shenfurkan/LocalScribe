# LocalScribe Distribution & Update Policy

This document defines what updates automatically, what does not, and how to release safely.

## 1) What users should run

- Users install with `LocalScribe_Setup.exe`.
- After installation, users launch LocalScribe from Start Menu/Desktop shortcut.
- Users should **not** copy `LocalScribe.exe` alone outside its installed folder.

## 2) What updates automatically

- Whisper model files (runtime assets) can be downloaded by the app at first run.
- Translation models (Argos) are downloaded on demand.

## 3) What does NOT auto-update at runtime

- Python packages bundled into the app (`PySide6`, `faster-whisper`, etc.).
- PyInstaller bootloader/runtime.
- Inno Setup installer logic.

These update only when maintainers publish a new installer build.

## 4) NVIDIA/CUDA policy

- CUDA/GPU is optional acceleration.
- App must work without CUDA (CPU fallback).
- Do not auto-install GPU drivers/runtime from the app.
- If CUDA libs are missing/incompatible, app logs warning and continues on CPU.

## 5) Good-path user scenarios

1. **Fresh install + internet available**
   - Install succeeds.
   - First launch downloads model.
   - Later launches are offline.

2. **Fresh install + no NVIDIA GPU**
   - App runs on CPU.
   - No extra action required.

3. **Returning user after 1+ months**
   - Existing model cache reused.
   - App keeps working without requiring pip/driver updates.

## 6) Bad-path scenarios and expected behavior

1. **User runs wrong file (`LocalScribe.exe` copied alone)**
   - Symptom: missing `python313.dll` / failed to load Python DLL.
   - Fix: install via `LocalScribe_Setup.exe` and use shortcut.

2. **Publisher built from wrong Python env**
   - Symptom: `No module named 'PySide6'` on user machine.
   - Fix: build from project venv only; fail-fast preflight in `build.py` enforces this.

3. **No internet on first launch**
   - Symptom: model download fails.
   - Fix: user retries later with internet.

4. **Inno Setup missing on publisher machine**
   - Symptom: no `LocalScribe_Setup.exe` generated.
   - Fix: install Inno Setup 6 and re-run release script.

## 7) Publisher release process (authoritative)

Run from project root:

```powershell
.\release_installer.ps1
```

Expected output file:

- `dist\LocalScribe_Setup.exe`

If you already built and only want to recompile installer:

```powershell
.\release_installer.ps1 -SkipBuild
```

## 8) Pre-release verification checklist

- Install the generated setup on a clean machine/VM.
- Launch from Start Menu.
- Confirm first-run model setup path works.
- Confirm transcription works in CPU mode.
- Upload only tested `dist\LocalScribe_Setup.exe` to GitHub Releases.
