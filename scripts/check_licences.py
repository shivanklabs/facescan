"""Fail if a forbidden model or package appears anywhere in the repository (invariant 9)."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = (
    re.compile(r"buffalo_[a-z]+", re.IGNORECASE),
    re.compile(r"^\s*\"?insightface\b", re.IGNORECASE | re.MULTILINE),
)
SCAN_FILES = (
    "pyproject.toml",
    "backend/pyproject.toml",
    "backend/requirements.txt",
    "ai/pyproject.toml",
    "ai/requirements.txt",
    "frontend/package.json",
    "ai/models/models.lock.json",
    "config/adapters.yaml",
)
SCAN_GLOBS = ("ai/src/**/*.py", "backend/app/**/*.py")
SKIP_DIRS = {".venv", "node_modules", ".git"}


def main() -> int:
    """
    Purpose: scan dependency manifests, model lock and code for forbidden models/packages.
    Inputs: repository files. Outputs: exit code 1 if anything forbidden is found.
    Side effects: prints findings.
    """
    paths = [ROOT / p for p in SCAN_FILES if (ROOT / p).exists()]
    for pattern in SCAN_GLOBS:
        paths += [p for p in ROOT.glob(pattern) if not SKIP_DIRS & set(p.parts)]
    paths += list((ROOT / "ai" / "models").glob("**/*.onnx"))
    findings = []
    for path in paths:
        text = path.name if path.suffix == ".onnx" else path.read_text(encoding="utf-8")
        for rx in FORBIDDEN:
            if rx.search(text):
                findings.append(f"{path.relative_to(ROOT)}: matches {rx.pattern}")
    for f in findings:
        print(f"FORBIDDEN {f}")
    if not findings:
        print("licence check passed")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
