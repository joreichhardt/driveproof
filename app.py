from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

BASE_DIR = Path(__file__).resolve().parent
REPORT_DIR = BASE_DIR / "reports"
DB_PATH = BASE_DIR / "state.db"
LEGAL_DOCS = {
    "license": BASE_DIR / "LICENSE",
    "third-party": BASE_DIR / "THIRD_PARTY_LICENSES.md",
    "commercial": BASE_DIR / "COMMERCIAL_SERVICES.md",
}
REPORT_DIR.mkdir(exist_ok=True)

app = Flask(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db_connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                device TEXT NOT NULL,
                mode TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL,
                progress REAL NOT NULL DEFAULT 0,
                current_step TEXT NOT NULL DEFAULT '',
                messages_json TEXT NOT NULL DEFAULT '[]',
                options_json TEXT NOT NULL DEFAULT '{}',
                result_json TEXT,
                error TEXT
            );

            CREATE TABLE IF NOT EXISTS device_status (
                device TEXT PRIMARY KEY,
                disk_json TEXT,
                selftest_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'smartctl'
            );
            """
        )
        columns = [row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()]
        if "options_json" not in columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN options_json TEXT NOT NULL DEFAULT '{}'")
        conn.execute(
            """
            UPDATE jobs
            SET status = 'interrupted',
                current_step = CASE
                    WHEN current_step = '' THEN 'App neu gestartet'
                    ELSE current_step || ' (App neu gestartet)'
                END,
                error = COALESCE(error, 'Job wurde durch App-Neustart unterbrochen.')
            WHERE status IN ('queued', 'running')
            """
        )


init_db()


def run_command(args: list[str], timeout: int = 20) -> tuple[int, str, str]:
    import subprocess

    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", f"Command not found: {args[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"Command timed out: {' '.join(args)}"


def list_disks() -> list[dict[str, Any]]:
    rc, out, err = run_command(
        [
            "lsblk",
            "-b",
            "-J",
            "-o",
            "NAME,PATH,SIZE,MODEL,SERIAL,TRAN,VENDOR,TYPE,ROTA,HOTPLUG,MOUNTPOINT",
        ]
    )
    if rc != 0:
        raise RuntimeError(err.strip() or "lsblk failed")

    payload = json.loads(out)
    disks: list[dict[str, Any]] = []
    for item in payload.get("blockdevices", []):
        if item.get("type") != "disk":
            continue
        name = item.get("name") or ""
        if name.startswith(("loop", "zram", "ram")):
            continue
        disks.append(
            {
                "name": name,
                "path": item.get("path"),
                "size_bytes": int(item.get("size") or 0),
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


def get_disk(device_name: str) -> dict[str, Any]:
    for disk in list_disks():
        if disk["name"] == device_name or disk["path"] == device_name:
            return disk
    raise FileNotFoundError(f"Device not found: {device_name}")


def classify_disk_kind(disk: dict[str, Any]) -> str:
    transport = (disk.get("transport") or "").lower()
    if transport == "nvme":
        return "NVMe"
    if disk.get("rotational"):
        return "HDD"
    return "SSD"


MODE_METADATA = {
    "quick": {
        "label": "Schnelltest",
        "hint": "Kurzer Stichproben-Lesetest fuer Vorsortierung.",
        "destructive": False,
    },
    "deep_sample": {
        "label": "Tiefer Lesetest",
        "hint": "Verteilter Lesetest ueber die Platte. Fuer HDDs sinnvoller als fuer SSD/NVMe.",
        "destructive": False,
    },
    "smart_short": {
        "label": "SMART Kurztest",
        "hint": "Kurzer Laufwerks-Selbsttest. Gut fuer SSD/NVMe und schnelle Vorpruefung.",
        "destructive": False,
    },
    "smart_extended": {
        "label": "SMART Extended",
        "hint": "Echter SMART Extended Self-Test des Laufwerks. Fuer Verkauf glaubwuerdig.",
        "destructive": False,
    },
    "full": {
        "label": "Vollscan",
        "hint": "Kompletter Lesetest. Dauert lange und ist fuer den Verkauf die staerkste Lesetest-Aussage.",
        "destructive": False,
    },
}


def recommended_modes_for_disk(disk: dict[str, Any]) -> list[dict[str, Any]]:
    kind = classify_disk_kind(disk)
    if kind == "HDD":
        order = ["quick", "deep_sample", "smart_extended", "full"]
    else:
        order = ["quick", "smart_short", "smart_extended", "full"]
    return [{"id": mode, **MODE_METADATA[mode]} for mode in order]


def get_block_tree() -> list[dict[str, Any]]:
    rc, out, err = run_command(["lsblk", "-J", "-o", "NAME,PATH,TYPE,MOUNTPOINTS"])
    if rc != 0:
        raise RuntimeError(err.strip() or "lsblk failed")
    return json.loads(out).get("blockdevices", [])


def find_disk_children(device_name: str) -> list[dict[str, Any]]:
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
    for top in get_block_tree():
        items.extend(walk(top))
    return items


def safe_remove_disk(device_name: str) -> dict[str, Any]:
    disk = get_disk(device_name)
    device_path = disk["path"]
    actions: list[str] = []

    actions.extend(unmount_disk_children(disk["name"]))
    rc, out, err = run_command(["udisksctl", "power-off", "-b", device_path], timeout=30)
    if rc == 0:
        actions.append(f"Powered off {device_path}")
        return {"device": device_path, "actions": actions}

    rc, out, err = run_command(["eject", device_path], timeout=30)
    if rc == 0:
        actions.append(f"Ejected {device_path}")
        return {"device": device_path, "actions": actions}

    raise RuntimeError(err.strip() or out.strip() or f"Safe remove failed for {device_path}")


def unmount_disk_children(device_name: str) -> list[str]:
    actions: list[str] = []
    for child in find_disk_children(device_name):
        mountpoints = child.get("mountpoints") or []
        mountpoints = [mp for mp in mountpoints if mp]
        if not mountpoints:
            continue

        child_path = child.get("path")
        rc, out, err = run_command(["udisksctl", "unmount", "-b", child_path], timeout=30)
        if rc != 0:
            rc, out, err = run_command(["umount", child_path], timeout=30)
        if rc != 0:
            raise RuntimeError(err.strip() or out.strip() or f"Unmount failed for {child_path}")
        actions.append(f"Unmounted {child_path}")
    return actions


def read_json_command(args: list[str], timeout: int = 30) -> dict[str, Any]:
    rc, out, err = run_command(args, timeout=timeout)
    if out.strip():
        try:
            payload = json.loads(out)
            payload["_command_rc"] = rc
            return payload
        except json.JSONDecodeError:
            pass
    raise RuntimeError(err.strip() or out.strip() or f"Command failed: {' '.join(args)}")


def smartctl_available() -> bool:
    rc, _, _ = run_command(["smartctl", "--version"])
    return rc == 0


def get_smart_data(device_path: str) -> dict[str, Any]:
    if not smartctl_available():
        return {
            "available": False,
            "error": "smartctl ist nicht installiert. Unter Debian/Ubuntu: sudo apt install smartmontools",
        }

    try:
        payload = read_json_command(["smartctl", "-a", "-j", device_path], timeout=40)
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
        return {
            "available": True,
            "payload": payload,
        }
    except Exception as exc:
        return {
            "available": True,
            "error": str(exc),
        }


def smart_payload(device_path: str) -> dict[str, Any]:
    if not smartctl_available():
        raise RuntimeError("smartctl ist nicht installiert")
    return read_json_command(["smartctl", "-a", "-j", device_path], timeout=40)


def smart_selftest_capabilities(device_path: str) -> dict[str, Any]:
    payload = smart_payload(device_path)
    status = payload.get("ata_smart_data", {}).get("self_test", {}).get("status", {})
    capabilities = payload.get("ata_smart_data", {}).get("capabilities", {})
    polling = payload.get("ata_smart_data", {}).get("self_test", {}).get("polling_minutes", {})
    return {
        "supported": bool(capabilities.get("self_tests_supported") or polling),
        "status": status,
        "polling_minutes": polling,
        "payload": payload,
    }


def smart_selftest_status_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
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


def get_external_selftest_status(device_path: str) -> dict[str, Any]:
    try:
        payload = smart_payload(device_path)
        return smart_selftest_status_from_payload(payload)
    except Exception as exc:
        return {
            "running": False,
            "remaining_percent": None,
            "status_value": None,
            "status_text": str(exc),
            "abort_supported": False,
        }


def sync_external_selftest_for_disk(disk: dict[str, Any]) -> dict[str, Any]:
    selftest = get_external_selftest_status(disk["path"])
    recovered_job = recover_internal_smart_job(disk["name"], selftest)
    selftest["source"] = "app" if recovered_job or has_active_internal_smart_job(disk["name"]) else "external"
    store_device_status(disk, selftest)
    return selftest


def sync_external_selftests(device: str | None = None) -> list[dict[str, Any]]:
    synced: list[dict[str, Any]] = []
    for disk in list_disks():
        if device and disk["name"] != device and disk["path"] != device:
            continue
        synced.append(sync_external_selftest_for_disk(disk))
    return synced


def external_selftest_jobs(device: str | None = None) -> list[dict[str, Any]]:
    with db_connect() as conn:
        if device:
            rows = conn.execute(
                "SELECT * FROM device_status WHERE device = ? ORDER BY updated_at DESC",
                (device,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM device_status ORDER BY updated_at DESC").fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        disk = json.loads(row["disk_json"]) if row["disk_json"] else {"name": row["device"], "path": f"/dev/{row['device']}"}
        selftest = json.loads(row["selftest_json"] or "{}")
        if not selftest.get("running"):
            continue
        if selftest.get("source") != "external":
            continue
        result.append(
            {
                "id": f"external-{row['device']}",
                "device": row["device"],
                "mode": "smart_extended_external",
                "created_at": row["updated_at"],
                "status": "running",
                "progress": (100 - int(selftest["remaining_percent"])) / 100 if isinstance(selftest.get("remaining_percent"), int) else 0.0,
                "current_step": f"Externer SMART Self-Test: {selftest.get('status_text') or 'laeuft'}",
                "messages": ["Von Laufwerk/Adapter gestarteter SMART Self-Test erkannt."],
                "result": {"selftest": selftest, "disk": disk},
                "error": None,
            }
        )
    return result


def external_selftest_job(job_id: str) -> dict[str, Any] | None:
    if not job_id.startswith("external-"):
        return None
    device = job_id.removeprefix("external-")
    sync_external_selftests(device=device)
    jobs = external_selftest_jobs(device=device)
    return jobs[0] if jobs else None


def has_active_job_for_device(device: str) -> bool:
    with db_connect() as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM jobs
            WHERE device = ?
              AND status IN ('queued', 'running')
            LIMIT 1
            """,
            (device,),
        ).fetchone()
    return bool(row)


