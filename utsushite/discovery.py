"""Find a paired iPhone on the local network through its Bonjour advertisement.

Used when the Apple Mobile Device Service does not list the phone as a
"Network" device (it often does not, e.g. right after unplugging USB).

The query goes to the Windows Bonjour service (dnssd.dll), which watches every
interface. pymobiledevice3's own browser only queries the default multicast
interface and can miss the phone when other adapters (VPN, Tailscale) exist.

Only the phone's own `_apple-mobdev2` instance (`<wifi-mac>@fe80::...`) is
used; another host (AltServer) advertises `<wifi-mac>@<udid>` for the same
phone, which is not the phone itself.
"""
from __future__ import annotations

import asyncio
import ctypes
import ipaddress
import os
import re
import select
import socket
import time

SERVICE = b"_apple-mobdev2._tcp"
REMOTEPAIRING = b"_remotepairing._tcp"
BOUND = 3.0  # upper bound per step for an absent advertisement, not a wait
_DLL = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "dnssd.dll")
_PHONE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}@fe80:", re.IGNORECASE)


class DiscoveryError(ConnectionError):
    pass


def available() -> bool:
    return os.path.isfile(_DLL)


class _DnsSd:
    MORE_COMING, ADD = 0x1, 0x2

    def __init__(self):
        self.dll = ctypes.WinDLL(_DLL)
        ref = ctypes.c_void_p
        self.browse_cb = ctypes.WINFUNCTYPE(None, ref, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_int32,
                                            ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p)
        self.resolve_cb = ctypes.WINFUNCTYPE(None, ref, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_int32,
                                             ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint16,
                                             ctypes.c_uint16, ctypes.c_void_p, ctypes.c_void_p)
        self.addr_cb = ctypes.WINFUNCTYPE(None, ref, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_int32,
                                          ctypes.c_char_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p)
        for name in ("DNSServiceBrowse", "DNSServiceResolve", "DNSServiceGetAddrInfo",
                     "DNSServiceProcessResult"):
            getattr(self.dll, name).restype = ctypes.c_int32
        self.dll.DNSServiceRefSockFD.restype = ctypes.c_int

    def _run(self, start, done, deadline):
        sd = ctypes.c_void_p()
        if err := start(ctypes.byref(sd)):
            raise DiscoveryError(f"Bonjour サービスに問い合わせできません ({err})")
        try:
            fd = self.dll.DNSServiceRefSockFD(sd)
            while not done():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return
                if select.select([fd], [], [], remaining)[0]:
                    if err := self.dll.DNSServiceProcessResult(sd):
                        raise DiscoveryError(f"Bonjour の応答を処理できません ({err})")
        finally:
            self.dll.DNSServiceRefDeallocate(sd)

    def browse(self, service: bytes = SERVICE) -> dict:
        found, state = {}, {"done": False}

        def reply(_sd, flags, iface, err, name, regtype, domain, _ctx):
            if not err and flags & self.ADD:
                found[name.decode("utf-8", "replace")] = (iface, regtype, domain)
            if not flags & self.MORE_COMING:
                state["done"] = True
        cb = self.browse_cb(reply)
        self._run(lambda sd: self.dll.DNSServiceBrowse(sd, 0, 0, service, b"local.", cb, None),
                  lambda: state["done"], time.monotonic() + BOUND)
        return found

    def resolve(self, name, iface, regtype, domain):
        out = {}

        def reply(_sd, _f, _i, err, _full, host, port, _tl, _txt, _ctx):
            out["host"] = None if err else host.decode()
            out["port"] = socket.ntohs(port)
        cb = self.resolve_cb(reply)
        self._run(lambda sd: self.dll.DNSServiceResolve(sd, 0, iface, name.encode(), regtype, domain, cb, None),
                  lambda: "host" in out, time.monotonic() + BOUND)
        return out.get("host"), out.get("port")

    def ipv4(self, host) -> list[str]:
        addrs, state = [], {"done": False}

        def reply(_sd, flags, _i, err, _host, address, _ttl, _ctx):
            if not err and address and flags & self.ADD:
                if ctypes.cast(address, ctypes.POINTER(ctypes.c_uint16))[0] == socket.AF_INET:
                    addrs.append(socket.inet_ntoa(ctypes.string_at(address + 4, 4)))
            if not flags & self.MORE_COMING:
                state["done"] = True
        cb = self.addr_cb(reply)
        self._run(lambda sd: self.dll.DNSServiceGetAddrInfo(sd, 0, 0, 1, host.encode(), cb, None),
                  lambda: state["done"], time.monotonic() + BOUND)
        return addrs


def _find_sync() -> list[dict]:
    client = _DnsSd()
    phones = []
    for name, (iface, regtype, domain) in client.browse().items():
        if not _PHONE.match(name):
            continue
        host, _port = client.resolve(name, iface, regtype, domain)
        if not host:
            continue
        addrs = [a for a in client.ipv4(host)
                 if not ipaddress.ip_address(a).is_link_local]
        if addrs:
            phones.append({"mac": name.split("@", 1)[0].lower(), "host": host, "addresses": addrs})
    return phones


async def find_phones() -> list[dict]:
    return await asyncio.to_thread(_find_sync)


def _find_remotepairing_sync() -> list[dict]:
    """Every `_remotepairing._tcp` endpoint. Instance names are opaque, so
    the caller identifies the phone by verifying its pairing record."""
    client = _DnsSd()
    out = []
    for name, (iface, regtype, domain) in client.browse(REMOTEPAIRING).items():
        host, port = client.resolve(name, iface, regtype, domain)
        if not host or not port:
            continue
        addrs = [a for a in client.ipv4(host) if not ipaddress.ip_address(a).is_link_local]
        if addrs:
            out.append({"name": name, "host": host, "port": port, "addresses": addrs})
    return out


async def find_remotepairing() -> list[dict]:
    return await asyncio.to_thread(_find_remotepairing_sync)
