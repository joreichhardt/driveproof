from __future__ import annotations

import json
import re
import subprocess
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

CommandRunner = Callable[..., tuple[int, str, str]]
ToolResolver = Callable[[str], str | None]
IntParser = Callable[[Any], int | None]


@dataclass(frozen=True)
class ZeroEraseDeps:
    resolved_command: Callable[[list[str]], list[str]]
    unmount_disk_children: Callable[[str], list[str]]
    persist_job_state: Callable[[Any], None]
    verify_erase_samples: Callable[..., dict[str, Any]]
    compliance_from_options: Callable[[dict[str, Any] | None], str]
    format_bytes: Callable[[int], str]
    utc_now_iso: Callable[[], str]


@dataclass(frozen=True)
class AtaEraseDeps(ZeroEraseDeps):
    run_command: CommandRunner


@dataclass(frozen=True)
class NvmeFormatDeps(ZeroEraseDeps):
    run_command: CommandRunner
    tool_path: ToolResolver
    parse_intish: IntParser


@dataclass(frozen=True)
class NvmeSanitizeDeps(NvmeFormatDeps):
    sanitize_log: Callable[[str], dict[str, Any]]
    sanitize_progress: Callable[[dict[str, Any]], tuple[float | None, bool, str]]


def hdparm_identity(run_command: CommandRunner, device_path: str) -> tuple[bool, str]:
    rc, out, err = run_command(["hdparm", "-I", device_path], timeout=40)
    text = out.strip() or err.strip()
    return rc == 0 and bool(out.strip()), text


def secure_erase_capabilities(run_command: CommandRunner, disk: dict[str, Any]) -> dict[str, Any]:
    ok, text = hdparm_identity(run_command, disk["path"])
    if not ok:
        return {
            "supported": False,
            "method": None,
            "reason": "ATA Secure Erase is not available. The USB dock or drive does not expose enough hdparm information.",
        }

    lower = text.lower()
    if "security:" not in lower:
        return {"supported": False, "method": None, "reason": "ATA security feature set not found."}

    enhanced = "supported: enhanced erase" in lower or "enhanced erase" in lower
    basic = "supported" in lower and "security" in lower
    if not (basic or enhanced):
        return {"supported": False, "method": None, "reason": "Drive does not report ATA Secure Erase support."}

    return {
        "supported": True,
        "method": "basic",
        "basic_supported": basic,
        "enhanced_supported": enhanced,
        "methods": [method for method, enabled in (("basic", basic), ("enhanced", enhanced)) if enabled],
        "reason": None,
    }


def nvme_erase_capabilities(
    run_command: CommandRunner,
    tool_path: ToolResolver,
    parse_intish: IntParser,
    disk: dict[str, Any],
) -> dict[str, Any]:
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
        except json.JSONDecodeError as exc:
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


def erase_allowed(disk: dict[str, Any], allow_internal: bool = False) -> None:
    if not allow_internal and not (disk.get("hotplug") or disk.get("transport") == "usb"):
        raise RuntimeError("Destructive erase is only enabled for externally attached drives.")


