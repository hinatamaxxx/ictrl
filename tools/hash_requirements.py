"""Add the files' SHA256 hashes to requirements.txt (pip/uv --require-hashes).

    python tools/hash_requirements.py

Each pinned line gets the hashes of its 64-bit Windows wheels on PyPI (iCtrl
installs binaries only), or of the wheel in wheels/ for the packages PyPI
has as source only.
Comments and order are kept. Run it again after changing a version.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "requirements.txt"


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def local_hashes(name: str, version: str) -> list[str]:
    stem = normalize(name).replace("-", "_")
    return [hashlib.sha256(w.read_bytes()).hexdigest()
            for w in sorted((ROOT / "wheels").glob(f"{stem}-{version}-*.whl"))]


def pypi_hashes(name: str, version: str) -> list[str]:
    with urllib.request.urlopen(f"https://pypi.org/pypi/{normalize(name)}/{version}/json", timeout=30) as r:
        files = json.load(r)["urls"]
    # iCtrl runs on 64-bit Windows only
    return sorted(f["digests"]["sha256"] for f in files
                  if f["filename"].endswith(("-win_amd64.whl", "-any.whl")))


def main() -> None:
    out = []
    # an earlier run's hash lines are rebuilt
    lines = [line.removesuffix("\\").rstrip() for line in REQUIREMENTS.read_text("utf-8").splitlines()
             if not line.lstrip().startswith("--hash")]
    for line in lines:
        spec = line.split("#", 1)[0].strip()
        if not spec:
            out.append(line)
            continue
        name, version = re.match(r"([A-Za-z0-9_.-]+)==([^;\s]+)", spec).groups()
        hashes = local_hashes(name, version) or pypi_hashes(name, version)
        if not hashes:
            raise SystemExit(f"{name}=={version}: wheel が見つかりません")
        out.append(spec + " \\")
        out.extend(f"    --hash=sha256:{h}" + (" \\" if n < len(hashes) - 1 else "")
                   for n, h in enumerate(hashes))
    REQUIREMENTS.write_text("\n".join(out) + "\n", "utf-8")


if __name__ == "__main__":
    main()
