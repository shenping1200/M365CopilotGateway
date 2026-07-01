"""Unified launcher: Flask API on 8080 + Gradio dashboard."""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
os.chdir(str(APP_DIR))

def _setup_stdio():
    log_dir = APP_DIR / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None:
        sys.stdout = open(log_dir / 'service_stdout.log', 'a', encoding='utf-8', buffering=1)
    else:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if sys.stderr is None:
        sys.stderr = open(log_dir / 'service_stderr.log', 'a', encoding='utf-8', buffering=1)
    else:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')

_setup_stdio()

import config
import gradio as gr
from auth import get_account_manager
from dashboard import create_dashboard, CUSTOM_JS
from request_logger import get_request_logger
from unified_server import app as flask_app


def main():
    manager = get_account_manager()
    logger = get_request_logger()
    os.makedirs(config.LOG_DIR, exist_ok=True)

    def run_flask():
        flask_app.run(
            host=config.SERVER_HOST,
            port=config.SERVER_PORT,
            debug=False,
            threaded=True,
            use_reloader=False,
        )

    thread = threading.Thread(target=run_flask, daemon=True)
    thread.start()
    print(f"[Launcher] Unified API running on http://localhost:{config.SERVER_PORT}/v1")
    print(f"[Launcher] API key policy: any value accepted")
    print(f"[Launcher] Dashboard running on http://localhost:{config.DASHBOARD_PORT}")

    dashboard = create_dashboard(manager, logger)
    dashboard.launch(
        server_name=config.DASHBOARD_HOST,
        server_port=config.DASHBOARD_PORT,
        share=False,
        show_error=True,
        quiet=False,
        theme=gr.themes.Soft(),
        js=CUSTOM_JS,
    )


if __name__ == '__main__':
    main()

