"""Publish the unsigned iCtrl WDA IPA on this repository's Releases (tag "wda").

    .venv\\Scripts\\python.exe tools\\release_wda.py            # Appium's latest release
    .venv\\Scripts\\python.exe tools\\release_wda.py v16.15.3   # a given Appium release

The IPA is Appium's real-device WebDriverAgent build with only the bundle ID
and the display name changed (ictrl.wda.build_ipa). WebDriverAgent is
BSD-3-Clause: a binary redistribution carries its copyright notice and
license text, so the release has WebDriverAgent-LICENSE.txt beside the IPA
and the notice in its notes. Users sign it themselves (AltStore Classic,
Sideloadly). The tag stays "wda" so the download link never changes; a newer
Appium release replaces the files. Needs the GitHub CLI (gh), signed in.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ictrl.wda import WDA_ASSET, build_ipa  # noqa: E402

TAG = "wda"
LICENSE = ROOT / "docs" / "licenses" / "WebDriverAgent-LICENSE.txt"


def appium_release(tag: str | None) -> dict:
    path = f"tags/{tag}" if tag else "latest"
    with urllib.request.urlopen(f"https://api.github.com/repos/appium/WebDriverAgent/releases/{path}",
                                timeout=30) as r:
        return json.load(r)


def main(argv: list[str]) -> None:
    target: list[str] = []
    if "--repo" in argv:
        i = argv.index("--repo")
        target = ["-R", argv[i + 1]]
        argv = argv[:i] + argv[i + 2:]
    release = appium_release(argv[0] if argv else None)
    asset = next(a for a in release["assets"] if a["name"] == WDA_ASSET)
    with urllib.request.urlopen(asset["browser_download_url"], timeout=120) as r:
        data = r.read()
    digest = hashlib.sha256(data).hexdigest()
    if asset.get("digest") != f"sha256:{digest}":
        raise SystemExit(f"{WDA_ASSET} の SHA256 が GitHub の記載と一致しません")
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / WDA_ASSET
        zip_path.write_bytes(data)
        ipa = Path(tmp) / "iCtrl-WDA.ipa"
        build_ipa(str(ipa), zip_path=str(zip_path))
        ipa_sha = hashlib.sha256(ipa.read_bytes()).hexdigest()
        notes = Path(tmp) / "notes.md"
        notes.write_text(
            f"iCtrl WDA: an unsigned IPA of WebDriverAgent for iCtrl's element-based operation.\n\n"
            f"- Source: Appium WebDriverAgent {release['tag_name']}, `{WDA_ASSET}` "
            f"(sha256 `{digest}`), unmodified except the bundle ID (`com.ictrl.wda`) and the display name (`iCtrl WDA`).\n"
            f"- `iCtrl-WDA.ipa` sha256: `{ipa_sha}`\n"
            f"- Sign and install it with your own Apple ID (AltStore Classic, Sideloadly). See the README.\n\n"
            f"WebDriverAgent: Copyright (c) 2015-present, Facebook, Inc. All rights reserved. "
            f"BSD-3-Clause; the license text is `WebDriverAgent-LICENSE.txt` in this release. "
            f"iCtrl is not affiliated with or endorsed by Facebook (Meta) or Appium.\n",
            "utf-8")
        exists = subprocess.run(["gh", "release", "view", TAG, *target], cwd=ROOT, capture_output=True).returncode == 0
        if exists:
            subprocess.run(["gh", "release", "edit", TAG, *target, "--notes-file", str(notes),
                            "--title", f"iCtrl WDA ({release['tag_name']})"], cwd=ROOT, check=True)
            subprocess.run(["gh", "release", "upload", TAG, *target, str(ipa), str(LICENSE), "--clobber"],
                           cwd=ROOT, check=True)
        else:
            subprocess.run(["gh", "release", "create", TAG, *target, str(ipa), str(LICENSE),
                            "--title", f"iCtrl WDA ({release['tag_name']})", "--notes-file", str(notes),
                            "--latest=false"],
                           cwd=ROOT, check=True)
    print(f"OK: {TAG} に iCtrl-WDA.ipa（Appium {release['tag_name']}）を置きました")


if __name__ == "__main__":
    main(sys.argv[1:])
