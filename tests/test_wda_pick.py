"""Which installed WebDriverAgent runner utsushite starts."""
import unittest

from utsushite.wda import pick_runner

RUNNER = {"CFBundleExecutable": "WebDriverAgentRunner-Runner"}


class PickRunnerTests(unittest.TestCase):
    def test_none_installed(self):
        self.assertIsNone(pick_runner({"com.example.app": {"CFBundleExecutable": "App"}}))

    def test_another_tools_runner_is_used(self):
        self.assertEqual(pick_runner({"com.facebook.WebDriverAgentRunner.xctrunner": RUNNER}),
                         "com.facebook.WebDriverAgentRunner.xctrunner")

    def test_utsushites_own_comes_first(self):
        apps = {"com.facebook.WebDriverAgentRunner.xctrunner": RUNNER,
                "com.utsushite.wda.ABCDE12345": RUNNER}            # a signing tool's suffix
        self.assertEqual(pick_runner(apps), "com.utsushite.wda.ABCDE12345")

    def test_own_by_display_name(self):
        apps = {"a.runner": RUNNER, "z.reused.app": {**RUNNER, "CFBundleDisplayName": "utsushite WDA"}}
        self.assertEqual(pick_runner(apps), "z.reused.app")

    def test_the_ipa_from_the_ictrl_days_is_still_own(self):
        apps = {"a.runner": RUNNER, "com.ictrl.wda.ABCDE12345": RUNNER}
        self.assertEqual(pick_runner(apps), "com.ictrl.wda.ABCDE12345")

    def test_choice_is_stable(self):
        apps = {"b.runner": RUNNER, "a.runner": RUNNER}
        self.assertEqual(pick_runner(apps), "a.runner")


if __name__ == "__main__":
    unittest.main()
