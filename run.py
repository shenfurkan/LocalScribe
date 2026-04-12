"""
run.py

Convenience launcher: activates the project virtual environment (if present)
and re-launches main.py inside it.  Running `python run.py` from any working
directory will always find the right interpreter.
"""
import os
import subprocess
import sys


def launch():
    print("Initializing LocalScribe Native Runtime...")

    # Resolve the project root from this file's location so the launcher
    # works regardless of the current working directory.
    project_root = os.path.dirname(os.path.abspath(__file__))
    venv_python  = os.path.join(project_root, "venv", "Scripts", "python.exe")
    main_script  = os.path.join(project_root, "main.py")

    # If a venv exists and we are NOT already running inside it, re-launch.
    venv_root    = os.path.join(project_root, "venv")
    already_in   = os.path.normcase(sys.prefix) == os.path.normcase(
        os.path.abspath(venv_root)
    )

    if os.path.exists(venv_python) and not already_in:
        print("Relaunching within virtual environment...")
        subprocess.run([venv_python, main_script])
    else:
        subprocess.run([sys.executable, main_script])


if __name__ == "__main__":
    launch()
