"""iCtrl's own lockdown pairing ("trust this computer" record).

By default pymobiledevice3 uses the pair record that usbmuxd keeps for this
PC (made by Apple Devices / AltServer). AltStore Classic's on-device
VPN mode uses that same record, so iOS showed iCtrl's connection in the
Dynamic Island as the paired computer "AltStore". With a
record of its own, paired under the host name "iCtrl", the phone shows iCtrl.

Creating it asks the user once on the iPhone to trust the computer (and for
the passcode); the shared usbmuxd record is never modified.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import plistlib
import socket
import uuid
from pathlib import Path
from typing import Optional

import os

from .paths import LOCAL_DIR as DATA_DIR  # keys bound to this PC (paths.py)

log = logging.getLogger(__name__)

HOST_NAME = "iCtrl"
PAIRING_DIR = DATA_DIR / "pairing"
PMD3_HOME = DATA_DIR / "pymobiledevice3"


def use_ictrl_identity() -> None:
    """Make pymobiledevice3 act as the computer "iCtrl" in this process.

    Its Wi-Fi (RemotePairing) identity is uuid3(computer name) and its
    records live in one shared folder keyed by device. AltStore Classic's
    pairing had the same identity, so the phone listed every iCtrl session
    under the name "AltStore" (USB and Wi-Fi). With its own name
    and folder, iCtrl pairs and appears as "iCtrl"."""
    import platform
    import shutil
    import pymobiledevice3.common as common
    platform.node = lambda: HOST_NAME
    PMD3_HOME.mkdir(parents=True, exist_ok=True)
    shared = common._HOMEFOLDER
    if shared != PMD3_HOME:
        # The developer disk image cache is reused rather than downloaded again.
        for item in ("Xcode_iOS_DDI_Personalized",):
            src, dst = Path(shared) / item, PMD3_HOME / item
            if src.exists() and not dst.exists():
                with contextlib.suppress(Exception):
                    shutil.copytree(src, dst)
        common._HOMEFOLDER = PMD3_HOME
    _use_own_device_info()


def _identity() -> dict:
    """Per-install values the iPhone uses to tell paired computers apart."""
    import json
    import secrets
    import string
    path = PAIRING_DIR / "identity.json"
    with contextlib.suppress(Exception):
        return json.loads(path.read_text("utf-8"))
    mac = bytes([0x02 | (secrets.randbits(8) & 0xFC)]) + secrets.token_bytes(5)   # locally administered
    ident = {
        "altIRK": secrets.token_hex(16),
        "btAddr": ":".join(f"{b:02X}" for b in (bytes([0x02]) + secrets.token_bytes(5))),
        "mac": mac.hex(),
        "serial": "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(12)),
    }
    PAIRING_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ident), "utf-8")
    return ident


def _use_own_device_info() -> None:
    """pymobiledevice3 describes every host to the phone with the same fixed
    values (serial "AAAAAAAAAAAA", Bluetooth 11:22:33:44:55:66, ...). The
    phone keys its "paired computers" by them, so all such pairings merged
    into the first one made (for example AltStore's), whatever name iCtrl
    sent. iCtrl sends its own values."""
    import pymobiledevice3.remote.tunnel_service as tunnel
    if getattr(tunnel.dumps, "_ictrl", False):
        return
    ident = _identity()
    original = tunnel.dumps

    def dumps(obj):
        if isinstance(obj, dict) and "remotepairing_serial_number" in obj:
            obj = {**obj, "altIRK": bytes.fromhex(ident["altIRK"]), "btAddr": ident["btAddr"],
                   "mac": bytes.fromhex(ident["mac"]), "remotepairing_serial_number": ident["serial"],
                   "name": HOST_NAME}
        return original(obj)
    dumps._ictrl = True
    tunnel.dumps = dumps


def record_path(udid: str) -> Path:
    return PAIRING_DIR / f"{udid}.plist"


def load(udid: str) -> Optional[dict]:
    path = record_path(udid)
    if not path.exists():
        return None
    with contextlib.suppress(Exception):
        return plistlib.loads(path.read_bytes())
    return None


async def create(udid: str, timeout: float = 120) -> dict:
    """Pair over USB as "iCtrl". Waits for the user to tap Trust."""
    from pymobiledevice3.lockdown import LockdownClient, create_using_usbmux
    PAIRING_DIR.mkdir(parents=True, exist_ok=True)
    host_id = str(uuid.uuid4()).upper()
    buid = str(uuid.uuid4()).upper()
    # A record the phone does not know: the session stays unpaired, so the
    # usbmuxd record is neither used nor rewritten.
    client = await create_using_usbmux(serial=udid, connection_type="USB", autopair=False,
                                       pair_record={"HostID": host_id, "SystemBUID": buid},
                                       pairing_records_cache_folder=PAIRING_DIR)
    try:
        client.host_id, client.system_buid, client.pair_record = host_id, buid, None
        # Only into iCtrl's folder: the usbmux client would also hand the
        # record to usbmuxd, replacing the shared one.
        client.save_pair_record = LockdownClient.save_pair_record.__get__(client)
        original = socket.gethostname
        socket.gethostname = lambda: HOST_NAME          # the name the iPhone shows
        try:
            log.info("pairing as %r: confirm 'Trust' on the iPhone", HOST_NAME)
            await asyncio.wait_for(client.pair(timeout=timeout), timeout + 10)
        finally:
            socket.gethostname = original
    finally:
        with contextlib.suppress(Exception):
            await client.close()
    if load(udid) is None:
        raise RuntimeError("ペアリングの記録を保存できませんでした")
    log.info("iCtrl pairing created")
    return {"ok": True, "host_name": HOST_NAME}
