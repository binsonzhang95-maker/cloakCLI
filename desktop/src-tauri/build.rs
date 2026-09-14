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
                "list_profiles",
                "list_skills",
                "ops_status",
                "teach_chat_start",
                "teach_chat_send",
                "teach_chat_cancel",
                "teach_chat_confirm",
                "teach_chat_status",
                "teach_chat_stop",
                "job_start",
                "job_cancel",
            ]),
        ),
    )
    .expect("tauri-build failed");
}
