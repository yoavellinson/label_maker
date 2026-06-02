import os
from pathlib import Path
import socket
import sys
import threading
import webbrowser

from webapp import app, app_data_dir, ensure_runtime_data


HOST = "127.0.0.1"
PORT = int(os.environ.get("LABEL_MAKER_PORT", "5001"))
LOCK_PATH = app_data_dir() / "label_maker.lock"


def open_browser():
    webbrowser.open(f"http://{HOST}:{PORT}")


def server_is_running():
    try:
        with socket.create_connection((HOST, PORT), timeout=0.35):
            return True
    except OSError:
        return False


def acquire_instance_lock():
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)

    try:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if server_is_running():
            return None

        try:
            LOCK_PATH.unlink()
        except OSError:
            return None

        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)

    with os.fdopen(fd, "w", encoding="utf-8") as lock_file:
        lock_file.write(str(os.getpid()))

    return LOCK_PATH


def release_instance_lock(lock_path: Path):
    try:
        lock_path.unlink()
    except OSError:
        pass


if __name__ == "__main__":
    ensure_runtime_data()
    if server_is_running():
        open_browser()
        sys.exit(0)

    lock_path = acquire_instance_lock()
    if lock_path is None:
        open_browser()
        sys.exit(0)

    threading.Timer(1.0, open_browser).start()
    try:
        app.run(host=HOST, port=PORT, debug=False, use_reloader=False)
    finally:
        release_instance_lock(lock_path)
