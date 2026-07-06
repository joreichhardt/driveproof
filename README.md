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

## Project Scope

DriveProof is an open-source live diagnostics and erase environment for local
drive resale workflows. The public repository contains the NixOS live image,
local web UI, diagnostics, erase workflows, reports, certificates, and signed
export bundles.

Commercial help can be offered around custom branding, build service, hardware
validation, deployment media, and support. See
[COMMERCIAL_SERVICES.md](COMMERCIAL_SERVICES.md).

## Download Live Image

Latest public live image:

- `0.0.1b`
- architecture: `amd64` / `x86_64`
- OneDrive release folder: <https://1drv.ms/f/c/8aa757f365d1fa83/IgAhoc92SZjpQqaPkGKHmod_AWk7fpZ1zPRKHlDMnKImvPM>

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
- automatic detection of drive type: `HDD`, `SSD`, and `NVMe`
- automatic capability checks for SMART tests and erase options
- drive-type-specific test UI:
  - HDD: `Quick`, `Deep Sample`, `SMART Extended`, `Full Read`
  - SSD/NVMe: `Quick`, `SMART Short`, `SMART Extended`, `Full Read`
- parallel jobs across multiple drives
- persistent job database for reloads and restarts
- detection of externally started SMART self-tests
- safe removal
- optional destructive erase functions with explicit safety unlocks
- firmware-based ATA Secure Erase and ATA Enhanced Secure Erase when supported
  by the drive and controller
- printable browser reports with DriveProof logo and GitHub QR code
- direct PDF download from each report
- automatic PDF and JSON report export to the live USB FAT32 partition
- compliance-oriented report profiles for resale, NIST Clear, and NIST Purge workflows
- report SHA-256 fingerprint and basic audit trail for stronger resale evidence
- dedicated pages for testing, erasure, reports, and generated certificates
- Ed25519-signed DriveProof certificates for erase reports
- optional combined erase-then-test workflow with separate erase and test reports
- NixOS live image with automatic app start and Chromium kiosk mode

## Local Run on Ubuntu/Debian

Full local execution is Linux-only at the moment. The app talks to Linux block
devices and uses Linux tooling such as `lsblk`, `udisksctl`, `smartctl`,
`hdparm`, and `nvme`.

Requirements:

- Python 3.11+
- `smartmontools` for SMART data and SMART self-tests
- `util-linux` for block-device helpers such as `lsblk`
- `udisks2` for safe removal and removable media handling
- `hdparm` for ATA Secure Erase and ATA Enhanced Secure Erase
- `nvme-cli` for NVMe device information, NVMe Format, and NVMe Sanitize
- `lsscsi` and `sg3_utils` for SCSI/SAS/HBA diagnostics
- `parted` and `dosfstools` for USB/export-partition workflows
- `eject` for safe media handling
- Chromium or Chrome for PDF export

Installation:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
sudo apt update
sudo apt install -y smartmontools udisks2 util-linux hdparm nvme-cli lsscsi sg3-utils parted dosfstools eject chromium-browser
```

On Debian or Ubuntu variants where `chromium-browser` is not available, install
`chromium` instead.

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

### Windows and macOS Local Run

Native local execution on Windows or macOS is not supported yet. DriveProof is
currently designed around Linux block-device APIs and Linux command-line tools.

Recommended options on Windows and macOS:

- boot the target machine with the DriveProof NixOS Live USB image
- use the bootable `driveproof-live-usb.img` when you want the built-in FAT32
  report export partition
- use a VM only for UI testing; direct SMART, ATA Secure Erase, NVMe, and USB
  passthrough behavior depends heavily on the hypervisor and is not a reliable
  resale-test workflow

WSL is not recommended for real drive testing because raw disk access, SMART
passthrough, USB docks, ATA security commands, and NVMe control commands are
not consistently available there.

## Linux Portable Binary

DriveProof can also be built as a Linux self-contained binary for workshop PCs
where you do not want to start it through a Python virtual environment.

The binary bundles the DriveProof Python application, templates, static assets,
and license documents. Low-level disk tools are expected to be installed on the
Linux host and available on `PATH`. This keeps the DriveProof binary small and
clear while leaving device access, udev/D-Bus integration, and browser PDF
export to the operating system.

The following host tools are required for full functionality:

- `smartctl` from `smartmontools` 7.4+
- `hdparm` 9.65+
- `nvme` from `nvme-cli` 2.8+
- `lsblk` / `blockdev` from `util-linux`
- `udisksctl` from `udisks2` 2.10+
- Chromium or Chrome for PDF export

The NixOS live image uses the pinned package set from `flake.lock`, so tool
versions are reproducible there. These minimum versions matter for local Linux
execution and for the portable binary on an existing host. DriveProof also
probes runtime capabilities because distributions may backport or patch CLI
features independently from upstream version numbers.

Build:

```bash
python3 -m venv .venv
source .venv/bin/activate
make binary-deps
make binary
```

Run:

```bash
sudo ./dist/driveproof
```

Then open:

```text
http://127.0.0.1:5055
```

Default state directory for the binary:

```text
~/.local/state/driveproof
```

Override it when needed:

```bash
sudo DRIVEPROOF_STATE_DIR=/var/lib/driveproof ./dist/driveproof
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

