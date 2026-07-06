import unittest

import app


class ModesApiTest(unittest.TestCase):
    def test_lists_mode_metadata_without_device_probe(self) -> None:
        client = app.app.test_client()

        response = client.get("/api/modes?category=erase")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        modes = {mode["id"]: mode for mode in payload["modes"]}
        self.assertIn("nvme_sanitize_crypto", modes)
        self.assertEqual(modes["nvme_sanitize_crypto"]["enterprise_entitlement"], "erase.nvme-sanitize")
        self.assertFalse(modes["nvme_sanitize_crypto"]["remote_allowed"])
        self.assertTrue(modes["nvme_sanitize_crypto"]["availability"]["available"])

    def test_rejects_unknown_category(self) -> None:
        client = app.app.test_client()

        response = client.get("/api/modes?category=other")

        self.assertEqual(response.status_code, 400)

    def test_capabilities_advertise_local_only_destructive_policy(self) -> None:
        client = app.app.test_client()

        response = client.get("/api/capabilities")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload["enterprise_enabled"])
        self.assertFalse(payload["destructive_policy"]["remote_allowed"])
        self.assertIn("modes", payload)


if __name__ == "__main__":
    unittest.main()
