import os
from pathlib import Path
import socket
import sys
import threading
import time
import webbrowser

from layout_dev_app import app, app_data_dir, ensure_runtime_data


HOST = "127.0.0.1"
PORT = int(os.environ.get("LABEL_MAKER_PORT", "5002"))
LOCK_PATH = app_data_dir() / "label_maker.lock"
LOCK_HANDLE = None


def open_browser():
    webbrowser.open(f"http://{HOST}:{PORT}")


def server_is_running():
    try:
        with socket.create_connection((HOST, PORT), timeout=0.35):
            return True
    except OSError:
        return False


def wait_for_server(timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server_is_running():
            return True
        time.sleep(0.2)
    return False


def acquire_instance_lock():
    global LOCK_HANDLE
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(LOCK_PATH, "a+", encoding="utf-8")

    try:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0)
            lock_file.write(" ")
            lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_file.close()
        return False

    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    LOCK_HANDLE = lock_file
    return True


def release_instance_lock():
    global LOCK_HANDLE
    if LOCK_HANDLE is not None:
        try:
            if os.name == "nt":
                import msvcrt

                LOCK_HANDLE.seek(0)
                msvcrt.locking(LOCK_HANDLE.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(LOCK_HANDLE.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            LOCK_HANDLE.close()
        except OSError:
            pass
        LOCK_HANDLE = None

    try:
        LOCK_PATH.unlink()
    except OSError:
        pass


if __name__ == "__main__":
    ensure_runtime_data()
    if server_is_running():
        open_browser()
        sys.exit(0)

    if not acquire_instance_lock():
        wait_for_server()
        open_browser()
        sys.exit(0)

    threading.Timer(1.0, open_browser).start()
    try:
        app.run(host=HOST, port=PORT, debug=False, use_reloader=False)
    finally:
        release_instance_lock()
