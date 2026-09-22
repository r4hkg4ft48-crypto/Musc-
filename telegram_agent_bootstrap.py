import base64
import os
import pathlib
import shutil
import subprocess
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent
PAYLOAD = ROOT / "agent_payload" / "full.b64"
TARGET = pathlib.Path("/tmp/telegram_ai_agent")
ENC = pathlib.Path("/tmp/telegram_ai_agent.enc")
ZIP = pathlib.Path("/tmp/telegram_ai_agent.zip")

def main():
    key = os.environ.get("AGENT_PACKAGE_KEY", "").strip()
    if not key:
        raise RuntimeError("AGENT_PACKAGE_KEY is not configured")
    if not PAYLOAD.exists():
        raise RuntimeError("Encrypted agent payload is missing")

    ENC.write_bytes(base64.b64decode(PAYLOAD.read_text(encoding="utf-8").strip()))
    subprocess.check_call([
        "openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2",
        "-in", str(ENC), "-out", str(ZIP), "-pass", "env:AGENT_PACKAGE_KEY"
    ])

    if TARGET.exists():
        shutil.rmtree(TARGET)
    TARGET.mkdir(parents=True)
    with zipfile.ZipFile(ZIP) as zf:
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
