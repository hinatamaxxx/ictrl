"""Which installed WebDriverAgent runner iCtrl starts."""
import unittest

from ictrl.wda import pick_runner

RUNNER = {"CFBundleExecutable": "WebDriverAgentRunner-Runner"}


class PickRunnerTests(unittest.TestCase):
    def test_none_installed(self):
        self.assertIsNone(pick_runner({"com.example.app": {"CFBundleExecutable": "App"}}))

    def test_another_tools_runner_is_used(self):
        self.assertEqual(pick_runner({"com.facebook.WebDriverAgentRunner.xctrunner": RUNNER}),
                         "com.facebook.WebDriverAgentRunner.xctrunner")

    def test_ictrls_own_comes_first(self):
        apps = {"com.facebook.WebDriverAgentRunner.xctrunner": RUNNER,
                "com.ictrl.wda.ABCDE12345": RUNNER}            # a signing tool's suffix
        self.assertEqual(pick_runner(apps), "com.ictrl.wda.ABCDE12345")

    def test_own_by_display_name(self):
        apps = {"a.runner": RUNNER, "z.reused.app": {**RUNNER, "CFBundleDisplayName": "iCtrl WDA"}}
        self.assertEqual(pick_runner(apps), "z.reused.app")

    def test_choice_is_stable(self):
        apps = {"b.runner": RUNNER, "a.runner": RUNNER}
        self.assertEqual(pick_runner(apps), "a.runner")


if __name__ == "__main__":
    unittest.main()
