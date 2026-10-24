# Keep nix-build and nix-shell on the same revision as nix build/develop.
let
  lock = builtins.fromJSON (builtins.readFile ../flake.lock);
  source = lock.nodes.nixpkgs.locked;
in import (builtins.fetchTarball {
  url = "https://github.com/${source.owner}/${source.repo}/archive/${source.rev}.tar.gz";
  sha256 = source.narHash;
}) {}