def abort_smart_selftest(device_path: str) -> dict[str, Any]:
    rc, out, err = run_command(["smartctl", "-X", device_path], timeout=30)
    if rc != 0:
        raise RuntimeError(err.strip() or out.strip() or "SMART Self-Test konnte nicht abgebrochen werden.")
    return get_external_selftest_status(device_path)


def hdparm_identity(device_path: str) -> tuple[bool, str]:
    rc, out, err = run_command(["hdparm", "-I", device_path], timeout=40)
    text = out.strip() or err.strip()
    return rc == 0 and bool(out.strip()), text


def secure_erase_capabilities(disk: dict[str, Any]) -> dict[str, Any]:
    ok, text = hdparm_identity(disk["path"])
    if not ok:
        return {
            "supported": False,
            "method": None,
            "reason": "ATA Secure Erase nicht verfuegbar. USB-Dock oder Laufwerk reicht hdparm-Informationen nicht durch.",
        }

    lower = text.lower()
    if "security:" not in lower:
        return {
            "supported": False,
            "method": None,
            "reason": "ATA Security-Feature-Set nicht gefunden.",
        }

    enhanced = "supported: enhanced erase" in lower or "enhanced erase" in lower
    basic = "supported" in lower and "security" in lower
    if not (basic or enhanced):
        return {
            "supported": False,
            "method": None,
            "reason": "Laufwerk meldet kein ATA Secure Erase.",
        }

    return {
        "supported": True,
        "method": "enhanced" if enhanced else "basic",
        "reason": None,
    }


