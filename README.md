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
- printable browser reports
- NixOS live image with automatic app start and Chromium kiosk mode

## Local Run on Ubuntu/Debian

Requirements:
- Python 3.11+
- `smartmontools`
- `util-linux`
- `udisks2`
- optional `hdparm` for ATA Secure Erase

Installation:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
sudo apt update
sudo apt install -y smartmontools udisks2 util-linux hdparm
```

Start:

```bash
sudo ./.venv/bin/python app.py
```

Then open in your browser:

```text
http://127.0.0.1:5055
```

## Typical Workflow

1. Attach drives via USB dock or directly inside a server.
2. Start the app with root permissions.
3. Select a drive.
4. Review SMART data and the summary panel.
5. Choose the appropriate test mode.
6. Monitor running jobs in the right-hand panel.
7. Open, print, or export the report for resale documentation.

## Nix Build

The project includes a Nix setup for a live ISO that starts the app automatically.

Requirements:
- a working `nix` installation with Flakes enabled

Build:

```bash
nix build .#iso
```

Result:

```text
./result/iso/driveproof-live.iso
```

## NixOS Live USB

The live image is intended for direct use on test systems:
- boot from USB
- automatic Flask app start
- automatic Chromium launch in kiosk mode
- direct usage without local installation

This is especially useful when multiple internal drives need to be checked in a
server or test machine.

## Create a Bootable USB Stick

Use the generated ISO image or the compressed release artifact. If you download
the compressed release asset, unpack it first:

```bash
gunzip driveproof-live.iso.gz
```

### Windows

Recommended tool: [Rufus](https://rufus.ie/)

1. Insert a USB stick.
2. Open Rufus.
3. Select the USB device.
4. Choose `driveproof-live.iso`.
5. Start the write process.
6. Boot the target machine from the USB stick.

### macOS

First identify the USB disk:

```bash
diskutil list
```

Unmount the target disk:

```bash
diskutil unmountDisk /dev/diskN
```

Write the ISO:

```bash
sudo dd if=driveproof-live.iso of=/dev/rdiskN bs=4m status=progress
sync
```

Eject the disk:

```bash
diskutil eject /dev/diskN
```

Replace `diskN` with the correct device identifier.

### Linux

Identify the USB disk:

```bash
lsblk
```

Write the ISO:

```bash
sudo dd if=driveproof-live.iso of=/dev/sdX bs=4M status=progress oflag=sync
sync
```

Replace `/dev/sdX` with the correct USB device.

Warning: writing the ISO will erase the target USB stick.

## GitHub Releases and Prebuilt Images

A prebuilt ISO can in principle be published as a GitHub release asset. The
current raw ISO build is larger than `2 GiB`, which makes direct GitHub release
upload impractical and likely above the usual asset limit.

Practical options:
- test a compressed artifact such as `.iso.gz` or `.iso.xz`
- host the raw ISO externally
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
