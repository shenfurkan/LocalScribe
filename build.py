"""
build.py — Build LocalScribe into a standalone Windows application.

Wraps PyInstaller so contributors can produce a distributable binary
without writing shell scripts or remembering flag combinations.

Usage
-----
.. code-block:: bash

    # IMPORTANT: run with the project’s virtual environment Python,
    # NOT with system Python.  System Python will not have the
    # required dependencies installed.
    .\\venv\\Scripts\\python.exe build.py

    # Run validations only (no PyInstaller build).
    .\\venv\\Scripts\\python.exe build.py --check-only

What it does
------------
1. **Preflight check** — imports every required package to fail fast
   with a helpful message if the venv is not activated.
2. **Install PyInstaller** — ensures it is available in the venv.
3. **Run PyInstaller** in ``--onedir --windowed`` mode:
   - Bundles ``main.py`` as the entry-point.
   - Includes ``assets/``, ``image/``, ``core/``, ``ui/`` as data
     directories via ``--add-data``.  This is how
     ``runtime_manifest.json``, QSS themes, and icons end up inside
     the ``_internal/`` folder next to the exe.
4. Output lands in ``dist/LocalScribe/``.

After building, compile the Inno Setup installer (``installer.iss``)
to produce the final ``dist/LocalScribe_Setup.exe``.
"""
import os
import json
import argparse
import compileall
import subprocess
import sys
from pathlib import Path


REQUIRED_IMPORTS = [
    "PySide6",
    "faster_whisper",
    "docx",
    "fpdf",
    "argostranslate",
    "huggingface_hub",
    "hf_xet",
]

PROJECT_ROOT = Path(__file__).resolve().parent


def _preflight_check():
    """Fail fast if the build environment is missing required packages."""
    print("Preflight: checking required packages...")
    missing = []
    for mod in REQUIRED_IMPORTS:
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        print("\n[ERROR] The following packages are missing from the current Python environment:")
        for m in missing:
            print(f"  - {m}")
        print(f"\nActive interpreter: {sys.executable}")
        print("Fix: activate your project venv and run  pip install -r requirements.txt")
        sys.exit(1)
    print("Preflight: all required packages found.")


def _syntax_check() -> None:
    """Compile project source files to catch syntax errors early."""
    print("Preflight: running Python syntax checks...")
    targets = [
        PROJECT_ROOT / "main.py",
        PROJECT_ROOT / "run.py",
        PROJECT_ROOT / "build.py",
        PROJECT_ROOT / "core",
        PROJECT_ROOT / "ui",
    ]

    ok = True
    for target in targets:
        if target.is_dir():
            ok = compileall.compile_dir(str(target), quiet=1, force=True) and ok
        else:
            ok = compileall.compile_file(str(target), quiet=1, force=True) and ok

    if not ok:
        print("[ERROR] Syntax check failed. Fix Python errors above and retry.")
        sys.exit(1)
    print("Preflight: syntax checks passed.")


def _runtime_asset_check() -> None:
    """Validate required runtime metadata/assets before building."""
    print("Preflight: checking runtime manifest and package assets...")

    manifest_path = PROJECT_ROOT / "core" / "runtime_manifest.json"
    if not manifest_path.exists():
        print(f"[ERROR] Missing runtime manifest: {manifest_path}")
        sys.exit(1)

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        _ = manifest["whisper_model"]["repo_id"]
        _ = manifest["whisper_model"]["local_dir_name"]
        _ = manifest["whisper_model"]["expected_file"]
    except Exception as exc:
        print(f"[ERROR] Invalid runtime manifest: {exc}")
        sys.exit(1)

    try:
        import faster_whisper  # imported here so missing package reports nicely
        vad_asset = Path(faster_whisper.__file__).resolve().parent / "assets" / "silero_vad_v6.onnx"
        if not vad_asset.exists():
            print(
                "[ERROR] faster_whisper VAD asset missing from environment:\n"
                f"  {vad_asset}\n"
                "Reinstall faster-whisper in the project venv before building."
            )
            sys.exit(1)
    except Exception as exc:
        print(f"[ERROR] Could not validate faster_whisper assets: {exc}")
        sys.exit(1)

    # Verify CTranslate2 CUDA DLLs are present so GPU works in packaged app
    try:
        import ctranslate2
        ct2_dir = Path(ctranslate2.package_dir)
        cuda_dlls = [f for f in ct2_dir.iterdir() if f.suffix == ".dll" and f.name != "libiomp5md.dll"]
        if cuda_dlls:
            print(f"Preflight: CTranslate2 DLLs found ({len(cuda_dlls)}): {', '.join(f.name for f in cuda_dlls)}")
        else:
            print("[WARNING] No CTranslate2 CUDA DLLs found — GPU acceleration may not work in packaged app.")
        gpu_count = ctranslate2.get_cuda_device_count()
        print(f"Preflight: CTranslate2 reports {gpu_count} CUDA device(s).")
    except Exception as exc:
        print(f"[WARNING] Could not validate CTranslate2 CUDA status: {exc}")

    # Verify hf_xet is available for accelerated Hugging Face downloads.
    try:
        import hf_xet  # noqa: F401
        print("Preflight: hf_xet available (accelerated HF downloads enabled).")
    except Exception as exc:
        print(
            "[WARNING] hf_xet is not available; downloads will use standard HTTP. "
            f"Details: {exc}"
        )

    print("Preflight: runtime asset checks passed.")


def run_prebuild_checks() -> None:
    """Run all checks that should pass before invoking PyInstaller."""
    _preflight_check()
    _syntax_check()
    _runtime_asset_check()


def build(check_only: bool = False):
    print("Starting LocalScribe PyInstaller Build Framework...")

    # 0. Verify all runtime deps are importable before we waste time building.
    run_prebuild_checks()

    if check_only:
        print("\nPre-build checks completed successfully. No build was executed (--check-only).")
        return

    # 1. Ensure PyInstaller is available.
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "pyinstaller"],
        check=True,
    )

    # 2. Core PyInstaller flags.
    #    --onedir    → produces a folder (not a single exe) for faster startup.
    #    --windowed  → hides the console window on launch (GUI app).
    #    --add-data  → bundles non-Python files into _internal/<dest>.
    #                   This is how runtime_manifest.json, QSS themes,
    #                   and icon files are included in the build.
    #    --collect-data faster_whisper
    #                 → includes package assets like
    #                   faster_whisper/assets/silero_vad_v6.onnx used by VAD.
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--windowed",
        "--name", "LocalScribe",
        "--icon", f"image{os.sep}LocalScribe.ico",
        "--add-data", f"assets{os.pathsep}assets",
        "--add-data", f"image{os.pathsep}image",
        "--add-data", f"core{os.pathsep}core",
        "--add-data", f"ui{os.pathsep}ui",
        "--collect-data", "faster_whisper",
        "--collect-data", "ctranslate2",
        "--collect-binaries", "ctranslate2",
    ]

    cmd.append("main.py")

    print("\nExecuting PyInstaller...")
    subprocess.run(cmd, check=True)

    dist_app_dir = os.path.join("dist", "LocalScribe")

    print("\n==================================")
    print("Build complete!")
    print(f"Output folder:  {dist_app_dir}")
    print("User data (models, transcripts) is stored in %LOCALAPPDATA%\\LocalScribe")
    print("==================================")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build LocalScribe with preflight checks.")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Run all pre-build checks and exit without running PyInstaller.",
    )
    args = parser.parse_args()
    build(check_only=args.check_only)
