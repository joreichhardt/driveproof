SHELL := /usr/bin/env bash

NIX ?= nix
NIX_FLAGS ?= --extra-experimental-features 'nix-command flakes'
BUILD_TARGET ?= .\#usbImage
CORES ?= $(shell nproc)
IMAGE_PATH ?= $(shell find -L result -maxdepth 3 -type f -name 'driveproof-live-usb.img' 2>/dev/null | head -n 1)
USB_DEVICE ?=

.PHONY: help build build-fast build-iso build-iso-fast image-path list-usb flash-usb unmount-usb app

help:
	@echo "DriveProof build helpers"
	@echo
	@echo "Targets:"
	@echo "  make build                Build the USB image via Nix"
	@echo "  make build-fast           Build an uncompressed USB image for testing"
	@echo "  make build-iso            Build the ISO only"
	@echo "  make build-iso-fast       Build an uncompressed ISO for testing"
	@echo "  make image-path           Print the detected USB image path"
	@echo "  make list-usb             List removable USB disks"
	@echo "  make flash-usb            Auto-detect and flash the USB stick"
	@echo "  make flash-usb USB_DEVICE=/dev/sdX"
	@echo "                            Flash a specific device"
	@echo "  make app                  Start the local Flask app"
	@echo
	@echo "Variables:"
	@echo "  CORES=<n>                 Number of CPU cores for nix build"
	@echo "  USB_DEVICE=/dev/sdX       Override automatic USB detection"

build:
	$(NIX) $(NIX_FLAGS) build $(BUILD_TARGET) --cores $(CORES)

build-fast:
	$(NIX) $(NIX_FLAGS) build .\#usbImageFast --cores $(CORES)

build-iso:
	$(NIX) $(NIX_FLAGS) build .\#iso --cores $(CORES)

build-iso-fast:
	$(NIX) $(NIX_FLAGS) build .\#isoFast --cores $(CORES)

image-path:
	@image="$$(find -L result -maxdepth 3 -type f -name 'driveproof-live-usb.img' 2>/dev/null | head -n 1)"; \
	if [[ -z "$$image" ]]; then \
		echo "No driveproof-live-usb.img found. Run 'make build' first." >&2; \
		exit 1; \
	fi; \
	readlink -f "$$image"

list-usb:
	@printf '%-12s %-8s %-6s %-3s %-6s %-24s %s\n' "PATH" "SIZE" "TRAN" "RM" "TYPE" "MODEL" "SERIAL"
	@lsblk -dpln -o PATH,SIZE,TRAN,RM,TYPE,MODEL,SERIAL | awk '$$3 == "usb" && $$4 == "1" && $$5 == "disk" { printf "%-12s %-8s %-6s %-3s %-6s %-24s %s\n", $$1, $$2, $$3, $$4, $$5, $$6, $$7 }'

unmount-usb:
	@device="$(USB_DEVICE)"; \
	if [[ -z "$$device" ]]; then \
		mapfile -t devices < <(lsblk -dpno PATH,TRAN,RM,TYPE | awk '$$2 == "usb" && $$3 == "1" && $$4 == "disk" { print $$1 }'); \
		if (( $${#devices[@]} == 0 )); then \
			echo "No removable USB disk detected." >&2; \
			exit 1; \
		elif (( $${#devices[@]} > 1 )); then \
			echo "Multiple removable USB disks detected:" >&2; \
			printf '  %s\n' "$${devices[@]}" >&2; \
			echo "Set USB_DEVICE=/dev/sdX explicitly." >&2; \
			exit 1; \
		fi; \
		device="$${devices[0]}"; \
	fi; \
	echo "Unmounting partitions on $$device"; \
	while read -r part _; do \
		sudo umount "$$part" 2>/dev/null || true; \
	done < <(lsblk -lnpo PATH,TYPE "$$device" | awk '$$2 == "part" { print $$1 }')

flash-usb:
	@image="$$(find -L result -maxdepth 3 -type f -name 'driveproof-live-usb.img' 2>/dev/null | head -n 1)"; \
	if [[ -z "$$image" ]]; then \
		echo "No driveproof-live-usb.img found. Run 'make build' first." >&2; \
		exit 1; \
	fi; \
	device="$(USB_DEVICE)"; \
	if [[ -z "$$device" ]]; then \
		mapfile -t devices < <(lsblk -dpno PATH,TRAN,RM,TYPE | awk '$$2 == "usb" && $$3 == "1" && $$4 == "disk" { print $$1 }'); \
		if (( $${#devices[@]} == 0 )); then \
			echo "No removable USB disk detected." >&2; \
			exit 1; \
		elif (( $${#devices[@]} > 1 )); then \
			echo "Multiple removable USB disks detected:" >&2; \
			printf '  %s\n' "$${devices[@]}" >&2; \
			echo "Set USB_DEVICE=/dev/sdX explicitly." >&2; \
			exit 1; \
		fi; \
		device="$${devices[0]}"; \
	fi; \
	if [[ ! -b "$$device" ]]; then \
		echo "Block device not found: $$device" >&2; \
		exit 1; \
	fi; \
	size="$$(blockdev --getsize64 "$$device")"; \
	image_size="$$(stat -c %s "$$image")"; \
	if (( size < image_size )); then \
		echo "USB device $$device is too small for $$image" >&2; \
		exit 1; \
	fi; \
	echo "Using image: $$(readlink -f "$$image")"; \
	echo "Target USB : $$device"; \
	echo "Unmounting existing partitions"; \
	while read -r part _; do \
		sudo umount "$$part" 2>/dev/null || true; \
	done < <(lsblk -lnpo PATH,TYPE "$$device" | awk '$$2 == "part" { print $$1 }'); \
	echo "Flashing image with progress"; \
	sudo dd if="$$(readlink -f "$$image")" of="$$device" bs=16M status=progress conv=fsync; \
	sync; \
	echo "Flash complete: $$device"

app:
	/home/jre/dev/hdd-test/.venv/bin/python /home/jre/dev/hdd-test/app.py
