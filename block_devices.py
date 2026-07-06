from __future__ import annotations

import json
from typing import Any, Callable

CommandRunner = Callable[[list[str]], tuple[int, str, str]]


def classify_disk_kind(disk: dict[str, Any]) -> str:
    transport = (disk.get("transport") or "").lower()
    if transport == "nvme":
        return "NVMe"
    if disk.get("rotational"):
        return "HDD"
    return "SSD"


def _flatten(node: dict[str, Any]) -> list[dict[str, Any]]:
    result = [node]
    for child in node.get("children") or []:
        result.extend(_flatten(child))
    return result


def _is_boot_media(node: dict[str, Any]) -> bool:
    nodes = _flatten(node)
    labels = {(entry.get("label") or "").strip() for entry in nodes}
    mountpoints = {(entry.get("mountpoint") or "").strip() for entry in nodes}
    fstypes = {(entry.get("fstype") or "").strip().lower() for entry in nodes}
    return "DRVPROOF" in labels or "/iso" in mountpoints or ("iso9660" in fstypes and "nixos-24.11-x86_64" in labels)


def _parse_lsblk_json(out: str) -> dict[str, Any]:
    try:
        payload = json.loads(out)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid lsblk JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Invalid lsblk JSON: expected object")
    return payload


def _size_bytes(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Invalid lsblk size: {value!r}") from exc


def list_disks(run_command: CommandRunner) -> list[dict[str, Any]]:
    rc, out, err = run_command(
        [
            "lsblk",
            "-b",
            "-J",
            "-o",
            "NAME,PATH,SIZE,MODEL,SERIAL,TRAN,VENDOR,TYPE,ROTA,HOTPLUG,MOUNTPOINT,FSTYPE,LABEL",
        ]
    )
    if rc != 0:
        raise RuntimeError(err.strip() or "lsblk failed")

    payload = _parse_lsblk_json(out)
    disks: list[dict[str, Any]] = []
    for item in payload.get("blockdevices", []):
        if item.get("type") != "disk":
            continue
        name = item.get("name") or ""
        if name.startswith(("loop", "zram", "ram", "fd")):
            continue
        if _is_boot_media(item):
            continue
        disks.append(
            {
                "name": name,
                "path": item.get("path"),
                "size_bytes": _size_bytes(item.get("size")),
                "model": (item.get("model") or "").strip(),
                "serial": (item.get("serial") or "").strip(),
                "vendor": (item.get("vendor") or "").strip(),
                "transport": item.get("tran") or "unknown",
                "rotational": bool(item.get("rota")),
                "hotplug": bool(item.get("hotplug")),
                "mountpoint": item.get("mountpoint"),
            }
        )
    for disk in disks:
        disk["kind"] = classify_disk_kind(disk)
        disk["internal"] = not (disk.get("hotplug") or disk.get("transport") == "usb")
    return disks


def get_block_tree(run_command: CommandRunner) -> list[dict[str, Any]]:
    rc, out, err = run_command(["lsblk", "-J", "-o", "NAME,PATH,TYPE,MOUNTPOINTS"])
    if rc != 0:
        raise RuntimeError(err.strip() or "lsblk failed")
    return _parse_lsblk_json(out).get("blockdevices", [])


def find_disk_children(device_name: str, run_command: CommandRunner) -> list[dict[str, Any]]:
    def walk(node: dict[str, Any], parent_disk: str | None = None) -> list[dict[str, Any]]:
        current_disk = parent_disk
        if node.get("type") == "disk":
            current_disk = node.get("name")
        found: list[dict[str, Any]] = []
        if current_disk == device_name and node.get("type") != "disk":
            found.append(node)
        for child in node.get("children", []) or []:
            found.extend(walk(child, current_disk))
        return found

    items: list[dict[str, Any]] = []
    for top in get_block_tree(run_command):
        items.extend(walk(top))
    return items