def format_bytes(num_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    value = float(num_bytes)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{num_bytes} B"


SMART_ATTRIBUTE_LABELS = {
    "Raw_Read_Error_Rate": "Lesefehler-Rate",
    "Reallocated_Sector_Ct": "Neu zugewiesene Sektoren",
    "Spin_Up_Time": "Anlaufzeit",
    "Start_Stop_Count": "Start/Stop-Zyklen",
    "Power_On_Hours": "Betriebsstunden",
    "Spin_Retry_Count": "Wiederholte Anlaufversuche",
    "Power_Cycle_Count": "Einschaltzyklen",
    "Current_Pending_Sector": "Ausstehende problematische Sektoren",
    "Offline_Uncorrectable": "Nicht korrigierbare Sektoren",
    "UDMA_CRC_Error_Count": "CRC-Uebertragungsfehler",
    "Temperature_Celsius": "Temperatur",
    "Media_Errors": "Medienfehler",
    "Unsafe_Shutdowns": "Unsichere Abschaltungen",
    "Percentage_Used": "Abnutzung",
}


def smart_label(name: str) -> str:
    return SMART_ATTRIBUTE_LABELS.get(name, name.replace("_", " "))


def parse_intish(value: Any) -> int | None:
    if value is None:
        return None
    token = str(value).strip().split()[0]
    try:
        return int(token)
    except ValueError:
        return None


def format_duration_hours(hours: int | None) -> str:
    if hours is None:
        return "n/a"
    days, rem_hours = divmod(hours, 24)
    if days:
        return f"{hours} h ({days} d {rem_hours} h)"
    return f"{hours} h"


def format_temperature(value: int | None) -> str:
    if value is None:
        return "n/a"
    fahrenheit = round((value * 9 / 5) + 32)
    return f"{value} °C / {fahrenheit} °F"


def smart_human_value(name: str, raw: Any) -> str:
    value = parse_intish(raw)
    if name == "Power_On_Hours":
        return format_duration_hours(value)
    if name == "Temperature_Celsius":
        if isinstance(raw, str):
            match = re.search(r"(-?\d+)", raw)
            if match:
                return format_temperature(int(match.group(1)))
        return format_temperature(value)
    if name == "Percentage_Used":
        return f"{value} %" if value is not None else "n/a"
    if name in {"Reallocated_Sector_Ct", "Current_Pending_Sector", "Offline_Uncorrectable", "UDMA_CRC_Error_Count", "Start_Stop_Count", "Power_Cycle_Count", "Media_Errors", "Unsafe_Shutdowns", "Spin_Retry_Count"}:
        return str(value) if value is not None else "n/a"
    if name == "Spin_Up_Time":
        return f"{value} ms" if value is not None else "n/a"
    return str(raw if raw not in (None, "") else "n/a")


def smart_severity(name: str, raw: Any) -> str:
    value = parse_intish(raw)
    if name in {"Reallocated_Sector_Ct", "Current_Pending_Sector", "Offline_Uncorrectable", "Media_Errors"}:
        if value is None or value == 0:
            return "ok"
        return "danger"
    if name in {"UDMA_CRC_Error_Count", "Unsafe_Shutdowns"}:
        if value is None or value == 0:
            return "ok"
        if value < 20:
            return "warn"
        return "danger"
    if name == "Temperature_Celsius":
        if value is None:
            return "neutral"
        if value >= 50:
            return "danger"
        if value >= 45:
            return "warn"
        return "ok"
    if name == "Percentage_Used":
        if value is None:
            return "neutral"
        if value >= 80:
            return "danger"
        if value >= 50:
            return "warn"
        return "ok"
    return "neutral"


def extract_ata_attributes(smart_payload: dict[str, Any]) -> dict[str, Any]:
    table = smart_payload.get("ata_smart_attributes", {}).get("table", [])
    attrs: dict[str, Any] = {}
    for row in table:
        name = row.get("name")
        if not name:
            continue
        attrs[name] = {
            "id": row.get("id"),
            "value": row.get("value"),
            "worst": row.get("worst"),
            "thresh": row.get("thresh"),
            "raw": row.get("raw", {}).get("value"),
            "when_failed": row.get("when_failed"),
        }
    return attrs


def normalized_smart_rows(smart_payload: dict[str, Any]) -> list[dict[str, Any]]:
    ata_rows = smart_payload.get("ata_smart_attributes", {}).get("table", [])
    if ata_rows:
        return [
            {
                "id": row.get("id"),
                "name": row.get("name"),
                "label": smart_label(row.get("name")),
                "value": row.get("value"),
                "worst": row.get("worst"),
                "thresh": row.get("thresh"),
                "raw": row.get("raw", {}).get("value"),
                "human": smart_human_value(row.get("name"), row.get("raw", {}).get("string") or row.get("raw", {}).get("value")),
                "severity": smart_severity(row.get("name"), row.get("raw", {}).get("value")),
                "when_failed": row.get("when_failed") or "",
            }
            for row in ata_rows
        ]

    nvme = smart_payload.get("nvme_smart_health_information_log", {})
    if not nvme:
        return []

    return [
        {"id": "-", "name": "Power_On_Hours", "label": smart_label("Power_On_Hours"), "value": "-", "worst": "-", "thresh": "-", "raw": smart_payload.get("power_on_time", {}).get("hours", 0), "human": smart_human_value("Power_On_Hours", smart_payload.get("power_on_time", {}).get("hours", 0)), "severity": smart_severity("Power_On_Hours", smart_payload.get("power_on_time", {}).get("hours", 0)), "when_failed": ""},
        {"id": "-", "name": "Temperature_Celsius", "label": smart_label("Temperature_Celsius"), "value": "-", "worst": "-", "thresh": "-", "raw": smart_payload.get("temperature", {}).get("current", 0), "human": smart_human_value("Temperature_Celsius", smart_payload.get("temperature", {}).get("current", 0)), "severity": smart_severity("Temperature_Celsius", smart_payload.get("temperature", {}).get("current", 0)), "when_failed": ""},
        {"id": "-", "name": "Media_Errors", "label": smart_label("Media_Errors"), "value": "-", "worst": "-", "thresh": "-", "raw": nvme.get("media_errors", 0), "human": smart_human_value("Media_Errors", nvme.get("media_errors", 0)), "severity": smart_severity("Media_Errors", nvme.get("media_errors", 0)), "when_failed": ""},
        {"id": "-", "name": "Unsafe_Shutdowns", "label": smart_label("Unsafe_Shutdowns"), "value": "-", "worst": "-", "thresh": "-", "raw": nvme.get("unsafe_shutdowns", 0), "human": smart_human_value("Unsafe_Shutdowns", nvme.get("unsafe_shutdowns", 0)), "severity": smart_severity("Unsafe_Shutdowns", nvme.get("unsafe_shutdowns", 0)), "when_failed": ""},
        {"id": "-", "name": "Percentage_Used", "label": smart_label("Percentage_Used"), "value": "-", "worst": "-", "thresh": "-", "raw": nvme.get("percentage_used", 0), "human": smart_human_value("Percentage_Used", nvme.get("percentage_used", 0)), "severity": smart_severity("Percentage_Used", nvme.get("percentage_used", 0)), "when_failed": ""},
    ]


def smart_overview(smart: dict[str, Any], disk: dict[str, Any]) -> dict[str, str]:
    payload = smart.get("payload") or {}
    rows = normalized_smart_rows(payload)
    row_map = {row["name"]: row for row in rows}

    def raw(name: str, fallback: str = "n/a") -> str:
        value = row_map.get(name, {}).get("raw", fallback)
        return str(value if value not in (None, "") else fallback)

    def human(name: str, fallback: str = "n/a") -> str:
        value = row_map.get(name, {}).get("human", fallback)
        return str(value if value not in (None, "") else fallback)

    capacity_gb = round(disk.get("size_bytes", 0) / 1024 / 1024 / 1024, 1)
    return {
        "interface": disk.get("transport") or "unknown",
        "capacity_gb": str(capacity_gb),
        "serial": disk.get("serial") or "n/a",
        "power_on_hours": raw("Power_On_Hours"),
        "start_stop_count": raw("Start_Stop_Count", "n/a"),
        "temperature_c": human("Temperature_Celsius", format_temperature(payload.get("temperature", {}).get("current"))),
        "reallocated": raw("Reallocated_Sector_Ct", "0"),
        "pending": raw("Current_Pending_Sector", "0"),
        "offline_uncorrectable": raw("Offline_Uncorrectable", "0"),
        "crc_errors": raw("UDMA_CRC_Error_Count", "0"),
    }


def enrich_report(report: dict[str, Any]) -> dict[str, Any]:
    payload = report.get("smart", {}).get("payload") or {}
    report["smart_rows"] = report.get("smart_rows") or normalized_smart_rows(payload)
    report["overview"] = smart_overview(report.get("smart", {}), report.get("device", {}))
    return report


def verkoop_summary(report: dict[str, Any]) -> dict[str, str]:
    health = report.get("health", {})
    overview = report.get("overview", {})
    test = report.get("test", {})
    score = int(health.get("score") or 0)
    pending = int(str(overview.get("pending", "0")).split()[0]) if str(overview.get("pending", "0")).split()[0].isdigit() else 0
    reallocated = int(str(overview.get("reallocated", "0")).split()[0]) if str(overview.get("reallocated", "0")).split()[0].isdigit() else 0
    offline = int(str(overview.get("offline_uncorrectable", "0")).split()[0]) if str(overview.get("offline_uncorrectable", "0")).split()[0].isdigit() else 0

    if pending or reallocated or offline or score < 60:
        verdict = "Nur mit deutlichem Hinweis verkaufen"
    elif score >= 85 and test.get("credibility_level") in {"high", "very_high"}:
        verdict = "Gut verkaufbar"
    else:
        verdict = "Verkaufbar mit normalem Hinweis"

    return {
        "verdict": verdict,
        "test_claim": test.get("buyer_claim", "Keine verkaeufergeeignete Testaussage hinterlegt."),
    }


def build_report_payload(
    disk: dict[str, Any],
    smart: dict[str, Any],
    health: dict[str, Any],
    test_result: dict[str, Any],
    *,
    preliminary: bool = False,
    source_job: TestJob | None = None,
) -> dict[str, Any]:
    report = {
        "report_id": uuid.uuid4().hex[:12],
        "generated_at": utc_now_iso(),
        "device": disk,
        "smart": smart,
        "health": health,
        "test": test_result,
        "preliminary": preliminary,
    }
    if source_job:
        report["source_job"] = {
            "id": source_job.id,
            "mode": source_job.mode,
            "status": source_job.status,
            "progress": source_job.progress,
            "current_step": source_job.current_step,
        }
    report["sales_summary"] = verkoop_summary(enrich_report(report))
    return report


def finalize_smart_job_report(job: TestJob, disk: dict[str, Any]) -> TestJob:
    smart = get_smart_data(disk["path"])
    health = compute_health(disk, smart)
    payload = smart.get("payload") or {}
    log_entries = payload.get("ata_smart_self_test_log", {}).get("standard", {}).get("table", [])
    latest = log_entries[0] if log_entries else {}
    variant = "short" if job.mode == "smart_short" else "extended"
    label = "SMART Short Self-Test" if variant == "short" else "SMART Extended Self-Test"
    test_result = {
        "type": f"smart_{variant}",
        "label": label,
        "credibility_level": "high" if variant == "short" else "very_high",
        "buyer_claim": (
            "Der vom Laufwerk selbst ausgefuehrte SMART Short Self-Test wurde abgeschlossen."
            if variant == "short"
            else "Der vom Laufwerk selbst ausgefuehrte SMART Extended Self-Test wurde abgeschlossen."
        ),
        "duration_s": None,
        "polling_minutes": payload.get("ata_smart_data", {}).get("self_test", {}).get("polling_minutes", {}).get(variant),
        "self_test_status": latest.get("status", {}).get("string") or payload.get("ata_smart_data", {}).get("self_test", {}).get("status", {}).get("string"),
        "smart_passed": payload.get("smart_status", {}).get("passed"),
        "log_entry": latest,
    }
    report = build_report_payload(disk, smart, health, test_result, source_job=job)
    report_id = save_report(report)
    job.progress = 1.0
    job.status = "done"
    job.current_step = "Fertig"
    job.error = None
    if "Test abgeschlossen" not in job.messages:
        job.messages.append("Test abgeschlossen")
    job.result = {"report_id": report_id, "report": report}
    save_job(job)
    return job


def compute_health(disk: dict[str, Any], smart: dict[str, Any]) -> dict[str, Any]:
    score = 100
    notes: list[str] = []

    if not smart.get("available"):
        return {
            "score": 45,
            "grade": "Eingeschraenkt",
            "summary": "SMART-Daten fehlen",
            "notes": [smart.get("error") or "SMART nicht verfuegbar"],
        }

    payload = smart.get("payload") or {}
    attrs = extract_ata_attributes(payload)

    def raw_value(name: str) -> int:
        raw = attrs.get(name, {}).get("raw")
        try:
            return int(str(raw).split()[0])
        except (TypeError, ValueError, AttributeError):
            return 0

    reallocated = raw_value("Reallocated_Sector_Ct")
    pending = raw_value("Current_Pending_Sector")
    offline_uncorrectable = raw_value("Offline_Uncorrectable")
    udma_crc = raw_value("UDMA_CRC_Error_Count")
    hours = raw_value("Power_On_Hours")
    start_stop = raw_value("Start_Stop_Count")
    temp = (
        payload.get("temperature", {}).get("current")
        or payload.get("ata_smart_attributes", {}).get("temperature", {}).get("current")
    )

    passed = payload.get("smart_status", {}).get("passed")
    if passed is False:
        score -= 50
        notes.append("SMART Gesamtstatus meldet Fehler.")

    if reallocated > 0:
        score -= min(35, 10 + reallocated)
        notes.append(f"Neu zugewiesene Sektoren: {reallocated}.")
    if pending > 0:
        score -= min(30, 12 + pending * 2)
        notes.append(f"Ausstehende fehlerhafte Sektoren: {pending}.")
    if offline_uncorrectable > 0:
        score -= min(25, 10 + offline_uncorrectable * 2)
        notes.append(f"Nicht korrigierbare Offline-Fehler: {offline_uncorrectable}.")
    if udma_crc > 0:
        score -= min(10, udma_crc)
        notes.append(f"CRC-Fehler im Uebertragungsweg: {udma_crc}.")
    if hours > 30000:
        score -= 12
        notes.append(f"Hohe Laufzeit: {hours} Stunden.")
    elif hours > 15000:
        score -= 6
        notes.append(f"Erhoehte Laufzeit: {hours} Stunden.")
    if start_stop > 20000:
        score -= 5
        notes.append(f"Viele Start/Stopp-Zyklen: {start_stop}.")
    if temp and isinstance(temp, (int, float)) and temp >= 50:
        score -= 8
        notes.append(f"Hohe Temperatur beobachtet: {temp} °C.")

    score = max(0, min(100, score))
    if score >= 90:
        grade = "Sehr gut"
    elif score >= 75:
        grade = "Gut"
    elif score >= 60:
        grade = "Ordentlich"
    elif score >= 40:
        grade = "Risikobehaftet"
    else:
        grade = "Problematisch"

    summary = "Verkaufsgeeignet" if score >= 75 else "Nur mit deutlichem Hinweis verkaufen"
    if not notes:
        notes.append("Keine kritischen SMART-Auffaelligkeiten erkannt.")

    return {
        "score": score,
        "grade": grade,
        "summary": summary,
        "notes": notes,
    }


def running_job_snapshot_test(job: TestJob, disk: dict[str, Any]) -> dict[str, Any]:
    label_map = {
        "quick": "Laufender Stichproben-Lesetest",
        "deep_sample": "Laufender verteilter Lesetest",
        "smart_short": "Laufender SMART Short Self-Test",
        "full": "Laufender vollstaendiger Lesetest",
        "smart_extended": "Laufender SMART Extended Self-Test",
        "erase_zero": "Laufendes Nullschreiben",
        "secure_erase_ata": "Laufendes ATA Secure Erase",
    }
    claim_map = {
        "quick": "Der Stichproben-Lesetest laeuft noch. Dieser Bericht zeigt nur einen Zwischenstand.",
        "deep_sample": "Der verteilte Lesetest laeuft noch. Dieser Bericht zeigt nur einen Zwischenstand.",
        "smart_short": "Der SMART Short Self-Test laeuft noch. Dieser Bericht zeigt nur einen Zwischenstand.",
        "full": "Der vollstaendige Lesetest laeuft noch. Dieser Bericht zeigt nur einen Zwischenstand.",
        "smart_extended": "Der SMART Extended Self-Test laeuft noch. Dieser Bericht zeigt nur einen Zwischenstand und ist kein Abschlussnachweis.",
        "erase_zero": "Das Nullschreiben laeuft noch. Dieser Bericht zeigt nur einen Zwischenstand.",
        "secure_erase_ata": "Das ATA Secure Erase laeuft noch. Dieser Bericht zeigt nur einen Zwischenstand.",
    }
    credibility = "low" if job.mode not in {"smart_short", "smart_extended"} else "medium"
    if job.mode in {"smart_short", "smart_extended"}:
        credibility = "medium"
    return {
        "type": f"{job.mode}_snapshot",
        "label": label_map.get(job.mode, "Laufender Test"),
        "credibility_level": credibility,
        "buyer_claim": claim_map.get(job.mode, "Der Test laeuft noch. Dieser Bericht zeigt nur einen Zwischenstand."),
        "status": job.status,
        "progress_percent": round((job.progress or 0.0) * 100, 1),
        "current_step": job.current_step,
        "device_path": disk["path"],
    }


@dataclass
class TestJob:
    id: str
    device: str
    mode: str
    created_at: str
    status: str = "queued"
    progress: float = 0.0
    current_step: str = "Wartet"
    messages: list[str] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None


jobs: dict[str, TestJob] = {}
jobs_lock = threading.Lock()


def row_to_job(row: sqlite3.Row) -> TestJob:
    return TestJob(
        id=row["id"],
        device=row["device"],
        mode=row["mode"],
        created_at=row["created_at"],
        status=row["status"],
        progress=float(row["progress"] or 0.0),
        current_step=row["current_step"] or "",
        messages=json.loads(row["messages_json"] or "[]"),
        options=json.loads(row["options_json"] or "{}") if "options_json" in row.keys() else {},
        result=json.loads(row["result_json"]) if row["result_json"] else None,
        error=row["error"],
    )


def save_job(job: TestJob) -> None:
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO jobs (id, device, mode, created_at, status, progress, current_step, messages_json, options_json, result_json, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                device = excluded.device,
                mode = excluded.mode,
                created_at = excluded.created_at,
                status = excluded.status,
                progress = excluded.progress,
                current_step = excluded.current_step,
                messages_json = excluded.messages_json,
                options_json = excluded.options_json,
                result_json = excluded.result_json,
                error = excluded.error
            """,
            (
                job.id,
                job.device,
                job.mode,
                job.created_at,
                job.status,
                job.progress,
                job.current_step,
                json.dumps(job.messages),
                json.dumps(job.options),
                json.dumps(job.result) if job.result is not None else None,
                job.error,
            ),
        )


def get_job(job_id: str) -> TestJob | None:
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return row_to_job(row) if row else None


def latest_internal_smart_job(device: str) -> TestJob | None:
    with db_connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM jobs
            WHERE device = ?
              AND mode IN ('smart_short', 'smart_extended')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (device,),
        ).fetchone()
    return row_to_job(row) if row else None


def has_active_internal_smart_job(device: str) -> bool:
    job = latest_internal_smart_job(device)
    return bool(job and job.status in {"queued", "running"})


def recover_internal_smart_job(device: str, selftest: dict[str, Any]) -> TestJob | None:
    job = latest_internal_smart_job(device)
    if not job:
        return None
    if job.status == "done":
        return None
    if selftest.get("running"):
        if job.status not in {"queued", "running", "interrupted"}:
            return None
        remaining = selftest.get("remaining_percent")
        job.status = "running"
        if isinstance(remaining, int):
            job.progress = max(0.01, min(0.99, (100 - remaining) / 100))
            job.current_step = f"{selftest.get('status_text') or 'SMART Self-Test laeuft'} ({100 - remaining}% abgeschlossen)"
        else:
            job.current_step = selftest.get("status_text") or "SMART Self-Test laeuft"
        job.error = None
        if "SMART-Test nach App-Neustart wieder verbunden" not in job.messages:
            job.messages.append("SMART-Test nach App-Neustart wieder verbunden")
        save_job(job)
        return job
    if job.status in {"queued", "running", "interrupted"} and not job.result:
        disk = get_disk(device)
        return finalize_smart_job_report(job, disk)
    return job


def store_device_status(disk: dict[str, Any], selftest: dict[str, Any], source: str = "smartctl") -> None:
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO device_status (device, disk_json, selftest_json, updated_at, source)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(device) DO UPDATE SET
                disk_json = excluded.disk_json,
                selftest_json = excluded.selftest_json,
                updated_at = excluded.updated_at,
                source = excluded.source
            """,
            (
                disk["name"],
                json.dumps(disk),
                json.dumps(selftest),
                utc_now_iso(),
                source,
            ),
        )


def get_device_status(device: str) -> dict[str, Any] | None:
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM device_status WHERE device = ?", (device,)).fetchone()
    if not row:
        return None
    return {
        "device": row["device"],
        "disk": json.loads(row["disk_json"]) if row["disk_json"] else None,
        "selftest": json.loads(row["selftest_json"] or "{}"),
        "updated_at": row["updated_at"],
        "source": row["source"],
    }


def serialized_jobs(device: str | None = None, active_only: bool = False) -> list[dict[str, Any]]:
    query = "SELECT * FROM jobs"
    clauses: list[str] = []
    params: list[Any] = []
    if device:
        clauses.append("device = ?")
        params.append(device)
    if active_only:
        clauses.append("status IN ('queued', 'running')")
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC"

    with db_connect() as conn:
        rows = conn.execute(query, params).fetchall()
    result = [asdict(row_to_job(row)) for row in rows]

    if active_only:
        external = external_selftest_jobs(device=device)
        result.extend(external)
    result.sort(key=lambda item: item["created_at"], reverse=True)
    return result


def save_report(report: dict[str, Any]) -> str:
    report = enrich_report(report)
    report_id = report["report_id"]
    path = REPORT_DIR / f"{report_id}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report_id


def load_report(report_id: str) -> dict[str, Any]:
    path = REPORT_DIR / f"{report_id}.json"
    if not path.exists():
        raise FileNotFoundError(report_id)
    return enrich_report(json.loads(path.read_text(encoding="utf-8")))


def delete_report(report_id: str) -> None:
    path = REPORT_DIR / f"{report_id}.json"
    if not path.exists():
        raise FileNotFoundError(report_id)
    path.unlink()


def persist_job_state(job: TestJob) -> None:
    with jobs_lock:
        save_job(job)


def sample_offsets(size_bytes: int, mode: str) -> list[tuple[int, int]]:
    mib = 1024 * 1024
    if mode == "quick":
        chunk = 128 * mib
        points = [0.0, 0.5, 0.9]
    elif mode == "deep_sample":
        chunk = 256 * mib
        points = [i / 9 for i in range(10)]
    else:
        chunk = 4 * mib
        points = []

    ranges = []
    for point in points:
        offset = int(max(0, (size_bytes - chunk) * point))
        ranges.append((offset, min(chunk, size_bytes - offset)))
    return ranges


def read_segments(device_path: str, ranges: list[tuple[int, int]], job: TestJob) -> dict[str, Any]:
    timings = []
    with open(device_path, "rb", buffering=0) as handle:
        for index, (offset, length) in enumerate(ranges, start=1):
            os.lseek(handle.fileno(), offset, os.SEEK_SET)
            remaining = length
            chunk_size = 4 * 1024 * 1024
            started = time.time()
            while remaining > 0:
                data = os.read(handle.fileno(), min(chunk_size, remaining))
                if not data:
                    raise IOError(f"Leerer Read an Offset {offset}")
                remaining -= len(data)
            duration = max(0.001, time.time() - started)
            throughput = length / duration / (1024 * 1024)
            timings.append(
                {
                    "segment": index,
                    "offset_bytes": offset,
                    "length_bytes": length,
                    "throughput_mib_s": round(throughput, 2),
                    "duration_s": round(duration, 2),
                }
            )
            job.progress = index / max(1, len(ranges))
            job.current_step = f"Segment {index}/{len(ranges)} gelesen"
            persist_job_state(job)
    average = sum(item["throughput_mib_s"] for item in timings) / max(1, len(timings))
    return {
        "type": "sample_read",
        "label": "Stichproben-Lesetest" if len(ranges) <= 3 else "Verteilter Lesetest",
        "credibility_level": "medium" if len(ranges) <= 3 else "high",
        "buyer_claim": "Mehrere verteilte Bereiche der Platte wurden erfolgreich lesend geprueft." if len(ranges) > 3 else "Mehrere Stichprobenbereiche der Platte wurden erfolgreich lesend geprueft.",
        "segments": timings,
        "average_throughput_mib_s": round(average, 2),
    }


def full_read_scan(device_path: str, size_bytes: int, job: TestJob) -> dict[str, Any]:
    chunk_size = 4 * 1024 * 1024
    read_bytes = 0
    started = time.time()
    checkpoints = []

    with open(device_path, "rb", buffering=0) as handle:
        while True:
            data = os.read(handle.fileno(), chunk_size)
            if not data:
                break
            read_bytes += len(data)
            job.progress = read_bytes / max(1, size_bytes)
            job.current_step = f"{format_bytes(read_bytes)} von {format_bytes(size_bytes)} gelesen"
            if read_bytes % (256 * 1024 * 1024) < chunk_size:
                persist_job_state(job)
            if len(checkpoints) < 25:
                checkpoints.append(
                    {
                        "read_bytes": read_bytes,
                        "throughput_mib_s": round(
                            (read_bytes / max(0.001, time.time() - started)) / (1024 * 1024), 2
                        ),
                    }
                )
    duration = max(0.001, time.time() - started)
    return {
        "type": "full_read",
        "label": "Vollstaendiger Lesetest",
        "credibility_level": "very_high",
        "buyer_claim": "Die komplette Platte wurde sequenziell lesend geprueft.",
        "bytes_read": read_bytes,
        "duration_s": round(duration, 2),
        "average_throughput_mib_s": round(read_bytes / duration / (1024 * 1024), 2),
        "checkpoints": checkpoints,
    }


def run_smart_extended_test(device_path: str, job: TestJob) -> dict[str, Any]:
    caps = smart_selftest_capabilities(device_path)
    if not caps.get("supported"):
        raise RuntimeError("SMART Extended Self-Test wird von diesem Laufwerk oder USB-Adapter nicht unterstuetzt.")

    rc, out, err = run_command(["smartctl", "-t", "long", device_path], timeout=30)
    if rc not in (0,):
        raise RuntimeError(err.strip() or out.strip() or "SMART Self-Test konnte nicht gestartet werden")

    polling_minutes = caps.get("polling_minutes", {}).get("extended")
    started_at = time.time()
    while True:
        payload = smart_payload(device_path)
        self_test = payload.get("ata_smart_data", {}).get("self_test", {})
        status = self_test.get("status", {})
        remaining = status.get("remaining_percent")
        string = status.get("string") or "SMART Self-Test laeuft"
        passed = payload.get("smart_status", {}).get("passed")
        log_entries = payload.get("ata_smart_self_test_log", {}).get("standard", {}).get("table", [])

        if isinstance(remaining, int):
            job.progress = max(0.01, min(0.99, (100 - remaining) / 100))
            job.current_step = f"{string} ({100 - remaining}% abgeschlossen)"
        else:
            elapsed_min = int((time.time() - started_at) / 60)
            estimate = f"seit {elapsed_min} min"
            if polling_minutes:
                estimate = f"{elapsed_min}/{polling_minutes} min"
            job.current_step = f"{string} ({estimate})"
        persist_job_state(job)

        if remaining in (0, None) and status.get("value") not in (241, 242):
            latest = log_entries[0] if log_entries else {}
            result = latest.get("status", {}).get("string") or string
            return {
                "type": "smart_extended",
                "label": "SMART Extended Self-Test",
                "credibility_level": "very_high",
                "buyer_claim": "Der vom Laufwerk selbst ausgefuehrte SMART Extended Self-Test wurde abgeschlossen.",
                "duration_s": round(time.time() - started_at, 2),
                "polling_minutes": polling_minutes,
                "self_test_status": result,
                "smart_passed": passed,
                "log_entry": latest,
            }

        time.sleep(60)


def run_smart_selftest(device_path: str, job: TestJob, variant: str) -> dict[str, Any]:
    if variant not in {"short", "long"}:
        raise RuntimeError(f"Unsupported SMART self-test variant: {variant}")

    rc, out, err = run_command(["smartctl", "-t", variant, device_path], timeout=30)
    if rc not in (0,):
        raise RuntimeError(err.strip() or out.strip() or "SMART Self-Test konnte nicht gestartet werden")

    started_at = time.time()
    poll_interval = 15 if variant == "short" else 60
    polling_key = "short" if variant == "short" else "extended"
    label = "SMART Short Self-Test" if variant == "short" else "SMART Extended Self-Test"
    credibility = "high" if variant == "short" else "very_high"
    buyer_claim = (
        "Der vom Laufwerk selbst ausgefuehrte SMART Short Self-Test wurde abgeschlossen."
        if variant == "short"
        else "Der vom Laufwerk selbst ausgefuehrte SMART Extended Self-Test wurde abgeschlossen."
    )

    while True:
        payload = smart_payload(device_path)
        self_test = payload.get("ata_smart_data", {}).get("self_test", {})
        status = self_test.get("status", {})
        remaining = status.get("remaining_percent")
        string = status.get("string") or f"{label} laeuft"
        polling_minutes = self_test.get("polling_minutes", {}).get(polling_key)
        passed = payload.get("smart_status", {}).get("passed")
        log_entries = payload.get("ata_smart_self_test_log", {}).get("standard", {}).get("table", [])

        if isinstance(remaining, int):
            job.progress = max(0.01, min(0.99, (100 - remaining) / 100))
            job.current_step = f"{string} ({100 - remaining}% abgeschlossen)"
        else:
            elapsed_min = int((time.time() - started_at) / 60)
            estimate = f"seit {elapsed_min} min"
            if polling_minutes:
                estimate = f"{elapsed_min}/{polling_minutes} min"
            job.current_step = f"{string} ({estimate})"
        persist_job_state(job)

        if remaining in (0, None) and status.get("value") not in (241, 242):
            latest = log_entries[0] if log_entries else {}
            result = latest.get("status", {}).get("string") or string
            return {
                "type": f"smart_{variant}",
                "label": label,
                "credibility_level": credibility,
                "buyer_claim": buyer_claim,
                "duration_s": round(time.time() - started_at, 2),
                "polling_minutes": polling_minutes,
                "self_test_status": result,
                "smart_passed": passed,
                "log_entry": latest,
            }

        time.sleep(poll_interval)


def erase_allowed(disk: dict[str, Any], allow_internal: bool = False) -> None:
    if not disk.get("rotational"):
        raise RuntimeError("Loeschen ist hier nur fuer rotierende Festplatten vorgesehen.")
    if not allow_internal and not (disk.get("hotplug") or disk.get("transport") == "usb"):
        raise RuntimeError("Destruktives Loeschen ist nur fuer extern angeschlossene Laufwerke freigeschaltet.")


def run_zero_erase(disk: dict[str, Any], job: TestJob, allow_internal: bool = False) -> dict[str, Any]:
    import subprocess

    erase_allowed(disk, allow_internal=allow_internal)
    actions = unmount_disk_children(disk["name"])
    total = max(1, disk["size_bytes"])
    cmd = [
        "dd",
        "if=/dev/zero",
        f"of={disk['path']}",
        "bs=16M",
        "oflag=direct",
        "conv=fsync",
        "status=progress",
    ]
    started = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    bytes_written = 0
    progress_re = re.compile(r"(\d+)\s+bytes")
    assert proc.stderr is not None
    for line in proc.stderr:
        match = progress_re.search(line)
        if match:
            bytes_written = int(match.group(1))
            job.progress = min(0.99, bytes_written / total)
            job.current_step = f"{format_bytes(bytes_written)} von {format_bytes(total)} ueberschrieben"
            persist_job_state(job)
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"Loeschen fehlgeschlagen (Exit {rc})")
    duration = max(0.001, time.time() - started)
    return {
        "type": "erase_zero",
        "label": "1x Nullschreiben",
        "credibility_level": "destructive",
        "buyer_claim": "Die Platte wurde vor dem Verkauf einmal vollstaendig mit Nullen ueberschrieben.",
        "duration_s": round(duration, 2),
        "bytes_written": total,
        "average_throughput_mib_s": round(total / duration / (1024 * 1024), 2),
        "actions": actions,
    }


