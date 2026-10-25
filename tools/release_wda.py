"""Publish the unsigned iCtrl WDA IPA on this repository's Releases (tag "wda").

    .venv\\Scripts\\python.exe tools\\release_wda.py            # Appium's latest release
    .venv\\Scripts\\python.exe tools\\release_wda.py v16.15.3   # a given Appium release
    .venv\\Scripts\\python.exe tools\\release_wda.py --repo OWNER/NAME  # another repository

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
    sys.stdout.reconfigure(encoding="utf-8")   # a pipe would get the console's code page
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
            f"iCtrl の補助アプリ「iCtrl WDA」（WebDriverAgent）の、署名していない IPA です。"
            f"iPhone に入れておくと、iCtrl が画面のボタンや文字を一覧で読み取れるようになります。\n\n"
            f"- 入れ方: 自分の Apple ID で署名して入れます（AltStore Classic、Sideloadly など）。"
            f"手順は README の「補助アプリ（WebDriverAgent）を入れる」にあります。\n"
            f"- 元: Appium の WebDriverAgent {release['tag_name']} の `{WDA_ASSET}`（sha256 `{digest}`）。"
            f"変えたのは、アプリの ID（`com.ictrl.wda`）と表示名（`iCtrl WDA`）だけです。\n"
            f"- `iCtrl-WDA.ipa` の sha256: `{ipa_sha}`\n\n"
            f"WebDriverAgent: Copyright (c) 2015-present, Facebook, Inc. All rights reserved. "
            f"ライセンスは BSD-3-Clause で、本文はこのリリースの `WebDriverAgent-LICENSE.txt` にあります。"
            f"iCtrl は Facebook（Meta）および Appium とは関係がなく、承認も受けていません。\n",
            "utf-8")
        exists = subprocess.run(["gh", "release", "view", TAG, *target], cwd=ROOT, capture_output=True).returncode == 0
        if exists:
            subprocess.run(["gh", "release", "edit", TAG, *target, "--notes-file", str(notes),
                            "--title", f"iCtrl WDA（WebDriverAgent {release['tag_name']}）"], cwd=ROOT, check=True)
            subprocess.run(["gh", "release", "upload", TAG, *target, str(ipa), str(LICENSE), "--clobber"],
                           cwd=ROOT, check=True)
        else:
            subprocess.run(["gh", "release", "create", TAG, *target, str(ipa), str(LICENSE),
                            "--title", f"iCtrl WDA（WebDriverAgent {release['tag_name']}）", "--notes-file", str(notes),
                            "--latest=false"],
                           cwd=ROOT, check=True)
    print(f"OK: {TAG} に iCtrl-WDA.ipa（Appium {release['tag_name']}）を置きました")


if __name__ == "__main__":
    main(sys.argv[1:])
