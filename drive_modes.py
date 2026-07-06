from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable, Literal

ModeCategory = Literal["diagnostic", "erase"]
ModeExecutor = Callable[[Any, dict[str, Any]], dict[str, Any]]
ModeCapabilityCheck = Callable[[dict[str, Any]], "ModeAvailability"]


@dataclass(frozen=True)
class ModeAvailability:
    available: bool
    reason: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModeSpec:
    id: str
    label: str
    hint: str
    category: ModeCategory
    destructive: bool
    allowed_kinds: tuple[str, ...]
    compliance_default: str
    post_erase_allowed: bool = False
    enterprise_entitlement: str | None = None
    requires_local_confirmation: bool = False
    remote_allowed: bool = False
    executor_key: str | None = None

    def metadata(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("executor_key", None)
        return payload


_MODE_SPECS: dict[str, ModeSpec] = {
    "quick": ModeSpec(
        id="quick",
        label="Quick",
        hint="Short sample read test for initial sorting.",
        category="diagnostic",
        destructive=False,
        allowed_kinds=("HDD", "SSD", "NVMe"),
        compliance_default="resale_basic",
        post_erase_allowed=True,
    ),
    "deep_sample": ModeSpec(
        id="deep_sample",
        label="Deep Sample",
        hint="Distributed read test across the drive. More useful for HDDs than SSD/NVMe.",
        category="diagnostic",
        destructive=False,
        allowed_kinds=("HDD",),
        compliance_default="resale_basic",
        post_erase_allowed=True,
    ),
    "smart_short": ModeSpec(
        id="smart_short",
        label="SMART Short",
        hint="Short drive self-test. Good for SSD/NVMe and quick pre-checks.",
        category="diagnostic",
        destructive=False,
        allowed_kinds=("HDD", "SSD", "NVMe"),
        compliance_default="resale_basic",
        post_erase_allowed=True,
    ),
    "smart_extended": ModeSpec(
        id="smart_extended",
        label="SMART Extended",
        hint="Real SMART Extended self-test executed by the drive. Credible for resale.",
        category="diagnostic",
        destructive=False,
        allowed_kinds=("HDD", "SSD", "NVMe"),
        compliance_default="resale_basic",
        post_erase_allowed=True,
    ),
    "full": ModeSpec(
        id="full",
        label="Full Read",
        hint="Full read test. Takes longer and provides the strongest read-test claim for resale.",
        category="diagnostic",
        destructive=False,
        allowed_kinds=("HDD", "SSD", "NVMe"),
        compliance_default="resale_basic",
        post_erase_allowed=True,
    ),
    "bsi_erase": ModeSpec(
        id="bsi_erase",
        label="BSI Erase",
        hint="BSI IT-Grundschutz CON.6-oriented erase: HDD random overwrite, SSD/NVMe controller sanitize/secure erase where supported.",
        category="erase",
        destructive=True,
        allowed_kinds=("HDD", "SSD", "NVMe"),
        compliance_default="bsi_con6",
        enterprise_entitlement="erase.bsi-con6",
        requires_local_confirmation=True,
        remote_allowed=False,
    ),
    "bsi_crypto_erase": ModeSpec(
        id="bsi_crypto_erase",
        label="BSI Crypto Erase",
        hint="Encrypt the complete logical device with a one-time AES-XTS key, then discard the key and protocol the evidence.",
        category="erase",
        destructive=True,
        allowed_kinds=("HDD", "SSD", "NVMe"),
        compliance_default="bsi_con6_crypto",
        enterprise_entitlement="erase.bsi-crypto",
        requires_local_confirmation=True,
        remote_allowed=False,
    ),
    "erase_zero": ModeSpec(
        id="erase_zero",
        label="Zero Erase",
        hint="Single-pass zero write. Destructive.",
        category="erase",
        destructive=True,
        allowed_kinds=("HDD", "SSD", "NVMe"),
        compliance_default="nist_clear",
        enterprise_entitlement="erase.local",
        requires_local_confirmation=True,
        remote_allowed=False,
    ),
    "secure_erase_ata": ModeSpec(
        id="secure_erase_ata",
        label="ATA Secure Erase",
        hint="ATA Secure Erase. Destructive.",
        category="erase",
        destructive=True,
        allowed_kinds=("HDD", "SSD"),
        compliance_default="nist_clear",
        enterprise_entitlement="erase.ata-secure",
        requires_local_confirmation=True,
        remote_allowed=False,
    ),
    "secure_erase_ata_enhanced": ModeSpec(
        id="secure_erase_ata_enhanced",
        label="ATA Enhanced Secure Erase",
        hint="ATA Enhanced Secure Erase. Destructive.",
        category="erase",
        destructive=True,
        allowed_kinds=("HDD", "SSD"),
        compliance_default="nist_purge",
        enterprise_entitlement="erase.ata-enhanced",
        requires_local_confirmation=True,
        remote_allowed=False,
    ),
    "nvme_format": ModeSpec(
        id="nvme_format",
        label="NVMe Format Erase",
        hint="NVMe Format NVM user-data erase. Destructive.",
        category="erase",
        destructive=True,
        allowed_kinds=("NVMe",),
        compliance_default="nist_purge",
        enterprise_entitlement="erase.nvme-format",
        requires_local_confirmation=True,
        remote_allowed=False,
    ),
    "nvme_sanitize_crypto": ModeSpec(
        id="nvme_sanitize_crypto",
        label="NVMe Sanitize Crypto",
        hint="NVMe Sanitize Crypto Erase. Destructive.",
        category="erase",
        destructive=True,
        allowed_kinds=("NVMe",),
        compliance_default="nist_purge",
        enterprise_entitlement="erase.nvme-sanitize",
        requires_local_confirmation=True,
        remote_allowed=False,
    ),
    "nvme_sanitize_block": ModeSpec(
        id="nvme_sanitize_block",
        label="NVMe Sanitize Block",
        hint="NVMe Sanitize Block Erase. Destructive.",
        category="erase",
        destructive=True,
        allowed_kinds=("NVMe",),
        compliance_default="nist_purge",
        enterprise_entitlement="erase.nvme-sanitize",
        requires_local_confirmation=True,
        remote_allowed=False,
    ),
}

_MODE_ORDER = tuple(_MODE_SPECS)
_EXECUTORS: dict[str, ModeExecutor] = {}
_CAPABILITY_CHECKS: dict[str, ModeCapabilityCheck] = {}


def classify_disk_kind(disk: dict[str, Any]) -> str:
    transport = (disk.get("transport") or "").lower()
    if transport == "nvme":
        return "NVMe"
    if disk.get("rotational"):
        return "HDD"
    return "SSD"


def get_mode(mode_id: str) -> ModeSpec:
    try:
        return _MODE_SPECS[mode_id]
    except KeyError as exc:
        raise ValueError(f"Unsupported mode: {mode_id}") from exc


def list_modes(category: ModeCategory | None = None) -> list[dict[str, Any]]:
    specs = [get_mode(mode_id) for mode_id in _MODE_ORDER]
    if category is not None:
        specs = [spec for spec in specs if spec.category == category]
    return [spec.metadata() for spec in specs]


def mode_metadata(category: ModeCategory | None = "diagnostic") -> dict[str, dict[str, Any]]:
    return {mode["id"]: {key: value for key, value in mode.items() if key != "id"} for mode in list_modes(category)}


def recommended_modes_for_disk(disk: dict[str, Any]) -> list[dict[str, Any]]:
    kind = classify_disk_kind(disk)
    if kind == "HDD":
        order = ("quick", "deep_sample", "smart_extended", "full")
    else:
        order = ("quick", "smart_short", "smart_extended", "full")
    return [{"id": mode_id, **get_mode(mode_id).metadata()} for mode_id in order]


def post_erase_test_modes() -> tuple[str, ...]:
    return tuple(mode_id for mode_id, spec in _MODE_SPECS.items() if spec.post_erase_allowed)


def register_executor(mode_id: str, executor: ModeExecutor) -> None:
    get_mode(mode_id)
    _EXECUTORS[mode_id] = executor


def register_capability_check(mode_id: str, check: ModeCapabilityCheck) -> None:
    get_mode(mode_id)
    _CAPABILITY_CHECKS[mode_id] = check


def availability(mode_id: str, disk: dict[str, Any] | None = None, *, category: ModeCategory | None = None) -> ModeAvailability:
    try:
        spec = get_mode(mode_id)
    except ValueError as exc:
        return ModeAvailability(False, str(exc))
    if category is not None and spec.category != category:
        return ModeAvailability(False, f"Mode {mode_id} is not a {category} mode.")
    if disk is not None:
        kind = classify_disk_kind(disk)
        if kind not in spec.allowed_kinds:
            allowed = ", ".join(spec.allowed_kinds)
            return ModeAvailability(False, f"Mode {mode_id} is for {allowed} drives, not {kind}.")
        check = _CAPABILITY_CHECKS.get(mode_id)
        if check:
            checked = check(disk)
            if not checked.available:
                return checked
            warnings = list(checked.warnings)
            if spec.destructive and spec.requires_local_confirmation:
                warnings.append("Requires local destructive-action confirmation.")
            return ModeAvailability(True, warnings=tuple(warnings))
    warnings = ("Requires local destructive-action confirmation.",) if spec.destructive and spec.requires_local_confirmation else ()
    return ModeAvailability(True, warnings=warnings)


def ensure_available(mode_id: str, disk: dict[str, Any] | None = None, *, category: ModeCategory | None = None) -> None:
    result = availability(mode_id, disk, category=category)
    if not result.available:
        raise ValueError(result.reason or f"Mode {mode_id} is not available.")


def execute_mode(job: Any, disk: dict[str, Any], mode_id: str | None = None) -> dict[str, Any]:
    selected_mode = mode_id or getattr(job, "mode", None)
    if not selected_mode:
        raise ValueError("Mode is required.")
    ensure_available(selected_mode, disk)
    executor = _EXECUTORS.get(selected_mode)
    if not executor:
        raise ValueError(f"No executor registered for mode: {selected_mode}")
    return executor(job, disk)