## Drive Type and Capability Detection

DriveProof automatically identifies whether a detected drive is an `HDD`, `SSD`,
or `NVMe` device. The UI then adapts the available diagnostics and safety
options to the detected device type and exposed controller capabilities.

Detection is used for:

- choosing sensible default test options for HDDs, SSDs, and NVMe drives
- showing SMART self-test options only when they are usable
- detecting externally running SMART self-tests
- checking whether ATA Secure Erase is exposed by the drive
- checking whether ATA Enhanced Secure Erase is explicitly supported
- separating internal drives from removable/USB-attached drives for safety

Firmware-based erase support depends on the drive firmware and the controller
path. Direct SATA connections usually expose more features than many USB docks.
DriveProof shows firmware erase options only after capability checks pass.

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

The `usbImage` build also includes writable data partitions:

`DRVPROOF` is a FAT32 partition intended for:

- automatically exported report bundles
- copying results to a work PC
- direct access from Windows, macOS, and Linux
- compatibility with simple printer kiosks and other FAT readers
- optional static network configuration

Current report partition details:

- filesystem label: `DRVPROOF`
- size: `512 MiB`
- report folder: `DriveProof-Reports`
- network config file: `driveproof-network.conf`

`DRVTOOLS` is an ext4 partition intended for optional vendor RAID/HBA tools:

- filesystem label: `DRVTOOLS`
- size: `512 MiB`
- tool folder: `DriveProof-Vendor-Tools`

At runtime DriveProof mounts these partitions automatically when present and
writable. Finished test reports are saved to `DRVPROOF` without requiring a manual
export step. The web UI shows whether the report was saved or whether export
failed.

The live system also mounts `DRVTOOLS` automatically at:

```text
/run/media/driveproof/DRVTOOLS
```

DriveProof creates a writable vendor-tool area on that partition:

```text
/run/media/driveproof/DRVTOOLS/DriveProof-Vendor-Tools
/run/media/driveproof/DRVTOOLS/DriveProof-Vendor-Tools/Downloads
```

The kiosk Chromium profile uses the `Downloads` folder as its default download
directory and disables the download prompt. When an operator opens a vendor
download site from `/settings`, the vendor page opens in a browser tab/window
and downloads should go directly to `DRVTOOLS`; no file browser selection should
be required. Close the vendor page with `Ctrl+W` to return to DriveProof.

If no DHCP server is available, open `/settings` and save a static network
configuration. DriveProof writes this file to the FAT32 report partition:

```text
ip=192.168.1.50/24
gw=192.168.1.1
dns=1.1.1.1,8.8.8.8
```

On boot, the live system reads `driveproof-network.conf` from `DRVPROOF` and
applies it to the first Ethernet interface through NetworkManager. If the file
is absent or empty, DHCP remains the default.

Before using vendor controller tools, first try to switch hardware RAID
controllers to **HBA**, **IT**, **JBOD**, or **passthrough** mode in the
controller firmware. DriveProof produces the strongest per-drive evidence when
Linux sees each physical HDD/SSD/NVMe device directly. Logical RAID volumes can
hide the individual disks, serials, SMART data, and erase capabilities.

Vendor RAID/HBA tools are not redistributed with the public image. If a site is
licensed to use tools such as `storcli`, `perccli`, `arcconf`, `ssacli`,
`hpssacli`, or Areca CLI tools, DriveProof can show vendor download links and
the target directory after the operator confirms that they accept the vendor
terms. Use these tools when passthrough/HBA mode is not possible, when the
controller needs to be reconfigured, or when controller-specific diagnostics are
required. The downloaded vendor archive/package is saved under:

```text
DriveProof-Vendor-Tools/Downloads/
```

After extraction, the final CLI binary should be placed directly under:

```text
DriveProof-Vendor-Tools/
```

A Linux filesystem such as ext4, XFS, or btrfs is used because it preserves Unix
permissions. The default `DRVPROOF` partition remains FAT32 for report exchange
with Windows, macOS, printer kiosks, and other simple readers. The generated
live USB image includes `DRVTOOLS` as the Linux tools partition.

Vendor downloads are often archives or distro packages rather than a single
binary. The live image includes common extraction helpers for ZIP, TAR, RPM, and
DEB style downloads. DriveProof should only activate a tool after a
vendor-specific adapter has found the expected binary name, copied it to the
tools directory, marked it executable, and verified it with a version/probe
command. It should not blindly execute vendor installers.

Reports are grouped by physical drive first. Each finished report is exported
into its own subfolder under the drive folder:

```text
DriveProof-Reports/<model>_<serial>/<timestamp>_<type>_<model>_<serial>_<report-id>/
```

Every report bundle contains:

- `report.pdf`
- `report.json`
- `manifest.json`

Erase report bundles additionally contain:

- `certificate.json`
- `audit-chain.json`
- `public-key.pem`
- `manifest.sig`

