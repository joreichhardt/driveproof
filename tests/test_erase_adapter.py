import json
import unittest

import erase_adapter


class EraseAdapterTest(unittest.TestCase):
    def test_secure_erase_capabilities_detects_basic_and_enhanced(self) -> None:
        def runner(args, timeout=20):
            return 0, "Security:\n\tsupported\n\tsupported: enhanced erase\n", ""

        caps = erase_adapter.secure_erase_capabilities(runner, {"path": "/dev/sda"})

        self.assertTrue(caps["supported"])
        self.assertTrue(caps["basic_supported"])
        self.assertTrue(caps["enhanced_supported"])
        self.assertEqual(caps["methods"], ["basic", "enhanced"])

    def test_nvme_erase_capabilities_reads_sanicap(self) -> None:
        def runner(args, timeout=20):
            return 0, json.dumps({"sanicap": "0x3"}), ""

        caps = erase_adapter.nvme_erase_capabilities(
            runner,
            lambda tool: "/run/current-system/sw/bin/nvme",
            lambda value: int(str(value), 0),
            {"path": "/dev/nvme0n1", "kind": "NVMe"},
        )

        self.assertTrue(caps["supported"])
        self.assertTrue(caps["sanitize_crypto_supported"])
        self.assertTrue(caps["sanitize_block_supported"])
        self.assertIn("sanitize_crypto", caps["methods"])

    def test_erase_allowed_blocks_internal_drive_without_override(self) -> None:
        with self.assertRaises(RuntimeError):
            erase_adapter.erase_allowed({"transport": "sata", "hotplug": False}, allow_internal=False)

    def test_sanitize_progress_completes_on_max_progress(self) -> None:
        def parse(value):
            return int(str(value), 0) if value is not None else None

        progress, complete, status = erase_adapter.sanitize_progress(parse, {"sprog": "65535"})

        self.assertEqual(progress, 1.0)
        self.assertTrue(complete)
        self.assertEqual(status, "sanitize in progress")


if __name__ == "__main__":
    unittest.main()
