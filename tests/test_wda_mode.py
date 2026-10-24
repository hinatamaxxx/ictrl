"""WebDriverAgent: always stopped on close; OCR elements."""
import unittest
from types import SimpleNamespace
from unittest import mock

from ictrl import screentext, wda


def fake_session(mode):
    return SimpleNamespace(wda_mode=mode, _set=lambda **_: None)


class CloseTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_always_stops_the_runner(self):
        for mode in ("on_demand", "off", "resident"):
            agent = wda.WdaAgent(fake_session(mode))
            agent._kill_old_runner = mock.AsyncMock(return_value=False)
            await agent.close()
            agent._kill_old_runner.assert_awaited()


class FindTests(unittest.TestCase):
    def test_find_in_prefers_exact(self):
        found = [{"label": "完了済み"}, {"label": "完了"}]
        self.assertEqual(wda.find_in(found, "完了")["label"], "完了")


class OcrElementTests(unittest.IsolatedAsyncioTestCase):
    async def run_ocr(self, texts):
        session = SimpleNamespace(screen_png=mock.AsyncMock(return_value=(b"png", 100, 200)))
        lines = [{"text": t, "x": 10, "y": 20 * i, "w": 40, "h": 10} for i, t in enumerate(texts)]
        with mock.patch("ictrl.ocr.recognize", mock.AsyncMock(return_value=lines)):
            return await screentext.ocr_elements(session)

    async def test_text_becomes_elements(self):
        result = await self.run_ocr(["完了", "最近"])
        self.assertEqual(result["source"], "ocr")
        self.assertEqual([e["label"] for e in result["elements"]], ["完了", "最近"])
        self.assertAlmostEqual(result["elements"][0]["x"], 0.3)

    async def test_passcode_screen_is_not_handed_out(self):
        result = await self.run_ocr(["iPhoneのバスコードを入力", "1", "2"])
        self.assertTrue(result["locked"])
        self.assertEqual(result["elements"], [])


if __name__ == "__main__":
    unittest.main()
