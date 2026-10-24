{
  description = "Native GTK controls for the KTC H27P3 monitor";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/02c087f3fe673c9e317f206a627cb6b910336dbe";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forSystems = nixpkgs.lib.genAttrs systems;
    in {
      packages = forSystems (system:
        let pkgs = import nixpkgs { inherit system; };
        in {
          default = import ./default.nix { inherit pkgs; };
          h27p3-control = self.packages.${system}.default;
        });
      devShells = forSystems (system: {
        default = import ./shell.nix { pkgs = import nixpkgs { inherit system; }; };
      });
      checks = forSystems (system: {
        package = self.packages.${system}.default;
      });
    };
}