def run_zero_erase(deps: ZeroEraseDeps, disk: dict[str, Any], job: Any, allow_internal: bool = False) -> dict[str, Any]:
    erase_allowed(disk, allow_internal=allow_internal)
    actions = deps.unmount_disk_children(disk["name"])
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
    started_iso = deps.utc_now_iso()
    proc = subprocess.Popen(deps.resolved_command(cmd), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    bytes_written = 0
    progress_re = re.compile(r"(\d+)\s+bytes")
    assert proc.stderr is not None
    for line in proc.stderr:
        match = progress_re.search(line)
        if match:
            try:
                bytes_written = int(match.group(1))
            except ValueError as exc:
                continue
            job.progress = min(0.99, bytes_written / total)
            job.current_step = f"{deps.format_bytes(bytes_written)} of {deps.format_bytes(total)} overwritten"
            deps.persist_job_state(job)
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"Erase failed (exit {rc})")
    job.progress = 0.99
    job.current_step = "Verifying erased samples"
    deps.persist_job_state(job)
    verification = deps.verify_erase_samples(disk["path"], total, expected_byte=0)
    if not verification["all_samples_match_expected"]:
        raise RuntimeError("Zero erase verification failed: sampled regions did not all contain zeros.")
    duration = max(0.001, time.time() - started)
    compliance = deps.compliance_from_options(job.options)
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
            "completed_at": deps.utc_now_iso(),
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
    deps: AtaEraseDeps,
    disk: dict[str, Any],
    job: Any,
    allow_internal: bool = False,
    enhanced: bool = False,
) -> dict[str, Any]:
    caps = secure_erase_capabilities(deps.run_command, disk)
    if not caps.get("supported"):
        raise RuntimeError(caps.get("reason") or "ATA Secure Erase nicht verfuegbar.")
    method = "enhanced" if enhanced else "basic"
    if method == "enhanced" and not caps.get("enhanced_supported"):
        raise RuntimeError("ATA Enhanced Secure Erase is not reported as supported by this drive.")
    if method == "basic" and not caps.get("basic_supported"):
        raise RuntimeError("ATA Secure Erase is not reported as supported by this drive.")

    erase_allowed(disk, allow_internal=allow_internal)
    actions = deps.unmount_disk_children(disk["name"])
    before_identity_ok, before_identity = hdparm_identity(deps.run_command, disk["path"])
    password = f"wipe-{uuid.uuid4().hex[:8]}"
    method_flag = "--security-erase-enhanced" if method == "enhanced" else "--security-erase"
    label = "ATA Enhanced Secure Erase" if method == "enhanced" else "ATA Secure Erase"
    started = time.time()
    started_iso = deps.utc_now_iso()

    set_pass = subprocess.run(
        deps.resolved_command(["hdparm", "--user-master", "u", "--security-set-pass", password, disk["path"]]),
        capture_output=True,
        text=True,
        check=False,
    )
    if set_pass.returncode != 0:
        raise RuntimeError(set_pass.stderr.strip() or set_pass.stdout.strip() or "Security-Passwort konnte nicht gesetzt werden.")

    job.progress = 0.02
    job.current_step = f"{label} started"
    deps.persist_job_state(job)

    proc = subprocess.Popen(
        deps.resolved_command(["hdparm", "--user-master", "u", method_flag, password, disk["path"]]),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    while proc.poll() is None:
        elapsed_min = round((time.time() - started) / 60)
        job.current_step = f"{label} running ({elapsed_min} min)"
        deps.persist_job_state(job)
        time.sleep(30)

    stdout, stderr = proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr.strip() or stdout.strip() or "ATA Secure Erase failed.")

    job.progress = 0.99
    job.current_step = "Verifying erased samples"
    deps.persist_job_state(job)
    verification = deps.verify_erase_samples(disk["path"], disk["size_bytes"], expected_byte=0)
    duration = max(0.001, time.time() - started)
    after_identity_ok, after_identity = hdparm_identity(deps.run_command, disk["path"])
    implausibly_fast = bool(disk.get("rotational") and disk.get("size_bytes", 0) >= 500 * 1000 * 1000 * 1000 and duration < 300)
    compliance = deps.compliance_from_options(job.options)
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
            "completed_at": deps.utc_now_iso(),
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


def run_nvme_format_erase(deps: NvmeFormatDeps, disk: dict[str, Any], job: Any, allow_internal: bool = False) -> dict[str, Any]:
    caps = nvme_erase_capabilities(deps.run_command, deps.tool_path, deps.parse_intish, disk)
    if not caps.get("supported"):
        raise RuntimeError(caps.get("reason") or "NVMe erase is not available.")

    erase_allowed(disk, allow_internal=allow_internal)
    actions = deps.unmount_disk_children(disk["name"])
    started = time.time()
    started_iso = deps.utc_now_iso()
    label = "NVMe Format NVM user-data erase"

    job.progress = 0.02
    job.current_step = f"{label} started"
    deps.persist_job_state(job)

    proc = subprocess.Popen(
        deps.resolved_command(["nvme", "format", disk["path"], "--ses=1", "--force"]),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    while proc.poll() is None:
        elapsed_min = round((time.time() - started) / 60)
        job.current_step = f"{label} running ({elapsed_min} min)"
        deps.persist_job_state(job)
        time.sleep(15)

    stdout, stderr = proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr.strip() or stdout.strip() or "NVMe Format NVM erase failed.")

    job.progress = 0.99
    job.current_step = "Verifying erased samples"
    deps.persist_job_state(job)
    verification = deps.verify_erase_samples(disk["path"], disk["size_bytes"], expected_byte=0)

    duration = max(0.001, time.time() - started)
    compliance = deps.compliance_from_options(job.options)
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
            "completed_at": deps.utc_now_iso(),
            "device": disk["path"],
            "serial": disk.get("serial"),
            "compliance_profile": compliance,
            "nvme_stdout": stdout.strip(),
            "nvme_stderr": stderr.strip(),
            "verification": "nvme-cli exit status plus post-erase sample reads. NVMe controller behavior can vary by firmware.",
            "verification_result": verification,
        },
    }