`manifest.json` contains SHA-256 hashes of all bundle files. For erase reports,
`manifest.sig` is an Ed25519 signature over the canonical manifest JSON. Test
reports intentionally do not create a certificate; they are diagnostic resale
reports, not erasure certificates.

## Erase Functions

DriveProof separates destructive actions from diagnostics. Erase functions are
hidden behind explicit safety checkboxes. Batch erase actions apply to the
selected drives, or to the currently selected drive when no batch selection is
active. Internal drives remain protected unless internal erase is explicitly
enabled.

DriveProof distinguishes software overwrite from firmware erase:

- software overwrite writes to the block device from the live system
- firmware erase asks the drive firmware to erase itself through ATA security
  commands when the command path is available

Available erase modes:

- `Single-pass zero erase`: writes zeros over the whole block device with `dd`
- `ATA Secure Erase`: firmware-based erase using `hdparm --security-erase`
  when the drive exposes ATA security support
- `ATA Enhanced Secure Erase`: firmware-based enhanced erase using
  `hdparm --security-erase-enhanced` only when the drive explicitly reports
  enhanced erase support
- `NVMe Format Erase`: uses `nvme format --ses=1 --force`. This asks the NVMe
  controller to format the namespace and perform a user-data erase. On many
  consumer SSDs this is the practical fast erase path, but the exact behavior is
  firmware-dependent and may be scoped to the selected namespace.
- `NVMe Sanitize Crypto`: uses `nvme sanitize --sanact=4`. This requests a
  cryptographic erase by destroying or replacing the media encryption key. It is
  typically very fast when the drive uses always-on internal encryption, but it
  is only meaningful when the device actually supports crypto sanitize.
- `NVMe Sanitize Block`: uses `nvme sanitize --sanact=2`. This requests a
  controller-managed block erase of the underlying media. It can take much
  longer than crypto sanitize, may continue internally after command submission,
  and depends on sanitize support reported by the controller.

NVMe Format and NVMe Sanitize are not the same command family. Format is a
namespace format operation with a secure erase setting. Sanitize is a controller
sanitize operation intended to make previously written user data unrecoverable
across the affected media according to the drive's implementation. DriveProof
offers only methods that `nvme-cli` and the controller report as available, and
records the chosen method in the report.

Drive type handling:

- HDDs are detected and offered HDD-oriented read tests plus ATA firmware erase
  checks where applicable.
- SATA SSDs are detected and can use ATA Secure Erase when the firmware and
  controller expose the required ATA security commands.
- NVMe drives are detected and tested with NVMe-aware health/SMART data.
  `nvme-cli` is included in the live image. Format and Sanitize methods are
  offered only when the controller and CLI report the required capabilities.
- SAS/SATA drives behind HBAs in IT/JBOD mode are expected to work through the
  normal Linux block/SCSI stack when the kernel exposes the physical drives.
  The live image includes `lsscsi` and `sg3_utils` for additional SCSI/SAS
  diagnostics.
- Hardware RAID controllers may expose only logical volumes. Prefer changing the
  controller to HBA/IT/JBOD/passthrough mode before testing or erasing so the
  physical drives are exposed directly. Physical-drive SMART access behind
  MegaRAID, HP Smart Array, Areca, 3ware, and similar controllers is
  controller-specific and may require explicit `smartctl -d ...` parameters or
  vendor tools. DriveProof should mark unsupported RAID logical volumes as
  limited evidence rather than pretending they are individual disks.

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
- for erase reports: a DriveProof certificate view with report hash, audit-chain hash, and Ed25519 signature
- for erase reports: a local verification endpoint at `/api/certificates/<report_id>/verify`
- export bundle verification at `/api/reports/<report_id>/verify-export`

Application pages:

- `/`: dashboard
- `/test`: diagnostics and batch test workflow
- `/erase`: destructive erase workflow
- `/reports`: report archive
- `/settings`: system tool and optional vendor controller tool setup
- `/certificate/<report_id>`: certificate-style proof page

Important notes:

- ATA Secure Erase support depends on the drive, controller, and USB/SATA adapter.
- Many USB docks do not pass ATA security commands through.
- SATA SSDs can use ATA Secure Erase when their firmware and adapter expose it.
- NVMe drives are detected and tested. `nvme-cli` is included in the live image. NVMe Format and Sanitize are offered only when the controller reports the required capabilities.
- DriveProof reports are resale evidence, not a replacement for a certified erasure platform such as Blancco unless your own process validates and accepts the workflow.
- FAT32 itself is not tamper-proof. Integrity comes from the signed manifest and certificate, not from the filesystem.
- The PDF file is not currently a native digitally signed PDF. Instead, the PDF is covered by the signed bundle manifest.
- Cloud verification, key custody policies, and third-party accreditation are future steps.

## Commercial Services

DriveProof can be used commercially under its open-source license. Commercial
services can include custom branding, customer-specific NixOS images, validated
USB/SATA/NVMe hardware setups, prepared boot media, and paid support.

DriveProof reports are intended as practical resale evidence. They are not a
substitute for a certified erasure process unless your own organization validates
and accepts the workflow.

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

- <https://buymeacoffee.com/joreichhardt>
