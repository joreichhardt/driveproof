from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

ERASE_MODES = {
    "erase_zero",
    "secure_erase_ata",
    "secure_erase_ata_enhanced",
    "nvme_format",
    "nvme_sanitize_crypto",
    "nvme_sanitize_block",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify_filename_part(value: str, fallback: str = "unknown") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", (value or "").strip())
    cleaned = cleaned.strip("._-")
    return cleaned[:80] or fallback


def classify_report_kind(report: dict[str, Any]) -> str:
    test = report.get("test") or {}
    mode = (report.get("source_job") or {}).get("mode") or test.get("type") or ""
    if test.get("erasure") or mode in ERASE_MODES:
        return "erase"
    return "test"


def report_kind_label(kind: str) -> str:
    return "Erase Report" if kind == "erase" else "Test Report"


def report_filename(report: dict[str, Any]) -> str:
    generated_at = report.get("generated_at") or utc_now_iso()
    try:
        stamp = datetime.fromisoformat(generated_at).strftime("%Y%m%d-%H%M%S")
    except ValueError:
        stamp = generated_at.replace(":", "-")
    device = report.get("device") or {}
    serial = device.get("serial") or device.get("wwn") or device.get("name") or "unknown"
    model = device.get("model") or device.get("vendor") or device.get("kind") or "disk"
    report_id = report.get("report_id") or uuid.uuid4().hex[:12]
    report_kind = report.get("report_kind") or classify_report_kind(report)
    return f"{stamp}_{slugify_filename_part(report_kind)}_{slugify_filename_part(model)}_{slugify_filename_part(serial)}_{report_id}.json"


def device_folder_name(report: dict[str, Any]) -> str:
    device = report.get("device") or {}
    serial = device.get("serial") or device.get("wwn") or device.get("name") or "unknown"
    model = device.get("model") or device.get("vendor") or device.get("kind") or "disk"
    return f"{slugify_filename_part(model)}_{slugify_filename_part(serial)}"


def report_run_id(report: dict[str, Any]) -> str:
    source = report.get("source_job") or {}
    options = source.get("options") or {}
    return str(options.get("run_id") or options.get("batch_id") or source.get("id") or report.get("report_id"))


def report_device_key(report: dict[str, Any]) -> str:
    device = report.get("device") or {}
    serial = str(device.get("serial") or "").strip()
    model = str(device.get("model") or "").strip()
    path = str(device.get("path") or "").strip()
    return "|".join(part for part in (serial, model, path) if part) or "unknown"


def disk_matches_report(disk: dict[str, Any], report: dict[str, Any]) -> bool:
    device = report.get("device") or {}
    disk_serial = str(disk.get("serial") or "").strip()
    report_serial = str(device.get("serial") or "").strip()
    if disk_serial and report_serial and disk_serial == report_serial:
        return True
    return str(disk.get("path") or "") == str(device.get("path") or "")


def sorted_reports_for_run(reports: list[dict[str, Any]], run_id: str) -> list[dict[str, Any]]:
    matching = [report for report in reports if report_run_id(report) == run_id]
    return sorted(
        matching,
        key=lambda report: (
            report_device_key(report),
            0 if (report.get("report_kind") or classify_report_kind(report)) == "erase" else 1,
            report.get("generated_at") or "",
        ),
    )


def cached_disk_health(disk: dict[str, Any], reports: list[dict[str, Any]]) -> dict[str, Any]:
    matching = [report for report in reports if disk_matches_report(disk, report)]
    if not matching:
        return {
            "score": None,
            "grade": "SMART n/a",
            "summary": "Open the drive or run a test to collect SMART health.",
            "available": False,
            "source": "none",
        }
    latest = sorted(matching, key=lambda report: report.get("generated_at") or "", reverse=True)[0]
    health = latest.get("health") or {}
    return {
        "score": health.get("score"),
        "grade": health.get("grade") or "Unknown",
        "summary": health.get("summary") or "Cached from latest report",
        "available": health.get("score") is not None,
        "source": "latest_report",
        "report_id": latest.get("report_id"),
        "generated_at": latest.get("generated_at"),
    }


def report_runs(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for report in reports:
        groups.setdefault(report_run_id(report), []).append(report)
    runs = []
    for run_id, group in groups.items():
        sorted_group = sorted_reports_for_run(reports, run_id)
        kinds = sorted({report.get("report_kind") or classify_report_kind(report) for report in sorted_group})
        devices = sorted({(report.get("device") or {}).get("path") or "unknown" for report in sorted_group})
        device_keys = {report_device_key(report) for report in sorted_group}
        runs.append(
            {
                "run_id": run_id,
                "generated_at": sorted_group[0].get("generated_at"),
                "completed_at": sorted_group[-1].get("generated_at"),
                "count": len(sorted_group),
                "document_count": len(sorted_group),
                "report_ids": [report["report_id"] for report in sorted_group],
                "report_kinds": kinds,
                "devices": devices,
                "device_count": len(device_keys),
            }
        )
    return sorted(runs, key=lambda run: run.get("completed_at") or "", reverse=True)
