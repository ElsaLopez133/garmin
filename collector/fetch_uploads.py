"""
Download all collected participant CSVs from the Drive folder to a local folder.

Reuses the same Apps Script endpoint + token as the uploader (via the doGet added
to upload_endpoint.gs). No Google Cloud project or OAuth needed.

Setup (once): export the same values the server uses, e.g.
    export GARMIN_ENDPOINT_URL="https://script.google.com/macros/s/AKfy.../exec"
    export GARMIN_UPLOAD_TOKEN="your-token"

Run:
    python collector/fetch_uploads.py              # -> data/participants/
    python collector/fetch_uploads.py somedir/     # custom output folder

Re-running just re-downloads everything and overwrites (cheap for small CSVs), so
it doubles as a "sync": run it whenever new participants have submitted.
"""

import base64
import os
import sys
from pathlib import Path

import requests


def load_env(path=None):
    """Load KEY=VALUE lines from a .env file (repo root) into os.environ.
    Real environment variables take precedence (setdefault)."""
    path = Path(path) if path else Path(__file__).resolve().parents[1] / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


load_env()
ENDPOINT_URL = os.environ.get("GARMIN_ENDPOINT_URL")
UPLOAD_TOKEN = os.environ.get("GARMIN_UPLOAD_TOKEN")
OUT_DIR = sys.argv[1] if len(sys.argv) > 1 else "data/participants"


def main():
    if not ENDPOINT_URL or not UPLOAD_TOKEN:
        sys.exit("Set GARMIN_ENDPOINT_URL and GARMIN_UPLOAD_TOKEN environment variables first.")

    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"Fetching uploads from Drive into {OUT_DIR}/ …")

    resp = requests.get(ENDPOINT_URL, params={"token": UPLOAD_TOKEN, "download": "1"}, timeout=300)
    resp.raise_for_status()
    if resp.text.strip() == "forbidden":
        sys.exit("Endpoint returned 'forbidden' — token mismatch.")

    try:
        files = resp.json()
    except ValueError:
        sys.exit(f"Unexpected response: {resp.text[:200]}")

    if not files:
        print("No files in the Drive folder yet.")
        return

    for f in files:
        path = os.path.join(OUT_DIR, f["name"])
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(f["data"]))
        print(f"  ✓ {f['name']}")

    print(f"\n✅ Downloaded {len(files)} file(s) to {OUT_DIR}/")


if __name__ == "__main__":
    main()
