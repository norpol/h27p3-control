{ pkgs ? import ./nix/pkgs.nix }:
pkgs.stdenv.mkDerivation {
  pname = "h27p3-control";
  version = "0.1.0";
  src = pkgs.lib.cleanSourceWith {
    src = ./.;
    # Only build inputs enter the Nix store, even in a working tree containing
    # local captures, vendor downloads, screenshots, or credentials.
    filter = path: type:
      let relative = pkgs.lib.removePrefix (toString ./. + "/") (toString path);
      in builtins.elem relative [ "Cargo.toml" "Cargo.lock" "LICENSE" "build.rs" "src" "backend" ]
        || (type == "regular" && builtins.match "src/[^/]+[.]rs" relative != null)
        || (type == "regular" && builtins.match "backend/[^/]+[.]py" relative != null);
  };
  nativeBuildInputs = with pkgs; [ rustc cargo pkg-config wrapGAppsHook4 python3 ];
  buildInputs = with pkgs; [ gtk4 libadwaita gsettings-desktop-schemas ];
  buildPhase = ''
    runHook preBuild
    export CARGO_HOME="$TMPDIR/cargo"
    cargo build --frozen --release
    runHook postBuild
  '';
  doCheck = true;
  checkPhase = ''
    runHook preCheck
    cargo test --frozen
    python3 -m unittest discover -s backend -p 'test_*.py' -v
    runHook postCheck
  '';
  installPhase = ''
    mkdir -p "$out/bin" "$out/share/h27p3-control" "$out/share/applications"
    install -m755 target/release/h27p3-control "$out/bin/h27p3-control"
    install -m644 backend/control.py backend/h27p3-*.py "$out/share/h27p3-control/"
    install -Dm644 LICENSE "$out/share/doc/h27p3-control/LICENSE"
    cat > "$out/share/applications/com.local.H27P3Control.desktop" <<EOF
    [Desktop Entry]
    Type=Application
    Name=H27P3 Control
    Comment=Control your KTC monitor and gaming overlays
    Exec=$out/bin/h27p3-control
    Icon=video-display
    Terminal=false
    Categories=Settings;HardwareSettings;
    Keywords=KTC;monitor;display;brightness;crosshair;gaming;
    StartupWMClass=com.local.H27P3Control
    EOF
  '';
  preFixup = ''
    gappsWrapperArgs+=(
      --set H27P3_BACKEND "$out/share/h27p3-control/control.py"
      --set H27P3_PYTHON "${pkgs.python3}/bin/python3"
      --prefix PATH : "${pkgs.lib.makeBinPath [ pkgs.ddcutil ]}"
    )
  '';
  meta = {
    description = "Native GTK controls for the KTC H27P3";
    homepage = "https://github.com/norpol/h27p3-control";
    license = pkgs.lib.licenses.mit;
    platforms = pkgs.lib.platforms.linux;
    mainProgram = "h27p3-control";
  };
}
