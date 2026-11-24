"""Manual, password-protected updates from GitHub releases.

Nothing here runs on its own: the app only contacts GitHub when someone presses
"בדיקת עדכונים" and enters the update password, and only installs after a
second explicit confirmation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

REPO = "yoavellinson/label_maker"
ASSET_NAME = "LabelMaker_v2_Setup.exe"
USER_AGENT = "LabelMaker-Updater"
# sha256 of the update password (kept as a hash so it is not in plain text).
PASSWORD_SHA256 = "bee474256534a6caf6cd41929e384b274f48f523f9e778cbe842329a8b822a24"


class UpdateError(RuntimeError):
    pass


def _ssl_context() -> ssl.SSLContext:
    """Verify GitHub with Mozilla's CA list (bundled), falling back to the system store."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


def check_password(password: str) -> bool:
    digest = hashlib.sha256(str(password or "").encode("utf-8")).hexdigest()
    return hmac.compare_digest(digest, PASSWORD_SHA256)


def parse_version(text: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", str(text or ""))
    return tuple(int(n) for n in numbers[:3]) or (0,)


def read_version(base_dir: Path) -> str:
    try:
        return (base_dir / "VERSION").read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def _get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=10, context=_ssl_context()) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("עדיין לא פורסמה גרסה להורדה.") from exc
        raise UpdateError(f"GitHub החזיר שגיאה {exc.code}.") from exc
    except Exception as exc:  # noqa: BLE001 - network problems are reported to the user
        raise UpdateError(f"אין חיבור ל-GitHub: {exc}") from exc


def latest_release() -> dict:
    data = _get_json(f"https://api.github.com/repos/{REPO}/releases/latest")
    asset = next((a for a in data.get("assets", []) if a.get("name") == ASSET_NAME), None)
    if not asset:
        raise UpdateError("בגרסה האחרונה אין קובץ התקנה.")
    return {
        "version": str(data.get("tag_name") or "").lstrip("v"),
        "notes": str(data.get("body") or "").strip(),
        "url": asset["browser_download_url"],
        "size": int(asset.get("size") or 0),
    }


def check(current: str) -> dict:
    release = latest_release()
    return {
        "current": current,
        "latest": release["version"],
        "available": parse_version(release["version"]) > parse_version(current),
        "notes": release["notes"][:2000],
    }


def can_install() -> bool:
    return os.name == "nt" and getattr(sys, "frozen", False)


def download_installer(url: str, size: int) -> Path:
    target = Path(tempfile.gettempdir()) / f"LabelMaker_update_{os.getpid()}.exe"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=120, context=_ssl_context()) as response, target.open("wb") as handle:
            while chunk := response.read(1 << 16):
                handle.write(chunk)
    except Exception as exc:  # noqa: BLE001
        target.unlink(missing_ok=True)
        raise UpdateError(f"ההורדה נכשלה: {exc}") from exc
    if size and target.stat().st_size != size:
        target.unlink(missing_ok=True)
        raise UpdateError("קובץ ההתקנה שהורד לא שלם. נסו שוב.")
    return target


def start_installer(installer: Path) -> None:
    """Run the installer silently in its own process; it closes this app and reopens it."""
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
        creationflags=flags,
        close_fds=True,
    )
