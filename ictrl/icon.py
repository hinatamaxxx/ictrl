"""iCtrl's Windows icon (.ico), drawn from the same shapes as ui/icon.svg.

The .ico holds 16, 24, 32, 48, 64 and 256 px images (PNG). Each size is
drawn at 4x and reduced, with about 4% empty margin like other Windows
apps (a full-bleed icon looks larger than its neighbours).
"""
from __future__ import annotations

import io
from pathlib import Path

SIZES = (16, 24, 32, 48, 64, 256)
BACKGROUND = (11, 13, 18, 255)       # #0b0d12
ACCENT = (122, 162, 255, 255)        # #7aa2ff


def draw(size: int):
    from PIL import Image, ImageDraw
    k = 4
    s = size * k
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    margin = max(k, round(s * 0.04))            # >= 1 px at the final size
    box = s - 2 * margin
    u = box / 32                                 # the SVG's 32-unit grid
    ox = oy = margin
    d.rounded_rectangle((ox, oy, ox + box - 1, oy + box - 1), radius=8 * u, fill=BACKGROUND)
    width = max(k, round(2.2 * u))
    d.rounded_rectangle((ox + 9 * u, oy + 4 * u, ox + 23 * u, oy + 28 * u), radius=4 * u,
                        outline=ACCENT, width=width)
    r = 3.4 * u
    d.ellipse((ox + 16 * u - r, oy + 16 * u - r, ox + 16 * u + r, oy + 16 * u + r), fill=ACCENT)
    return img.resize((size, size), Image.LANCZOS)


def write_ico(path: Path) -> Path:
    """Write the icon; returns the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    images = [draw(n) for n in SIZES]
    buf = io.BytesIO()
    images[-1].save(buf, format="ICO", sizes=[(n, n) for n in SIZES], append_images=images[:-1])
    path.write_bytes(buf.getvalue())
    return path


def ensure_ico() -> Path:
    """The icon in LOCAL_DIR, written when missing."""
    from .paths import LOCAL_DIR
    path = LOCAL_DIR / "iCtrl.ico"
    if not path.exists():
        write_ico(path)
    return path
