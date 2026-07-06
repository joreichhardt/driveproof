import json
import unittest

import smart_adapter


class SmartAdapterTest(unittest.TestCase):
    def test_get_smart_data_returns_warning_when_metrics_exist_with_errors(self) -> None:
        payload = {
            "smartctl": {"messages": [{"severity": "error", "string": "USB bridge warning"}]},
            "smart_status": {"passed": True},
        }

        def runner(args, timeout=20):
            if args == ["smartctl", "--version"]:
                return 0, "smartctl 7.4", ""
            return 0, json.dumps(payload), ""

        result = smart_adapter.get_smart_data(runner, "/dev/sda")

        self.assertTrue(result["available"])
        self.assertEqual(result["warning"], "USB bridge warning")

    def test_get_smart_data_reports_missing_smartctl(self) -> None:
        def runner(args, timeout=20):
            return 127, "", "not found"

        result = smart_adapter.get_smart_data(runner, "/dev/sda")

        self.assertFalse(result["available"])
        self.assertIn("smartctl ist nicht installiert", result["error"])

    def test_selftest_status_from_payload(self) -> None:
        payload = {
            "ata_smart_data": {
                "self_test": {"status": {"value": 249, "remaining_percent": 70, "string": "Self-test in progress"}},
                "capabilities": {"self_tests_supported": True},
            }
        }

        status = smart_adapter.selftest_status_from_payload(payload)

        self.assertTrue(status["running"])
        self.assertEqual(status["remaining_percent"], 70)
        self.assertTrue(status["abort_supported"])


if __name__ == "__main__":
    unittest.main()
