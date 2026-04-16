import ssl
import json
import urllib.request
import urllib.error
import time
from pathlib import Path
from PySide6.QtCore import QObject, Signal

from core.version import VERSION
from core.paths import data_root

class UpdateCheckerWorker(QObject):
    # Emits (latest_version, release_url)
    update_available = Signal(str, str)
    error = Signal(str)

    def run(self):
        try:
            state_file = data_root() / "setup_state.json"
            state = {}
            if state_file.exists():
                with open(state_file, "r", encoding="utf-8") as f:
                    try:
                        state = json.load(f)
                    except json.JSONDecodeError:
                        state = {}
            
            last_check = state.get("last_update_check", 0)
            now = time.time()
            if now - last_check < 24 * 3600:
                # Less than 24 hours ago
                return

            req = urllib.request.Request(
                "https://api.github.com/repos/shenfurkan/LocalScribe/releases/latest",
                headers={"User-Agent": f"LocalScribe/{VERSION}"}
            )
            # Create a default context, usually handles HTTPS well
            context = ssl.create_default_context()
            
            with urllib.request.urlopen(req, context=context, timeout=10) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode('utf-8'))
                    latest_tag = data.get("tag_name", "").lstrip("v")
                    
                    if self._is_newer(latest_tag, VERSION):
                        self.update_available.emit(latest_tag, data.get("html_url", ""))
                        
                    state["last_update_check"] = now
                    tmp = state_file.with_suffix(".tmp")
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(state, f, indent=2)
                    try:
                        import os
                        os.replace(tmp, state_file)
                    except Exception:
                        import shutil
                        shutil.move(tmp, state_file)
        except Exception as e:
            self.error.emit(str(e))

    def _is_newer(self, latest: str, current: str) -> bool:
        try:
            l_parts = [int(x) for x in latest.split(".")]
            c_parts = [int(x) for x in current.split(".")]
            for i in range(max(len(l_parts), len(c_parts))):
                l_v = l_parts[i] if i < len(l_parts) else 0
                c_v = c_parts[i] if i < len(c_parts) else 0
                if l_v > c_v:
                    return True
                elif l_v < c_v:
                    return False
            return False
        except Exception:
            return False
