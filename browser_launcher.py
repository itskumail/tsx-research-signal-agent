"""Recover only the existing Canadian Stock Chrome profile; never authenticate."""

from datetime import datetime, timezone
import http.client
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
PORT = 9224
COOLDOWN_SECONDS = 600


def _paths():
    return (Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(os.getenv("LOCALAPPDATA", "")) / "CanadianStockChrome" / "Profile")


def _endpoint_state() -> str:
    connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=2)
    try:
        connection.request("GET", "/json/version")
        response = connection.getresponse()
        if response.status != 200:
            return "invalid"
        data = json.loads(response.read(16384))
        websocket = urlparse(data.get("webSocketDebuggerUrl", ""))
        if (str(data.get("Browser", "")).startswith("Chrome/") and
                websocket.hostname in ("127.0.0.1", "localhost", "::1") and websocket.port == PORT):
            return "ready"
        return "invalid"
    except ConnectionRefusedError:
        return "closed"
    except Exception:
        # A timeout/permission error is not proof that Chrome is closed.
        return "unknown"
    finally:
        connection.close()


def _process_state(profile: Path) -> dict:
    script = r"""
    $ErrorActionPreference = 'Stop'
    $p = [regex]::Escape($env:CANADIAN_CHROME_PROFILE)
    $pattern = '--user-data-dir=(?:"' + $p + '"|' + $p + '(?=\s|$))'
    $running = @(Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe'" |
        Where-Object { $_.CommandLine -match $pattern }).Count -gt 0
    @{ profile_running = $running;
       interactive_session = ([System.Diagnostics.Process]::GetCurrentProcess().SessionId -gt 0)
    } | ConvertTo-Json -Compress
    """
    env = os.environ.copy()
    env["CANADIAN_CHROME_PROFILE"] = str(profile)
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError("Process check unavailable")
    data = json.loads(result.stdout)
    if not isinstance(data, dict) or any(type(data.get(key)) is not bool for key in
                                       ("profile_running", "interactive_session")):
        raise ValueError("Invalid process check")
    return data


def ensure_local_browser(*, state_path: Path | None = None) -> dict:
    """Caller holds browser_lock; launch once, without killing/resetting any Chrome."""
    if os.getenv("OPENCLAW_BROWSER_AUTORESTART", "true").lower() != "true":
        return {"status": "disabled"}
    if os.name != "nt":
        return {"status": "unsupported"}
    endpoint = _endpoint_state()
    if endpoint == "ready":
        return {"status": "ready", "launched": False}
    if endpoint != "closed":
        return {"status": "failed", "reason": "local_endpoint_unknown"}
    chrome, profile = _paths()
    if not chrome.is_file() or not profile.is_absolute() or not profile.is_dir():
        return {"status": "failed", "reason": "launcher_or_profile_missing"}
    try:
        process = _process_state(profile)
    except Exception:
        return {"status": "failed", "reason": "local_process_check_failed"}
    if process["profile_running"]:
        return {"status": "failed", "reason": "profile_already_running"}
    if not process["interactive_session"]:
        return {"status": "failed", "reason": "windows_login_required"}
    state_path = state_path or ROOT / "logs" / "browser_recovery_state.json"
    now = datetime.now(timezone.utc).timestamp()
    try:
        previous = json.loads(state_path.read_text(encoding="utf-8"))
        elapsed = now - float(previous.get("attempted_at", 0))
        if elapsed < COOLDOWN_SECONDS:
            return {"status": "cooldown", "reason": "restart_cooldown"}
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"attempted_at": now}), encoding="utf-8")
        temporary.replace(state_path)
        subprocess.Popen([str(chrome), "--remote-debugging-port=9224", "--remote-debugging-address=127.0.0.1",
                          f"--user-data-dir={profile}", "--no-first-run",
                          "https://ndcdyn.interactivebrokers.com/portal/"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:
        return {"status": "failed", "reason": "chrome_launch_failed"}
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if _endpoint_state() == "ready":
            return {"status": "started", "launched": True}
        time.sleep(1)
    return {"status": "failed", "reason": "chrome_start_not_ready", "launched": True}
