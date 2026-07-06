import unittest

import drive_modes


class DriveModesTest(unittest.TestCase):
    def test_hdd_recommendations_keep_existing_order(self) -> None:
        disk = {"transport": "sata", "rotational": True}

        self.assertEqual(
            [mode["id"] for mode in drive_modes.recommended_modes_for_disk(disk)],
            ["quick", "deep_sample", "smart_extended", "full"],
        )

    def test_nvme_recommendations_keep_existing_order(self) -> None:
        disk = {"transport": "nvme", "rotational": False}

        self.assertEqual(
            [mode["id"] for mode in drive_modes.recommended_modes_for_disk(disk)],
            ["quick", "smart_short", "smart_extended", "full"],
        )

    def test_destructive_modes_advertise_enterprise_policy_metadata(self) -> None:
        mode = drive_modes.get_mode("nvme_sanitize_crypto")

        self.assertTrue(mode.destructive)
        self.assertEqual(mode.enterprise_entitlement, "erase.nvme-sanitize")
        self.assertTrue(mode.requires_local_confirmation)
        self.assertFalse(mode.remote_allowed)

    def test_bsi_modes_are_destructive_local_only_erase_modes(self) -> None:
        modes = {mode["id"]: mode for mode in drive_modes.list_modes(category="erase")}

        self.assertEqual(modes["bsi_erase"]["compliance_default"], "bsi_con6")
        self.assertEqual(modes["bsi_crypto_erase"]["compliance_default"], "bsi_con6_crypto")
        self.assertTrue(modes["bsi_erase"]["requires_local_confirmation"])
        self.assertFalse(modes["bsi_crypto_erase"]["remote_allowed"])

    def test_availability_rejects_wrong_drive_kind(self) -> None:
        disk = {"transport": "sata", "rotational": True}

        availability = drive_modes.availability("nvme_format", disk, category="erase")

        self.assertFalse(availability.available)
        self.assertIn("not HDD", availability.reason or "")


if __name__ == "__main__":
    unittest.main()
