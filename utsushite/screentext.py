"""Screen elements from OCR, for when WebDriverAgent is not used.

WebDriverAgent gives exact labels fast, but starting it can make iOS ask for
the passcode ("enter the iPhone passcode to use XCTest"), which nobody can
answer in unattended use. With wda_mode "off", or when WDA cannot start, the
element tools read the text on screen with Windows OCR instead: the same
shape (type, label, centre and size in 0..1), marked "source": "ocr".
Icons without text are not listed; agents use a screenshot for those.
"""
from __future__ import annotations

import re

# A passcode screen (lock screen, XCTest request, app lock): OCR reads パ as
# バ at times. Its keypad is not handed out, as with WDA.
_PASSCODE = re.compile(r"[パバハ]スコード|passcode", re.I)


async def ocr_elements(session) -> dict:
    from . import ocr
    png, width, height = await session.screen_png()
    lines = await ocr.recognize(png)
    elements = []
    for line in lines:
        label = str(line.get("text", "")).strip()
        if not label or not width or not height:
            continue
        elements.append({
            "type": "Text", "label": label,
            "x": round((line["x"] + line["w"] / 2) / width, 4),
            "y": round((line["y"] + line["h"] / 2) / height, 4),
            "w": round(line["w"] / width, 4), "h": round(line["h"] / height, 4),
        })
    locked = any(_PASSCODE.search(e["label"]) for e in elements)
    return {"elements": [] if locked else elements, "locked": locked, "source": "ocr"}
