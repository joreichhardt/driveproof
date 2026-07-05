from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import uuid
from io import BytesIO
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file
from markupsafe import Markup
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

def bundled() -> bool:
    return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def resource_base_dir() -> Path:
    if bundled():
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent


def default_state_dir() -> Path:
    if bundled():
        return Path.home() / ".local" / "state" / "driveproof"
    return BASE_DIR


BASE_DIR = resource_base_dir()
STATE_DIR = Path(os.environ.get("DRIVEPROOF_STATE_DIR", default_state_dir()))
REPORT_DIR = STATE_DIR / "reports"
DB_PATH = STATE_DIR / "state.db"
SIGNING_KEY_PATH = STATE_DIR / "driveproof-signing.key"
LEGAL_DOCS = {
    "license": BASE_DIR / "LICENSE",
    "third-party": BASE_DIR / "THIRD_PARTY_LICENSES.md",
    "commercial": BASE_DIR / "COMMERCIAL_SERVICES.md",
}
REPORT_DIR.mkdir(parents=True, exist_ok=True)
EXPORT_MOUNT_ROOT = Path("/run/media/driveproof")
VENDOR_TOOL_DIR_NAME = "DriveProof-Vendor-Tools"
VENDOR_TOOL_DOWNLOAD_DIR_NAME = "Downloads"
NETWORK_CONFIG_FILENAME = "driveproof-network.conf"
VENDOR_TOOL_NAMES = ("storcli", "storcli64", "perccli", "perccli64", "arcconf", "ssacli", "hpssacli", "areca-cli", "cli64")
VENDOR_TOOL_CATALOG = {
    "storcli": {
        "label": "Broadcom/LSI StorCLI",
        "tool_names": ["storcli", "storcli64"],
        "vendor": "Broadcom",
        "download_url": "https://www.broadcom.com/support/download-search?pg=Storage+Adapters,+Controllers,+and+ICs&pf=RAID+Controller+Cards&pn=MegaRAID+SAS+Software+User+Guide&pa=Management+Software+and+Tools",
        "license_note": "Download and use are subject to Broadcom license terms.",
    },
    "perccli": {
        "label": "Dell PERCCLI",
        "tool_names": ["perccli", "perccli64"],
        "vendor": "Dell",
        "download_url": "https://www.dell.com/support/home/drivers/driversdetails?driverid=f48c2",
        "license_note": "Download and use are subject to the Dell Software License Agreement.",
    },
    "arcconf": {
        "label": "Microchip/Adaptec ARCCONF",
        "tool_names": ["arcconf"],
        "vendor": "Microchip",
        "download_url": "https://www.microchip.com/en-us/adaptec",
        "license_note": "Download and use are subject to Microchip software license terms.",
    },
    "ssacli": {
        "label": "HPE SSACLI",
        "tool_names": ["ssacli", "hpssacli"],
        "vendor": "HPE",
        "download_url": "https://support.hpe.com/",
        "license_note": "Download and use are subject to HPE software license terms.",
    },
    "areca": {
        "label": "Areca CLI",
        "tool_names": ["areca-cli", "cli64"],
        "vendor": "Areca",
        "download_url": "https://www.areca.com.tw/support/downloads.html",
        "license_note": "Download and use are subject to Areca license terms.",
    },
}
GITHUB_QR_PATH = BASE_DIR / "static" / "assets" / "github-qr.svg"

COMPLIANCE_PROFILES = {
    "resale_basic": {
        "label": "Resale Basic",
        "standard": "DriveProof resale workflow",
        "description": "SMART health capture plus a selected read test. This is a resale diagnostic report, not a certified data-erasure certificate.",
        "requires_erase": False,
    },
    "nist_clear": {
        "label": "NIST SP 800-88 Clear",
        "standard": "NIST SP 800-88 Rev. 1 Clear",
        "description": "Maps to logical overwrite or firmware erase workflows intended for reuse within normal assurance requirements.",
        "requires_erase": True,
    },
    "nist_purge": {
        "label": "NIST SP 800-88 Purge",
        "standard": "NIST SP 800-88 Rev. 1 Purge",
        "description": "Maps to firmware cryptographic erase, ATA Enhanced Secure Erase, or NVMe sanitize workflows where supported.",
        "requires_erase": True,
    },
}

SYSTEM_TOOL_REQUIREMENTS = {
    "smartctl": {
        "package": "smartmontools",
        "minimum_version": "7.4",
        "features": {
            "json_output": ["smartctl", "-j", "--version"],
            "selftests": ["smartctl", "--version"],
        },
    },
    "hdparm": {
        "package": "hdparm",
        "minimum_version": "9.65",
        "features": {
            "ata_secure_erase": ["hdparm", "--security-help"],
        },
    },
    "nvme": {
        "package": "nvme-cli",
        "minimum_version": "2.8",
        "features": {
            "format_nvm": ["nvme", "format", "--help"],
            "sanitize": ["nvme", "sanitize", "--help"],
            "sanitize_log": ["nvme", "sanitize-log", "--help"],
            "json_output": ["nvme", "version"],
        },
    },
    "udisksctl": {
        "package": "udisks2",
        "minimum_version": "2.10",
        "features": {
            "safe_remove": ["udisksctl", "--help"],
        },
    },
    "browser": {
        "package": "chromium, chromium-browser, google-chrome, or chrome",
        "minimum_version": "120",
        "features": {
            "headless_pdf": ["browser", "--headless", "--help"],
        },
    },
}

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"), static_folder=str(BASE_DIR / "static"))


def tool_path(name: str) -> str | None:
    if os.path.sep in name:
        return name if os.access(name, os.X_OK) else None
    if name in VENDOR_TOOL_NAMES:
        vendor_path = vendor_tool_path(name)
        if vendor_path:
            return vendor_path
    path = shutil.which(name)
    if path:
        return path
    for directory in ("/run/current-system/sw/bin", "/etc/profiles/per-user/kiosk/bin", "/usr/bin", "/bin"):
        candidate = Path(directory) / name
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def resolved_command(args: list[str]) -> list[str]:
    resolved = list(args)
    resolved_tool = tool_path(resolved[0])
    if resolved_tool:
        resolved[0] = resolved_tool
    return resolved


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify_filename_part(value: str, fallback: str = "unknown") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", (value or "").strip())
    cleaned = cleaned.strip("._-")
    return cleaned[:80] or fallback


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


def classify_report_kind(report: dict[str, Any]) -> str:
    test = report.get("test") or {}
    mode = (report.get("source_job") or {}).get("mode") or test.get("type") or ""
    if test.get("erasure") or mode in {
        "erase_zero",
        "secure_erase_ata",
        "secure_erase_ata_enhanced",
        "nvme_format",
        "nvme_sanitize_crypto",
        "nvme_sanitize_block",
    }:
        return "erase"
    return "test"


def report_kind_label(kind: str) -> str:
    return "Erase Report" if kind == "erase" else "Test Report"


def report_file_path(report_id: str, report: dict[str, Any] | None = None) -> Path:
    legacy = REPORT_DIR / f"{report_id}.json"
    if legacy.exists():
        return legacy
    matches = sorted(REPORT_DIR.glob(f"*_{report_id}.json"))
    if matches:
        return matches[-1]
    if report is None:
        return legacy
    return REPORT_DIR / report_filename(report)


def github_qr_inline_svg() -> Markup:
    if not GITHUB_QR_PATH.exists():
        return Markup("")
    content = GITHUB_QR_PATH.read_text(encoding="utf-8")
    match = re.search(r"(<svg\b.*</svg>)", content, re.DOTALL)
    return Markup(match.group(1) if match else content)


def signing_private_key() -> Ed25519PrivateKey:
    if SIGNING_KEY_PATH.exists():
        data = SIGNING_KEY_PATH.read_bytes()
        if b"BEGIN PRIVATE KEY" in data:
            return serialization.load_pem_private_key(data, password=None)
        backup = SIGNING_KEY_PATH.with_suffix(".legacy")
        try:
            SIGNING_KEY_PATH.replace(backup)
        except OSError:
            pass

    SIGNING_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    SIGNING_KEY_PATH.write_bytes(pem)
    SIGNING_KEY_PATH.chmod(0o600)
    return key


def signing_public_key() -> Ed25519PublicKey:
    return signing_private_key().public_key()


def signing_public_key_pem() -> str:
    return signing_public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")


def signing_key_fingerprint() -> str:
    return hashlib.sha256(signing_public_key_pem().encode("utf-8")).hexdigest()[:16]


def public_key_fingerprint(public_key_pem: str) -> str:
    return hashlib.sha256((public_key_pem or "").encode("utf-8")).hexdigest()[:16]


def sign_bytes(payload: bytes) -> str:
    return signing_private_key().sign(payload).hex()


def verify_signature(public_key_pem: str, payload: bytes, signature_hex: str) -> bool:
    try:
        public_key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
        public_key.verify(bytes.fromhex(signature_hex), payload)
        return True
    except (ValueError, InvalidSignature):
        return False


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def report_integrity_payload(report: dict[str, Any]) -> dict[str, Any]:
    payload = dict(report)
    payload.pop("integrity", None)
    payload.pop("certificate", None)
    payload.pop("smart_rows", None)
    payload.pop("overview", None)
    return payload


def report_sha256(report: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(report_integrity_payload(report))).hexdigest()


def compliance_from_options(options: dict[str, Any] | None) -> dict[str, Any]:
    key = (options or {}).get("compliance_profile") or "resale_basic"
    profile = COMPLIANCE_PROFILES.get(key, COMPLIANCE_PROFILES["resale_basic"])
    return {"id": key if key in COMPLIANCE_PROFILES else "resale_basic", **profile}


def audit_event(action: str, **details: Any) -> dict[str, Any]:
    return {
        "timestamp": utc_now_iso(),
        "action": action,
        "details": details,
    }