def run_secure_erase_ata(disk: dict[str, Any], job: TestJob, allow_internal: bool = False) -> dict[str, Any]:
    caps = secure_erase_capabilities(disk)
    if not caps.get("supported"):
        raise RuntimeError(caps.get("reason") or "ATA Secure Erase nicht verfuegbar.")

    import subprocess

    erase_allowed(disk, allow_internal=allow_internal)
    actions = unmount_disk_children(disk["name"])
    password = f"wipe-{uuid.uuid4().hex[:8]}"
    method_flag = "--security-erase-enhanced" if caps.get("method") == "enhanced" else "--security-erase"
    started = time.time()

    set_pass = subprocess.run(
        ["hdparm", "--user-master", "u", f"--security-set-pass", password, disk["path"]],
        capture_output=True,
        text=True,
        check=False,
    )
    if set_pass.returncode != 0:
        raise RuntimeError(set_pass.stderr.strip() or set_pass.stdout.strip() or "Security-Passwort konnte nicht gesetzt werden.")

    job.progress = 0.02
    job.current_step = "ATA Secure Erase gestartet"
    persist_job_state(job)

    proc = subprocess.Popen(
        ["hdparm", "--user-master", "u", method_flag, password, disk["path"]],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    while proc.poll() is None:
        elapsed_min = int((time.time() - started) / 60)
        job.current_step = f"ATA Secure Erase laeuft ({elapsed_min} min)"
        persist_job_state(job)
        time.sleep(30)

    stdout, stderr = proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr.strip() or stdout.strip() or "ATA Secure Erase fehlgeschlagen.")

    duration = max(0.001, time.time() - started)
    return {
        "type": "secure_erase_ata",
        "label": "ATA Secure Erase",
        "credibility_level": "destructive",
        "buyer_claim": "Die Platte wurde per ATA Secure Erase geloescht, sofern vom Laufwerk und Adapter unterstuetzt.",
        "duration_s": round(duration, 2),
        "actions": actions,
        "method": caps.get("method"),
    }


