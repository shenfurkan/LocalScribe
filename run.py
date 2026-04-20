"""run.py — Development convenience launcher.

Activates the project virtual environment (if present) and re-launches
``main.py`` inside it.  This ensures that all pip-installed dependencies
(PySide6, faster-whisper, huggingface_hub, etc.) are available even when
the user invokes ``python run.py`` with their system Python.

Strategy
--------
1. Locate a ``venv/`` or ``.venv/`` directory under the project root.
2. Resolve the Python interpreter inside it (handles both Windows and
   POSIX layouts).
3. Verify that all ``REQUIRED_MODULES`` are importable in that venv.
   If any are missing, offer to install them via pip.
4. Re-exec ``main.py`` under the venv Python using ``subprocess``.

If no virtual environment is found, the script falls back to the current
Python interpreter (``sys.executable``).
"""
import subprocess
import sys
import os
from pathlib import Path


REQUIRED_MODULES = [
    "PySide6",
    "faster_whisper",
    "docx",
    "fpdf",
    "huggingface_hub",
]


def _find_project_venv_python(project_root: Path) -> tuple[Path | None, Path | None]:
    """Return (venv_root, python_path) for the first valid local venv, if any."""
    found_env_dirs = []
    for env_name in ("venv", ".venv"):
        venv_root = project_root / env_name
        if not venv_root.exists():
            continue

        found_env_dirs.append(venv_root)

        candidates = [
            venv_root / "Scripts" / "python.exe",  # Windows
            venv_root / "Scripts" / "python",      # Some Windows setups
            venv_root / "bin" / "python3",         # Linux/macOS
            venv_root / "bin" / "python",          # Linux/macOS fallback
        ]

        for interpreter in candidates:
            if interpreter.exists():
                return venv_root, interpreter

    if found_env_dirs:
        locations = ", ".join(str(path) for path in found_env_dirs)
        print(
            "[WARNING] Found virtual environment folder(s) but no Python "
            f"interpreter inside: {locations}."
        )

    return None, None


def _is_runnable_python(interpreter: Path, cwd: Path) -> bool:
    """Return True if the interpreter can execute a simple command."""
    try:
        result = subprocess.run(
            [str(interpreter), "--version"],
            cwd=str(cwd),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0
    except OSError:
        return False


def _check_modules(interpreter: Path, cwd: Path, modules: list[str]) -> tuple[bool, list[str]]:
    """Return (ok, missing_modules) for the given module list."""
    script = (
        "import importlib.util; "
        f"mods={modules!r}; "
        "missing=[m for m in mods if importlib.util.find_spec(m) is None]; "
        "print('\\n'.join(missing))"
    )
    try:
        result = subprocess.run(
            [str(interpreter), "-c", script],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False, modules

    if result.returncode != 0:
        return False, modules

    missing = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return len(missing) == 0, missing


def _print_missing_modules_help(interpreter: Path, missing: list[str]) -> None:
    print("[ERROR] Missing Python dependencies in selected environment:")
    for mod in missing:
        print(f"  - {mod}")
    print("[ERROR] Install them with:")
    print(f"  {interpreter} -m pip install -r requirements.txt")


def launch():
    print("Initializing LocalScribe Native Runtime...")

    # Resolve the project root from this file's location so the launcher
    # works regardless of the current working directory.
    project_root = Path(__file__).resolve().parent
    main_script = project_root / "main.py"
    venv_root, venv_python = _find_project_venv_python(project_root)

    # If a venv exists and we are NOT already running inside it, re-launch.
    already_in = (
        venv_root is not None
        and Path(sys.prefix).resolve() == venv_root.resolve()
    )

    if venv_python is not None and not already_in:
        if _is_runnable_python(venv_python, project_root):
            ok, missing = _check_modules(venv_python, project_root, REQUIRED_MODULES)
            if not ok:
                _print_missing_modules_help(venv_python, missing)
                return 1

            print("Relaunching within virtual environment...")
            result = subprocess.run([str(venv_python), str(main_script)], cwd=str(project_root))
            return result.returncode

        print("[WARNING] Local virtual environment appears broken/stale.")
        print(f"[WARNING] Interpreter is not runnable: {venv_python}")
        env_name = venv_root.name if venv_root is not None else "venv"
        print(f"[WARNING] Recreate it with: python -m venv {env_name}")
        return 1

    current_python = Path(sys.executable)
    ok, missing = _check_modules(current_python, project_root, REQUIRED_MODULES)
    if not ok:
        _print_missing_modules_help(current_python, missing)
        return 1

    result = subprocess.run(
        [sys.executable, str(main_script)],
        cwd=str(project_root),
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(launch())
