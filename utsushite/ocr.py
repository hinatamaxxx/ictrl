"""Text on the iPhone screen through Windows' built-in OCR (Windows.Media.Ocr).

Used where iOS's accessibility tree cannot be read in time: while Safari
shows a JavaScript dialog the page answers no accessibility queries and
every WebDriverAgent read waits ~10 s (iOS 27), but the dialog
is on screen and in the video.
"""
from __future__ import annotations

import io
import logging
from typing import Optional

log = logging.getLogger(__name__)


async def recognize(png: bytes, language: str = "ja") -> list[dict]:
    """Lines of text with their boxes in the image's pixels."""
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    engine = OcrEngine.try_create_from_language(Language(language)) or \
        OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise RuntimeError("Windows の文字認識（OCR）が使えません")
    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(png)
    await writer.store_async()
    await writer.flush_async()
    writer.detach_stream()
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    result = await engine.recognize_async(bitmap)
    lines = []
    for line in result.lines:
        for words in _segments(list(line.words)):
            lines.append(_join(words))
    return lines


def _segments(words: list) -> list[list]:
    """Split an OCR line at wide gaps: side-by-side buttons ("キャンセル"
    and "OK") come back as one line."""
    groups: list[list] = []
    for word in words:
        if groups:
            prev = groups[-1][-1]
            gap = word.bounding_rect.x - (prev.bounding_rect.x + prev.bounding_rect.width)
            if gap <= 1.2 * max(word.bounding_rect.height, prev.bounding_rect.height):
                groups[-1].append(word)
                continue
        groups.append([word])
    return groups


def _join(words: list) -> dict:
    x1 = min(w.bounding_rect.x for w in words)
    y1 = min(w.bounding_rect.y for w in words)
    x2 = max(w.bounding_rect.x + w.bounding_rect.width for w in words)
    y2 = max(w.bounding_rect.y + w.bounding_rect.height for w in words)
    # Japanese OCR returns each character as a word: join them, but keep
    # a space between Latin words ("utsushite test confirm").
    text = words[0].text
    for prev, word in zip(words, words[1:]):
        gap = word.bounding_rect.x - (prev.bounding_rect.x + prev.bounding_rect.width)
        latin = prev.text[-1:].isascii() and word.text[:1].isascii()
        text += (" " if latin and gap > 0.25 * word.bounding_rect.height else "") + word.text
    return {"text": text, "x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1}


def crop_png(png: bytes, box: tuple[float, float, float, float]) -> tuple[bytes, int, int]:
    """Crop (x, y, w, h in 0..1) out of a PNG, enlarged 2x for OCR."""
    from PIL import Image
    image = Image.open(io.BytesIO(png)).convert("RGB")
    x, y, w, h = box
    left, top = int(x * image.width), int(y * image.height)
    region = image.crop((left, top, int((x + w) * image.width), int((y + h) * image.height)))
    region = region.resize((region.width * 2, region.height * 2))
    out = io.BytesIO()
    region.save(out, "PNG")
    return out.getvalue(), left, top
