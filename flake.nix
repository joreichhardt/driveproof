{
  description = "DriveProof NixOS live boot image for disk diagnostics and resale testing";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.11";

  outputs = { self, nixpkgs, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
      exportPartitionSizeMiB = 512;
      mkUsbImage = iso:
        pkgs.runCommand "driveproof-live-usb-image"
          {
            nativeBuildInputs = [
              pkgs.coreutils
              pkgs.dosfstools
              pkgs.xorriso
            ];
          }
          ''
            set -euo pipefail

            iso_path="${iso}/iso/driveproof-live.iso"
            mkdir -p "$out/usb"
            img_path="$out/usb/driveproof-live-usb.img"
            fat_path="$TMPDIR/driveproof-export.fat"
            truncate -s ${toString exportPartitionSizeMiB}M "$fat_path"
            mkfs.vfat -F 32 -n DRVPROOF "$fat_path"

            xorriso \
              -indev "$iso_path" \
              -outdev "$img_path" \
              -boot_image any replay \
              -append_partition 3 0x0c "$fat_path" \
              -commit
          '';
    in {
      nixosConfigurations = {
        driveproof-live = nixpkgs.lib.nixosSystem {
          inherit system;
          modules = [
            ./nixos/live-image.nix
          ];
        };

        driveproof-live-fast = nixpkgs.lib.nixosSystem {
          inherit system;
          modules = [
            ./nixos/live-image.nix
            {
              isoImage.squashfsCompression = nixpkgs.lib.mkForce "gzip -no-compression";
            }
          ];
        };
      };

      packages.${system} = {
        iso = self.nixosConfigurations.driveproof-live.config.system.build.isoImage;
        isoFast = self.nixosConfigurations.driveproof-live-fast.config.system.build.isoImage;

        usbImage = mkUsbImage self.packages.${system}.iso;
        usbImageFast = mkUsbImage self.packages.${system}.isoFast;
      };
    };
}
