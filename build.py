"""
build.py

Native Python build script — wraps PyInstaller so users can build
a distributable without writing shell scripts.

Usage:
    python build.py
"""
import os
import subprocess
import sys


def build():
    print("Starting LocalScribe PyInstaller Build Framework...")

    # 1. Ensure PyInstaller is available.
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "pyinstaller"],
        check=True,
    )

    # 2. Core PyInstaller flags.
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--windowed",           # hide the console window on launch
        "--name", "LocalScribe",
        "--icon", f"image{os.sep}LocalScribe.ico",
        "--add-data", f"assets{os.pathsep}assets",
        "--add-data", f"image{os.pathsep}image",
        "--add-data", f"core{os.pathsep}core",
        "--add-data", f"ui{os.pathsep}ui",
    ]

    # 3. Bundle NVIDIA CUDA DLLs if present.
    #    PyInstaller --add-data / --add-binary do NOT expand globs, so we must
    #    enumerate each DLL file individually.
    site_packages = next((p for p in sys.path if "site-packages" in p), None)
    if site_packages:
        for pkg in ("cublas", "cudnn"):
            bin_path = os.path.join(site_packages, "nvidia", pkg, "bin")
            if os.path.exists(bin_path):
                for fname in os.listdir(bin_path):
                    if fname.lower().endswith(".dll"):
                        full = os.path.join(bin_path, fname)
                        # Deposit the DLL at the root of the bundle (same dir as the exe).
                        cmd.extend(["--add-binary", f"{full}{os.pathsep}."])

    cmd.append("main.py")

    print("\nExecuting PyInstaller…")
    subprocess.run(cmd, check=True)

    # 4. Create directories the app expects to find at runtime.
    dist_app_dir = os.path.join("dist", "LocalScribe")
    os.makedirs(os.path.join(dist_app_dir, "models"), exist_ok=True)
    os.makedirs(os.path.join(dist_app_dir, "transcripts"), exist_ok=True)

    print("\n==================================")
    print("Build complete! ✨")
    print(f"ZIP the folder at:  {dist_app_dir}")
    print("==================================")


if __name__ == "__main__":
    build()
