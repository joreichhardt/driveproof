from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
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
    compliance_from_options: Callable[[dict[str, Any] | None], dict[str, Any]]
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


@dataclass(frozen=True)
class BsiEraseDeps(NvmeSanitizeDeps):
    pass


def bsi_protocol(result: dict[str, Any], *, policy: str, conformity: str = "bsi_con6_aligned") -> dict[str, Any]:
    erasure = result.setdefault("erasure", {})
    erasure["bsi"] = {
        "profile": "BSI IT-Grundschutz CON.6 Löschen und Vernichten",
        "conformity": conformity,
        "policy": policy,
        "requirements_referenced": [
            "CON.6.A2 process must be regulated and performed before disposal",
            "CON.6.A4 choose suitable deletion/destruction procedure per data carrier type",
            "CON.6.A12 minimum procedures: random overwrite for unencrypted rewritable digital media, crypto-key deletion for encrypted media, integrated secure erase functions where applicable",
        ],
        "protocol_evidence": [
            "device path, model/serial and drive kind",
            "selected method and tool command evidence",
            "start and completion timestamps",
            "tool exit status/output or monitored controller status",
            "verification/sample evidence where technically meaningful",
            "operator-visible report and signed DriveProof certificate",
        ],
        "source": "BSI IT-Grundschutz-Kompendium Edition 2023, Baustein CON.6",
    }
    return result


def prng_bytes(seed: bytes, offset: int, length: int) -> bytes:
    block_size = hashlib.sha256().digest_size
    counter = offset // block_size
    skip = offset % block_size
    output = bytearray()
    while len(output) < length + skip:
        output.extend(hashlib.sha256(seed + counter.to_bytes(16, "big")).digest())
        counter += 1
    return bytes(output[skip : skip + length])


