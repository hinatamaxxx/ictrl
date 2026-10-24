"""Fresh accessibility observations without WDA, video or screenshots."""
from __future__ import annotations

import asyncio
import contextlib
import re
import unicodedata

from pymobiledevice3.services.accessibilityaudit import AccessibilityAudit


@contextlib.asynccontextmanager
async def _open_audit(rsd):
    audit = AccessibilityAudit(rsd)
    # Upstream __aenter__ catches Exception, which excludes CancelledError.
    # Register ownership before connecting so WDA startup cancellation cannot
    # leave a partially opened inspector redirecting another reader's events.
    try:
        await audit.__aenter__()
        yield audit
    finally:
        await audit.close()


def label_text(value: str) -> str:
    # AX captions are spoken labels, e.g. 'Enable UI Automation, 静的テキスト'.
    # iOS also inserts U+2060 WORD JOINER inside the Japanese word passcode.
    value = ''.join(c for c in value if unicodedata.category(c) != 'Cf').strip()
    roles = {'静的テキスト', 'ボタン', 'テキストフィールド', 'セキュアテキストフィールド',
             'static text', 'button', 'text field', 'secure text field'}
    label, comma, role = value.rpartition(',')
    if comma and role.strip().lower() in roles:
        return label.strip()
    return value


async def read_prompt(session) -> bool:
    """Serialize inspector clients through teardown, including manual entry.

    Opening another inspector while one is alive can leave the first reader
    without focus events (reproduced on iOS 27.0.1).
    Store the lock on the owning session (not a global tied to an event loop).
    """
    lock = getattr(session, "_ax_prompt_lock", None)
    if lock is None:
        # No await before assignment: simultaneous callers share this lock.
        lock = session._ax_prompt_lock = asyncio.Lock()
    async with lock:
        return await _read_prompt(session)


async def _read_prompt(session) -> bool:
    """Stop at a verified XCTest heading and explicitly empty passcode field.

    Closing the provider discards its event queue. Missing required labels
    never authorize input. The owning WDA startup cancels outstanding reads.
    """
    from .passcode import is_xctest_prompt, has_entered_passcode

    rows = []
    empty_field = False
    async with asyncio.timeout(12):
        async with _open_audit(session.rsd) as audit:
            try:
                # Hide the inspector's moving focus rectangle on the phone.
                await audit.set_show_visuals(False)
                async for item in audit.iter_elements():
                    value = label_text(item.caption or item.spoken_description or "")
                    # Actual iOS 27 AX label. Require the entered count, rather
                    # than assuming a missing masked value means an empty field.
                    field = re.fullmatch(r'パスコードフィールド,\s*([0-9]+)個中([0-9]+)個の値が入力済み', value)
                    if field:
                        capacity, entered = map(int, field.groups())
                        if entered != 0 or capacity < 1:
                            return False
                        empty_field = True
                    rows.extend({"text": line.strip()} for line in value.splitlines() if line.strip())
                    if has_entered_passcode(rows):
                        return False
                    if empty_field and is_xctest_prompt(rows):
                        return True
                    if len(rows) > 160:
                        return False
            finally:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(audit.set_app_monitoring_enabled(False), 2)
    return False
