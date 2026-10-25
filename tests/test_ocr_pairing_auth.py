"""OCR line handling, the pairing identity, and the same-user API check."""
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


def word(text, x, w=20, h=20):
    return SimpleNamespace(text=text, bounding_rect=SimpleNamespace(x=x, y=0, width=w, height=h))


class OcrTests(unittest.TestCase):
    def test_side_by_side_buttons_are_split(self):
        from utsushite import ocr
        line = [word(c, 10 + i * 22) for i, c in enumerate("キャンセル")] + [word("OK", 400)]
        texts = [ocr._join(group)["text"] for group in ocr._segments(line)]
        self.assertEqual(texts, ["キャンセル", "OK"])

    def test_latin_words_keep_spaces(self):
        from utsushite import ocr
        words = [word("utsushite", 0, 50), word("test", 60, 40), word("confirm", 110, 70)]
        self.assertEqual(ocr._join(words)["text"], "utsushite test confirm")


class PairingIdentityTests(unittest.TestCase):
    def test_device_info_is_utsushites_own(self):
        import opack2
        from utsushite import pairing
        import pymobiledevice3.remote.tunnel_service as tunnel
        with tempfile.TemporaryDirectory() as tmp:
            old_dir = pairing.PAIRING_DIR
            pairing.PAIRING_DIR = Path(tmp)
            try:
                pairing._use_own_device_info()
                sent = opack2.loads(tunnel.dumps({
                    "remotepairing_serial_number": "AAAAAAAAAAAA", "btAddr": "11:22:33:44:55:66",
                    "mac": b"\x11\x22\x33\x44\x55\x66", "altIRK": b"x" * 16, "name": "host", "accountID": "a"}))
            finally:
                pairing.PAIRING_DIR = old_dir
        self.assertEqual(sent["name"], "utsushite")
        self.assertNotEqual(sent["remotepairing_serial_number"], "AAAAAAAAAAAA")
        self.assertNotEqual(sent["btAddr"], "11:22:33:44:55:66")
        self.assertEqual(sent["accountID"], "a")


@unittest.skipUnless(sys.platform == "win32", "Windows TCP table")
class SameUserTests(unittest.TestCase):
    def test_own_connection_is_allowed(self):
        from utsushite import localauth
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        client = socket.create_connection(server.getsockname())
        try:
            client_port = client.getsockname()[1]
            server_port = server.getsockname()[1]
            self.assertEqual(localauth._pid_for(client_port, server_port), os.getpid())
            self.assertTrue(localauth.allowed(client_port, server_port))
        finally:
            client.close()
            server.close()

    def test_system_process_is_another_user(self):
        from utsushite import localauth
        self.assertNotEqual(localauth._sid_of(4), localauth._own())


if __name__ == "__main__":
    unittest.main()