def run_test_job(job_id: str) -> None:
    with jobs_lock:
        job = jobs.get(job_id) or get_job(job_id)
        if not job:
            return
        jobs[job_id] = job
        job.status = "running"
        job.current_step = "Lese Geraetedaten"
        job.messages.append("Test gestartet")
        save_job(job)

    try:
        disk = get_disk(job.device)
        smart = get_smart_data(disk["path"])
        health = compute_health(disk, smart)

        allow_internal_erase = bool(job.options.get("allow_internal_erase"))

        if job.mode in {"quick", "deep_sample"}:
            ranges = sample_offsets(disk["size_bytes"], job.mode)
            test_result = read_segments(disk["path"], ranges, job)
        elif job.mode == "smart_short":
            test_result = run_smart_selftest(disk["path"], job, "short")
        elif job.mode == "full":
            test_result = full_read_scan(disk["path"], disk["size_bytes"], job)
        elif job.mode == "smart_extended":
            test_result = run_smart_extended_test(disk["path"], job)
        elif job.mode == "erase_zero":
            test_result = run_zero_erase(disk, job, allow_internal=allow_internal_erase)
        elif job.mode == "secure_erase_ata":
            test_result = run_secure_erase_ata(disk, job, allow_internal=allow_internal_erase)
        else:
            raise ValueError(f"Unsupported mode: {job.mode}")

        report = build_report_payload(disk, smart, health, test_result, source_job=job)
        report_id = save_report(report)

        with jobs_lock:
            job.progress = 1.0
            job.status = "done"
            job.current_step = "Fertig"
            job.messages.append("Test abgeschlossen")
            job.result = {"report_id": report_id, "report": report}
            save_job(job)
    except Exception as exc:
        with jobs_lock:
            job.status = "error"
            job.error = str(exc)
            job.messages.append(f"Fehler: {exc}")
            save_job(job)


