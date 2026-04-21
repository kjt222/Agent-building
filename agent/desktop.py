from __future__ import annotations

import socket
import threading
import time
from typing import Optional

import uvicorn

from .ui.server import create_app


def _wait_for_port(host: str, port: int, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            try:
                sock.connect((host, port))
                return True
            except OSError:
                time.sleep(0.1)
    return False


def run_desktop(host: str = "127.0.0.1", port: int = 8686, config_dir: Optional[str] = None) -> None:
    try:
        import webview
    except ImportError as exc:
        raise RuntimeError("pywebview not installed. Install `pywebview`.") from exc

    app = create_app(config_dir)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    if not _wait_for_port(host, port):
        server.should_exit = True
        raise RuntimeError("UI server failed to start.")

    webview.create_window(
        "Agent Console",
        f"http://{host}:{port}",
        width=1200,
        height=820,
        resizable=True,
    )
    webview.start()
    server.should_exit = True