def audit_hash_chain(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    previous = "0" * 64
    chained: list[dict[str, Any]] = []
    for index, event in enumerate(events, start=1):
        payload = {
            "index": index,
            "previous_hash": previous,
            "event": event,
        }
        event_hash = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        chained.append({**payload, "hash": event_hash})
        previous = event_hash
    return chained


def certificate_payload(report: dict[str, Any]) -> dict[str, Any]:
    events = report.get("audit") or []
    chain = audit_hash_chain(events)
    report_hash = report_sha256(report)
    chain_hash = chain[-1]["hash"] if chain else hashlib.sha256(b"").hexdigest()
    signed_payload = {
        "report_id": report.get("report_id"),
        "report_sha256": report_hash,
        "audit_chain_sha256": chain_hash,
    }
    signature = sign_bytes(canonical_json_bytes(signed_payload))
    test = report.get("test") or {}
    has_erasure = bool(test.get("erasure"))
    certificate_type = "DriveProof Certificate of Erasure" if has_erasure else "DriveProof Certificate of Drive Test"
    return {
        "type": certificate_type,
        "issuer": "DriveProof Local Certificate Authority",
        "issued_at": report.get("generated_at") or utc_now_iso(),
        "report_id": report.get("report_id"),
        "device_serial": (report.get("device") or {}).get("serial"),
        "device_path": (report.get("device") or {}).get("path"),
        "report_sha256": report_hash,
        "audit_chain_sha256": chain_hash,
        "audit_chain": chain,
        "signature_algorithm": "Ed25519",
        "signature": signature,
        "signed_payload": signed_payload,
        "public_key_pem": signing_public_key_pem(),
        "signing_key_fingerprint": signing_key_fingerprint(),
        "verification": "Verify report_sha256 and audit_chain_sha256, then validate the Ed25519 signature with the embedded public key.",
        "disclaimer": "Generated by DriveProof. This is a software-generated certificate, not third-party accreditation.",
    }


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

    resolved_args = resolved_command(args)

    try:
        proc = subprocess.run(
            resolved_args,
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


def command_version_string(tool: str) -> str:
    probes = {
        "smartctl": ["smartctl", "--version"],
        "hdparm": ["hdparm", "-V"],
        "nvme": ["nvme", "version"],
        "udisksctl": ["udisksctl", "--version"],
        "browser": [chrome_binary() or "chromium", "--version"],
    }
    args = probes.get(tool, [tool, "--version"])
    rc, out, err = run_command(args, timeout=10)
    if rc != 0 and tool == "browser":
        return "not installed"
    text = (out.strip() or err.strip()).splitlines()
    return text[0] if text else "unknown"


def feature_probe(args: list[str], expected_tokens: list[str] | None = None) -> dict[str, Any]:
    rc, out, err = run_command(args, timeout=10)
    text = f"{out}\n{err}"
    available = rc == 0 or bool(out.strip() or err.strip())
    if expected_tokens:
        lower_text = text.lower()
        available = available and all(token.lower() in lower_text for token in expected_tokens)
    return {
        "available": available,
        "return_code": rc,
    }


def system_tool_inventory() -> dict[str, Any]:
    tools = {}
    for name, requirement in SYSTEM_TOOL_REQUIREMENTS.items():
        path = chrome_binary() if name == "browser" else tool_path(name)
        features = {}
        for feature, probe_args in requirement["features"].items():
            args = list(probe_args)
            if name == "browser" and path:
                args[0] = path
            expected = None
            if name == "nvme" and feature == "format_nvm":
                expected = ["--ses"]
            elif name == "nvme" and feature == "sanitize":
                expected = ["--sanact"]
            elif name == "nvme" and feature == "sanitize_log":
                expected = ["sanitize-log"]
            elif name == "smartctl" and feature == "json_output":
                expected = ["JSON"]
            elif name == "hdparm" and feature == "ata_secure_erase":
                expected = ["security"]
            if path:
                features[feature] = feature_probe(args, expected_tokens=expected)
            else:
                features[feature] = {"available": False, "return_code": 127}
        tools[name] = {
            "installed": bool(path),
            "path": path,
            "package": requirement["package"],
            "minimum_version": requirement["minimum_version"],
            "version": command_version_string(name) if path else "not installed",
            "features": features,
        }
    return {
        "generated_at": utc_now_iso(),
        "tools": tools,
    }


def list_disks() -> list[dict[str, Any]]:
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

    payload = json.loads(out)
    disks: list[dict[str, Any]] = []

    def flatten(node: dict[str, Any]) -> list[dict[str, Any]]:
        result = [node]
        for child in node.get("children") or []:
            result.extend(flatten(child))
        return result

    def is_boot_media(node: dict[str, Any]) -> bool:
        nodes = flatten(node)
        labels = {(entry.get("label") or "").strip() for entry in nodes}
        mountpoints = {(entry.get("mountpoint") or "").strip() for entry in nodes}
        fstypes = {(entry.get("fstype") or "").strip().lower() for entry in nodes}
        return (
            "DRVPROOF" in labels
            or "/iso" in mountpoints
            or ("iso9660" in fstypes and "nixos-24.11-x86_64" in labels)
        )

    for item in payload.get("blockdevices", []):
        if item.get("type") != "disk":
            continue
        name = item.get("name") or ""
        if name.startswith(("loop", "zram", "ram", "fd")):
            continue
        if is_boot_media(item):
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


def list_export_targets() -> list[dict[str, Any]]:
    ensure_export_partition_mounted()
    rc, out, err = run_command(
        [
            "lsblk",
            "-b",
            "-J",
            "-o",
            "NAME,PATH,SIZE,MODEL,SERIAL,TRAN,VENDOR,TYPE,HOTPLUG,MOUNTPOINT,FSTYPE,LABEL,RM",
        ]
    )
    if rc != 0:
        raise RuntimeError(err.strip() or "lsblk failed")

    allowed_fs = {"vfat", "exfat", "msdos", "ext2", "ext3", "ext4", "xfs", "btrfs"}
    payload = json.loads(out)
    targets: list[dict[str, Any]] = []
    for item in payload.get("blockdevices", []):
        stack = [item]
        while stack:
            current = stack.pop()
            stack.extend(current.get("children") or [])
            mountpoint = current.get("mountpoint")
            fstype = (current.get("fstype") or "").lower()
            if not mountpoint or fstype not in allowed_fs:
                continue
            mount = Path(mountpoint)
            if not mount.exists() or not os.access(mount, os.W_OK):
                continue
            label = (current.get("label") or current.get("name") or mount.name or "EXPORT").strip()
            report_compatible = fstype in {"vfat", "exfat", "msdos"}
            targets.append(
                {
                    "id": mountpoint,
                    "mountpoint": mountpoint,
                    "label": label,
                    "fstype": fstype,
                    "size_bytes": int(current.get("size") or 0),
                    "path": current.get("path"),
                    "removable": bool(current.get("rm")) or bool(current.get("hotplug")),
                    "linux_permissions": fstype in {"ext2", "ext3", "ext4", "xfs", "btrfs"},
                    "report_compatible": report_compatible,
                }
            )
    targets.sort(key=lambda item: (not item["removable"], item["label"].lower(), item["mountpoint"]))
    return targets


def ensure_export_partition_mounted() -> None:
    rc, out, _err = run_command(
        [
            "lsblk",
            "-b",
            "-J",
            "-o",
            "NAME,PATH,SIZE,TYPE,MOUNTPOINT,FSTYPE,LABEL,RM,HOTPLUG",
        ]
    )
    if rc != 0:
        return

    payload = json.loads(out)
    candidates: list[dict[str, Any]] = []

    def walk(node: dict[str, Any]) -> None:
        fstype = (node.get("fstype") or "").lower()
        label = (node.get("label") or "").strip()
        if node.get("type") == "part" and fstype in {"vfat", "exfat", "msdos", "ext2", "ext3", "ext4", "xfs", "btrfs"}:
            candidates.append(node)
        for child in node.get("children") or []:
            walk(child)

    for item in payload.get("blockdevices", []):
        walk(item)

    candidates.sort(
        key=lambda item: (
            {"DRVPROOF": 0, "DRVTOOLS": 1}.get((item.get("label") or "").strip(), 2),
            not (bool(item.get("rm")) or bool(item.get("hotplug"))),
            item.get("path") or "",
        )
    )

    for item in candidates:
        if item.get("mountpoint"):
            continue
        label = slugify_filename_part(item.get("label") or item.get("name") or "EXPORT")
        mountpoint = EXPORT_MOUNT_ROOT / label
        try:
            mountpoint.mkdir(parents=True, exist_ok=True)
            mount_options = "rw,umask=000" if (item.get("fstype") or "").lower() in {"vfat", "exfat", "msdos"} else "rw"
            rc, _out, _err = run_command(
                ["mount", "-o", mount_options, item["path"], str(mountpoint)],
                timeout=20,
            )
        except Exception:
            continue


def find_export_target(mountpoint: str) -> dict[str, Any]:
    for target in list_export_targets():
        if target["mountpoint"] == mountpoint:
            if not target.get("report_compatible"):
                raise FileNotFoundError(f"Report target is not FAT/exFAT-compatible: {mountpoint}")
            return target
    raise FileNotFoundError(f"Export target not found or not writable: {mountpoint}")


def default_export_target() -> dict[str, Any]:
    targets = [target for target in list_export_targets() if target.get("report_compatible")]
    if not targets:
        raise FileNotFoundError("No writable FAT/exFAT export partition found.")
    for target in targets:
        if target["label"] == "DRVPROOF":
            return target
    for target in targets:
        if target["removable"]:
            return target
    return targets[0]


def network_config_path() -> Path:
    return Path(default_export_target()["mountpoint"]) / NETWORK_CONFIG_FILENAME


def parse_network_config_text(text: str) -> dict[str, str]:
    config = {"ip": "", "gw": "", "dns": ""}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lower()
        if key in config:
            config[key] = value.strip()
    return config


def read_network_config() -> dict[str, Any]:
    try:
        path = network_config_path()
    except Exception as exc:
        return {"available": False, "error": str(exc), "config": {"ip": "", "gw": "", "dns": ""}}
    if not path.exists():
        return {"available": True, "path": str(path), "exists": False, "config": {"ip": "", "gw": "", "dns": ""}}
    return {"available": True, "path": str(path), "exists": True, "config": parse_network_config_text(path.read_text(encoding="utf-8"))}


def validate_network_config(payload: dict[str, Any]) -> dict[str, str]:
    ip = (payload.get("ip") or "").strip()
    gw = (payload.get("gw") or "").strip()
    dns = (payload.get("dns") or "").strip()
    if bool(ip) != bool(gw):
        raise ValueError("ip and gw must be set together, or both left empty for DHCP.")
    if ip and "/" not in ip:
        raise ValueError("ip must include CIDR prefix, for example 192.168.1.50/24.")
    return {"ip": ip, "gw": gw, "dns": dns}


def write_network_config(payload: dict[str, Any]) -> dict[str, Any]:
    config = validate_network_config(payload)
    path = network_config_path()
    if not config["ip"] and not config["gw"] and not config["dns"]:
        path.unlink(missing_ok=True)
        return {"saved": True, "deleted": True, "path": str(path), "config": config}
    lines = [
        "# DriveProof static network config",
        "# Leave this file absent or empty to use DHCP.",
        f"ip={config['ip']}",
        f"gw={config['gw']}",
        f"dns={config['dns']}",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return {"saved": True, "deleted": False, "path": str(path), "config": config}


def vendor_tool_roots() -> list[Path]:
    roots = []
    for target in list_export_targets():
        if target.get("linux_permissions"):
            root = Path(target["mountpoint"]) / VENDOR_TOOL_DIR_NAME
            roots.append(root)
    return roots


def ensure_vendor_tool_root_permissions(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    downloads = root / VENDOR_TOOL_DOWNLOAD_DIR_NAME
    downloads.mkdir(parents=True, exist_ok=True)
    for path in (root, downloads):
        try:
            path.chmod(0o777)
        except OSError:
            pass


def default_vendor_tool_root() -> Path:
    targets = list_export_targets()
    if not targets:
        raise FileNotFoundError("No writable export partition found for vendor tools.")
    for target in targets:
        if target.get("linux_permissions"):
            root = Path(target["mountpoint"]) / VENDOR_TOOL_DIR_NAME
            ensure_vendor_tool_root_permissions(root)
            return root
    raise FileNotFoundError("No writable Linux filesystem found for vendor tools. Add or mount an ext4, XFS, or btrfs partition.")


def vendor_tool_path(name: str) -> str | None:
    for root in vendor_tool_roots():
        candidate = root / name
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def list_vendor_tools() -> dict[str, Any]:
    tools = []
    roots = vendor_tool_roots()
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.iterdir()):
            if not path.is_file():
                continue
            executable = os.access(path, os.X_OK)
            known = path.name in VENDOR_TOOL_NAMES
            version = "unknown"
            if executable:
                rc, out, err = run_command([str(path), "--version"], timeout=10)
                version = (out.strip() or err.strip()).splitlines()[0] if (out.strip() or err.strip()) else f"probe exit {rc}"
            tools.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "root": str(root),
                    "size_bytes": path.stat().st_size,
                    "executable": executable,
                    "known": known,
                    "version": version,
                }
            )
    installed_names = {tool["name"] for tool in tools}
    catalog = []
    for key, item in VENDOR_TOOL_CATALOG.items():
        catalog.append(
            {
                "id": key,
                **item,
                "installed": any(name in installed_names for name in item["tool_names"]),
            }
        )
    return {
        "directory_name": VENDOR_TOOL_DIR_NAME,
        "download_directory_name": VENDOR_TOOL_DOWNLOAD_DIR_NAME,
        "known_tool_names": list(VENDOR_TOOL_NAMES),
        "roots": [str(root) for root in roots],
        "download_directories": [str(root / VENDOR_TOOL_DOWNLOAD_DIR_NAME) for root in roots if root.exists()],
        "catalog": catalog,
        "tools": tools,
    }


def read_first_existing_text(paths: list[Path]) -> str | None:
    for path in paths:
        try:
            value = path.read_text(encoding="utf-8", errors="ignore").strip()
        except OSError:
            continue
        if value:
            return value
    return None


def driver_from_sysfs_path(path: Path) -> str | None:
    try:
        current = path.resolve()
    except OSError:
        return None
    while current != Path("/sys"):
        driver_link = current / "driver"
        if driver_link.is_symlink():
            try:
                driver = driver_link.resolve().name
            except OSError:
                driver = None
            if driver and driver != "driver":
                return driver
        current = current.parent
    return None


def list_storage_controllers() -> dict[str, Any]:
    scsi_hosts = []
    for host_path in sorted(Path("/sys/class/scsi_host").glob("host*")):
        host_name = host_path.name
        proc_name = read_first_existing_text([host_path / "proc_name"])
        model = read_first_existing_text([host_path / "model_name", host_path / "model"])
        firmware = read_first_existing_text([host_path / "fw_version", host_path / "firmware_version"])
        driver_version = read_first_existing_text([host_path / "driver_version"])
        driver = driver_from_sysfs_path(host_path) or proc_name
        scsi_hosts.append(
            {
                "host": host_name,
                "driver": driver or proc_name or "unknown",
                "proc_name": proc_name,
                "model": model,
                "firmware": firmware,
                "driver_version": driver_version,
                "sysfs": str(host_path),
            }
        )

    vendor_tools = list_vendor_tools()
    installed_tools = [tool for tool in vendor_tools["tools"] if tool.get("executable")]
    return {
        "scsi_hosts": scsi_hosts,
        "vendor_catalog": vendor_tools["catalog"],
        "installed_vendor_tools": installed_tools,
        "note": "Hardware RAID controllers may expose logical volumes only unless the matching vendor CLI is installed.",
    }


def chrome_binary() -> str | None:
    for name in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        path = tool_path(name)
        if path:
            return path
    return None


def render_report_pdf_to_path(report_id: str, pdf_path: Path) -> None:
    pdf_engine = chrome_binary()
    if not pdf_engine:
        raise RuntimeError(f"No Chromium or Chrome binary found for PDF export. PATH={os.environ.get('PATH', '')}")

    url = f"http://127.0.0.1:5055/report/{report_id}"
    rc, out, err = run_command(
        [
            pdf_engine,
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=1500",
            f"--print-to-pdf={pdf_path}",
            url,
        ],
        timeout=180,
    )
    if rc != 0 or not pdf_path.exists():
        raise RuntimeError(err.strip() or out.strip() or "PDF export failed.")


def render_report_run_pdf_to_path(run_id: str, pdf_path: Path) -> None:
    pdf_engine = chrome_binary()
    if not pdf_engine:
        raise RuntimeError(f"No Chromium or Chrome binary found for PDF export. PATH={os.environ.get('PATH', '')}")

    url = f"http://127.0.0.1:5055/report-run/{run_id}"
    rc, out, err = run_command(
        [
            pdf_engine,
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=2000",
            f"--print-to-pdf={pdf_path}",
            url,
        ],
        timeout=240,
    )
    if rc != 0 or not pdf_path.exists():
        raise RuntimeError(err.strip() or out.strip() or "Combined PDF export failed.")


def list_printers() -> list[dict[str, Any]]:
    lpstat = tool_path("lpstat")
    if not lpstat:
        return []
    rc, out, _err = run_command([lpstat, "-e"], timeout=10)
    if rc != 0:
        return []
    printers = [{"name": line.strip()} for line in out.splitlines() if line.strip()]
    rc, out, _err = run_command([lpstat, "-v"], timeout=10)
    devices: dict[str, str] = {}
    if rc == 0:
        for line in out.splitlines():
            match = re.match(r"device for ([^:]+):\s*(.+)", line.strip())
            if match:
                devices[match.group(1)] = match.group(2)
    for printer in printers:
        device_uri = devices.get(printer["name"], "")
        printer["device_uri"] = device_uri
        printer["network"] = device_uri.startswith(("ipp://", "ipps://", "socket://", "lpd://", "dnssd://"))
        printer["usb"] = device_uri.startswith("usb://")
    return printers


def print_report_pdf(report_id: str, printer: str | None = None) -> dict[str, Any]:
    lp = tool_path("lp")
    if not lp:
        raise RuntimeError("CUPS lp command is not available.")

    printers = list_printers()
    if not printers:
        raise RuntimeError("No CUPS printers are configured or discovered.")
    selected = printer or printers[0]["name"]
    if selected not in {item["name"] for item in printers}:
        raise RuntimeError(f"Unknown printer: {selected}")

    report = load_report(report_id)
    base_name = report_filename(report).removesuffix(".json")
    tmp = tempfile.NamedTemporaryFile(prefix=f"{base_name}_", suffix=".pdf", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()
    try:
        render_report_pdf_to_path(report_id, tmp_path)
        rc, out, err = run_command([lp, "-d", selected, "-t", base_name, str(tmp_path)], timeout=60)
        if rc != 0:
            raise RuntimeError(err.strip() or out.strip() or "Print job failed.")
        return {
            "status": "submitted",
            "printer": selected,
            "message": (out.strip() or "Print job submitted."),
            "report_id": report_id,
        }
    finally:
        tmp_path.unlink(missing_ok=True)


def submit_pdf_to_printer(pdf_path: Path, title: str, printer: str | None = None) -> dict[str, Any]:
    lp = tool_path("lp")
    if not lp:
        raise RuntimeError("CUPS lp command is not available.")
    printers = list_printers()
    if not printers:
        raise RuntimeError("No CUPS printers are configured or discovered.")
    selected = printer or printers[0]["name"]
    if selected not in {item["name"] for item in printers}:
        raise RuntimeError(f"Unknown printer: {selected}")
    rc, out, err = run_command([lp, "-d", selected, "-t", title, str(pdf_path)], timeout=60)
    if rc != 0:
        raise RuntimeError(err.strip() or out.strip() or "Print job failed.")
    return {"status": "submitted", "printer": selected, "message": (out.strip() or "Print job submitted.")}


def print_report_run(run_id: str, printer: str | None = None, combined: bool = True) -> dict[str, Any]:
    reports = reports_for_run(run_id)
    if not reports:
        raise FileNotFoundError(run_id)
    if not combined:
        jobs = [print_report_pdf(report["report_id"], printer) for report in reports]
        return {"status": "submitted", "mode": "individual", "run_id": run_id, "count": len(jobs), "jobs": jobs}

    tmp = tempfile.NamedTemporaryFile(prefix=f"driveproof_run_{slugify_filename_part(run_id)}_", suffix=".pdf", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()
    try:
        render_report_run_pdf_to_path(run_id, tmp_path)
        result = submit_pdf_to_printer(tmp_path, f"DriveProof run {run_id}", printer)
        return {**result, "mode": "combined", "run_id": run_id, "count": len(reports)}
    finally:
        tmp_path.unlink(missing_ok=True)


def export_report_pdf(report_id: str, mountpoint: str | None = None) -> dict[str, Any]:
    report = load_report(report_id)
    target = find_export_target(mountpoint) if mountpoint else default_export_target()

    device_dir = Path(target["mountpoint"]) / "DriveProof-Reports" / device_folder_name(report)
    device_dir.mkdir(parents=True, exist_ok=True)

    base_name = report_filename(report).removesuffix(".json")
    bundle_dir = device_dir / base_name
    bundle_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = bundle_dir / f"{base_name}.pdf"
    json_path = bundle_dir / "report.json"
    certificate_path = bundle_dir / "certificate.json"
    audit_path = bundle_dir / "audit-chain.json"
    public_key_path = bundle_dir / "public-key.pem"
    manifest_path = bundle_dir / "manifest.json"
    signature_path = bundle_dir / "manifest.sig"

    render_report_pdf_to_path(report_id, pdf_path)

    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    def file_sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    files = {
        pdf_path.name: file_sha256(pdf_path),
        json_path.name: file_sha256(json_path),
    }
    has_certificate = report.get("report_kind") == "erase" and bool(report.get("certificate"))
    if has_certificate:
        certificate_path.write_text(json.dumps(report.get("certificate", {}), indent=2), encoding="utf-8")
        audit_path.write_text(json.dumps(report.get("certificate", {}).get("audit_chain", []), indent=2), encoding="utf-8")
        public_key_path.write_text(report.get("certificate", {}).get("public_key_pem") or signing_public_key_pem(), encoding="utf-8")
        files.update(
            {
                certificate_path.name: file_sha256(certificate_path),
                audit_path.name: file_sha256(audit_path),
                public_key_path.name: file_sha256(public_key_path),
            }
        )

    manifest = {
        "schema": "driveproof.bundle.v1",
        "created_at": utc_now_iso(),
        "report_id": report_id,
        "report_kind": report.get("report_kind"),
        "report_kind_label": report.get("report_kind_label"),
        "device_folder": device_dir.name,
        "certificate_type": report.get("certificate", {}).get("type") if has_certificate else None,
        "files": files,
        "report_sha256": report.get("certificate", {}).get("report_sha256"),
        "audit_chain_sha256": report.get("certificate", {}).get("audit_chain_sha256"),
        "public_key_fingerprint": report.get("certificate", {}).get("signing_key_fingerprint"),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if has_certificate:
        signature_path.write_text(sign_bytes(canonical_json_bytes(manifest)), encoding="utf-8")

    result = {
        "report_id": report_id,
        "status": "saved",
        "message": f"Saved to {target['label']}",
        "target": target,
        "device_path": str(device_dir),
        "device_folder": device_dir.name,
        "bundle_path": str(bundle_dir),
        "bundle_name": bundle_dir.name,
        "pdf_path": str(pdf_path),
        "json_path": str(json_path),
        "pdf_name": pdf_path.name,
        "json_name": json_path.name,
        "manifest_name": manifest_path.name,
        "has_certificate": has_certificate,
    }
    if has_certificate:
        result.update(
            {
                "certificate_name": certificate_path.name,
                "signature_name": signature_path.name,
            }
        )
    return result


def update_report_export_status(report_id: str, export: dict[str, Any]) -> None:
    path = report_file_path(report_id)
    if not path.exists():
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["export"] = export
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def auto_export_report_pdf(report_id: str) -> dict[str, Any]:
    try:
        result = export_report_pdf(report_id)
        update_report_export_status(report_id, result)
        return result
    except Exception as exc:
        result = {
            "report_id": report_id,
            "status": "error",
            "message": str(exc),
        }
        update_report_export_status(report_id, result)
        return result


def verify_export_bundle(bundle_path: str) -> dict[str, Any]:
    bundle = Path(bundle_path)
    if not bundle.is_dir():
        raise FileNotFoundError(f"Bundle not found: {bundle_path}")

    manifest_path = bundle / "manifest.json"
    signature_path = bundle / "manifest.sig"
    public_key_path = bundle / "public-key.pem"
    if not manifest_path.exists():
        raise FileNotFoundError("Bundle manifest is missing.")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    file_checks: dict[str, bool] = {}
    for name, expected_hash in (manifest.get("files") or {}).items():
        file_path = bundle / name
        file_checks[name] = file_path.exists() and hashlib.sha256(file_path.read_bytes()).hexdigest() == expected_hash

    signature_valid = None
    if signature_path.exists() or public_key_path.exists():
        if not signature_path.exists() or not public_key_path.exists():
            raise FileNotFoundError("Signed bundle is missing manifest signature or public key.")
        signature = signature_path.read_text(encoding="utf-8").strip()
        public_key = public_key_path.read_text(encoding="utf-8")
        signature_valid = verify_signature(public_key, canonical_json_bytes(manifest), signature)
    return {
        "bundle_path": str(bundle),
        "valid": (signature_valid is not False) and all(file_checks.values()),
        "signature_valid": signature_valid,
        "file_checks": file_checks,
        "manifest": manifest,
    }


def network_status() -> dict[str, Any]:
    default_interface = None
    route_rc, route_out, _ = run_command(["ip", "-j", "route", "show", "default"], timeout=5)
    if route_rc == 0:
        try:
            routes = json.loads(route_out)
            if routes:
                default_interface = routes[0].get("dev")
        except Exception:
            default_interface = None

    rc, out, err = run_command(["ip", "-j", "addr"], timeout=5)
    addresses: list[dict[str, Any]] = []
    primary_address = None
    if rc == 0:
        try:
            for iface in json.loads(out):
                if iface.get("ifname") == "lo":
                    continue
                for addr in iface.get("addr_info") or []:
                    if addr.get("family") == "inet":
                        item = {"interface": iface.get("ifname"), "address": addr.get("local"), "prefixlen": addr.get("prefixlen")}
                        addresses.append(item)
                        if iface.get("ifname") == default_interface and primary_address is None:
                            primary_address = item
        except Exception:
            pass
    return {
        "mode": "dhcp",
        "addresses": addresses,
        "primary_address": primary_address or (addresses[0] if addresses else None),
        "error": None if rc == 0 else (err or out),
        "configuration_available": True,
    }


def service_status(force: bool = False) -> dict[str, Any]:
    return {
        "state": "disabled",
        "reason": "Standalone local mode.",
        "features": {
            "network_configuration": True,
        },
        "network": network_status(),
    }


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
        "label": "Quick",
        "hint": "Short sample read test for initial sorting.",
        "destructive": False,
    },
    "deep_sample": {
        "label": "Deep Sample",
        "hint": "Distributed read test across the drive. More useful for HDDs than SSD/NVMe.",
        "destructive": False,
    },
    "smart_short": {
        "label": "SMART Short",
        "hint": "Short drive self-test. Good for SSD/NVMe and quick pre-checks.",
        "destructive": False,
    },
    "smart_extended": {
        "label": "SMART Extended",
        "hint": "Real SMART Extended self-test executed by the drive. Credible for resale.",
        "destructive": False,
    },
    "full": {
        "label": "Full Read",
        "hint": "Full read test. Takes longer and provides the strongest read-test claim for resale.",
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
    selftest["source"] = "app" if recovered_job or has_active_app_smart_selftest(disk["name"]) else "external"
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
                "current_step": f"External SMART self-test: {selftest.get('status_text') or 'running'}",
                "messages": ["SMART self-test started by drive or adapter detected."],
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
        raise RuntimeError(err.strip() or out.strip() or "Could not abort SMART self-test.")
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
            "reason": "ATA Secure Erase is not available. The USB dock or drive does not expose enough hdparm information.",
        }

    lower = text.lower()
    if "security:" not in lower:
        return {
            "supported": False,
            "method": None,
            "reason": "ATA security feature set not found.",
        }

    enhanced = "supported: enhanced erase" in lower or "enhanced erase" in lower
    basic = "supported" in lower and "security" in lower
    if not (basic or enhanced):
        return {
            "supported": False,
            "method": None,
            "reason": "Drive does not report ATA Secure Erase support.",
        }

    return {
        "supported": True,
        "method": "basic",
        "basic_supported": basic,
        "enhanced_supported": enhanced,
        "methods": [method for method, enabled in (("basic", basic), ("enhanced", enhanced)) if enabled],
        "reason": None,
    }


def nvme_erase_capabilities(disk: dict[str, Any]) -> dict[str, Any]:
    if disk.get("kind") != "NVMe" and (disk.get("transport") or "").lower() != "nvme":
        return {"supported": False, "reason": "Not an NVMe drive."}
    if not tool_path("nvme"):
        return {"supported": False, "reason": "nvme-cli is not installed in this image."}
    rc, out, err = run_command(["nvme", "id-ctrl", disk["path"], "-o", "json"], timeout=30)
    details: dict[str, Any] = {}
    sanicap = 0
    if out.strip():
        try:
            details = json.loads(out)
            sanicap = parse_intish(details.get("sanicap")) or 0
        except json.JSONDecodeError:
            details = {"raw": out.strip()[:1000]}
    crypto_supported = bool(sanicap & 0b001)
    block_supported = bool(sanicap & 0b010)
    overwrite_supported = bool(sanicap & 0b100)
    methods = ["format"]
    if crypto_supported:
        methods.append("sanitize_crypto")
    if block_supported:
        methods.append("sanitize_block")
    return {
        "supported": True,
        "reason": "NVMe Format NVM is available. NVMe Sanitize is offered when reported by controller capabilities.",
        "tool": "nvme-cli",
        "format_supported": True,
        "sanitize_supported": crypto_supported or block_supported or overwrite_supported,
        "sanitize_crypto_supported": crypto_supported,
        "sanitize_block_supported": block_supported,
        "sanitize_overwrite_supported": overwrite_supported,
        "methods": methods,
        "details": details,
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
    "Temperature_Celsius": "Temperature",
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
        return int(token, 0)
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
        "media_errors": raw("Media_Errors", "n/a"),
        "unsafe_shutdowns": raw("Unsafe_Shutdowns", "n/a"),
    }


def enrich_report(report: dict[str, Any]) -> dict[str, Any]:
    payload = report.get("smart", {}).get("payload") or {}
    report["report_kind"] = report.get("report_kind") or classify_report_kind(report)
    report["report_kind_label"] = report_kind_label(report["report_kind"])
    report["smart_rows"] = report.get("smart_rows") or normalized_smart_rows(payload)
    report["overview"] = smart_overview(report.get("smart", {}), report.get("device", {}))
    report.setdefault("compliance", compliance_from_options(report.get("source_job", {}).get("options", {})))
    report.setdefault(
        "audit",
        [
            {
                "timestamp": report.get("generated_at") or "unknown",
                "action": "legacy_report_loaded",
                "details": {"reason": "Report was created before signed audit-chain support existed."},
            }
        ],
    )
    report["integrity"] = {
        "algorithm": "SHA-256",
        "canonical_scope": "report JSON excluding integrity, certificate, derived overview, and rendered SMART rows",
        "sha256": report_sha256(report),
    }
    if report["report_kind"] == "erase":
        report["certificate"] = certificate_payload(report)
    else:
        report.pop("certificate", None)
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
        verdict = "Sell only with clear disclosure"
    elif score >= 85 and test.get("credibility_level") in {"high", "very_high"}:
        verdict = "Good for resale"
    else:
        verdict = "Sell with normal disclosure"

    return {
        "verdict": verdict,
        "test_claim": test.get("buyer_claim", "No resale-oriented test statement recorded."),
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
            "options": source_job.options,
        }
    report["report_kind"] = classify_report_kind(report)
    report["report_kind_label"] = report_kind_label(report["report_kind"])
    report["compliance"] = compliance_from_options(source_job.options if source_job else {})
    report["audit"] = [
        audit_event(
            "report_created",
            device=disk.get("path"),
            serial=disk.get("serial"),
            mode=test_result.get("type"),
            compliance=report["compliance"]["id"],
        )
    ]
    if test_result.get("actions"):
        report["audit"].append(audit_event("pre_erase_device_actions", actions=test_result.get("actions")))
    if test_result.get("erasure"):
        report["audit"].append(audit_event("erase_completed", erasure=test_result.get("erasure")))
        verification = (test_result.get("erasure") or {}).get("verification_result")
        if verification:
            report["audit"].append(audit_event("erase_verification_recorded", verification=verification))
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
            "The SMART Short self-test executed by the drive completed successfully."
            if variant == "short"
            else "The SMART Extended self-test executed by the drive completed successfully."
        ),
        "duration_s": None,
        "polling_minutes": payload.get("ata_smart_data", {}).get("self_test", {}).get("polling_minutes", {}).get(variant),
        "self_test_status": latest.get("status", {}).get("string") or payload.get("ata_smart_data", {}).get("self_test", {}).get("status", {}).get("string"),
        "smart_passed": payload.get("smart_status", {}).get("passed"),
        "log_entry": latest,
    }
    job.progress = 1.0
    job.status = "done"
    job.current_step = "Done"
    job.error = None
    if "Test completed" not in job.messages:
        job.messages.append("Test completed")
    report = build_report_payload(disk, smart, health, test_result, source_job=job)
    report_id = save_report(report)
    job.current_step = "Saving report to export partition"
    save_job(job)
    export = auto_export_report_pdf(report_id)
    job.result = {"report_id": report_id, "report": report, "export": export}
    save_job(job)
    return job


def compute_health(disk: dict[str, Any], smart: dict[str, Any]) -> dict[str, Any]:
    score = 100
    notes: list[str] = []

    if not smart.get("available"):
        return {
            "score": 45,
            "grade": "Limited",
            "summary": "SMART data unavailable",
            "notes": [smart.get("error") or "SMART unavailable"],
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
        notes.append("SMART overall status reports a failure.")

    if reallocated > 0:
        score -= min(35, 10 + reallocated)
        notes.append(f"Reallocated sectors: {reallocated}.")
    if pending > 0:
        score -= min(30, 12 + pending * 2)
        notes.append(f"Pending bad sectors: {pending}.")
    if offline_uncorrectable > 0:
        score -= min(25, 10 + offline_uncorrectable * 2)
        notes.append(f"Offline uncorrectable errors: {offline_uncorrectable}.")
    if udma_crc > 0:
        score -= min(10, udma_crc)
        notes.append(f"CRC errors on the transfer path: {udma_crc}.")
    if hours > 30000:
        score -= 12
        notes.append(f"High power-on time: {hours} hours.")
    elif hours > 15000:
        score -= 6
        notes.append(f"Elevated power-on time: {hours} hours.")
    if start_stop > 20000:
        score -= 5
        notes.append(f"Many start/stop cycles: {start_stop}.")
    if temp and isinstance(temp, (int, float)) and temp >= 50:
        score -= 8
        notes.append(f"High temperature observed: {temp} °C.")

    score = max(0, min(100, score))
    if score >= 90:
        grade = "Excellent"
    elif score >= 75:
        grade = "Good"
    elif score >= 60:
        grade = "Fair"
    elif score >= 40:
        grade = "Risky"
    else:
        grade = "Problematic"

    summary = "Resale ready" if score >= 75 else "Sell only with clear disclosure"
    if not notes:
        notes.append("No critical SMART issues detected.")

    return {
        "score": score,
        "grade": grade,
        "summary": summary,
        "notes": notes,
    }


def running_job_snapshot_test(job: TestJob, disk: dict[str, Any]) -> dict[str, Any]:
    label_map = {
        "quick": "Running sample read test",
        "deep_sample": "Running distributed read test",
        "smart_short": "Running SMART Short self-test",
        "full": "Running full read test",
        "smart_extended": "Running SMART Extended self-test",
        "erase_zero": "Running zero erase",
        "secure_erase_ata": "Running ATA Secure Erase",
        "secure_erase_ata_enhanced": "Running ATA Enhanced Secure Erase",
        "nvme_format": "Running NVMe Format Erase",
        "nvme_sanitize_crypto": "Running NVMe Sanitize Crypto Erase",
        "nvme_sanitize_block": "Running NVMe Sanitize Block Erase",
    }
    claim_map = {
        "quick": "The sample read test is still running. This report is only a snapshot.",
        "deep_sample": "The distributed read test is still running. This report is only a snapshot.",
        "smart_short": "The SMART Short self-test is still running. This report is only a snapshot.",
        "full": "The full read test is still running. This report is only a snapshot.",
        "smart_extended": "The SMART Extended self-test is still running. This report is only a snapshot and not final proof.",
        "erase_zero": "The zero erase is still running. This report is only a snapshot.",
        "secure_erase_ata": "The ATA Secure Erase is still running. This report is only a snapshot.",
        "secure_erase_ata_enhanced": "The ATA Enhanced Secure Erase is still running. This report is only a snapshot.",
        "nvme_format": "The NVMe Format Erase is still running. This report is only a snapshot.",
        "nvme_sanitize_crypto": "The NVMe Sanitize Crypto Erase is still running. This report is only a snapshot.",
        "nvme_sanitize_block": "The NVMe Sanitize Block Erase is still running. This report is only a snapshot.",
    }
    credibility = "low" if job.mode not in {"smart_short", "smart_extended"} else "medium"
    if job.mode in {"smart_short", "smart_extended"}:
        credibility = "medium"
    return {
        "type": f"{job.mode}_snapshot",
        "label": label_map.get(job.mode, "Running test"),
        "credibility_level": credibility,
        "buyer_claim": claim_map.get(job.mode, "The test is still running. This report is only a snapshot."),
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
SMART_SELFTEST_MODES = {"smart_short", "smart_extended"}


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


def has_active_app_smart_selftest(device: str) -> bool:
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM jobs
            WHERE device = ?
              AND status IN ('queued', 'running')
            """,
            (device,),
        ).fetchall()
    for row in rows:
        job = row_to_job(row)
        if job.mode in SMART_SELFTEST_MODES:
            return True
        if job.options.get("active_post_test_mode") in SMART_SELFTEST_MODES:
            return True
    return False


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
            job.current_step = f"{selftest.get('status_text') or 'SMART self-test running'} ({100 - remaining}% complete)"
        else:
            job.current_step = selftest.get("status_text") or "SMART self-test running"
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
    path = report_file_path(report_id, report=report)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report_id


def import_exported_reports() -> None:
    try:
        targets = list_export_targets()
    except Exception:
        return

    for target in targets:
        report_dir = Path(target["mountpoint"]) / "DriveProof-Reports"
        if not report_dir.is_dir():
            continue
        sources = list(report_dir.glob("*.json")) + list(report_dir.glob("*/report.json")) + list(report_dir.glob("*/*/report.json"))
        for source in sources:
            try:
                payload = json.loads(source.read_text(encoding="utf-8"))
                report_id = payload.get("report_id")
                if not report_id:
                    continue
                destination = report_file_path(report_id, report=payload)
                if destination.exists():
                    continue
                payload.setdefault(
                    "export",
                    {
                        "report_id": report_id,
                        "status": "saved",
                        "message": f"Loaded from {target['label']}",
                        "target": target,
                        "json_path": str(source),
                        "json_name": source.name,
                    },
                )
                save_report(payload)
            except Exception:
                continue


def load_report(report_id: str) -> dict[str, Any]:
    path = report_file_path(report_id)
    if not path.exists():
        raise FileNotFoundError(report_id)
    raw = json.loads(path.read_text(encoding="utf-8"))
    original = json.loads(json.dumps(raw))
    enriched = enrich_report(raw)
    if (
        raw.get("certificate") != enriched.get("certificate")
        or raw.get("integrity") != enriched.get("integrity")
        or original.get("certificate") != enriched.get("certificate")
        or original.get("integrity") != enriched.get("integrity")
    ):
        try:
            path.write_text(json.dumps(enriched, indent=2), encoding="utf-8")
        except OSError:
            pass
    return enriched


def all_reports() -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for path in sorted(REPORT_DIR.glob("*.json"), reverse=True):
        try:
            reports.append(enrich_report(json.loads(path.read_text(encoding="utf-8"))))
        except Exception:
            continue
    return reports


def report_run_id(report: dict[str, Any]) -> str:
    source = report.get("source_job") or {}
    options = source.get("options") or {}
    return str(options.get("run_id") or options.get("batch_id") or source.get("id") or report.get("report_id"))


def reports_for_run(run_id: str) -> list[dict[str, Any]]:
    reports = [report for report in all_reports() if report_run_id(report) == run_id]
    return sorted(reports, key=lambda report: report.get("generated_at") or "")


def report_runs() -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for report in all_reports():
        groups.setdefault(report_run_id(report), []).append(report)
    runs = []
    for run_id, reports in groups.items():
        reports = sorted(reports, key=lambda report: report.get("generated_at") or "")
        kinds = sorted({report.get("report_kind") or classify_report_kind(report) for report in reports})
        devices = sorted({(report.get("device") or {}).get("path") or "unknown" for report in reports})
        runs.append(
            {
                "run_id": run_id,
                "generated_at": reports[0].get("generated_at"),
                "completed_at": reports[-1].get("generated_at"),
                "count": len(reports),
                "report_ids": [report["report_id"] for report in reports],
                "report_kinds": kinds,
                "devices": devices,
                "device_count": len(devices),
            }
        )
    return sorted(runs, key=lambda run: run.get("completed_at") or "", reverse=True)


def delete_report(report_id: str) -> None:
    path = report_file_path(report_id)
    if not path.exists():
        raise FileNotFoundError(report_id)
    path.unlink()


def persist_job_state(job: TestJob) -> None:
    with jobs_lock:
        save_job(job)


def start_test_job(device: str, mode: str, options: dict[str, Any] | None = None) -> str:
    job_id = uuid.uuid4().hex[:12]
    job = TestJob(id=job_id, device=device, mode=mode, created_at=utc_now_iso(), options=options or {})
    with jobs_lock:
        jobs[job_id] = job
    save_job(job)
    thread = threading.Thread(target=run_test_job, args=(job_id,), daemon=True)
    thread.start()
    return job_id


def start_job(device: str, mode: str, options: dict[str, Any] | None = None) -> str:
    job_id = uuid.uuid4().hex[:12]
    job = TestJob(id=job_id, device=device, mode=mode, created_at=utc_now_iso(), options=options or {})
    with jobs_lock:
        jobs[job_id] = job
    save_job(job)
    thread = threading.Thread(target=run_test_job, args=(job_id,), daemon=True)
    thread.start()
    return job_id


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
        "buyer_claim": "Multiple distributed areas of the drive were read successfully." if len(ranges) > 3 else "Multiple sample areas of the drive were read successfully.",
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
        "buyer_claim": "The full drive was verified with a sequential read pass.",
        "bytes_read": read_bytes,
        "duration_s": round(duration, 2),
        "average_throughput_mib_s": round(read_bytes / duration / (1024 * 1024), 2),
        "checkpoints": checkpoints,
    }


def verification_offsets(size_bytes: int, sample_size: int) -> list[int]:
    if size_bytes <= 0:
        return [0]
    max_offset = max(0, size_bytes - sample_size)
    points = [0.0, 0.5, 0.98]
    return sorted({int(max_offset * point) for point in points})


def verify_erase_samples(
    device_path: str,
    size_bytes: int,
    *,
    expected_byte: int = 0,
    sample_size: int = 1024 * 1024,
) -> dict[str, Any]:
    samples = []
    all_match = True
    expected = bytes([expected_byte])
    with open(device_path, "rb", buffering=0) as handle:
        for index, offset in enumerate(verification_offsets(size_bytes, sample_size), start=1):
            os.lseek(handle.fileno(), offset, os.SEEK_SET)
            data = os.read(handle.fileno(), min(sample_size, max(1, size_bytes - offset)))
            if not data:
                raise IOError(f"Verification read returned no data at offset {offset}")
            sha256 = hashlib.sha256(data).hexdigest()
            matches = data == expected * len(data)
            all_match = all_match and matches
            samples.append(
                {
                    "sample": index,
                    "offset_bytes": offset,
                    "length_bytes": len(data),
                    "expected": f"0x{expected_byte:02x}",
                    "matches_expected": matches,
                    "sha256": sha256,
                }
            )
    return {
        "type": "post_erase_sample_read",
        "sample_count": len(samples),
        "sample_size_bytes": sample_size,
        "expected_pattern": f"0x{expected_byte:02x}",
        "all_samples_match_expected": all_match,
        "samples": samples,
        "statement": "All sampled regions matched the expected erased pattern." if all_match else "At least one sampled region did not match the expected erased pattern.",
    }


def run_smart_extended_test(device_path: str, job: TestJob) -> dict[str, Any]:
    caps = smart_selftest_capabilities(device_path)
    if not caps.get("supported"):
        raise RuntimeError("SMART Extended self-test is not supported by this drive or USB adapter.")

    rc, out, err = run_command(["smartctl", "-t", "long", device_path], timeout=30)
    if rc not in (0,):
        raise RuntimeError(err.strip() or out.strip() or "Could not start SMART self-test.")

    polling_minutes = caps.get("polling_minutes", {}).get("extended")
    started_at = time.time()
    while True:
        payload = smart_payload(device_path)
        self_test = payload.get("ata_smart_data", {}).get("self_test", {})
        status = self_test.get("status", {})
        remaining = status.get("remaining_percent")
        string = status.get("string") or "SMART self-test running"
        passed = payload.get("smart_status", {}).get("passed")
        log_entries = payload.get("ata_smart_self_test_log", {}).get("standard", {}).get("table", [])

        if isinstance(remaining, int):
            job.progress = max(0.01, min(0.99, (100 - remaining) / 100))
            job.current_step = f"{string} ({100 - remaining}% complete)"
        else:
            elapsed_min = int((time.time() - started_at) / 60)
            estimate = f"{elapsed_min} min elapsed"
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
                "buyer_claim": "The SMART Extended self-test executed by the drive completed successfully.",
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
        raise RuntimeError(err.strip() or out.strip() or "Could not start SMART self-test.")

    started_at = time.time()
    poll_interval = 15 if variant == "short" else 60
    polling_key = "short" if variant == "short" else "extended"
    label = "SMART Short Self-Test" if variant == "short" else "SMART Extended Self-Test"
    credibility = "high" if variant == "short" else "very_high"
    buyer_claim = (
        "The SMART Short self-test executed by the drive completed successfully."
        if variant == "short"
        else "The SMART Extended self-test executed by the drive completed successfully."
    )

    while True:
        payload = smart_payload(device_path)
        self_test = payload.get("ata_smart_data", {}).get("self_test", {})
        status = self_test.get("status", {})
        remaining = status.get("remaining_percent")
        string = status.get("string") or f"{label} running"
        polling_minutes = self_test.get("polling_minutes", {}).get(polling_key)
        passed = payload.get("smart_status", {}).get("passed")
        log_entries = payload.get("ata_smart_self_test_log", {}).get("standard", {}).get("table", [])

        if isinstance(remaining, int):
            job.progress = max(0.01, min(0.99, (100 - remaining) / 100))
            job.current_step = f"{string} ({100 - remaining}% complete)"
        else:
            elapsed_min = int((time.time() - started_at) / 60)
            estimate = f"{elapsed_min} min elapsed"
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
    if not allow_internal and not (disk.get("hotplug") or disk.get("transport") == "usb"):
        raise RuntimeError("Destructive erase is only enabled for externally attached drives.")


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
    started_iso = utc_now_iso()
    proc = subprocess.Popen(resolved_command(cmd), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    bytes_written = 0
    progress_re = re.compile(r"(\d+)\s+bytes")
    assert proc.stderr is not None
    for line in proc.stderr:
        match = progress_re.search(line)
        if match:
            bytes_written = int(match.group(1))
            job.progress = min(0.99, bytes_written / total)
            job.current_step = f"{format_bytes(bytes_written)} of {format_bytes(total)} overwritten"
            persist_job_state(job)
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"Erase failed (exit {rc})")
    job.progress = 0.99
    job.current_step = "Verifying erased samples"
    persist_job_state(job)
    verification = verify_erase_samples(disk["path"], total, expected_byte=0)
    if not verification["all_samples_match_expected"]:
        raise RuntimeError("Zero erase verification failed: sampled regions did not all contain zeros.")
    duration = max(0.001, time.time() - started)
    compliance = compliance_from_options(job.options)
    return {
        "type": "erase_zero",
        "label": "Single-pass zero erase",
        "credibility_level": "destructive",
        "buyer_claim": "The drive was fully overwritten with zeros once before resale.",
        "duration_s": round(duration, 2),
        "bytes_written": total,
        "average_throughput_mib_s": round(total / duration / (1024 * 1024), 2),
        "actions": actions,
        "erasure": {
            "method": "single_pass_zero_overwrite",
            "tool": "dd",
            "coverage": "full_device_overwrite",
            "observability": "DriveProof wrote zeros across the full reported device size and recorded the byte count.",
            "started_at": started_iso,
            "completed_at": utc_now_iso(),
            "device": disk["path"],
            "serial": disk.get("serial"),
            "bytes_targeted": total,
            "bytes_confirmed_by_tool": total,
            "compliance_profile": compliance,
            "verification": "dd exit status, byte count, and post-erase sample reads",
            "verification_result": verification,
        },
    }


def run_secure_erase_ata(
    disk: dict[str, Any],
    job: TestJob,
    allow_internal: bool = False,
    enhanced: bool = False,
) -> dict[str, Any]:
    caps = secure_erase_capabilities(disk)
    if not caps.get("supported"):
        raise RuntimeError(caps.get("reason") or "ATA Secure Erase nicht verfuegbar.")
    method = "enhanced" if enhanced else "basic"
    if method == "enhanced" and not caps.get("enhanced_supported"):
        raise RuntimeError("ATA Enhanced Secure Erase is not reported as supported by this drive.")
    if method == "basic" and not caps.get("basic_supported"):
        raise RuntimeError("ATA Secure Erase is not reported as supported by this drive.")

    import subprocess

    erase_allowed(disk, allow_internal=allow_internal)
    actions = unmount_disk_children(disk["name"])
    before_identity_ok, before_identity = hdparm_identity(disk["path"])
    password = f"wipe-{uuid.uuid4().hex[:8]}"
    method_flag = "--security-erase-enhanced" if method == "enhanced" else "--security-erase"
    label = "ATA Enhanced Secure Erase" if method == "enhanced" else "ATA Secure Erase"
    started = time.time()
    started_iso = utc_now_iso()

    set_pass = subprocess.run(
        resolved_command(["hdparm", "--user-master", "u", f"--security-set-pass", password, disk["path"]]),
        capture_output=True,
        text=True,
        check=False,
    )
    if set_pass.returncode != 0:
        raise RuntimeError(set_pass.stderr.strip() or set_pass.stdout.strip() or "Security-Passwort konnte nicht gesetzt werden.")

    job.progress = 0.02
    job.current_step = f"{label} started"
    persist_job_state(job)

    proc = subprocess.Popen(
        resolved_command(["hdparm", "--user-master", "u", method_flag, password, disk["path"]]),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    while proc.poll() is None:
        elapsed_min = int((time.time() - started) / 60)
        job.current_step = f"{label} running ({elapsed_min} min)"
        persist_job_state(job)
        time.sleep(30)

    stdout, stderr = proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr.strip() or stdout.strip() or "ATA Secure Erase failed.")

    job.progress = 0.99
    job.current_step = "Verifying erased samples"
    persist_job_state(job)
    verification = verify_erase_samples(disk["path"], disk["size_bytes"], expected_byte=0)
    duration = max(0.001, time.time() - started)
    after_identity_ok, after_identity = hdparm_identity(disk["path"])
    implausibly_fast = bool(disk.get("rotational") and disk.get("size_bytes", 0) >= 500 * 1000 * 1000 * 1000 and duration < 300)
    compliance = compliance_from_options(job.options)
    return {
        "type": "secure_erase_ata",
        "label": label,
        "credibility_level": "destructive",
        "buyer_claim": f"The drive was erased using {label}, as reported supported by the drive and adapter.",
        "duration_s": round(duration, 2),
        "actions": actions,
        "method": method,
        "erasure": {
            "method": "ata_secure_erase_enhanced" if enhanced else "ata_secure_erase",
            "tool": "hdparm",
            "coverage": "firmware_controller_erase",
            "observability": "DriveProof issued an ATA firmware erase command and waited for hdparm to return; the internal erase work is performed by the drive firmware.",
            "implausibly_fast_warning": "Large rotational drive completed ATA firmware erase unusually quickly; treat this result as lower confidence unless independently verified." if implausibly_fast else None,
            "started_at": started_iso,
            "completed_at": utc_now_iso(),
            "device": disk["path"],
            "serial": disk.get("serial"),
            "compliance_profile": compliance,
            "hdparm_set_password_stdout": set_pass.stdout.strip(),
            "hdparm_set_password_stderr": set_pass.stderr.strip(),
            "hdparm_erase_stdout": stdout.strip(),
            "hdparm_erase_stderr": stderr.strip(),
            "hdparm_identity_before_available": before_identity_ok,
            "hdparm_identity_before_excerpt": before_identity[:4000],
            "hdparm_identity_after_available": after_identity_ok,
            "hdparm_identity_after_excerpt": after_identity[:4000],
            "verification": "hdparm exit status plus post-erase sample reads. Firmware erase implementations may not always return a zero pattern after completion.",
            "verification_result": verification,
        },
    }


def run_nvme_format_erase(disk: dict[str, Any], job: TestJob, allow_internal: bool = False) -> dict[str, Any]:
    import subprocess

    caps = nvme_erase_capabilities(disk)
    if not caps.get("supported"):
        raise RuntimeError(caps.get("reason") or "NVMe erase is not available.")

    erase_allowed(disk, allow_internal=allow_internal)
    actions = unmount_disk_children(disk["name"])
    started = time.time()
    started_iso = utc_now_iso()
    label = "NVMe Format NVM user-data erase"

    job.progress = 0.02
    job.current_step = f"{label} started"
    persist_job_state(job)

    proc = subprocess.Popen(
        resolved_command(["nvme", "format", disk["path"], "--ses=1", "--force"]),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    while proc.poll() is None:
        elapsed_min = int((time.time() - started) / 60)
        job.current_step = f"{label} running ({elapsed_min} min)"
        persist_job_state(job)
        time.sleep(15)

    stdout, stderr = proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr.strip() or stdout.strip() or "NVMe Format NVM erase failed.")

    job.progress = 0.99
    job.current_step = "Verifying erased samples"
    persist_job_state(job)
    verification = verify_erase_samples(disk["path"], disk["size_bytes"], expected_byte=0)

    duration = max(0.001, time.time() - started)
    compliance = compliance_from_options(job.options)
    return {
        "type": "nvme_format",
        "label": label,
        "credibility_level": "destructive",
        "buyer_claim": "The NVMe drive was erased using NVMe Format NVM with Secure Erase Setting 1, followed by sample verification.",
        "duration_s": round(duration, 2),
        "actions": actions,
        "erasure": {
            "method": "nvme_format_nvm_ses_1_user_data_erase",
            "tool": "nvme-cli",
            "coverage": "firmware_controller_erase",
            "observability": "DriveProof issued an NVMe Format command with secure erase setting 1 and waited for nvme-cli to return; the internal erase work is performed by the controller firmware.",
            "started_at": started_iso,
            "completed_at": utc_now_iso(),
            "device": disk["path"],
            "serial": disk.get("serial"),
            "compliance_profile": compliance,
            "nvme_stdout": stdout.strip(),
            "nvme_stderr": stderr.strip(),
            "verification": "nvme-cli exit status plus post-erase sample reads. NVMe controller behavior can vary by firmware.",
            "verification_result": verification,
        },
    }


def nvme_sanitize_log(device_path: str) -> dict[str, Any]:
    rc, out, err = run_command(["nvme", "sanitize-log", device_path, "-o", "json"], timeout=30)
    if rc != 0:
        return {"available": False, "error": err.strip() or out.strip() or "sanitize-log failed"}
    try:
        payload = json.loads(out) if out.strip() else {}
    except json.JSONDecodeError:
        return {"available": True, "raw": out.strip()}
    payload["available"] = True
    return payload


def nvme_sanitize_progress(log: dict[str, Any]) -> tuple[float | None, bool, str]:
    sprog = parse_intish(log.get("sprog"))
    sstat = parse_intish(log.get("sstat"))
    status_text = str(log.get("sstat") or log.get("status") or "sanitize in progress")
    complete = False
    progress: float | None = None
    if sprog is not None:
        progress = max(0.0, min(1.0, sprog / 65535))
        complete = sprog >= 65535
    if sstat is not None:
        status_bits = sstat & 0b111
        complete = complete or status_bits in {1, 2, 3}
        status_text = f"sanitize status {sstat}"
    return progress, complete, status_text


def run_nvme_sanitize_erase(
    disk: dict[str, Any],
    job: TestJob,
    *,
    method: str,
    allow_internal: bool = False,
) -> dict[str, Any]:
    import subprocess

    caps = nvme_erase_capabilities(disk)
    if method == "crypto" and not caps.get("sanitize_crypto_supported"):
        raise RuntimeError("NVMe Sanitize Crypto Erase is not reported as supported by this controller.")
    if method == "block" and not caps.get("sanitize_block_supported"):
        raise RuntimeError("NVMe Sanitize Block Erase is not reported as supported by this controller.")
    if method not in {"crypto", "block"}:
        raise RuntimeError(f"Unsupported NVMe sanitize method: {method}")

    erase_allowed(disk, allow_internal=allow_internal)
    actions = unmount_disk_children(disk["name"])
    sanact = "4" if method == "crypto" else "2"
    method_name = "crypto_erase" if method == "crypto" else "block_erase"
    label = "NVMe Sanitize Crypto Erase" if method == "crypto" else "NVMe Sanitize Block Erase"
    started = time.time()
    started_iso = utc_now_iso()

    job.progress = 0.02
    job.current_step = f"{label} started"
    persist_job_state(job)

    proc = subprocess.run(
        resolved_command(["nvme", "sanitize", disk["path"], f"--sanact={sanact}", "--ause"]),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"{label} failed to start.")

    final_log: dict[str, Any] = {}
    while True:
        final_log = nvme_sanitize_log(disk["path"])
        progress, complete, status_text = nvme_sanitize_progress(final_log)
        elapsed_min = int((time.time() - started) / 60)
        if progress is not None:
            job.progress = max(0.02, min(0.98, progress))
            job.current_step = f"{label} running ({round(progress * 100)}%, {elapsed_min} min)"
        else:
            job.current_step = f"{label} running ({status_text}, {elapsed_min} min)"
        persist_job_state(job)
        if complete:
            break
        time.sleep(15)

    job.progress = 0.99
    job.current_step = "Verifying erased samples"
    persist_job_state(job)
    verification = verify_erase_samples(disk["path"], disk["size_bytes"], expected_byte=0)

    duration = max(0.001, time.time() - started)
    implausibly_fast = bool(method == "block" and disk.get("size_bytes", 0) >= 500 * 1000 * 1000 * 1000 and duration < 300)
    compliance = compliance_from_options(job.options)
    return {
        "type": f"nvme_sanitize_{method}",
        "label": label,
        "credibility_level": "destructive",
        "buyer_claim": f"The NVMe drive was erased using {label}, followed by sample verification.",
        "duration_s": round(duration, 2),
        "actions": actions,
        "erasure": {
            "method": f"nvme_sanitize_{method_name}",
            "tool": "nvme-cli",
            "coverage": "firmware_controller_erase",
            "observability": "DriveProof issued an NVMe Sanitize command and monitored the sanitize log; the internal erase work is performed by the controller firmware.",
            "implausibly_fast_warning": "Large drive completed NVMe block sanitize unusually quickly; treat this result as lower confidence unless independently verified." if implausibly_fast else None,
            "started_at": started_iso,
            "completed_at": utc_now_iso(),
            "device": disk["path"],
            "serial": disk.get("serial"),
            "sanitize_log": final_log,
            "nvme_sanitize_stdout": proc.stdout.strip(),
            "nvme_sanitize_stderr": proc.stderr.strip(),
            "compliance_profile": compliance,
            "verification": "nvme-cli sanitize completion plus post-erase sample reads. NVMe controller behavior can vary by firmware.",
            "verification_result": verification,
        },
    }


def execute_job_mode(job: TestJob, disk: dict[str, Any], mode: str) -> dict[str, Any]:
    allow_internal_erase = bool(job.options.get("allow_internal_erase"))
    if mode in {"quick", "deep_sample"}:
        ranges = sample_offsets(disk["size_bytes"], mode)
        return read_segments(disk["path"], ranges, job)
    if mode == "smart_short":
        return run_smart_selftest(disk["path"], job, "short")
    if mode == "full":
        return full_read_scan(disk["path"], disk["size_bytes"], job)
    if mode == "smart_extended":
        return run_smart_extended_test(disk["path"], job)
    if mode == "erase_zero":
        return run_zero_erase(disk, job, allow_internal=allow_internal_erase)
    if mode in {"secure_erase_ata", "secure_erase_ata_enhanced"}:
        return run_secure_erase_ata(
            disk,
            job,
            allow_internal=allow_internal_erase,
            enhanced=mode == "secure_erase_ata_enhanced",
        )
    if mode == "nvme_format":
        return run_nvme_format_erase(disk, job, allow_internal=allow_internal_erase)
    if mode in {"nvme_sanitize_crypto", "nvme_sanitize_block"}:
        return run_nvme_sanitize_erase(
            disk,
            job,
            method="crypto" if mode == "nvme_sanitize_crypto" else "block",
            allow_internal=allow_internal_erase,
        )
    raise ValueError(f"Unsupported mode: {mode}")


def save_and_export_job_report(job: TestJob, disk: dict[str, Any], smart: dict[str, Any], health: dict[str, Any], test_result: dict[str, Any]) -> dict[str, Any]:
    report = build_report_payload(disk, smart, health, test_result, source_job=job)
    report_id = save_report(report)
    job.current_step = "Saving report to export partition"
    save_job(job)
    export = auto_export_report_pdf(report_id)
    return {"report_id": report_id, "report": report, "export": export}


def run_test_job(job_id: str) -> None:
    with jobs_lock:
        job = jobs.get(job_id) or get_job(job_id)
        if not job:
            return
        jobs[job_id] = job
        job.status = "running"
        job.current_step = "Reading drive data"
        job.messages.append("Test started")
        save_job(job)

    try:
        disk = get_disk(job.device)
        smart = get_smart_data(disk["path"])
        health = compute_health(disk, smart)

        test_result = execute_job_mode(job, disk, job.mode)
        post_test_mode = job.options.get("post_test_mode")
        is_erase_report = classify_report_kind({"test": test_result, "source_job": {"mode": job.mode}}) == "erase"
        allowed_post_modes = {"quick", "deep_sample", "smart_short", "smart_extended", "full"}
        if post_test_mode and (not is_erase_report or post_test_mode not in allowed_post_modes):
            raise ValueError(f"Unsupported post erase test mode: {post_test_mode}")

        result = save_and_export_job_report(job, disk, smart, health, test_result)
        post_result = None

        if is_erase_report and post_test_mode:
            with jobs_lock:
                job.status = "running"
                job.progress = 0.0
                job.current_step = f"Running post-erase test: {post_test_mode}"
                job.messages.append(f"Post-erase test started: {post_test_mode}")
                job.options["post_erase_parent_report_id"] = result["report_id"]
                job.options["active_post_test_mode"] = post_test_mode
                save_job(job)

            disk = get_disk(job.device)
            smart = get_smart_data(disk["path"])
            health = compute_health(disk, smart)
            post_test_result = execute_job_mode(job, disk, post_test_mode)
            post_result = save_and_export_job_report(job, disk, smart, health, post_test_result)
            job.messages.append("Post-erase test completed")

        with jobs_lock:
            job.progress = 1.0
            job.status = "done"
            job.current_step = "Done"
            if "Test completed" not in job.messages:
                job.messages.append("Test completed")
            job.result = {**result, "post_test": post_result}
            save_job(job)
    except Exception as exc:
        with jobs_lock:
            job.status = "error"
            job.error = str(exc)
            job.messages.append(f"Error: {exc}")
            save_job(job)


@app.route("/")
def index() -> str:
    return render_template("index.html", active_page="drives")


@app.route("/test")
def test_page() -> str:
    return redirect("/")


@app.route("/erase")
def erase_page() -> str:
    return redirect("/")


@app.route("/device/<device_name>")
def device_page(device_name: str) -> str:
    return render_template("index.html", active_page="device", selected_device=device_name)


@app.route("/jobs")
def jobs_page() -> str:
    return render_template("index.html", active_page="jobs")


@app.route("/reports")
def reports_page() -> str:
    return render_template("index.html", active_page="reports")


@app.route("/settings")
def settings_page() -> str:
    return render_template("index.html", active_page="settings")


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
        return render_template("legal.html", docs=[], active_slug=slug, error="Document not found."), 404

    docs = [{"slug": key, "title": value.stem.replace("_", " ").replace("-", " ").title()} for key, value in LEGAL_DOCS.items()]
    content = path.read_text(encoding="utf-8") if path.exists() else "Not found."
    return render_template("legal.html", docs=docs, active_slug=slug, content=content, title=path.stem.replace("_", " ").replace("-", " ").title())


@app.route("/report/<report_id>")
def report_view(report_id: str) -> str:
    try:
        report = load_report(report_id)
    except FileNotFoundError:
        abort(404)
    return render_template("report.html", report=report, github_qr_svg=github_qr_inline_svg())


@app.route("/report-run/<run_id>")
def report_run_view(run_id: str) -> str:
    reports = reports_for_run(run_id)
    if not reports:
        abort(404)
    return render_template("combined_reports.html", run_id=run_id, reports=reports, github_qr_svg=github_qr_inline_svg())


@app.route("/certificate/<report_id>")
def certificate_view(report_id: str) -> str:
    try:
        report = load_report(report_id)
    except FileNotFoundError:
        abort(404)
    if report.get("report_kind") != "erase" or not report.get("certificate"):
        abort(404)
    return render_template("certificate.html", report=report, certificate=report.get("certificate", {}), github_qr_svg=github_qr_inline_svg())


@app.get("/api/certificates/<report_id>/verify")
def api_verify_certificate(report_id: str):
    path = report_file_path(report_id)
    if not path.exists():
        return jsonify({"error": "Report not found"}), 404

    raw = json.loads(path.read_text(encoding="utf-8"))
    if classify_report_kind(raw) != "erase":
        return jsonify({"error": "Certificates are only generated for erase reports."}), 404
    certificate = raw.get("certificate") or {}
    expected_report = enrich_report(dict(raw))
    expected = expected_report.get("certificate") or {}
    persisted = bool(certificate)
    if not certificate:
        certificate = expected
    checks = {
        "report_sha256": certificate.get("report_sha256") == expected.get("report_sha256"),
        "audit_chain_sha256": certificate.get("audit_chain_sha256") == expected.get("audit_chain_sha256"),
        "signature": verify_signature(
            certificate.get("public_key_pem") or "",
            canonical_json_bytes(certificate.get("signed_payload") or {}),
            certificate.get("signature") or "",
        ),
        "signing_key_fingerprint": certificate.get("signing_key_fingerprint") == public_key_fingerprint(certificate.get("public_key_pem") or ""),
    }
    return jsonify(
        {
            "report_id": report_id,
            "valid": all(checks.values()),
            "persisted": persisted,
            "checks": checks,
            "certificate": certificate,
        }
    )


@app.get("/report/<report_id>/pdf")
def report_pdf_download(report_id: str):
    try:
        report = load_report(report_id)
    except FileNotFoundError:
        abort(404)

    base_name = report_filename(report).removesuffix(".json")
    tmp = tempfile.NamedTemporaryFile(prefix=f"{base_name}_", suffix=".pdf", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()
    try:
        render_report_pdf_to_path(report_id, tmp_path)
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        return jsonify({"error": str(exc)}), 500

    data = tmp_path.read_bytes()
    tmp_path.unlink(missing_ok=True)
    return send_file(BytesIO(data), as_attachment=True, download_name=f"{base_name}.pdf", mimetype="application/pdf")


@app.get("/report-run/<run_id>/pdf")
def report_run_pdf_download(run_id: str):
    if not reports_for_run(run_id):
        abort(404)
    base_name = f"driveproof_run_{slugify_filename_part(run_id)}"
    tmp = tempfile.NamedTemporaryFile(prefix=f"{base_name}_", suffix=".pdf", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()
    try:
        render_report_run_pdf_to_path(run_id, tmp_path)
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        return jsonify({"error": str(exc)}), 500

    data = tmp_path.read_bytes()
    tmp_path.unlink(missing_ok=True)
    return send_file(BytesIO(data), as_attachment=True, download_name=f"{base_name}.pdf", mimetype="application/pdf")


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


@app.get("/api/controllers")
def api_controllers():
    try:
        return jsonify(list_storage_controllers())
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
        nvme_erase = nvme_erase_capabilities(disk)
        selftest = sync_external_selftest_for_disk(disk)
        return jsonify(
            {
                "disk": disk,
                "smart": smart,
                "overview": smart_overview(smart, disk),
                "smart_rows": normalized_smart_rows((smart.get("payload") or {})),
                "health": health,
                "erase": erase,
                "nvme_erase": nvme_erase,
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
    compliance_profile = payload.get("compliance_profile") or "resale_basic"
    run_id = (payload.get("run_id") or "").strip() or None
    allowed_modes = {"quick", "deep_sample", "smart_short", "smart_extended", "full"}
    if not device:
        return jsonify({"error": "device is required"}), 400
    if mode not in allowed_modes:
        return jsonify({"error": f"Unknown test mode: {mode}"}), 400
    if has_active_job_for_device(device):
        return jsonify({"error": "An app job is already running for this drive."}), 409
    disk = get_disk(device)
    selftest = sync_external_selftest_for_disk(disk)
    if selftest.get("running") and mode in {"smart_short", "smart_extended"}:
        return jsonify({"error": "A SMART self-test is already running on this drive."}), 409

    job_id = start_test_job(device, mode, {"compliance_profile": compliance_profile, "run_id": run_id})
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
    allow_internal = bool(payload.get("allow_internal"))
    compliance_profile = payload.get("compliance_profile") or "nist_clear"
    run_id = (payload.get("run_id") or "").strip() or None
    post_test_mode = (payload.get("post_test_mode") or "").strip() or None
    if post_test_mode and post_test_mode not in {"quick", "deep_sample", "smart_short", "smart_extended", "full"}:
        return jsonify({"error": "post_test_mode must be quick, deep_sample, smart_short, smart_extended, or full"}), 400
    try:
        disk = get_disk(device_name)
        if has_active_job_for_device(device_name):
            return jsonify({"error": "An app job is already running for this drive."}), 409
        job_id = start_job(
            device_name,
            "erase_zero",
            {"allow_internal_erase": allow_internal, "compliance_profile": compliance_profile, "post_test_mode": post_test_mode, "run_id": run_id},
        )
        return jsonify({"job_id": job_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/disks/<device_name>/secure-erase")
def api_secure_erase_disk(device_name: str):
    payload = request.get_json(silent=True) or {}
    allow_internal = bool(payload.get("allow_internal"))
    method = (payload.get("method") or "basic").strip().lower()
    compliance_profile = payload.get("compliance_profile") or ("nist_purge" if method == "enhanced" else "nist_clear")
    run_id = (payload.get("run_id") or "").strip() or None
    post_test_mode = (payload.get("post_test_mode") or "").strip() or None
    if post_test_mode and post_test_mode not in {"quick", "deep_sample", "smart_short", "smart_extended", "full"}:
        return jsonify({"error": "post_test_mode must be quick, deep_sample, smart_short, smart_extended, or full"}), 400
    if method not in {"basic", "enhanced"}:
        return jsonify({"error": "method must be basic or enhanced"}), 400
    try:
        disk = get_disk(device_name)
        caps = secure_erase_capabilities(disk)
        if not caps.get("supported"):
            return jsonify({"error": caps.get("reason") or "ATA Secure Erase is not available."}), 400
        if method == "enhanced" and not caps.get("enhanced_supported"):
            return jsonify({"error": "ATA Enhanced Secure Erase is not supported by this drive."}), 400
        if method == "basic" and not caps.get("basic_supported"):
            return jsonify({"error": "ATA Secure Erase is not supported by this drive."}), 400
        if has_active_job_for_device(device_name):
            return jsonify({"error": "An app job is already running for this drive."}), 409
        job_id = start_job(
            device_name,
            "secure_erase_ata_enhanced" if method == "enhanced" else "secure_erase_ata",
            {
                "allow_internal_erase": allow_internal,
                "secure_erase_method": method,
                "compliance_profile": compliance_profile,
                "post_test_mode": post_test_mode,
                "run_id": run_id,
            },
        )
        return jsonify({"job_id": job_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/disks/<device_name>/nvme-erase")
def api_nvme_erase_disk(device_name: str):
    payload = request.get_json(silent=True) or {}
    allow_internal = bool(payload.get("allow_internal"))
    method = (payload.get("method") or "format").strip().lower()
    compliance_profile = payload.get("compliance_profile") or "nist_purge"
    run_id = (payload.get("run_id") or "").strip() or None
    post_test_mode = (payload.get("post_test_mode") or "").strip() or None
    if post_test_mode and post_test_mode not in {"quick", "deep_sample", "smart_short", "smart_extended", "full"}:
        return jsonify({"error": "post_test_mode must be quick, deep_sample, smart_short, smart_extended, or full"}), 400
    allowed_methods = {"format", "sanitize_crypto", "sanitize_block"}
    if method not in allowed_methods:
        return jsonify({"error": "method must be format, sanitize_crypto, or sanitize_block"}), 400
    try:
        disk = get_disk(device_name)
        caps = nvme_erase_capabilities(disk)
        if not caps.get("supported"):
            return jsonify({"error": caps.get("reason") or "NVMe erase is not available."}), 400
        if method == "sanitize_crypto" and not caps.get("sanitize_crypto_supported"):
            return jsonify({"error": "NVMe Sanitize Crypto Erase is not supported by this controller."}), 400
        if method == "sanitize_block" and not caps.get("sanitize_block_supported"):
            return jsonify({"error": "NVMe Sanitize Block Erase is not supported by this controller."}), 400
        if has_active_job_for_device(device_name):
            return jsonify({"error": "An app job is already running for this drive."}), 409
        job_id = start_job(
            device_name,
            {
                "format": "nvme_format",
                "sanitize_crypto": "nvme_sanitize_crypto",
                "sanitize_block": "nvme_sanitize_block",
            }[method],
            {
                "allow_internal_erase": allow_internal,
                "nvme_erase_method": method,
                "compliance_profile": compliance_profile,
                "post_test_mode": post_test_mode,
                "run_id": run_id,
            },
        )
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
        return jsonify({"error": "Job not found"}), 404
    if job.mode in {"smart_short", "smart_extended"} and job.status in {"queued", "running", "interrupted"}:
        sync_external_selftests(device=job.device)
        job = get_job(job_id) or job
    return jsonify(asdict(job))


@app.get("/api/reports")
def api_reports():
    import_exported_reports()
    reports = []
    for payload in all_reports():
        try:
            reports.append(
                {
                    "report_id": payload["report_id"],
                    "generated_at": payload["generated_at"],
                    "run_id": report_run_id(payload),
                    "report_kind": classify_report_kind(payload),
                    "report_kind_label": report_kind_label(classify_report_kind(payload)),
                    "device": payload["device"],
                    "health": payload["health"],
                    "test": payload.get("test") or {},
                    "export": payload.get("export"),
                }
            )
        except Exception:
            continue
    return jsonify({"reports": reports, "runs": report_runs()})


@app.get("/api/export-targets")
def api_export_targets():
    try:
        return jsonify({"targets": list_export_targets(), "pdf_engine": chrome_binary()})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/printers")
def api_printers():
    return jsonify({"printers": list_printers(), "printing_available": bool(tool_path("lp"))})


@app.get("/api/compliance-profiles")
def api_compliance_profiles():
    return jsonify({"profiles": COMPLIANCE_PROFILES})


@app.get("/api/system-tools")
def api_system_tools():
    return jsonify(system_tool_inventory())


@app.get("/api/network-config")
def api_get_network_config():
    return jsonify(read_network_config())


@app.post("/api/network-config")
def api_save_network_config():
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(write_network_config(payload))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.get("/api/vendor-tools")
def api_vendor_tools():
    try:
        return jsonify(list_vendor_tools())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/vendor-tools/<tool_id>/download-info")
def api_vendor_tool_download_info(tool_id: str):
    payload = request.get_json(silent=True) or {}
    if not payload.get("accepted_terms"):
        return jsonify({"error": "You must confirm that you accept the vendor license terms before downloading."}), 400
    item = VENDOR_TOOL_CATALOG.get(tool_id)
    if not item:
        return jsonify({"error": f"Unknown vendor tool: {tool_id}"}), 404
    try:
        root = default_vendor_tool_root()
        root.mkdir(parents=True, exist_ok=True)
        return jsonify(
            {
                "tool": {"id": tool_id, **item},
                "download_url": item["download_url"],
                "target_directory": str(root),
                "download_directory": str(root / VENDOR_TOOL_DOWNLOAD_DIR_NAME),
                "expected_names": item["tool_names"],
                "message": "The vendor site opens in the kiosk browser. Downloads are saved automatically to the DriveProof vendor tools download folder.",
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/service/status")
def api_service_status():
    return jsonify(service_status(force=request.args.get("refresh") == "1"))


@app.post("/api/reports/<report_id>/export-pdf")
def api_export_report_pdf(report_id: str):
    payload = request.get_json(silent=True) or {}
    mountpoint = (payload.get("mountpoint") or "").strip()
    try:
        result = export_report_pdf(report_id, mountpoint or None)
        update_report_export_status(report_id, result)
        return jsonify(result)
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/reports/<report_id>/print")
def api_print_report(report_id: str):
    payload = request.get_json(silent=True) or {}
    printer = (payload.get("printer") or "").strip() or None
    try:
        return jsonify(print_report_pdf(report_id, printer))
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/report-runs/<run_id>/print")
def api_print_report_run(run_id: str):
    payload = request.get_json(silent=True) or {}
    printer = (payload.get("printer") or "").strip() or None
    combined = payload.get("mode", "combined") != "individual"
    try:
        return jsonify(print_report_run(run_id, printer=printer, combined=combined))
    except FileNotFoundError:
        return jsonify({"error": "Report run not found"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/reports/<report_id>/verify-export")
def api_verify_export_bundle(report_id: str):
    try:
        report = load_report(report_id)
    except FileNotFoundError:
        return jsonify({"error": "Report not found"}), 404
    export = report.get("export") or {}
    bundle_path = export.get("bundle_path")
    if not bundle_path:
        return jsonify({"error": "Report has no exported bundle path."}), 404
    try:
        return jsonify(verify_export_bundle(bundle_path))
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.delete("/api/reports/<report_id>")
def api_delete_report(report_id: str):
    try:
        delete_report(report_id)
        return jsonify({"deleted": True, "report_id": report_id})
    except FileNotFoundError:
        return jsonify({"error": "Report not found"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(debug=False, host="127.0.0.1", port=5055, threaded=True)
