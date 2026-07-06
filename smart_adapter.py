from __future__ import annotations

import json
from typing import Any, Callable

CommandRunner = Callable[..., tuple[int, str, str]]

SMARTCTL_MISSING_MESSAGE = "smartctl ist nicht installiert. Unter Debian/Ubuntu: sudo apt install smartmontools"


def read_json_command(run_command: CommandRunner, args: list[str], timeout: int = 30) -> dict[str, Any]:
    rc, out, err = run_command(args, timeout=timeout)
    if out.strip():
        try:
            payload = json.loads(out)
        except json.JSONDecodeError as exc:
            payload = None
        if isinstance(payload, dict):
            payload["_command_rc"] = rc
            return payload
    raise RuntimeError(err.strip() or out.strip() or f"Command failed: {' '.join(args)}")


def smartctl_available(run_command: CommandRunner) -> bool:
    rc, _, _ = run_command(["smartctl", "--version"])
    return rc == 0


def smart_payload(run_command: CommandRunner, device_path: str, timeout: int = 40) -> dict[str, Any]:
    if not smartctl_available(run_command):
        raise RuntimeError("smartctl ist nicht installiert")
    return read_json_command(run_command, ["smartctl", "-a", "-j", device_path], timeout=timeout)


def get_smart_data(run_command: CommandRunner, device_path: str, timeout: int = 40) -> dict[str, Any]:
    if not smartctl_available(run_command):
        return {"available": False, "error": SMARTCTL_MISSING_MESSAGE}

    try:
        payload = smart_payload(run_command, device_path, timeout=timeout)
        messages = payload.get("smartctl", {}).get("messages", [])
        errors = [msg.get("string") for msg in messages if msg.get("severity") == "error" and msg.get("string")]
        has_metrics = bool(
            payload.get("smart_status")
            or payload.get("ata_smart_attributes")
            or payload.get("nvme_smart_health_information_log")
        )
        if errors:
            return {
                "available": True,
                "payload": payload,
                "warning" if has_metrics else "error": " | ".join(errors),
            }
        return {"available": True, "payload": payload}
    except Exception as exc:
        return {"available": False, "error": str(exc)}


def selftest_capabilities(run_command: CommandRunner, device_path: str) -> dict[str, Any]:
    payload = smart_payload(run_command, device_path)
    status = payload.get("ata_smart_data", {}).get("self_test", {}).get("status", {})
    capabilities = payload.get("ata_smart_data", {}).get("capabilities", {})
    polling = payload.get("ata_smart_data", {}).get("self_test", {}).get("polling_minutes", {})
    return {
        "supported": bool(capabilities.get("self_tests_supported") or polling),
        "status": status,
        "polling_minutes": polling,
        "payload": payload,
    }


def selftest_status_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    status = payload.get("ata_smart_data", {}).get("self_test", {}).get("status", {})
    value = status.get("value")
    remaining = status.get("remaining_percent")
    string = status.get("string") or ""
    running = isinstance(remaining, int) and 0 < remaining < 100
    abort_supported = bool(payload.get("ata_smart_data", {}).get("capabilities", {}).get("self_tests_supported"))
    return {
        "running": running,
        "remaining_percent": remaining,
        "status_value": value,
        "status_text": string,
        "abort_supported": abort_supported,
    }


def external_selftest_status(run_command: CommandRunner, device_path: str) -> dict[str, Any]:
    try:
        payload = smart_payload(run_command, device_path)
        return selftest_status_from_payload(payload)
    except Exception as exc:
        return {
            "running": False,
            "remaining_percent": None,
            "status_value": None,
            "status_text": str(exc),
            "abort_supported": False,
        }


def abort_selftest(run_command: CommandRunner, device_path: str) -> dict[str, Any]:
    rc, out, err = run_command(["smartctl", "-X", device_path], timeout=30)
    if rc != 0:
        raise RuntimeError(err.strip() or out.strip() or "Could not abort SMART self-test.")
    return external_selftest_status(run_command, device_path)
