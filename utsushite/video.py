"""RTP/HEVC (RFC 7798) reassembly, subscriber fan-out and still-frame decode.

The iPhone's CoreDevice display stream is HEVC over RTP. Each access unit is
forwarded as length-prefixed NAL units (hvcC sample format), which browsers
decode in hardware through WebCodecs with the matching hvcC description.

Late joiners receive the complete sequence from the latest keyframe, so they
can start decoding immediately: the device emits long GOPs and may not answer
a keyframe request at once.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

log = logging.getLogger(__name__)

NAL_VPS, NAL_SPS, NAL_PPS = 32, 33, 34
NAL_AP, NAL_FU = 48, 49
KEY_NALS = (19, 20, 21)  # IDR_W_RADL, IDR_N_LP, CRA

# Apple's display service appends this constant trailer to coded slices.
_TRAILER = bytes.fromhex("04f00ac0000003000004ec0ab003")

MAX_GOP_UNITS = 360          # ~6 s at 60 fps; then ask for a fresh keyframe
MAX_GOP_BYTES = 48 * 1024 * 1024


@dataclass(slots=True)
class AccessUnit:
    key: bool
    timestamp: int          # RTP 90k/24k clock as sent by the device
    data: bytes             # 4-byte length prefixed NAL units
    received: float = field(default_factory=time.monotonic)


def _strip(nal: bytes) -> bytes:
    return nal[:-len(_TRAILER)] if nal.endswith(_TRAILER) else nal


def _rbsp(nal: bytes) -> bytes:
    out = bytearray()
    zeros = 0
    for b in nal[2:]:
        if zeros >= 2 and b == 3:
            zeros = 0
            continue
        out.append(b)
        zeros = zeros + 1 if b == 0 else 0
    return bytes(out)


class _Bits:
    def __init__(self, data: bytes):
        self.data, self.pos = data, 0

    def u(self, n: int) -> int:
        v = 0
        for _ in range(n):
            byte = self.data[self.pos >> 3]
            v = (v << 1) | ((byte >> (7 - (self.pos & 7))) & 1)
            self.pos += 1
        return v

    def ue(self) -> int:
        zeros = 0
        while self.u(1) == 0:
            zeros += 1
        return (1 << zeros) - 1 + (self.u(zeros) if zeros else 0)


@dataclass(slots=True)
class SpsInfo:
    codec: str
    width: int
    height: int
    profile_space: int
    tier: int
    profile_idc: int
    compat_flags: int
    constraint: bytes
    level_idc: int
    chroma_format_idc: int
    bit_depth_luma: int
    bit_depth_chroma: int


def parse_sps(sps: bytes) -> SpsInfo:
    r = _Bits(_rbsp(sps))
    r.u(4)                       # sps_video_parameter_set_id
    max_sub_layers = r.u(3) + 1
    r.u(1)                       # temporal_id_nesting
    profile_space, tier, profile_idc = r.u(2), r.u(1), r.u(5)
    compat = r.u(32)
    constraint = bytes(r.u(8) for _ in range(6))
    level_idc = r.u(8)
    sub_profile, sub_level = [], []
    for _ in range(max_sub_layers - 1):
        sub_profile.append(r.u(1))
        sub_level.append(r.u(1))
    if max_sub_layers > 1:
        for _ in range(max_sub_layers - 1, 8):
            r.u(2)
    for i in range(max_sub_layers - 1):
        if sub_profile[i]:
            r.u(88)
        if sub_level[i]:
            r.u(8)
    r.ue()                       # sps_seq_parameter_set_id
    chroma = r.ue()
    if chroma == 3:
        r.u(1)
    width, height = r.ue(), r.ue()
    if r.u(1):                   # conformance window
        sub_w = 2 if chroma in (1, 2) else 1
        sub_h = 2 if chroma == 1 else 1
        left, right, top, bottom = r.ue(), r.ue(), r.ue(), r.ue()
        width -= sub_w * (left + right)
        height -= sub_h * (top + bottom)
    depth_luma, depth_chroma = r.ue() + 8, r.ue() + 8
    # WebCodecs / ISO 14496-15 codec string.
    rev = int(f"{compat:032b}"[::-1], 2)
    cons = constraint.rstrip(b"\0") or b"\0"
    codec = "hvc1.{}{}.{:X}.{}{}.{}".format(
        "" if profile_space == 0 else "ABC"[profile_space - 1], profile_idc, rev,
        "H" if tier else "L", level_idc, ".".join(f"{b:X}" for b in cons))
    return SpsInfo(codec, width, height, profile_space, tier, profile_idc, compat,
                   constraint, level_idc, chroma, depth_luma, depth_chroma)


def hvcc_record(vps: bytes, sps: bytes, pps: bytes, info: SpsInfo) -> bytes:
    """HEVCDecoderConfigurationRecord (ISO/IEC 14496-15 8.3.3.1)."""
    out = bytearray()
    out.append(1)
    out.append((info.profile_space << 6) | (info.tier << 5) | info.profile_idc)
    out += info.compat_flags.to_bytes(4, "big")
    out += info.constraint
    out.append(info.level_idc)
    out += (0xF000).to_bytes(2, "big")          # min_spatial_segmentation 0
    out.append(0xFC)                             # parallelismType 0
    out.append(0xFC | info.chroma_format_idc)
    out.append(0xF8 | (info.bit_depth_luma - 8))
    out.append(0xF8 | (info.bit_depth_chroma - 8))
    out += (0).to_bytes(2, "big")               # avgFrameRate
    out.append(0x0F)                             # lengthSizeMinusOne = 3
    out.append(3)
    for kind, nal in ((NAL_VPS, vps), (NAL_SPS, sps), (NAL_PPS, pps)):
        out.append(0x80 | kind)
        out += (1).to_bytes(2, "big")
        out += len(nal).to_bytes(2, "big")
        out += nal
    return bytes(out)


@dataclass(slots=True)
class StreamConfig:
    codec: str
    width: int
    height: int
    description: bytes

    def to_json(self, screen=None) -> dict:
        cw, ch = content_box(self.width, self.height, screen)
        return {"t": "config", "codec": self.codec, "width": self.width,
                "height": self.height, "content_width": cw, "content_height": ch,
                "description": base64.b64encode(self.description).decode("ascii")}


class HevcAssembler:
    """Pure RTP packet -> AccessUnit state machine (no I/O, unit-testable).

    A sequence gap drops the access unit in progress: decoders render missing
    slices as visible corruption that persists until the next keyframe, so a
    short freeze plus a keyframe request is the better trade.
    """

    def __init__(self):
        self.fu = bytearray()
        self.nals: list[bytes] = []
        self.key = False
        self.corrupt = False
        self.last_seq: Optional[int] = None
        self.highest_ext_seq = 0
        self.packets = 0
        self.frames = 0
        self.dropped = 0
        self.last_dropped = False
        self.last_rtp_ts = 0
        self.vps = self.sps = self.pps = None
        self.config: Optional[StreamConfig] = None
        self.config_changed = False

    def _track_seq(self, seq: int) -> None:
        cur = self.highest_ext_seq
        cycles, last16 = cur >> 16, cur & 0xFFFF
        if seq < last16 and last16 - seq > 0x8000:
            cycles = (cycles + 1) & 0xFFFF
        new = (cycles << 16) | seq
        if cur == 0 or ((new - cur) & 0xFFFFFFFF) < 0x80000000:
            self.highest_ext_seq = new
        if self.last_seq is not None and seq != (self.last_seq + 1) & 0xFFFF:
            self.fu.clear()
            self.corrupt = True
        if self.last_seq is None or ((seq - self.last_seq) & 0xFFFF) < 0x8000:
            self.last_seq = seq

    def _nal(self, nal: bytes) -> None:
        if not nal:
            return
        kind = (nal[0] >> 1) & 0x3F
        if kind in (NAL_VPS, NAL_SPS, NAL_PPS):
            if kind == NAL_VPS:
                changed, self.vps = nal != self.vps, nal
            elif kind == NAL_SPS:
                changed, self.sps = nal != self.sps, nal
            else:
                changed, self.pps = nal != self.pps, nal
            if changed and self.vps and self.sps and self.pps:
                info = parse_sps(self.sps)
                self.config = StreamConfig(info.codec, info.width, info.height,
                                           hvcc_record(self.vps, self.sps, self.pps, info))
                self.config_changed = True
        elif kind in KEY_NALS:
            self.key = True
        self.nals.append(nal)

    def feed(self, packet: bytes) -> Optional[AccessUnit]:
        if len(packet) < 12 or packet[0] >> 6 != 2:
            return None
        if 64 <= (packet[1] & 0x7F) <= 95:          # RTCP multiplexed
            return None
        offset = 12 + 4 * (packet[0] & 0x0F)
        if packet[0] & 0x10:
            if offset + 4 > len(packet):
                return None
            offset += 4 + 4 * int.from_bytes(packet[offset + 2:offset + 4], "big")
        end = len(packet)
        if packet[0] & 0x20:
            end -= packet[-1]
        if offset >= end:
            return None
        payload = packet[offset:end]
        marker = bool(packet[1] & 0x80)
        self.packets += 1
        self.last_rtp_ts = int.from_bytes(packet[4:8], "big")
        self._track_seq(int.from_bytes(packet[2:4], "big"))
        if len(payload) >= 2:
            kind = (payload[0] >> 1) & 0x3F
            if kind == NAL_AP:
                i = 2
                while i + 2 <= len(payload):
                    size = int.from_bytes(payload[i:i + 2], "big")
                    i += 2
                    self._nal(payload[i:i + size])
                    i += size
            elif kind == NAL_FU and len(payload) >= 3:
                header = payload[2]
                if header & 0x80:
                    self.fu[:] = bytes([(payload[0] & 0x81) | ((header & 0x3F) << 1),
                                        payload[1]]) + payload[3:]
                elif self.fu:
                    self.fu += payload[3:]
                if header & 0x40 and self.fu:
                    self._nal(_strip(bytes(self.fu)))
                    self.fu.clear()
            else:
                self._nal(_strip(payload))
        if not marker:
            return None
        unit = None
        self.last_dropped = self.corrupt
        if self.corrupt:
            self.dropped += 1
        elif self.nals:
            self.frames += 1
            unit = AccessUnit(self.key, self.last_rtp_ts,
                              b"".join(len(n).to_bytes(4, "big") + n for n in self.nals))
        self.nals = []
        self.key = False
        self.corrupt = False
        return unit


class DeviceLag:
    """Diagnostics only: how much later than the device's own clock frames
    arrive. The RTP clock is 24 kHz (867307 ticks in
    36.15 s); the earliest arrival seen is taken as zero lag. Nothing is
    restarted or dropped because of it."""

    RATE = 24000
    REPORT = 0.5     # seconds

    def __init__(self):
        self.base: Optional[float] = None
        self.last_ts: Optional[int] = None
        self.wraps = 0
        self.lag = 0.0
        self.worst = 0.0

    def observe(self, now: float, timestamp: int) -> float:
        if self.last_ts is not None:
            if timestamp < self.last_ts and self.last_ts - timestamp > 1 << 31:
                self.wraps += 1
            elif timestamp < self.last_ts and self.last_ts - timestamp > self.RATE * 10:
                self.base = None          # the stream restarted its clock
        self.last_ts = timestamp
        offset = now - ((self.wraps << 32) + timestamp) / self.RATE
        if self.base is None or offset < self.base:
            self.base = offset
        lag = offset - self.base
        if lag >= self.REPORT and self.lag < self.REPORT:
            log.info("video: frames arrive %.1f s behind the device clock", lag)
        if lag >= self.REPORT:
            self.worst = max(self.worst, lag)
        elif self.lag >= self.REPORT:
            log.info("video: caught up with the device clock (worst %.1f s)", self.worst)
            self.worst = 0.0
        self.lag = lag
        return lag


class Subscriber:
    """One viewer. Holds deltas until a keyframe after any overflow."""

    BACKLOG = 120
    LAGGING = 30    # about 0.5 s at 60 fps: worth a diagnostic line

    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.needs_key = True
        self.limit = self.BACKLOG
        self.lagging = False

    def offer(self, item) -> bool:
        """Returns False when the subscriber overflowed and needs a keyframe."""
        if self.queue.qsize() >= self.limit:
            while not self.queue.empty():
                self.queue.get_nowait()
            self.limit = self.BACKLOG
            self.needs_key = True
            log.info("video: a viewer fell %d units behind; skipped to the next keyframe", self.BACKLOG)
            return False
        self.queue.put_nowait(item)
        depth = self.queue.qsize()
        if depth >= self.LAGGING and not self.lagging:
            self.lagging = True
            log.info("video: a viewer is %d units behind", depth)
        elif depth <= 2 and self.lagging:
            self.lagging = False
            log.info("video: the viewer caught up")
        return True


class VideoHub:
    """Fan-out of access units, plus the current GOP for joiners/snapshots."""

    def __init__(self, request_keyframe: Callable[[str], None]):
        self.request_keyframe = request_keyframe
        self.config: Optional[StreamConfig] = None
        self.gop: list[AccessUnit] = []
        self.gop_bytes = 0
        self.subscribers: set[Subscriber] = set()
        self.units = 0
        self.keyframes = 0
        self.bytes = 0
        self._key_waiters: list[asyncio.Future] = []

    def reset(self) -> None:
        self.config = None
        self.gop.clear()
        self.gop_bytes = 0
        for sub in self.subscribers:
            sub.needs_key = True

    def close(self) -> None:
        """Session ended: wake every subscriber with an end marker."""
        self.reset()
        for sub in self.subscribers:
            while not sub.queue.empty():
                sub.queue.get_nowait()
            sub.queue.put_nowait(("end", None))

    def mark_broken(self) -> None:
        """A dropped access unit invalidates every later delta until a key."""
        self.gop = []
        self.gop_bytes = 0
        for sub in self.subscribers:
            sub.needs_key = True

    def set_config(self, config: StreamConfig) -> None:
        self.config = config
        self.gop.clear()
        self.gop_bytes = 0
        for sub in self.subscribers:
            sub.needs_key = True
            sub.queue.put_nowait(("config", config))

    def publish(self, unit: AccessUnit) -> None:
        self.units += 1
        self.bytes += len(unit.data)
        if unit.key:
            self.keyframes += 1
            self.gop = [unit]
            self.gop_bytes = len(unit.data)
        elif self.gop:
            if len(self.gop) >= MAX_GOP_UNITS or self.gop_bytes > MAX_GOP_BYTES:
                # Bound memory: drop the GOP; the next keyframe rebuilds it.
                self.gop = []
                self.gop_bytes = 0
                self.request_keyframe("gop-limit")
            else:
                self.gop.append(unit)
                self.gop_bytes += len(unit.data)
        if unit.key:
            for waiter in self._key_waiters:
                if not waiter.done():
                    waiter.set_result(unit)
            self._key_waiters.clear()
        for sub in list(self.subscribers):
            if sub.needs_key:
                if not unit.key:
                    continue
                sub.needs_key = False
            if not sub.offer(("unit", unit)):
                self.request_keyframe("subscriber-overflow")

    async def next_keyframe(self, timeout: float) -> Optional[AccessUnit]:
        """Ask the encoder for a keyframe and wait (bounded) for it."""
        waiter = asyncio.get_running_loop().create_future()
        self._key_waiters.append(waiter)
        self.request_keyframe("snapshot")
        try:
            return await asyncio.wait_for(waiter, timeout)
        except asyncio.TimeoutError:
            return None

    def subscribe(self) -> Subscriber:
        sub = Subscriber()
        if self.config is not None:
            # The joiner gets the whole current GOP at once; size its backlog
            # allowance accordingly so the replay itself is not an overflow.
            sub.queue.put_nowait(("config", self.config))
            for unit in self.gop:
                sub.queue.put_nowait(("unit", unit))
            sub.limit = len(self.gop) + Subscriber.BACKLOG
            sub.needs_key = not self.gop
        self.subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        self.subscribers.discard(sub)


def content_box(frame_w: int, frame_h: int, screen) -> tuple[int, int]:
    """The encoder pads the picture to its block size (e.g. 1206x2622 inside
    1216x2656). The screen occupies the top-left corner; return its size."""
    if screen:
        sw, sh = screen
        if 0 < sw <= frame_w and 0 < sh <= frame_h and frame_w - sw <= 64 and frame_h - sh <= 64:
            return sw, sh
    return frame_w, frame_h


def unit_message(unit: AccessUnit) -> bytes:
    """Binary WebSocket frame: [type u8][timestamp u32][payload]."""
    return bytes([0 if unit.key else 1]) + (unit.timestamp & 0xFFFFFFFF).to_bytes(4, "big") + unit.data


class SnapshotUnavailable(RuntimeError):
    pass
