"""Start all Python services together (gateway 8000, auth 8001, ops 8002, report 8003, media 8004, chat 8005, notify 8006).

    python scripts/start_all.py

Run it with the project's virtual environment. Press Ctrl+C to stop everything.
Logs of every service are printed in this one window with a colored prefix.
"""
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = [  # name, folder, module, port, color
    ("auth", "auth_service", "auth_app.main:app", 8001, "\033[36m"),
    ("ops", "ops_service", "ops_app.main:app", 8002, "\033[33m"),
    ("report", "report_service", "report_app.main:app", 8003, "\033[35m"),
    ("media", "media_service", "media_app.main:app", 8004, "\033[34m"),
    ("chat", "chat_service", "chat_app.main:app", 8005, "\033[31m"),
    ("notify", "notify_service", "notify_app.main:app", 8006, "\033[36;1m"),
    ("gateway", "gateway", "gateway_app.main:app", 8000, "\033[32m"),
]
RESET = "\033[0m"
procs: list[subprocess.Popen] = []


def pump(name: str, color: str, proc: subprocess.Popen) -> None:
    for line in proc.stdout:  # type: ignore[union-attr]
        print(f"{color}[{name:<7}]{RESET} {line.rstrip()}", flush=True)


def main() -> None:
    if not (ROOT / ".env").exists():
        print("Missing .env - copy .env.example to .env first.")
        sys.exit(1)
    reload_flag = "--reload" in sys.argv
    for name, folder, module, port, color in SERVICES:
        cmd = [sys.executable, "-m", "uvicorn", module, "--port", str(port)]
        if name == "gateway":
            cmd.append("--no-access-log")  # WebSocket URLs carry a token, so keep them out of the log (the gateway logs its own safe lines)
        if reload_flag:
            cmd.append("--reload")
        p = subprocess.Popen(cmd, cwd=ROOT / folder, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, env={**os.environ, "PYTHONUNBUFFERED": "1"})
        procs.append(p)
        threading.Thread(target=pump, args=(name, color, p), daemon=True).start()
    print("Services starting: gateway http://localhost:8000  (docs: /docs)")

    def stop(*_):
        for p in procs:
            p.terminate()
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    for p in procs:
        p.wait()


if __name__ == "__main__":
    main()
