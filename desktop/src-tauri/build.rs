fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(
            tauri_build::AppManifest::new().commands(&[
                "shell_status",
                "set_home",
                "pty_start",
                "pty_write",
                "pty_resize",
                "pty_stop",
            ]),
        ),
    )
    .expect("tauri-build failed");
}
