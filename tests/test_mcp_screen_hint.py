"""After ictrl_connect the agent is told to show the iCtrl screen when nobody sees it."""
import unittest
from unittest import mock

from ictrl import mcp


class ScreenHintTests(unittest.TestCase):
    def connect(self, pages):
        client = mock.Mock(url="http://127.0.0.1:47800")
        client.call.return_value = {"t": "status", "state": "ready", "pages": pages}
        return [part["text"] for part in mcp.handle_tool(client, "ictrl_connect", {"transport": "wifi"})]

    def test_no_screen_open_asks_the_agent_to_open_it(self):
        texts = self.connect(0)
        self.assertEqual(len(texts), 2)
        self.assertIn("http://127.0.0.1:47800/", texts[1])
        self.assertIn("アプリ内ブラウザー", texts[1])

    def test_an_open_screen_needs_nothing(self):
        self.assertEqual(len(self.connect(1)), 1)

    def test_the_connect_tool_says_so(self):
        tool = next(t for t in mcp.TOOLS if t["name"] == "ictrl_connect")
        self.assertIn("アプリ内ブラウザー", tool["description"])


if __name__ == "__main__":
    unittest.main()
