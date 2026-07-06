import json
import unittest

import block_devices


class BlockDevicesTest(unittest.TestCase):
    def test_list_disks_filters_boot_media_and_enriches_kind(self) -> None:
        payload = {
            "blockdevices": [
                {"name": "loop0", "type": "disk"},
                {
                    "name": "sda",
                    "path": "/dev/sda",
                    "size": "1000",
                    "model": "Drive",
                    "serial": "S1",
                    "vendor": "Vendor",
                    "tran": "sata",
                    "type": "disk",
                    "rota": True,
                    "hotplug": False,
                    "children": [],
                },
                {
                    "name": "sdb",
                    "path": "/dev/sdb",
                    "size": "1000",
                    "type": "disk",
                    "label": "DRVPROOF",
                },
            ]
        }

        def runner(args):
            return 0, json.dumps(payload), ""

        disks = block_devices.list_disks(runner)

        self.assertEqual(len(disks), 1)
        self.assertEqual(disks[0]["name"], "sda")
        self.assertEqual(disks[0]["kind"], "HDD")
        self.assertTrue(disks[0]["internal"])

    def test_find_disk_children_returns_nested_partitions(self) -> None:
        payload = {
            "blockdevices": [
                {
                    "name": "sda",
                    "path": "/dev/sda",
                    "type": "disk",
                    "children": [
                        {"name": "sda1", "path": "/dev/sda1", "type": "part", "mountpoints": ["/mnt"]}
                    ],
                }
            ]
        }

        def runner(args):
            return 0, json.dumps(payload), ""

        children = block_devices.find_disk_children("sda", runner)

        self.assertEqual(children[0]["name"], "sda1")


if __name__ == "__main__":
    unittest.main()
