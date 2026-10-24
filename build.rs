use std::process::Command;

fn main() {
    println!("cargo:rerun-if-changed=build.rs");
    let out = Command::new("pkg-config")
        .args(["--libs", "gtk4", "libadwaita-1"])
        .output()
        .expect("Run inside nix-shell to provide pkg-config and GTK");
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    for flag in String::from_utf8(out.stdout).unwrap().split_whitespace() {
        if let Some(path) = flag.strip_prefix("-L") {
            println!("cargo:rustc-link-search=native={path}");
            println!("cargo:rustc-link-arg=-Wl,-rpath,{path}");
        } else if let Some(lib) = flag.strip_prefix("-l") {
            println!("cargo:rustc-link-lib={lib}");
        }
    }
}
