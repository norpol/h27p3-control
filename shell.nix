{ pkgs ? import ./nix/pkgs.nix }:
pkgs.mkShell {
  packages = with pkgs; [ rustc cargo rustfmt pkg-config gtk4 libadwaita python3 ddcutil ];
}
