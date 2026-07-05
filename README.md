# DriveProof

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-support-ffdd00?logo=buymeacoffee&logoColor=000000)](https://buymeacoffee.com/joreichhardt)

`DriveProof` is a Linux- and NixOS-oriented alternative to CrystalDiskInfo and GSmartControl for structured testing of used HDDs, SSDs, and NVMe drives before resale.

The focus is not just SMART visibility, but a credible resale workflow:
- detect drives automatically
- offer matching tests based on drive type
- test multiple drives in parallel
- recover running jobs after reloads
- generate resale-friendly reports
- boot directly from a NixOS live USB in kiosk mode

## Editions

DriveProof is split into a public live client and planned commercial Enterprise
components:

- `DriveProof Live`: open-source NixOS live image, local web UI, diagnostics,
  erase workflows, reports, certificates, and signed export bundles.
- `DriveProof Enterprise Server`: planned closed-source management portal for
  central report storage, fleet visibility, remote orchestration, user login,
  audit workflows, and network configuration.
- `DriveProof License Server`: planned closed-source licensing and entitlement
  service for Enterprise features.

The live image works fully in standalone mode. Enterprise features stay disabled
automatically unless a licensed Enterprise Server is discovered on the network.
See [docs/enterprise-server.md](docs/enterprise-server.md) for the public
integration contract and product split.

## Download Live Image

Latest public live image:

- `0.0.1b`
- architecture: `amd64` / `x86_64`
- OneDrive release folder: https://1drv.ms/f/c/8aa757f365d1fa83/IgAhoc92SZjpQqaPkGKHmod_AWk7fpZ1zPRKHlDMnKImvPM

Included files:
- `driveproof-live-usb.img`
- `driveproof-live-usb.img.sha256`

Architecture note:
- current live builds target `amd64` / `x86_64`
- this means standard 64-bit Intel and AMD PCs and servers
- ARM systems are not supported by this image

Verify:

```bash
sha256sum -c driveproof-live-usb.img.sha256
```

## Purpose

DriveProof is built for workshop, inventory, and server scenarios where multiple
drives need to be checked one after another or in parallel without first setting
up a full desktop environment.

## Features

- SMART evaluation via `smartctl`
- human-readable SMART attribute table
- health score with resale-oriented summary
- automatic detection of `HDD`, `SSD`, and `NVMe`
- drive-type-specific test UI:
  - HDD: `Quick`, `Deep Sample`, `SMART Extended`, `Full Read`
  - SSD/NVMe: `Quick`, `SMART Short`, `SMART Extended`, `Full Read`
- parallel jobs across multiple drives
- persistent job database for reloads and restarts
- detection of externally started SMART self-tests
- safe removal
- optional destructive erase functions with explicit safety unlocks
- printable browser reports with DriveProof logo and GitHub QR code
- direct PDF download from each report
- automatic PDF and JSON report export to the live USB FAT32 partition
- compliance-oriented report profiles for resale, NIST Clear, and NIST Purge workflows
- report SHA-256 fingerprint and basic audit trail for stronger resale evidence
- dedicated pages for testing, erasure, reports, and generated certificates
- Ed25519-signed DriveProof certificates with audit-chain hash and verification endpoint
- signed export bundles for the FAT32 report partition
- NixOS live image with automatic app start and Chromium kiosk mode

## Local Run on Ubuntu/Debian

Requirements:
- Python 3.11+
- `smartmontools`
- `util-linux`
- `udisks2`
- `hdparm` for ATA Secure Erase
- Chromium or Chrome for PDF export

Installation:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
sudo apt update
sudo apt install -y smartmontools udisks2 util-linux hdparm chromium-browser
```

Start:

```bash
sudo ./.venv/bin/python app.py
```

Then open in your browser:

```text
http://127.0.0.1:5055
```

You can also use the Makefile shortcut:

```bash
sudo make app
```

## Typical Workflow

1. Attach drives via USB dock or directly inside a server.
2. Start the app with root permissions.
3. Select a drive.
4. Review SMART data and the summary panel.
5. Choose the appropriate test mode.
6. Monitor running jobs in the right-hand panel.
7. When a test finishes, DriveProof creates a report.
8. On the live USB image, the app automatically saves PDF and JSON copies to the `DRVPROOF` FAT32 export partition.
9. Open the report, download the PDF, print it, or copy it from the USB stick for resale documentation.

## Nix Build

The project includes Nix builds for:
- a live `ISO`
- a bootable `USB disk image` with a pre-created FAT32 export partition

Requirements:
- a working `nix` installation with Flakes enabled

## Makefile Shortcuts

For local build and flashing on Linux, the repository includes a `Makefile`.

Show available commands:

```bash
make help
```

Build the bootable USB image:

```bash
make build
```

Build the uncompressed USB image:

```bash
make build-fast
```

Build the ISO only:

```bash
make build-iso
```

Build the ISO only without SquashFS compression:

```bash
make build-iso-fast
```

Print the generated USB image path:

```bash
make image-path
```

List removable USB sticks:

```bash
make list-usb
```

Flash the generated USB image to the only removable USB stick detected:

```bash
make flash-usb
```

Flash a specific device explicitly:

```bash
make flash-usb USB_DEVICE=/dev/sdX
```

Optional build tuning:

```bash
make build CORES=8
```

Under the hood, `make build` runs the Nix `usbImage` target and `make flash-usb`
uses automatic removable USB disk detection plus `dd` with progress output.
Both USB image targets are currently configured without SquashFS compression.
That makes the image larger, but keeps live-boot CPU overhead lower and avoids
slow decompression on weaker test machines.

## Direct Nix Commands

If you prefer raw Nix commands instead of `make`:

Build the ISO:

```bash
nix build .#iso
```

Result:

```text
./result/iso/driveproof-live.iso
```

Build the USB image with export partition:

```bash
nix build .#usbImage
```

Result:

```text
./result/usb/driveproof-live-usb.img
```

Uncompressed build targets:

```bash
nix build .#isoFast
nix build .#usbImageFast
```

## NixOS Live USB

The live image is intended for direct use on test systems:
- boot from USB
- automatic Flask app start
- automatic Chromium launch in kiosk mode
- direct usage without local installation

This is especially useful when multiple internal drives need to be checked in a
server or test machine.

The `usbImage` build also includes a writable FAT32 partition intended for:
- automatically exported report bundles
- copying results to a work PC
- direct access from Windows, macOS, and Linux

Current export partition details:
- filesystem label: `DRVPROOF`
- size: `512 MiB`
- report folder: `DriveProof-Reports`

At runtime DriveProof mounts this partition automatically when it is present and
writable. Finished test reports are saved there without requiring a manual
export step. The web UI shows whether the report was saved or whether export
failed.

Each finished report is exported into its own folder under:

```text
DriveProof-Reports/<timestamp>_<model>_<serial>_<report-id>/
```

The bundle contains:
- `report.pdf`
- `report.json`
- `certificate.json`
- `audit-chain.json`
- `public-key.pem`
- `manifest.json`
- `manifest.sig`

`manifest.json` contains SHA-256 hashes of all bundle files. `manifest.sig` is
an Ed25519 signature over the canonical manifest JSON. This makes changes to the
PDF, JSON report, certificate, audit chain, or public key detectable.

## Erase Functions

DriveProof separates destructive actions from diagnostics. Erase functions are
hidden behind explicit checkboxes and require typing the exact device path or
serial number before a job starts.

Available erase modes:
- `Single-pass zero erase`: writes zeros over the whole block device with `dd`
- `ATA Secure Erase`: uses `hdparm --security-erase` when the drive exposes ATA security support
- `ATA Enhanced Secure Erase`: uses `hdparm --security-erase-enhanced` only when the drive explicitly reports enhanced erase support

Compliance/report profiles:
- `Resale Basic`: SMART and read-test evidence for selling used drives
- `NIST SP 800-88 Clear`: intended for overwrite or firmware erase workflows
- `NIST SP 800-88 Purge`: intended for enhanced firmware or cryptographic erase workflows where supported

Reports include:
- selected compliance profile
- erase/test method
- device identity and serial number where available
- audit events
- SHA-256 fingerprint over the report JSON content
- a DriveProof certificate view with report hash, audit-chain hash, and Ed25519 signature
- a local verification endpoint at `/api/certificates/<report_id>/verify`
- signed export bundle verification at `/api/reports/<report_id>/verify-export`

Application pages:
- `/`: dashboard
- `/test`: diagnostics and batch test workflow
- `/erase`: destructive erase workflow
- `/reports`: report archive
- `/certificate/<report_id>`: certificate-style proof page

Important notes:
- ATA Secure Erase support depends on the drive, controller, and USB/SATA adapter.
- Many USB docks do not pass ATA security commands through.
- SATA SSDs can use ATA Secure Erase when their firmware and adapter expose it.
- NVMe drives are detected and tested. `nvme-cli` is included in the live image, but destructive NVMe Sanitize/Format execution is not enabled yet.
- For NVMe resale workflows today, use SMART/NVMe health data plus read tests, or erase with a trusted external NVMe-specific tool before reporting.
- DriveProof reports are resale evidence, not a replacement for a certified enterprise erasure platform such as Blancco unless your own process validates and accepts the workflow.
- FAT32 itself is not tamper-proof. Integrity comes from the signed manifest and certificate, not from the filesystem.
- The PDF file is not currently a native digitally signed PDF. Instead, the PDF is covered by the signed bundle manifest.
- Cloud verification, key custody policies, and third-party accreditation are future steps.

## Enterprise Server Roadmap

To compete more directly with enterprise tools, DriveProof should have a
separate server application in addition to the live boot image.

The DriveProof live image remains open source. The Enterprise Server, Management
Portal, and License Server are planned as closed-source commercial components.
This repository only contains the live client and public integration boundary.

Standalone live boot behavior:
- networking uses DHCP by default
- if no licensed Enterprise Server is discovered, Enterprise features stay
  disabled automatically
- network configuration controls are only exposed when a licensed Enterprise
  Server advertises live-client enrollment
- destructive remote erase is disabled in the open-source live client

Recommended server components:
- central report and certificate storage
- upload endpoint for live systems
- OIDC login for Google Workspace, Microsoft Entra ID, Keycloak, Authentik, etc.
- LDAP/Active Directory login for on-premise workshops
- role model: operator, supervisor, auditor, admin
- immutable report store with append-only audit events
- server-side public-key verification of every uploaded bundle
- web UI for search by serial, model, asset ID, operator, customer, and date
- API for exporting reports and certificates
- optional PXE/netboot profile management later
- license server for subscriptions, feature entitlements, offline licenses, and
  activation/revocation

This mirrors the market direction of enterprise products. Blancco offers
centralized management/reporting through Management Portal and Management Portal
On-Premise, including report/certificate management and integrations such as
Active Directory/LDAP or SAML depending on product variant. KillDisk Industrial
documents configurable XML report locations, including mapped network resources,
and certificate/report workflows.

See [docs/enterprise-server.md](docs/enterprise-server.md) for the public
integration and product split.

## Create a Bootable USB Stick

Use the generated ISO image or USB disk image. Current public release artifacts
are intended to be raw, directly flashable `.img` files rather than compressed
archives.

If you want a pre-created writable export partition on the same stick, use
`driveproof-live-usb.img` and flash that directly.

Architecture:
- the current image is `amd64` / `x86_64`
- use it on standard 64-bit Intel or AMD machines
- it is not intended for ARM-based systems

Recommended image choice:
- `driveproof-live-usb.img`: recommended for real testing workflows, because it
  already includes a writable FAT32 export partition
- `driveproof-live.iso`: useful for VM boot, optical media style workflows, or
  when you want to manage extra partitions manually

### Windows

Recommended tool: [Rufus](https://rufus.ie/)

1. Insert a USB stick.
2. Open Rufus.
3. Select the USB device.
4. Choose `driveproof-live-usb.img` if you want the built-in export partition.
5. Use `driveproof-live.iso` only if you do not need that pre-created FAT32 area.
6. Start the write process.
7. Boot the target machine from the USB stick.

### macOS

For the USB image with writable export partition, use:

```bash
diskutil list
diskutil unmountDisk /dev/diskN
sudo dd if=driveproof-live-usb.img of=/dev/rdiskN bs=4m status=progress
sync
diskutil eject /dev/diskN
```

For the plain ISO, use:

```bash
diskutil list
diskutil unmountDisk /dev/diskN
sudo dd if=driveproof-live.iso of=/dev/rdiskN bs=4m status=progress
sync
diskutil eject /dev/diskN
```

Replace `diskN` with the correct device identifier.

### Linux

From the repository on Linux, prefer the Makefile:

```bash
make build
make list-usb
make flash-usb
```

If more than one removable USB disk is attached:

```bash
make flash-usb USB_DEVICE=/dev/sdX
```

For manual flashing of the USB image with writable export partition, use:

```bash
lsblk
sudo dd if=driveproof-live-usb.img of=/dev/sdX bs=4M status=progress oflag=sync
sync
```

For the plain ISO, use:

```bash
lsblk
sudo dd if=driveproof-live.iso of=/dev/sdX bs=4M status=progress oflag=sync
sync
```

Replace `/dev/sdX` with the correct USB device.

Warning: writing the image will erase the target USB stick.

## GitHub Releases and Prebuilt Images

A prebuilt image can be published through GitHub Releases, but live images can
be several GiB. For this project the preferred public artifact is the raw
`driveproof-live-usb.img` hosted externally, because download size is acceptable
and users can flash it directly without unpacking.

Practical options:
- publish the current public build through external hosting
- host the raw USB image externally
- keep only the source on GitHub and build locally or in CI

## Project Positioning

DriveProof is not a 1:1 clone of CrystalDiskInfo or GSmartControl. It is better
described as a Linux- and NixOS-oriented resale and batch-testing interface for:
- SMART checks
- drive testing
- resale reports
- live USB operation
- multi-drive workflows

## License

The DriveProof application code in this repository is licensed under the MIT License:

- `LICENSE`

The live ISO and bundled system tools remain subject to their respective upstream
licenses. A practical overview is available here:

- `THIRD_PARTY_LICENSES.md`

Inside the live ISO, these files are available both through the app and under:

```text
/etc/driveproof/
```

## Commercial Services

DriveProof is intentionally structured to support commercial service offerings,
for example:

- custom branding
- build service
- white-label live images
- hardware-specific kiosk builds
- support and maintenance

Details:

- `COMMERCIAL_SERVICES.md`

## Support

If DriveProof is useful to you, you can support the project here:

- https://buymeacoffee.com/joreichhardt