def run_nvme_sanitize_erase(
    deps: NvmeSanitizeDeps,
    disk: dict[str, Any],
    job: Any,
    *,
    method: str,
    allow_internal: bool = False,
) -> dict[str, Any]:
    caps = nvme_erase_capabilities(deps.run_command, deps.tool_path, deps.parse_intish, disk)
    if method == "crypto" and not caps.get("sanitize_crypto_supported"):
        raise RuntimeError("NVMe Sanitize Crypto Erase is not reported as supported by this controller.")
    if method == "block" and not caps.get("sanitize_block_supported"):
        raise RuntimeError("NVMe Sanitize Block Erase is not reported as supported by this controller.")
    if method not in {"crypto", "block"}:
        raise RuntimeError(f"Unsupported NVMe sanitize method: {method}")

    erase_allowed(disk, allow_internal=allow_internal)
    actions = deps.unmount_disk_children(disk["name"])
    sanact = "4" if method == "crypto" else "2"
    method_name = "crypto_erase" if method == "crypto" else "block_erase"
    label = "NVMe Sanitize Crypto Erase" if method == "crypto" else "NVMe Sanitize Block Erase"
    started = time.time()
    started_iso = deps.utc_now_iso()

    job.progress = 0.02
    job.current_step = f"{label} started"
    deps.persist_job_state(job)

    proc = subprocess.run(
        deps.resolved_command(["nvme", "sanitize", disk["path"], f"--sanact={sanact}", "--ause"]),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"{label} failed to start.")

    final_log: dict[str, Any] = {}
    while True:
        final_log = deps.sanitize_log(disk["path"])
        progress, complete, status_text = deps.sanitize_progress(final_log)
        elapsed_min = round((time.time() - started) / 60)
        if progress is not None:
            job.progress = max(0.02, min(0.98, progress))
            job.current_step = f"{label} running ({round(progress * 100)}%, {elapsed_min} min)"
        else:
            job.current_step = f"{label} running ({status_text}, {elapsed_min} min)"
        deps.persist_job_state(job)
        if complete:
            break
        time.sleep(15)

    job.progress = 0.99
    job.current_step = "Verifying erased samples"
    deps.persist_job_state(job)
    verification = deps.verify_erase_samples(disk["path"], disk["size_bytes"], expected_byte=0)

    duration = max(0.001, time.time() - started)
    implausibly_fast = bool(method == "block" and disk.get("size_bytes", 0) >= 500 * 1000 * 1000 * 1000 and duration < 300)
    compliance = deps.compliance_from_options(job.options)
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
            "completed_at": deps.utc_now_iso(),
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


def sanitize_log(run_command: CommandRunner, device_path: str) -> dict[str, Any]:
    rc, out, err = run_command(["nvme", "sanitize-log", device_path, "-o", "json"], timeout=30)
    if rc != 0:
        return {"available": False, "error": err.strip() or out.strip() or "sanitize-log failed"}
    try:
        payload = json.loads(out) if out.strip() else {}
    except json.JSONDecodeError as exc:
        return {"available": True, "raw": out.strip()}
    payload["available"] = True
    return payload


def sanitize_progress(parse_intish: IntParser, log: dict[str, Any]) -> tuple[float | None, bool, str]:
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
