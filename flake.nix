{
  description = "DriveProof NixOS live boot image for disk diagnostics and resale testing";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.11";

  outputs = { self, nixpkgs, ... }:
    let
      system = "x86_64-linux";
    in {
      nixosConfigurations.driveproof-live = nixpkgs.lib.nixosSystem {
        inherit system;
        modules = [
          ./nixos/live-image.nix
        ];
      };

      packages.${system}.iso =
        self.nixosConfigurations.driveproof-live.config.system.build.isoImage;
    };
}