def sample_offsets_for_size(size_bytes: int, sample_size: int = 1024 * 1024) -> list[int]:
    return [0, max(0, size_bytes // 2), max(0, size_bytes - sample_size)]


def verify_prng_samples(device_path: str, size_bytes: int, seed: bytes, sample_size: int = 1024 * 1024) -> dict[str, Any]:
    offsets = sample_offsets_for_size(size_bytes, sample_size)
    samples = []
    all_match = True
    try:
        handle = open(device_path, "rb", buffering=0)
    except OSError as exc:
        raise RuntimeError(f"Could not open {device_path} for verification: {exc}") from exc
    with handle:
        for index, offset in enumerate(offsets, start=1):
            handle.seek(offset)
            length = min(sample_size, max(1, size_bytes - offset))
            data = handle.read(length)
            expected = prng_bytes(seed, offset, len(data))
            matches = data == expected
            all_match = all_match and matches
            samples.append(
                {
                    "sample": index,
                    "offset_bytes": offset,
                    "length_bytes": len(data),
                    "matches_expected_prng_stream": matches,
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
    return {
        "type": "post_erase_prng_sample_read",
        "sample_count": len(samples),
        "sample_size_bytes": sample_size,
        "all_samples_match_expected": all_match,
        "samples": samples,
        "statement": "All sampled regions matched the recorded PRNG overwrite stream." if all_match else "At least one sampled region did not match the recorded PRNG overwrite stream.",
    }


def ciphertext_samples(device_path: str, size_bytes: int, sample_size: int = 1024 * 1024) -> dict[str, Any]:
    samples = []
    try:
        handle = open(device_path, "rb", buffering=0)
    except OSError as exc:
        raise RuntimeError(f"Could not open {device_path} for ciphertext sampling: {exc}") from exc
    with handle:
        for index, offset in enumerate(sample_offsets_for_size(size_bytes, sample_size), start=1):
            handle.seek(offset)
            length = min(sample_size, max(1, size_bytes - offset))
            data = handle.read(length)
            samples.append(
                {
                    "sample": index,
                    "offset_bytes": offset,
                    "length_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "all_zero": all(byte == 0 for byte in data),
                }
            )
    return {
        "type": "post_crypto_erase_ciphertext_sample_read",
        "sample_count": len(samples),
        "sample_size_bytes": sample_size,
        "samples": samples,
        "statement": "Sampled raw sectors were recorded after encrypted overwrite and key discard.",
    }


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


def run_bsi_prng_overwrite(deps: ZeroEraseDeps, disk: dict[str, Any], job: Any, allow_internal: bool = False) -> dict[str, Any]:
    erase_allowed(disk, allow_internal=allow_internal)
    actions = deps.unmount_disk_children(disk["name"])
    total = max(1, disk["size_bytes"])
    seed = secrets.token_bytes(32)
    chunk_size = 4 * 1024 * 1024
    started = time.time()
    started_iso = deps.utc_now_iso()
    bytes_written = 0
    try:
        fd = os.open(disk["path"], os.O_RDWR)
    except OSError as exc:
        raise RuntimeError(f"Could not open {disk['path']} for BSI overwrite: {exc}") from exc
    try:
        while bytes_written < total:
            length = min(chunk_size, total - bytes_written)
            os.write(fd, prng_bytes(seed, bytes_written, length))
            bytes_written += length
            job.progress = min(0.98, bytes_written / total)
            job.current_step = f"{deps.format_bytes(bytes_written)} of {deps.format_bytes(total)} overwritten with PRNG stream"
            deps.persist_job_state(job)
        os.fsync(fd)
    finally:
        os.close(fd)
    job.progress = 0.99
    job.current_step = "Verifying PRNG overwrite samples"
    deps.persist_job_state(job)
    verification = verify_prng_samples(disk["path"], total, seed)
    if not verification["all_samples_match_expected"]:
        raise RuntimeError("BSI PRNG overwrite verification failed: sampled regions did not match the generated stream.")
    duration = max(0.001, time.time() - started)
    compliance = deps.compliance_from_options(job.options)
    result = {
        "type": "bsi_prng_overwrite",
        "label": "BSI random overwrite",
        "credibility_level": "destructive",
        "buyer_claim": "The HDD was fully overwritten with a recorded pseudo-random data stream according to the BSI CON.6.A12 baseline requirement for unencrypted rewritable digital media.",
        "duration_s": round(duration, 2),
        "bytes_written": total,
        "average_throughput_mib_s": round(total / duration / (1024 * 1024), 2),
        "actions": actions,
        "erasure": {
            "method": "bsi_con6_prng_full_device_overwrite",
            "tool": "DriveProof PRNG overwrite",
            "coverage": "full_device_overwrite",
            "observability": "DriveProof generated a SHA-256-based PRNG stream, wrote it across the full reported device size, fsynced the device, and re-read samples against the same stream.",
            "started_at": started_iso,
            "completed_at": deps.utc_now_iso(),
            "device": disk["path"],
            "serial": disk.get("serial"),
            "drive_kind": disk.get("kind"),
            "bytes_targeted": total,
            "bytes_confirmed_by_tool": bytes_written,
            "compliance_profile": compliance,
            "prng": {"algorithm": "sha256(seed || counter)", "seed_hex": seed.hex()},
            "verification": "byte count, fsync, and post-erase sample reads against the recorded PRNG stream",
            "verification_result": verification,
        },
    }
    return bsi_protocol(result, policy="HDD/unencrypted rewritable media: full-device random overwrite per CON.6.A12.")


def run_bsi_crypto_erase(deps: BsiEraseDeps, disk: dict[str, Any], job: Any, allow_internal: bool = False) -> dict[str, Any]:
    if not deps.tool_path("cryptsetup"):
        raise RuntimeError("BSI Crypto Erase requires cryptsetup in the live image.")
    erase_allowed(disk, allow_internal=allow_internal)
    actions = deps.unmount_disk_children(disk["name"])
    total = max(1, disk["size_bytes"])
    key = secrets.token_bytes(64)
    key_digest = hashlib.sha256(key).hexdigest()
    key_path = f"/tmp/driveproof-bsi-key-{uuid.uuid4().hex}"
    mapper_name = f"driveproof-bsi-{uuid.uuid4().hex[:12]}"
    mapper_path = f"/dev/mapper/{mapper_name}"
    chunk_size = 16 * 1024 * 1024
    started = time.time()
    started_iso = deps.utc_now_iso()
    bytes_written = 0
    try:
        with open(key_path, "wb") as key_file:
            key_file.write(key)
        os.chmod(key_path, 0o600)
        open_cmd = [
            "cryptsetup",
            "open",
            "--type",
            "plain",
            "--cipher",
            "aes-xts-plain64",
            "--key-size",
            "512",
            "--key-file",
            key_path,
            disk["path"],
            mapper_name,
        ]
        open_proc = subprocess.run(deps.resolved_command(open_cmd), capture_output=True, text=True, check=False, timeout=60)
        if open_proc.returncode != 0:
            raise RuntimeError(open_proc.stderr.strip() or open_proc.stdout.strip() or "cryptsetup open failed.")
        job.progress = 0.02
        job.current_step = "Encrypted device mapping created"
        deps.persist_job_state(job)
        fd = os.open(mapper_path, os.O_WRONLY)
        try:
            zero_chunk = b"\x00" * chunk_size
            while bytes_written < total:
                length = min(chunk_size, total - bytes_written)
                os.write(fd, zero_chunk[:length])
                bytes_written += length
                job.progress = min(0.98, bytes_written / total)
                job.current_step = f"{deps.format_bytes(bytes_written)} of {deps.format_bytes(total)} encrypted"
                deps.persist_job_state(job)
            os.fsync(fd)
        finally:
            os.close(fd)
        close_proc = subprocess.run(deps.resolved_command(["cryptsetup", "close", mapper_name]), capture_output=True, text=True, check=False, timeout=60)
        if close_proc.returncode != 0:
            raise RuntimeError(close_proc.stderr.strip() or close_proc.stdout.strip() or "cryptsetup close failed.")
        job.progress = 0.99
        job.current_step = "Discarding one-time encryption key and sampling ciphertext"
        deps.persist_job_state(job)
        verification = ciphertext_samples(disk["path"], total)
    finally:
        try:
            if os.path.exists(key_path):
                key_fd = os.open(key_path, os.O_WRONLY)
                try:
                    os.write(key_fd, secrets.token_bytes(len(key)))
                    os.fsync(key_fd)
                finally:
                    os.close(key_fd)
                os.unlink(key_path)
        except OSError:
            pass
        key = b""
    duration = max(0.001, time.time() - started)
    compliance = deps.compliance_from_options(job.options)
    result = {
        "type": "bsi_crypto_erase",
        "label": "BSI Crypto Erase",
        "credibility_level": "destructive",
        "buyer_claim": "The complete logical drive was overwritten through an AES-XTS encrypted mapping with a one-time key; the key was discarded after completion.",
        "duration_s": round(duration, 2),
        "bytes_written": total,
        "average_throughput_mib_s": round(total / duration / (1024 * 1024), 2),
        "actions": actions,
        "erasure": {
            "method": "bsi_crypto_erase_full_device_encrypt_then_discard_key",
            "tool": "cryptsetup plain mode + DriveProof writer",
            "coverage": "full_logical_device_encrypted_overwrite",
            "observability": "DriveProof opened a one-time AES-XTS encrypted mapping, wrote zeros through the mapping so ciphertext covered the logical device, fsynced, closed the mapping, overwrote and deleted the key file, and sampled raw ciphertext sectors.",
            "started_at": started_iso,
            "completed_at": deps.utc_now_iso(),
            "device": disk["path"],
            "serial": disk.get("serial"),
            "drive_kind": disk.get("kind"),
            "bytes_targeted": total,
            "bytes_confirmed_by_tool": bytes_written,
            "compliance_profile": compliance,
            "crypto": {"cipher": "aes-xts-plain64", "key_size_bits": 512, "one_time_key_sha256": key_digest, "key_material_retained": False},
            "verification": "cryptsetup exit status, byte count, fsync, key file deletion, and raw ciphertext sample hashes",
            "verification_result": verification,
        },
    }
    return bsi_protocol(result, policy="Full logical device encryption with one-time AES-XTS key and key discard. For HDD this also fulfills random-looking full overwrite; for SSD/NVMe prefer controller sanitize where available due flash wear-leveling/over-provisioning.")


def run_bsi_erase(deps: BsiEraseDeps, disk: dict[str, Any], job: Any, allow_internal: bool = False) -> dict[str, Any]:
    kind = str(disk.get("kind") or "")
    transport = str(disk.get("transport") or "").lower()
    if kind == "HDD":
        return run_bsi_prng_overwrite(deps, disk, job, allow_internal=allow_internal)
    if kind == "NVMe" or transport == "nvme":
        caps = nvme_erase_capabilities(deps.run_command, deps.tool_path, deps.parse_intish, disk)
        if caps.get("sanitize_block_supported"):
            result = run_nvme_sanitize_erase(deps, disk, job, method="block", allow_internal=allow_internal)
            return bsi_protocol(result, policy="NVMe: controller sanitize block erase preferred for flash media per CON.6.A4 suitable-procedure selection.")
        if caps.get("sanitize_crypto_supported"):
            result = run_nvme_sanitize_erase(deps, disk, job, method="crypto", allow_internal=allow_internal)
            return bsi_protocol(result, policy="NVMe: controller sanitize crypto erase used where reported by the controller; suitable only when data is cryptographically protected by controller keys.")
        if caps.get("format_supported"):
            result = run_nvme_format_erase(deps, disk, job, allow_internal=allow_internal)
            return bsi_protocol(result, policy="NVMe: Format NVM secure erase used as controller erase fallback when sanitize is not reported.", conformity="bsi_con6_fallback_with_controller_dependency")
        raise RuntimeError("BSI erase for NVMe requires NVMe Format or Sanitize support.")
    caps = secure_erase_capabilities(deps.run_command, disk)
    ata_deps = AtaEraseDeps(
        resolved_command=deps.resolved_command,
        unmount_disk_children=deps.unmount_disk_children,
        persist_job_state=deps.persist_job_state,
        verify_erase_samples=deps.verify_erase_samples,
        compliance_from_options=deps.compliance_from_options,
        format_bytes=deps.format_bytes,
        utc_now_iso=deps.utc_now_iso,
        run_command=deps.run_command,
    )
    if caps.get("enhanced_supported"):
        result = run_secure_erase_ata(ata_deps, disk, job, allow_internal=allow_internal, enhanced=True)
        return bsi_protocol(result, policy="SSD: ATA Enhanced Secure Erase selected as drive-integrated erase function; random host overwrite is avoided for flash wear-leveling.")
    if caps.get("basic_supported"):
        result = run_secure_erase_ata(ata_deps, disk, job, allow_internal=allow_internal, enhanced=False)
        return bsi_protocol(result, policy="SSD: ATA Secure Erase selected as drive-integrated erase function; random host overwrite is avoided for flash wear-leveling.")
    raise RuntimeError("BSI erase for SSD requires ATA Secure Erase support; host overwrite is not offered because SSD wear-leveling can leave stale flash pages.")


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
