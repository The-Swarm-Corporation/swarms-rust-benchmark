//! Exposes the resolved swarms-rs version to the benchmark as SWARMS_RS_VERSION.

fn main() {
    println!("cargo:rerun-if-changed=Cargo.lock");
    let lock = std::fs::read_to_string("Cargo.lock").unwrap_or_default();
    let mut lines = lock.lines();
    let mut version = "unknown".to_owned();
    while let Some(line) = lines.next() {
        if line == "name = \"swarms-rs\"" {
            if let Some(v) = lines.next().and_then(|l| l.strip_prefix("version = \"")) {
                version = v.trim_end_matches('"').to_owned();
            }
            break;
        }
    }
    println!("cargo:rustc-env=SWARMS_RS_VERSION={version}");
}
