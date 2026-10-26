"""
Download all collected participant data from the Drive folder to a local folder.

CSVs (server / desktop collector) are saved as-is. Export uploads from the
participant page (garmin_export_<id>.zip) are kept raw in data/participant_exports/
and converted to garmin_data_<id>.csv next to the other CSVs, so they can be
re-parsed later (e.g. after adding metrics to garmin_export.py) with:
    python collector/garmin_export.py data/participant_exports/garmin_export_<id>.zip out.csv

Reuses the same Apps Script endpoint + token as the uploader (via the doGet added
to upload_endpoint.gs). No Google Cloud project or OAuth needed.

Setup (once): export the same values the server uses, e.g.
    export GARMIN_ENDPOINT_URL="https://script.google.com/macros/s/AKfy.../exec"
    export GARMIN_UPLOAD_TOKEN="your-token"

Run:
    python collector/fetch_uploads.py              # -> data/participants/
    python collector/fetch_uploads.py somedir/     # custom output folder

It works as a "sync": only uploads not already present locally are downloaded (one
request per file), so run it whenever new participants have submitted. Delete a
local file to fetch it again. A zip that fails to convert is removed, so the next
run retries it.
"""

import base64
import os
import sys
from pathlib import Path

import requests

import garmin_export


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
EXPORTS_DIR = "data/participant_exports"


def _get(params, timeout):
    """GET the Apps Script endpoint with the token and return the parsed JSON."""
    resp = requests.get(ENDPOINT_URL, params={"token": UPLOAD_TOKEN, **params}, timeout=timeout)
    resp.raise_for_status()
    if resp.text.strip() == "forbidden":
        sys.exit("Endpoint returned 'forbidden' — token mismatch.")
    try:
        return resp.json()
    except ValueError:
        sys.exit(f"Unexpected response: {resp.text[:200]}")


def main():
    if not ENDPOINT_URL or not UPLOAD_TOKEN:
        sys.exit("Set GARMIN_ENDPOINT_URL and GARMIN_UPLOAD_TOKEN environment variables first.")

    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"Fetching uploads from Drive into {OUT_DIR}/ …")

    files = _get({}, timeout=60)
    if not files:
        print("No files in the Drive folder yet.")
        return
    if isinstance(files[0], str):  # the old doGet listed bare names
        sys.exit("The Drive endpoint is out of date: redeploy collector/upload_endpoint.gs "
                 "(Manage deployments -> Edit -> Version: New version) and run again.")

    new = present = failed = 0
    for f in files:
        name = os.path.basename(f["name"])  # never write outside the target folder
        is_zip = name.endswith(".zip")
        path = os.path.join(EXPORTS_DIR if is_zip else OUT_DIR, name)
        if os.path.exists(path):
            present += 1
            continue

        data = base64.b64decode(_get({"id": f["id"]}, timeout=300)["data"])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        if not is_zip:
            print(f"  ✓ {name}")
            new += 1
            continue

        try:
            df = garmin_export.participant_dataframe(path)
        except Exception as e:  # noqa: BLE001 — one bad upload shouldn't stop the sync
            os.remove(path)  # so the next sync retries it
            print(f"  ✗ {name}: {e}")
            failed += 1
            continue
        csv_name = f"garmin_data_{df['participant_id'].iloc[0]}.csv"
        df.to_csv(os.path.join(OUT_DIR, csv_name), index=False)
        print(f"  ✓ {name} -> {csv_name} ({len(df)} days)")
        new += 1

    print(f"\n✅ {new} new file(s), {present} already present"
          + (f", {failed} failed" if failed else "") + f" — CSVs in {OUT_DIR}/")


if __name__ == "__main__":
    main()
