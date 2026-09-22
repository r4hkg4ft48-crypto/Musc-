import base64, io, os, pathlib, shutil, subprocess, sys, zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer

PREFIX = "AGENT_PKG_"
TARGET = pathlib.Path("/tmp/telegram_ai_agent")

def package_parts():
    items = [(k, v) for k, v in os.environ.items() if k.startswith(PREFIX) and v]
    return [v for k, v in sorted(items)]

def placeholder():
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"Telegram AI Agent bootstrap ready"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_):
            pass
    HTTPServer(("0.0.0.0", int(os.environ.get("PORT", "10000"))), H).serve_forever()

def main():
    parts = package_parts()
    if not parts:
        placeholder()
        return

    raw = base64.b64decode("".join(parts))
    if TARGET.exists():
        shutil.rmtree(TARGET)
    TARGET.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        zf.extractall(TARGET)

    req = TARGET / "requirements.txt"
    if req.exists():
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "-r", str(req)])

    start = TARGET / "cloud_start.sh"
    if not start.exists():
        raise RuntimeError("cloud_start.sh missing from cloud package")
    os.chdir(TARGET)
    os.execv("/bin/bash", ["bash", str(start)])

if __name__ == "__main__":
    main()
