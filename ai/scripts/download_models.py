"""Download model weights listed in ai/models/models.lock.json and verify SHA-256.

Entries whose URL or checksum is still TODO are skipped with a warning.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parents[1] / "models"
LOCK = MODELS_DIR / "models.lock.json"
TIMEOUT_SECONDS = 120


def find_gh() -> str | None:
    """
    Purpose: locate gh CLI executable on system PATH or standard install location.
    Inputs: none.
    Outputs: path string or None.
    Side effects: none.
    """
    found = shutil.which("gh")
    if found:
        return found
    if sys.platform == "win32":
        default_win = Path(r"C:\Program Files\GitHub CLI\gh.exe")
        if default_win.is_file():
            return str(default_win)
    return None


def sha256_of(path: Path) -> str:
    """Purpose: hash a file. Inputs: path. Outputs: hex digest. Side effects: reads file."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    """
    Purpose: fetch and verify every pinned model.
    Inputs: models.lock.json. Outputs: exit code (1 on any checksum mismatch).
    Side effects: writes weights into ai/models/ (git-ignored); deletes files that fail checks.
    """
    entries = json.loads(LOCK.read_text(encoding="utf-8"))["models"]
    failed = False
    for entry in entries:
        url, expected, name = entry["source_url"], entry["sha256"], entry["file"]
        if "TODO" in url or "TODO" in expected or not url.startswith("https://"):
            print(f"skip {entry['name']}: URL or sha256 not pinned yet (TODO)")
            continue
        target = MODELS_DIR / name
        if not target.exists():
            if url.endswith(".pth"):
                print(f"run ai/scripts/convert_silent_face_onnx.py to generate {entry['name']}")
                failed = True
                continue
            print(f"download {entry['name']}")
            if "/releases/download/" in url:
                tag = url.split("/releases/download/")[1].split("/")[0]
                gh = find_gh()
                if gh:
                    subprocess.run(  # noqa: S603
                        [
                            gh,
                            "release",
                            "download",
                            tag,
                            "-p",
                            name,
                            "-D",
                            str(MODELS_DIR),
                            "--clobber",
                        ],
                        check=True,
                    )
                else:
                    req = urllib.request.Request(  # noqa: S310
                        url, headers={"User-Agent": "FaceScan"}
                    )
                    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
                    if token:
                        req.add_header("Authorization", f"Bearer {token}")
                    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:  # noqa: S310
                        target.write_bytes(resp.read())
            else:
                with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as resp:  # noqa: S310
                    target.write_bytes(resp.read())
        actual = sha256_of(target)
        if actual != expected:
            print(f"FAIL {entry['name']}: sha256 mismatch, deleting")
            target.unlink()
            failed = True
        else:
            print(f"ok   {entry['name']}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
