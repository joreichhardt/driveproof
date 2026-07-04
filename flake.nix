{
  description = "Live-boot NixOS image for HDD resale testing kiosk";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.11";

  outputs = { self, nixpkgs, ... }:
    let
      system = "x86_64-linux";
    in {
      nixosConfigurations.hdd-resale-live = nixpkgs.lib.nixosSystem {
        inherit system;
        modules = [
          ./nixos/live-image.nix
        ];
      };

      packages.${system}.iso =
        self.nixosConfigurations.hdd-resale-live.config.system.build.isoImage;
    };
}
