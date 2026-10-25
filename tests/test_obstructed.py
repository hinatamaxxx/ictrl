"""tap_element scrolls an element into view first when a tap on its centre
would not reach it (Settings' 一般 lay under iOS 27's floating
search field, in the bottom strip where a touch starts the home gesture)."""
import unittest

from utsushite.server import _obstructed


class ObstructedTests(unittest.TestCase):
    def test_bottom_strip(self):
        row = {"type": "Button", "label": "一般", "x": 0.5, "y": 0.974, "w": 0.92, "h": 0.05}
        self.assertTrue(_obstructed(row, [row]))

    def test_under_a_floating_search_field(self):
        search = {"type": "SearchField", "label": "検索", "x": 0.5, "y": 0.94, "w": 0.9, "h": 0.05}
        row = {"type": "Button", "label": "デベロッパ", "x": 0.5, "y": 0.93, "w": 0.92, "h": 0.05}
        self.assertTrue(_obstructed(row, [row, search]))

    def test_a_whole_screen_toolbar_container_hides_nothing(self):
        toolbar = {"type": "Toolbar", "label": "ツールバー", "x": 0.5, "y": 0.5, "w": 1.0, "h": 1.0}
        row = {"type": "Button", "label": "一般", "x": 0.5, "y": 0.5, "w": 0.92, "h": 0.07}
        self.assertFalse(_obstructed(row, [row, toolbar]))

    def test_visible_row_and_the_bar_itself(self):
        search = {"type": "SearchField", "label": "検索", "x": 0.5, "y": 0.94, "w": 0.9, "h": 0.05}
        row = {"type": "Button", "label": "アプリ", "x": 0.5, "y": 0.6, "w": 0.92, "h": 0.05}
        self.assertFalse(_obstructed(row, [row, search]))
        self.assertFalse(_obstructed(search, [row, search]))


if __name__ == "__main__":
    unittest.main()
