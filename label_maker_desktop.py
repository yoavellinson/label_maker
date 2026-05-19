import os
import threading
import webbrowser

from webapp import app, ensure_runtime_data


HOST = "127.0.0.1"
PORT = int(os.environ.get("LABEL_MAKER_PORT", "5001"))


def open_browser():
    webbrowser.open(f"http://{HOST}:{PORT}")


if __name__ == "__main__":
    ensure_runtime_data()
    threading.Timer(1.0, open_browser).start()
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False)
