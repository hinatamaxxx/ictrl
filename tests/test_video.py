import asyncio
import base64
import json
import struct
import unittest
from pathlib import Path

from ictrl import video

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "config.json").read_text())


def param_sets():
    """VPS/SPS/PPS from a real device stream's hvcC record."""
    rec = base64.b64decode(FIXTURE["description"])
    i, out = 23, {}
    for _ in range(rec[22]):
        kind = rec[i] & 0x3F
        size = int.from_bytes(rec[i + 3:i + 5], "big")
        out[kind] = rec[i + 5:i + 5 + size]
        i += 5 + size
    return out[32], out[33], out[34]


def rtp(seq, payload, marker=False, ts=1000):
    return struct.pack("!BBHII", 0x80, (0x80 if marker else 0) | 123, seq, ts, 0x1234) + payload


def nal(kind, body=b"\x01\x02\x03"):
    return bytes([kind << 1, 1]) + body


def fu(kind, part, start, end):
    header = (0x80 if start else 0) | (0x40 if end else 0) | kind
    return bytes([49 << 1, 1, header]) + part


class SpsTests(unittest.TestCase):
    def test_matches_device_config(self):
        vps, sps, pps = param_sets()
        info = video.parse_sps(sps)
        self.assertEqual(info.codec, FIXTURE["codec"])
        self.assertEqual((info.width, info.height), (FIXTURE["width"], FIXTURE["height"]))
        self.assertEqual(video.hvcc_record(vps, sps, pps, info),
                         base64.b64decode(FIXTURE["description"]))

    def test_matches_pymobiledevice3_reference(self):
        from pymobiledevice3.remote.core_device import screen_stream
        vps, sps, pps = param_sets()
        info = video.parse_sps(sps)
        reference = screen_stream.hevc_codec_string_from_sps(sps)
        # Same fields; the reference uses the hev1 sample-entry prefix.
        self.assertEqual(info.codec.split(".", 1)[1].rstrip(".0"), reference.split(".", 1)[1].rstrip(".0"))


    def test_content_box_crops_encoder_padding(self):
        self.assertEqual(video.content_box(1216, 2656, (1206, 2622)), (1206, 2622))
        self.assertEqual(video.content_box(1216, 2656, None), (1216, 2656))
        # A screen that does not fit the frame (rotation, scaling) is ignored.
        self.assertEqual(video.content_box(1216, 2656, (2622, 1206)), (1216, 2656))

    def test_config_message_for_the_viewer(self):
        # The viewer configures its decoder from this message (a missing
        # helper once raised NameError here, so no video reached the viewer).
        info = video.StreamConfig("hvc1.1.6.L150.B0", 1216, 2656, b"desc")
        message = info.to_json((1206, 2622))
        self.assertEqual((message["t"], message["content_width"], message["content_height"]),
                         ("config", 1206, 2622))

    def test_device_lag_is_measured_against_the_earliest_arrival(self):
        lag = video.DeviceLag()
        self.assertEqual(lag.observe(100.0, 0), 0.0)
        self.assertAlmostEqual(lag.observe(101.0, 24000), 0.0)
        with self.assertLogs("ictrl.video", "INFO"):
            self.assertAlmostEqual(lag.observe(104.0, 48000), 2.0)
        with self.assertLogs("ictrl.video", "INFO"):
            self.assertAlmostEqual(lag.observe(103.05, 72000), 0.05, places=3)

