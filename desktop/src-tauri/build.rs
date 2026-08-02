fn main() {
    println!("cargo:rerun-if-changed=swift/AudioCaptureBridge.swift");
    println!("cargo:rerun-if-changed=swift/Package.swift");

    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos") {
        swift_rs::SwiftLinker::new("15.0")
            .with_package("AudioCaptureBridge", "swift")
            .link();
    }

    tauri_build::build();
}
