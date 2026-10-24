"""HID usage tables (USB HID Usage Tables 1.4, Keyboard page 0x07)."""

# name -> (usage page, usage code, hold seconds)
BUTTONS: dict[str, tuple[int, int, float]] = {
    "home": (0x0C, 0x40, 0.05),
    "lock": (0x0C, 0x30, 0.3),
    "volume-up": (0x0C, 0xE9, 0.05),
    "volume-down": (0x0C, 0xEA, 0.05),
    "mute": (0x0C, 0xE2, 0.05),
    "siri": (0x0C, 0xCF, 1.0),
    "eject": (0x0C, 0xB8, 0.05),     # shows the on-screen keyboard
}

MODIFIERS = {
    "ctrl": 0xE0, "shift": 0xE1, "alt": 0xE2, "option": 0xE2, "cmd": 0xE3, "meta": 0xE3,
}

NAMED = {
    "enter": 0x28, "return": 0x28, "escape": 0x29, "esc": 0x29, "backspace": 0x2A,
    "delete": 0x2A, "tab": 0x2B, "space": 0x2C, "forward-delete": 0x4C,
    "right": 0x4F, "left": 0x50, "down": 0x51, "up": 0x52,
    "home": 0x4A, "end": 0x4D, "pageup": 0x4B, "pagedown": 0x4E, "capslock": 0x39,
    **{f"f{i}": 0x3A + i - 1 for i in range(1, 13)},
    # Japanese input mode keys of Apple JIS keyboards (iOS honours them):
    # LANG1 = かな (Japanese input), LANG2 = 英数 (alphanumeric input).
    "lang1": 0x90, "kana": 0x90, "lang2": 0x91, "eisu": 0x91,
    "yen": 0x89, "ro": 0x87,
}

# char -> (usage, needs shift), US layout
CHARS: dict[str, tuple[int, bool]] = {}
for i, c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    CHARS[c] = (0x04 + i, False)
    CHARS[c.upper()] = (0x04 + i, True)
for i, c in enumerate("1234567890"):
    CHARS[c] = (0x1E + i, False)
for i, c in enumerate("!@#$%^&*()"):
    CHARS[c] = (0x1E + i, True)
for c, u, s in [("\n", 0x28, False), ("\t", 0x2B, False), (" ", 0x2C, False),
                ("-", 0x2D, False), ("_", 0x2D, True), ("=", 0x2E, False), ("+", 0x2E, True),
                ("[", 0x2F, False), ("{", 0x2F, True), ("]", 0x30, False), ("}", 0x30, True),
                ("\\", 0x31, False), ("|", 0x31, True), (";", 0x33, False), (":", 0x33, True),
                ("'", 0x34, False), ('"', 0x34, True), ("`", 0x35, False), ("~", 0x35, True),
                (",", 0x36, False), ("<", 0x36, True), (".", 0x37, False), (">", 0x37, True),
                ("/", 0x38, False), ("?", 0x38, True)]:
    CHARS[c] = (u, s)


# Characters at the same position on US and JIS hardware layouts. Everything
# else is pasted, because the iPhone's hardware keyboard layout is unknown.
_LAYOUT_SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 \n\t-,./")


def is_typable(text: str) -> bool:
    return all(ch in _LAYOUT_SAFE for ch in text)


def usage_for_name(name: str) -> int:
    key = name.lower()
    if key in NAMED:
        return NAMED[key]
    if key in CHARS:
        return CHARS[key][0]
    raise ValueError(f"不明なキー: {name}")


def is_modifier(usage: int) -> bool:
    """Ctrl/Shift/Alt/Cmd (0xE0-0xE7) alone do not count as typing on iOS."""
    return 0xE0 <= usage <= 0xE7

