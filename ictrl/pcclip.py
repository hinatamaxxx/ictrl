"""Write to (and read from) the Windows clipboard from the iCtrl server.

Done server-side with the Win32 clipboard API, so an iPhone copy reaches the
PC even when the iCtrl window is in the background (a browser page may only
write the clipboard while it has focus).

Images are stored three ways so every kind of Windows app can paste them:
CF_DIB (classic apps), the registered "PNG" format (Office, browsers, Slack...)
and CF_DIBV5 is left to Windows to synthesise from CF_DIB.
"""
from __future__ import annotations

import ctypes
import io
import time
from typing import Optional

def _cf_html(fragment: str) -> bytes:
    """Windows "HTML Format" clipboard payload around an HTML fragment."""
    header = ("Version:0.9\r\nStartHTML:{:010d}\r\nEndHTML:{:010d}\r\n"
              "StartFragment:{:010d}\r\nEndFragment:{:010d}\r\n")
    prefix = "<html><body><!--StartFragment-->"
    suffix = "<!--EndFragment--></body></html>"
    dummy = header.format(0, 0, 0, 0).encode("utf-8")
    start_html = len(dummy)
    start_frag = start_html + len(prefix.encode("utf-8"))
    end_frag = start_frag + len(fragment.encode("utf-8"))
    end_html = end_frag + len(suffix.encode("utf-8"))
    return (header.format(start_html, end_html, start_frag, end_frag) + prefix + fragment + suffix).encode("utf-8")


def _open():
    import pywintypes
    import win32clipboard
    # Another program can hold the clipboard for a moment; give it a few
    # short chances, then report instead of waiting indefinitely.
    for _ in range(10):
        try:
            win32clipboard.OpenClipboard()
            return win32clipboard
        except pywintypes.error:
            time.sleep(0.03)
    raise RuntimeError("PC のクリップボードが他のアプリに使用中です")


def to_png(data: bytes) -> bytes:
    """Any image the iPhone puts on its pasteboard (PNG, JPEG, HEIC, ...) as PNG."""
    from PIL import Image
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception:
        pass
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        buf = io.BytesIO()
        image.save(buf, "PNG")
        return buf.getvalue()


def _dib(png: bytes) -> bytes:
    from PIL import Image
    with Image.open(io.BytesIO(png)) as image:
        rgb = image.convert("RGB")
        buf = io.BytesIO()
        rgb.save(buf, "BMP")
        return buf.getvalue()[14:]  # drop BITMAPFILEHEADER -> CF_DIB


def set_text(text: str, html: Optional[str] = None) -> None:
    import win32con
    clip = _open()
    try:
        clip.EmptyClipboard()
        clip.SetClipboardData(win32con.CF_UNICODETEXT, text)
        if html:
            clip.SetClipboardData(clip.RegisterClipboardFormat("HTML Format"), _cf_html(html))
    finally:
        clip.CloseClipboard()


def set_image(png: bytes, text: Optional[str] = None) -> None:
    import win32con
    dib = _dib(png)
    clip = _open()
    try:
        clip.EmptyClipboard()
        clip.SetClipboardData(win32con.CF_DIB, dib)
        clip.SetClipboardData(clip.RegisterClipboardFormat("PNG"), png)
        if text:
            clip.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        clip.CloseClipboard()


def get() -> dict:
    """Current PC clipboard: text and/or PNG image."""
    import win32con
    clip = _open()
    try:
        result: dict = {}
        if clip.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            result["text"] = clip.GetClipboardData(win32con.CF_UNICODETEXT)
        png_format = clip.RegisterClipboardFormat("PNG")
        if clip.IsClipboardFormatAvailable(png_format):
            result["png"] = clip.GetClipboardData(png_format)
        elif clip.IsClipboardFormatAvailable(win32con.CF_DIB):
            dib = clip.GetClipboardData(win32con.CF_DIB)
            result["png"] = _dib_to_png(dib)
        return result
    finally:
        clip.CloseClipboard()


def _dib_to_png(dib: bytes) -> bytes:
    import struct
    from PIL import Image
    header_size = struct.unpack_from("<I", dib, 0)[0]
    bit_count = struct.unpack_from("<H", dib, 14)[0]
    colors = struct.unpack_from("<I", dib, 32)[0] if header_size >= 36 else 0
    compression = struct.unpack_from("<I", dib, 16)[0]
    palette = (colors or (1 << bit_count if bit_count <= 8 else 0)) * 4
    masks = 12 if compression == 3 and header_size == 40 else 0
    offset = 14 + header_size + palette + masks
    bmp = b"BM" + struct.pack("<IHHI", 14 + len(dib), 0, 0, offset) + dib
    with Image.open(io.BytesIO(bmp)) as image:
        buf = io.BytesIO()
        image.save(buf, "PNG")
        return buf.getvalue()
