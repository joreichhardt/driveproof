import tempfile
import unittest
from pathlib import Path

import app


class PostEraseSmartDetectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db_path = app.DB_PATH
        app.DB_PATH = Path(self.tmp.name) / "state.db"
        app.init_db()

    def tearDown(self) -> None:
        app.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def test_post_erase_smart_selftest_is_owned_by_app_job(self) -> None:
        job = app.TestJob(
            id="job1",
            device="sda",
            mode="secure_erase_ata",
            created_at=app.utc_now_iso(),
            status="running",
            options={
                "post_test_mode": "smart_extended",
                "active_post_test_mode": "smart_extended",
            },
        )
        app.save_job(job)

        self.assertTrue(app.has_active_app_smart_selftest("sda"))

    def test_pending_post_erase_smart_selftest_is_not_owned_before_it_starts(self) -> None:
        job = app.TestJob(
            id="job2",
            device="sdb",
            mode="secure_erase_ata",
            created_at=app.utc_now_iso(),
            status="running",
            options={"post_test_mode": "smart_extended"},
        )
        app.save_job(job)

        self.assertFalse(app.has_active_app_smart_selftest("sdb"))


if __name__ == "__main__":
    unittest.main()