@app.route("/")
def index() -> str:
    return render_template("index.html")


@app.route("/legal")
def legal_index() -> str:
    docs = []
    for slug, path in LEGAL_DOCS.items():
        docs.append(
            {
                "slug": slug,
                "title": path.stem.replace("_", " ").replace("-", " ").title(),
                "content": path.read_text(encoding="utf-8") if path.exists() else "Not found.",
            }
        )
    return render_template("legal.html", docs=docs, active_slug="license")


@app.route("/legal/<slug>")
def legal_doc(slug: str) -> str:
    path = LEGAL_DOCS.get(slug)
    if not path:
        return render_template("legal.html", docs=[], active_slug=slug, error="Dokument nicht gefunden."), 404

    docs = [{"slug": key, "title": value.stem.replace("_", " ").replace("-", " ").title()} for key, value in LEGAL_DOCS.items()]
    content = path.read_text(encoding="utf-8") if path.exists() else "Not found."
    return render_template("legal.html", docs=docs, active_slug=slug, content=content, title=path.stem.replace("_", " ").replace("-", " ").title())


@app.route("/report/<report_id>")
def report_view(report_id: str) -> str:
    report = load_report(report_id)
    return render_template("report.html", report=report)


@app.get("/api/disks")
def api_disks():
    try:
        disks = list_disks()
        for disk in disks:
            disk["modes"] = recommended_modes_for_disk(disk)
        return jsonify(
            {
                "disks": disks,
                "smartctl_available": smartctl_available(),
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/disks/<device_name>")
def api_disk_detail(device_name: str):
    try:
        disk = get_disk(device_name)
        disk["modes"] = recommended_modes_for_disk(disk)
        smart = get_smart_data(disk["path"])
        health = compute_health(disk, smart)
        erase = secure_erase_capabilities(disk)
        selftest = sync_external_selftest_for_disk(disk)
        return jsonify(
            {
                "disk": disk,
                "smart": smart,
                "overview": smart_overview(smart, disk),
                "smart_rows": normalized_smart_rows((smart.get("payload") or {})),
                "health": health,
                "erase": erase,
                "selftest": selftest,
                "modes": disk["modes"],
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/tests", methods=["GET", "POST"])
def api_tests():
    if request.method == "GET":
        device = request.args.get("device")
        active_only = request.args.get("active") == "1"
        sync_external_selftests(device=device)
        return jsonify({"jobs": serialized_jobs(device=device, active_only=active_only)})

    payload = request.get_json(silent=True) or {}
    device = payload.get("device")
    mode = payload.get("mode", "quick")
    allowed_modes = {"quick", "deep_sample", "smart_short", "smart_extended", "full"}
    if not device:
        return jsonify({"error": "device fehlt"}), 400
    if mode not in allowed_modes:
        return jsonify({"error": f"Unbekannter Testmodus: {mode}"}), 400
    if has_active_job_for_device(device):
        return jsonify({"error": "Fuer dieses Laufwerk laeuft bereits ein App-Job."}), 409
    disk = get_disk(device)
    selftest = sync_external_selftest_for_disk(disk)
    if selftest.get("running") and mode in {"smart_short", "smart_extended"}:
        return jsonify({"error": "Auf diesem Laufwerk laeuft bereits ein SMART Self-Test."}), 409

    job_id = uuid.uuid4().hex[:12]
    job = TestJob(id=job_id, device=device, mode=mode, created_at=utc_now_iso())
    with jobs_lock:
        jobs[job_id] = job
    save_job(job)

    thread = threading.Thread(target=run_test_job, args=(job_id,), daemon=True)
    thread.start()
    return jsonify({"job_id": job_id})


@app.post("/api/disks/<device_name>/safe-remove")
def api_safe_remove(device_name: str):
    try:
        result = safe_remove_disk(device_name)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/disks/<device_name>/erase")
def api_erase_disk(device_name: str):
    payload = request.get_json(silent=True) or {}
    confirmation = (payload.get("confirmation") or "").strip()
    allow_internal = bool(payload.get("allow_internal"))
    try:
        disk = get_disk(device_name)
        if has_active_job_for_device(device_name):
            return jsonify({"error": "Fuer dieses Laufwerk laeuft bereits ein App-Job."}), 409
        expected = {disk["path"], disk["serial"]}
        if confirmation not in expected:
            return jsonify({"error": "Bestaetigung muss exakt Device-Pfad oder Seriennummer sein."}), 400
        job_id = uuid.uuid4().hex[:12]
        job = TestJob(
            id=job_id,
            device=device_name,
            mode="erase_zero",
            created_at=utc_now_iso(),
            options={"allow_internal_erase": allow_internal},
        )
        with jobs_lock:
            jobs[job_id] = job
        save_job(job)
        thread = threading.Thread(target=run_test_job, args=(job_id,), daemon=True)
        thread.start()
        return jsonify({"job_id": job_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/disks/<device_name>/secure-erase")
def api_secure_erase_disk(device_name: str):
    payload = request.get_json(silent=True) or {}
    confirmation = (payload.get("confirmation") or "").strip()
    allow_internal = bool(payload.get("allow_internal"))
    try:
        disk = get_disk(device_name)
        caps = secure_erase_capabilities(disk)
        if not caps.get("supported"):
            return jsonify({"error": caps.get("reason") or "ATA Secure Erase nicht verfuegbar."}), 400
        if has_active_job_for_device(device_name):
            return jsonify({"error": "Fuer dieses Laufwerk laeuft bereits ein App-Job."}), 409
        expected = {disk["path"], disk["serial"]}
        if confirmation not in expected:
            return jsonify({"error": "Bestaetigung muss exakt Device-Pfad oder Seriennummer sein."}), 400
        job_id = uuid.uuid4().hex[:12]
        job = TestJob(
            id=job_id,
            device=device_name,
            mode="secure_erase_ata",
            created_at=utc_now_iso(),
            options={"allow_internal_erase": allow_internal},
        )
        with jobs_lock:
            jobs[job_id] = job
        save_job(job)
        thread = threading.Thread(target=run_test_job, args=(job_id,), daemon=True)
        thread.start()
        return jsonify({"job_id": job_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/disks/<device_name>/abort-selftest")
def api_abort_selftest(device_name: str):
    try:
        disk = get_disk(device_name)
        status = abort_smart_selftest(disk["path"])
        store_device_status(disk, status, source="smartctl-abort")
        return jsonify({"status": status})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/tests/<job_id>")
def api_job_status(job_id: str):
    external = external_selftest_job(job_id)
    if external:
        return jsonify(external)
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job nicht gefunden"}), 404
    if job.mode in {"smart_short", "smart_extended"} and job.status in {"queued", "running", "interrupted"}:
        sync_external_selftests(device=job.device)
        job = get_job(job_id) or job
    return jsonify(asdict(job))


@app.get("/api/reports")
def api_reports():
    reports = []
    for path in sorted(REPORT_DIR.glob("*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            reports.append(
                {
                    "report_id": payload["report_id"],
                    "generated_at": payload["generated_at"],
                    "device": payload["device"],
                    "health": payload["health"],
                }
            )
        except Exception:
            continue
    return jsonify({"reports": reports})


@app.delete("/api/reports/<report_id>")
def api_delete_report(report_id: str):
    try:
        delete_report(report_id)
        return jsonify({"deleted": True, "report_id": report_id})
    except FileNotFoundError:
        return jsonify({"error": "Bericht nicht gefunden"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(debug=False, host="127.0.0.1", port=5055)
