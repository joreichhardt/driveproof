# DriveProof Context

DriveProof is a workshop appliance for testing and erasing disks in place before resale.

## Domain vocabulary

### Workshop Appliance

Bootable local system used in a workshop/server room. Operator boots an old server from the DriveProof live USB, leaves installed disks in bays, then tests/erases many drives without moving them to USB docks.

### Live Instance

One running DriveProof live USB session. It owns local disk discovery, local safety confirmations, local job execution, and local report generation. In the future, an Enterprise Portal may enroll live instances and receive heartbeats.

### Drive

Physical HDD, SSD, or NVMe device discovered through Linux block-device tooling. Drive identity includes path, serial, model, vendor, transport, size, and kind. Serial is preferred for matching reports to drives; path is fallback.

### Drive Mode

A registry entry describing one diagnostic or erase workflow. A Drive Mode includes label, hint, category, destructive flag, allowed drive kinds, compliance default, Enterprise entitlement metadata, local confirmation policy, capability checks, and executor.

Examples: `quick`, `smart_extended`, `erase_zero`, `secure_erase_ata`, `nvme_format`, `nvme_sanitize_crypto`.

### Diagnostic Mode

Non-destructive Drive Mode used to produce resale evidence about drive health or readability. Examples: sample read, full read, SMART short, SMART extended.

### Erase Mode

Destructive Drive Mode used to remove existing data. Erase Modes require explicit local confirmation. Remote destructive erase is disabled unless future Enterprise identity, device matching, approval, policy, and audit requirements are implemented.

### Job

Persisted execution record for one Drive Mode on one Drive. Jobs track status, progress, current step, messages, options, result, and error. Jobs survive reloads through SQLite state.

### Post-Erase Test

Diagnostic job queued after an erase job to produce separate evidence that the erased drive is still readable/healthy. Erase report and post-erase test report stay separate but share a run id.

### Evidence Report

JSON/PDF output for a diagnostic or erase workflow. It records drive identity, SMART snapshot, health summary, job mode, result, report kind, integrity metadata, and export status.

### Erase Certificate

Buyer-facing certificate for erase evidence. It is derived from an erase report and signed with DriveProof's Ed25519 key.

### Export Bundle

Report files exported to the live USB/report partition or another configured target. Bundles may include JSON, PDF, certificate, hash, and signature material.

### Enterprise Portal

Planned control-plane product for authenticated live-instance enrollment, heartbeat ingestion, inventory/job visibility, central report archive, verification, audit log, licensing, entitlements, and eventually remote orchestration. Remote destructive erase remains disabled until safety requirements are met.

## Architecture rules

- Local live instance is source of truth for destructive action execution.
- Drive Mode registry is source of truth for mode labels, capabilities, safety policy, and Enterprise entitlement metadata.
- Reports are evidence, not just UI output; preserve identity, audit, and compliance semantics.
- Prefer in-place server-bay workflows over requiring USB adapters.
- Enterprise features must not weaken local destructive safety defaults.