class AssemblerTests(unittest.TestCase):
    def test_single_and_fragmented_units(self):
        a = video.HevcAssembler()
        vps, sps, pps = param_sets()
        ap = bytes([48 << 1, 1]) + b"".join(len(n).to_bytes(2, "big") + n for n in (vps, sps, pps))
        self.assertIsNone(a.feed(rtp(1, ap)))
        self.assertTrue(a.config_changed)
        self.assertIsNone(a.feed(rtp(2, fu(19, b"AAA", True, False))))
        unit = a.feed(rtp(3, fu(19, b"BBB", False, True), marker=True))
        self.assertTrue(unit.key)
        sizes, data, i = [], unit.data, 0
        while i < len(data):
            n = int.from_bytes(data[i:i + 4], "big")
            sizes.append(n)
            i += 4 + n
        self.assertEqual(sizes, [len(vps), len(sps), len(pps), 2 + 6])
        delta = a.feed(rtp(4, nal(1), marker=True, ts=2000))
        self.assertFalse(delta.key)

    def test_sequence_gap_drops_unit(self):
        a = video.HevcAssembler()
        a.feed(rtp(10, fu(1, b"x", True, False)))
        self.assertIsNone(a.feed(rtp(12, fu(1, b"y", False, True), marker=True)))
        self.assertTrue(a.last_dropped)
        self.assertEqual(a.dropped, 1)
        self.assertIsNotNone(a.feed(rtp(13, nal(1), marker=True)))
        self.assertFalse(a.last_dropped)

    def test_trailer_is_stripped(self):
        a = video.HevcAssembler()
        unit = a.feed(rtp(1, nal(1, b"\x09" + video._TRAILER), marker=True))
        self.assertTrue(unit.data.endswith(b"\x09"))

    def test_rtcp_and_garbage_ignored(self):
        a = video.HevcAssembler()
        self.assertIsNone(a.feed(b"\x81\xc8" + b"\0" * 30))
        self.assertIsNone(a.feed(b"\x00" * 4))
        self.assertEqual(a.packets, 0)


class HubTests(unittest.IsolatedAsyncioTestCase):
    def make(self):
        requests = []
        hub = video.VideoHub(requests.append)
        hub.set_config(video.StreamConfig("hvc1", 10, 10, b""))
        return hub, requests

    async def test_joiner_gets_config_and_entire_long_gop(self):
        hub, _ = self.make()
        hub.publish(video.AccessUnit(True, 0, b"k"))
        for i in range(300):  # longer than the per-subscriber backlog
            hub.publish(video.AccessUnit(False, i, b"d"))
        sub = hub.subscribe()
        kinds = [sub.queue.get_nowait()[0] for _ in range(sub.queue.qsize())]
        self.assertEqual(kinds[0], "config")
        self.assertEqual(len(kinds), 1 + 301)
        self.assertFalse(sub.needs_key)

    async def test_overflow_waits_for_key_and_requests_one(self):
        hub, requests = self.make()
        hub.publish(video.AccessUnit(True, 0, b"k"))
        sub = hub.subscribe()
        for i in range(video.Subscriber.BACKLOG + 5):
            hub.publish(video.AccessUnit(False, i, b"d"))
        self.assertTrue(requests)
        self.assertTrue(sub.needs_key)
        hub.publish(video.AccessUnit(False, 0, b"d"))
        self.assertTrue(sub.queue.empty())
        hub.publish(video.AccessUnit(True, 0, b"k"))
        self.assertEqual(sub.queue.get_nowait()[1].key, True)

    async def test_broken_stream_holds_deltas(self):
        hub, _ = self.make()
        hub.publish(video.AccessUnit(True, 0, b"k"))
        sub = hub.subscribe()
        while not sub.queue.empty():
            sub.queue.get_nowait()
        hub.mark_broken()
        hub.publish(video.AccessUnit(False, 1, b"d"))
        self.assertTrue(sub.queue.empty())
        self.assertEqual(hub.gop, [])

    async def test_close_wakes_subscribers(self):
        hub, _ = self.make()
        sub = hub.subscribe()
        hub.close()
        self.assertEqual(await asyncio.wait_for(sub.queue.get(), 1), ("end", None))

    async def test_next_keyframe(self):
        hub, requests = self.make()
        waiter = asyncio.create_task(hub.next_keyframe(1))
        await asyncio.sleep(0)
        hub.publish(video.AccessUnit(True, 5, b"k"))
        self.assertEqual((await waiter).timestamp, 5)
        self.assertEqual(requests, ["snapshot"])


if __name__ == "__main__":
    unittest.main()
