import unittest

import report_model


class ReportModelTest(unittest.TestCase):
    def test_classifies_erase_report_from_mode(self) -> None:
        report = {"source_job": {"mode": "nvme_format"}, "test": {}}

        self.assertEqual(report_model.classify_report_kind(report), "erase")

    def test_report_runs_count_devices_and_documents(self) -> None:
        reports = [
            {"report_id": "r1", "generated_at": "2024-01-01T00:00:00", "source_job": {"options": {"run_id": "run"}}, "device": {"path": "/dev/sda", "serial": "A"}, "test": {"type": "quick"}},
            {"report_id": "r2", "generated_at": "2024-01-01T00:01:00", "source_job": {"options": {"run_id": "run"}}, "device": {"path": "/dev/sda", "serial": "A"}, "test": {"type": "erase_zero", "erasure": {}}},
            {"report_id": "r3", "generated_at": "2024-01-01T00:02:00", "source_job": {"options": {"run_id": "run"}}, "device": {"path": "/dev/sdb", "serial": "B"}, "test": {"type": "quick"}},
        ]

        [run] = report_model.report_runs(reports)

        self.assertEqual(run["document_count"], 3)
        self.assertEqual(run["device_count"], 2)
        self.assertEqual(run["report_kinds"], ["erase", "test"])

    def test_cached_disk_health_uses_latest_matching_report(self) -> None:
        disk = {"path": "/dev/sda", "serial": "A"}
        reports = [
            {"report_id": "old", "generated_at": "2024-01-01T00:00:00", "device": {"path": "/dev/sda", "serial": "A"}, "health": {"score": 50, "grade": "OK"}},
            {"report_id": "new", "generated_at": "2024-01-02T00:00:00", "device": {"path": "/dev/sda", "serial": "A"}, "health": {"score": 90, "grade": "Good"}},
        ]

        health = report_model.cached_disk_health(disk, reports)

        self.assertEqual(health["score"], 90)
        self.assertEqual(health["report_id"], "new")


if __name__ == "__main__":
    unittest.main()
