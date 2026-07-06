import unittest

import app


class AppSmokeTest(unittest.TestCase):
    def test_index_renders(self) -> None:
        client = app.app.test_client()

        response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"DriveProof", response.data)

    def test_compliance_profiles_endpoint(self) -> None:
        client = app.app.test_client()

        response = client.get("/api/compliance-profiles")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("resale_basic", payload["profiles"])

    def test_disk_list_enriches_uncached_health_from_smart(self) -> None:
        client = app.app.test_client()
        original_list_disks = app.list_disks
        original_cached_disk_health = app.cached_disk_health
        original_get_smart_data = app.get_smart_data

        try:
            app.list_disks = lambda: [
                {
                    "name": "sda",
                    "path": "/dev/sda",
                    "kind": "SSD",
                    "transport": "sata",
                    "size_bytes": 1024,
                    "internal": False,
                }
            ]
            app.cached_disk_health = lambda disk: {"available": False, "score": None, "grade": "SMART n/a"}
            app.get_smart_data = lambda path: {
                "available": True,
                "payload": {"smart_status": {"passed": True}, "ata_smart_attributes": {"table": []}},
            }

            response = client.get("/api/disks")

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertEqual(payload["disks"][0]["health"]["score"], 100)
            self.assertEqual(payload["disks"][0]["health"]["grade"], "Excellent")
        finally:
            app.list_disks = original_list_disks
            app.cached_disk_health = original_cached_disk_health
            app.get_smart_data = original_get_smart_data


if __name__ == "__main__":
    unittest.main()
