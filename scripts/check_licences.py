"""Fail if a forbidden model or package appears anywhere in the repository (invariant 9).

Checks:
1. Dependency manifests, the model lock, adapter config and code must not mention InsightFace
   packages or model packs (`insightface`, `buffalo_*`, `scrfd_*`).
2. No file anywhere in the repository may be an InsightFace detector or pack file
   (`scrfd_*`, `buffalo_*`), whatever its extension.
3. From the AuraFace repository only `glintr100.onnx` is approved: no other AuraFace file may be
   present, and AuraFace entries in models.lock.json must point to glintr100.onnx.
"""

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_TEXT = (
    re.compile(r"buffalo_[a-z]+", re.IGNORECASE),
    re.compile(r"scrfd_[a-z0-9]+", re.IGNORECASE),
    re.compile(r"^\s*\"?insightface\b", re.IGNORECASE | re.MULTILINE),
)
FORBIDDEN_FILE_NAME = re.compile(r"^(scrfd_|buffalo_)", re.IGNORECASE)
AURAFACE_APPROVED = "glintr100.onnx"
# Model files published in the fal/AuraFace-v1 repository besides the approved one.
AURAFACE_OTHER_FILES = frozenset(
    {"scrfd_10g_bnkps.onnx", "1k3d68.onnx", "2d106det.onnx", "genderage.onnx"}
)
AURAFACE_SOURCE = re.compile(r"auraface", re.IGNORECASE)
MODEL_LOCK = "ai/models/models.lock.json"
SCAN_FILES = (
    "pyproject.toml",
    "backend/pyproject.toml",
    "backend/requirements.txt",
    "ai/pyproject.toml",
    "ai/requirements.txt",
    "frontend/package.json",
    MODEL_LOCK,
    "settings/services.yaml",
)
SCAN_GLOBS = ("ai/src/**/*.py", "ai/accuracy_test/**/*.py", "backend/app/**/*.py")
SKIP_DIRS = frozenset(
    {".venv", "node_modules", ".git", ".mypy_cache", ".ruff_cache", ".pytest_cache", "__pycache__"}
)


def iter_repo_files() -> list[Path]:
    """
    Purpose: list every file in the repository except tool caches and dependency folders.
    Outputs: paths. Side effects: walks the filesystem.
    """
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        found.extend(Path(dirpath) / name for name in filenames)
    return found


def check_text() -> list[str]:
    """Purpose: check 1 (manifests and code). Outputs: findings. Side effects: reads files."""
    paths = [ROOT / p for p in SCAN_FILES if (ROOT / p).exists()]
    for pattern in SCAN_GLOBS:
        paths += [p for p in ROOT.glob(pattern) if not SKIP_DIRS & set(p.parts)]
    findings = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for rx in FORBIDDEN_TEXT:
            if rx.search(text):
                findings.append(f"{path.relative_to(ROOT).as_posix()}: mentions {rx.pattern}")
    return findings


def check_file_names(files: list[Path]) -> list[str]:
    """Purpose: checks 2 and 3 (files on disk). Outputs: findings. Side effects: none."""
    findings = []
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        if FORBIDDEN_FILE_NAME.match(path.name):
            findings.append(f"{rel}: InsightFace detector/pack file")
        elif path.name.lower() in AURAFACE_OTHER_FILES:
            findings.append(f"{rel}: AuraFace file other than {AURAFACE_APPROVED}")
    return findings


def check_model_lock() -> list[str]:
    """Purpose: check 3 (lock entries). Outputs: findings. Side effects: reads the lock file."""
    lock = ROOT / MODEL_LOCK
    if not lock.exists():
        return []
    findings = []
    for entry in json.loads(lock.read_text(encoding="utf-8"))["models"]:
        name, file, url = str(entry["name"]), str(entry["file"]), str(entry["source_url"])
        is_auraface = AURAFACE_SOURCE.search(name) or AURAFACE_SOURCE.search(url)
        if is_auraface and (file != AURAFACE_APPROVED or not url.endswith(AURAFACE_APPROVED)):
            findings.append(f"{MODEL_LOCK}: AuraFace entry '{name}' is not {AURAFACE_APPROVED}")
    return findings


def main() -> int:
    """
    Purpose: run every check.
    Inputs: repository files. Outputs: exit code 1 if anything forbidden is found.
    Side effects: prints findings.
    """
    findings = check_text() + check_file_names(iter_repo_files()) + check_model_lock()
    for finding in findings:
        print(f"FORBIDDEN {finding}")
    if not findings:
        print("licence check passed")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
