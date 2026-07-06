import tempfile
import unittest
from pathlib import Path

import app


class JobRunGroupingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db_path = app.DB_PATH
        self.original_report_dir = app.REPORT_DIR
        self.original_jobs = app.jobs
        self.original_get_disk = app.get_disk
        self.original_get_smart_data = app.get_smart_data
        self.original_compute_health = app.compute_health
        self.original_execute_job_mode = app.execute_job_mode
        self.original_auto_export_report_pdf = app.auto_export_report_pdf
        self.original_signing_key_path = app.SIGNING_KEY_PATH

        root = Path(self.tmp.name)
        app.DB_PATH = root / "state.db"
        app.REPORT_DIR = root / "reports"
        app.SIGNING_KEY_PATH = root / "driveproof-signing.key"
        app.REPORT_DIR.mkdir(parents=True, exist_ok=True)
        app.jobs = {}
        app.init_db()

    def tearDown(self) -> None:
        app.DB_PATH = self.original_db_path
        app.REPORT_DIR = self.original_report_dir
        app.SIGNING_KEY_PATH = self.original_signing_key_path
        app.jobs = self.original_jobs
        app.get_disk = self.original_get_disk
        app.get_smart_data = self.original_get_smart_data
        app.compute_health = self.original_compute_health
        app.execute_job_mode = self.original_execute_job_mode
        app.auto_export_report_pdf = self.original_auto_export_report_pdf
        self.tmp.cleanup()

    def test_post_erase_test_becomes_visible_child_job(self) -> None:
        disk = {
            "name": "sda",
            "path": "/dev/sda",
            "model": "TestDisk",
            "serial": "SERIAL-A",
            "size_bytes": 1024 * 1024,
            "transport": "sata",
            "kind": "HDD",
            "internal": True,
        }
        app.get_disk = lambda device: disk
        app.get_smart_data = lambda path: {"available": True, "payload": {"smart_status": {"passed": True}}}
        app.compute_health = lambda disk_arg, smart: {"score": 100, "grade": "Excellent", "summary": "Resale ready", "notes": []}
        app.auto_export_report_pdf = lambda report_id: {"status": "skipped", "report_id": report_id}

        def fake_execute(job, disk_arg, mode):
            if mode == "erase_zero":
                return {
                    "type": "erase_zero",
                    "label": "Single-pass zero erase",
                    "credibility_level": "high",
                    "buyer_claim": "Erased.",
                    "erasure": {"verification_result": {"all_match": True}},
                }
            return {
                "type": "quick",
                "label": "Quick read test",
                "credibility_level": "high",
                "buyer_claim": "Tested.",
            }

        app.execute_job_mode = fake_execute
        parent = app.TestJob(
            id="erase-parent",
            device="sda",
            mode="erase_zero",
            created_at=app.utc_now_iso(),
            options={"run_id": "run-1", "post_test_mode": "quick", "allow_internal_erase": True},
        )
        app.save_job(parent)

        app.run_test_job(parent.id)

        parent = app.get_job(parent.id)
        self.assertEqual(parent.status, "done")
        self.assertIn("post_test_job_id", parent.result)
        child = app.get_job(parent.result["post_test_job_id"])
        self.assertIsNotNone(child)
        self.assertEqual(child.mode, "quick")
        self.assertEqual(child.status, "done")
        self.assertEqual(child.options["parent_job_id"], parent.id)
        self.assertEqual(child.options["run_id"], "run-1")
        self.assertEqual(len(app.reports_for_run("run-1")), 2)

    def test_report_runs_count_unique_devices_separately_from_documents(self) -> None:
        for index in range(4):
            disk = {
                "name": f"sd{chr(ord('a') + index)}",
                "path": f"/dev/sd{chr(ord('a') + index)}",
                "model": "BatchDisk",
                "serial": f"SERIAL-{index}",
                "size_bytes": 1024 * 1024,
                "transport": "sata",
                "kind": "HDD",
                "internal": True,
            }
            job = app.TestJob(
                id=f"job-{index}",
                device=disk["name"],
                mode="quick",
                created_at=app.utc_now_iso(),
                options={"run_id": "batch-run"},
            )
            report = app.build_report_payload(
                disk,
                {"available": True, "payload": {"smart_status": {"passed": True}}},
                {"score": 100, "grade": "Excellent", "summary": "Resale ready", "notes": []},
                {"type": "quick", "label": "Quick read test", "credibility_level": "high", "buyer_claim": "Tested."},
                source_job=job,
            )
            app.save_report(report)

        first_disk = {
            "name": "sda",
            "path": "/dev/sda",
            "model": "BatchDisk",
            "serial": "SERIAL-0",
            "size_bytes": 1024 * 1024,
            "transport": "sata",
            "kind": "HDD",
            "internal": True,
        }
        erase_job = app.TestJob(
            id="erase-0",
            device="sda",
            mode="erase_zero",
            created_at=app.utc_now_iso(),
            options={"run_id": "batch-run"},
        )
        erase_report = app.build_report_payload(
            first_disk,
            {"available": True, "payload": {"smart_status": {"passed": True}}},
            {"score": 100, "grade": "Excellent", "summary": "Resale ready", "notes": []},
            {
                "type": "erase_zero",
                "label": "Single-pass zero erase",
                "credibility_level": "high",
                "buyer_claim": "Erased.",
                "erasure": {"verification_result": {"all_match": True}},
            },
            source_job=erase_job,
        )
        app.save_report(erase_report)

        [run] = [item for item in app.report_runs() if item["run_id"] == "batch-run"]
        self.assertEqual(run["device_count"], 4)
        self.assertEqual(run["document_count"], 5)


if __name__ == "__main__":
    unittest.main()
