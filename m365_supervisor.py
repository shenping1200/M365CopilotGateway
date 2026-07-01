from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import config


ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / '.venv' / 'Scripts' / 'python.exe'
LOG_DIR = ROOT / 'logs'
SUPERVISOR_LOG = LOG_DIR / 'supervisor.log'


def log(message: str) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}"
    print(line, flush=True)
    with SUPERVISOR_LOG.open('a', encoding='utf-8') as handle:
        handle.write(line + '\n')


def healthy() -> bool:
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{config.SERVER_PORT}/health', timeout=5) as response:
            return response.status == 200
    except Exception:
        return False


def start_child(service_command: list[str] | None = None) -> subprocess.Popen:
    python_exe = str(PYTHON if PYTHON.exists() else Path(sys.executable))
    command = service_command or [python_exe, '-X', 'utf8', 'app_launcher.py']
    log('starting M365 gateway: ' + ' '.join(command))
    return subprocess.Popen(
        command,
        cwd=str(ROOT),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0,
    )


def stop_child(child: subprocess.Popen | None) -> None:
    if not child or child.poll() is not None:
        return
    log(f'stopping child pid={child.pid}')
    try:
        if os.name == 'nt':
            child.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            child.terminate()
        child.wait(timeout=10)
    except Exception:
        child.kill()


def supervise(interval: int, max_failures: int, service_command: list[str] | None = None) -> int:
    child = start_child(service_command)
    failures = 0
    try:
        while True:
            time.sleep(interval)
            code = child.poll()
            if code is not None:
                log(f'child exited code={code}; restarting')
                child = start_child(service_command)
                failures = 0
                continue
            if healthy():
                failures = 0
                continue
            failures += 1
            log(f'health check failed {failures}/{max_failures}')
            if failures >= max_failures:
                stop_child(child)
                child = start_child(service_command)
                failures = 0
    except KeyboardInterrupt:
        log('supervisor interrupted')
        stop_child(child)
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='M365 gateway supervisor')
    parser.add_argument('--interval', type=int, default=15)
    parser.add_argument('--max-failures', type=int, default=3)
    args = parser.parse_args()
    return supervise(args.interval, args.max_failures)


if __name__ == '__main__':
    raise SystemExit(main())

