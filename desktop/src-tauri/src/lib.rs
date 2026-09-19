mod catalog;
mod env_inherit;
mod fleet;
mod paths;
mod pty;
mod redact;
mod runs;
mod teach;
mod teach_event;

use catalog::{OpsStatus, ProfileDto, SkillListDto};
use fleet::{
    FleetBatchSpec, FleetConfigSpec, FleetSnapshotDto, FleetSubmitResult, FleetSubmitSpec,
    LedgerPartitionDto,
};
use pty::SharedPty;
use runs::{LlmStatusDto, ResumeHintDto, RunDto};
use serde::Serialize;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use tauri::menu::Menu;
use tauri::{AppHandle, Manager, WindowEvent};
use teach::{JobStartDto, SharedTeach, TeachStartDto, TeachStatusDto};

struct AppState {
    pty: SharedPty,
    teach: SharedTeach,
    config_dir: PathBuf,
}

#[derive(Serialize)]
struct ShellStatus {
    binary: Option<String>,
    binary_error: Option<String>,
    home: Option<String>,
    home_error: Option<String>,
    running: bool,
}

fn current_exe() -> Result<PathBuf, String> {
    std::env::current_exe().map_err(|e| format!("current_exe: {e}"))
}

fn resolve_status(state: &AppState) -> ShellStatus {
    let exe = current_exe().ok();
    let stored = paths::load_stored_config(&state.config_dir);

    let (binary, binary_error) = match exe.as_ref().map(|p| paths::resolve_bin(p)) {
        Some(Ok(path)) => (Some(path.display().to_string()), None),
        Some(Err(err)) => (None, Some(err)),
        None => (None, Some("cannot resolve current executable".into())),
    };

    let (home, home_error) = match exe.as_ref() {
        Some(exe) => match paths::resolve_home(stored.cloakcli_home.as_deref(), exe) {
            Ok(path) => (Some(path.display().to_string()), None),
            Err(err) => (None, Some(err)),
        },
        None => (None, Some("cannot resolve current executable".into())),
    };

    ShellStatus {
        binary,
        binary_error,
        home,
        home_error,
        running: pty::is_running(&state.pty),
    }
}

#[tauri::command]
fn shell_status(state: tauri::State<AppState>) -> ShellStatus {
    resolve_status(&state)
}

#[tauri::command]
fn set_home(state: tauri::State<AppState>, path: String) -> Result<String, String> {
    let home = paths::validate_home(&path)?;
    paths::save_stored_home(&state.config_dir, &home)?;
    Ok(home.display().to_string())
}

#[tauri::command]
fn pty_start(app: AppHandle, cols: u16, rows: u16) -> Result<(), String> {
    let state = app.state::<AppState>();
    let exe = current_exe()?;
    let stored = paths::load_stored_config(&state.config_dir);
    let bin = paths::resolve_bin(&exe)?;
    let home = paths::resolve_home(stored.cloakcli_home.as_deref(), &exe)?;
    pty::start(&state.pty, app.clone(), &bin, &home, cols, rows)
}

#[tauri::command]
fn pty_write(state: tauri::State<AppState>, data: String) -> Result<(), String> {
    pty::write_input(&state.pty, &data)
}

#[tauri::command]
fn pty_resize(state: tauri::State<AppState>, cols: u16, rows: u16) -> Result<(), String> {
    pty::resize(&state.pty, cols, rows)
}

#[tauri::command]
fn pty_stop(app: AppHandle) -> Result<(), String> {
    let state = app.state::<AppState>();
    pty::stop(&state.pty, Some(&app))
}

fn resolved_home(state: &AppState) -> Result<PathBuf, String> {
    let exe = current_exe()?;
    let stored = paths::load_stored_config(&state.config_dir);
    paths::resolve_home(stored.cloakcli_home.as_deref(), &exe)
}

#[tauri::command]
fn list_profiles(state: tauri::State<AppState>) -> Result<Vec<ProfileDto>, String> {
    let home = resolved_home(&state)?;
    catalog::list_profiles(&home)
}

#[tauri::command]
fn list_skills(state: tauri::State<AppState>) -> Result<SkillListDto, String> {
    let home = resolved_home(&state)?;
    catalog::list_skills(&home)
}

#[tauri::command]
fn teach_chat_start(
    app: AppHandle,
    profile: String,
    url: Option<String>,
    spawn_browser: Option<bool>,
) -> Result<TeachStartDto, String> {
    let state = app.state::<AppState>();
    let exe = current_exe()?;
    let stored = paths::load_stored_config(&state.config_dir);
    let bin = paths::resolve_bin(&exe)?;
    let home = paths::resolve_home(stored.cloakcli_home.as_deref(), &exe)?;
    let spawn = spawn_browser.unwrap_or_else(teach::default_spawn_browser);
    teach::start(
        &state.teach,
        app.clone(),
        &bin,
        &home,
        &profile,
        url.as_deref(),
        spawn,
    )
}

#[tauri::command]
fn teach_chat_send(
    state: tauri::State<AppState>,
    goal: String,
    profile: Option<String>,
    skill: Option<String>,
) -> Result<(), String> {
    teach::send(
        &state.teach,
        &goal,
        profile.as_deref(),
        skill.as_deref(),
    )
}

#[tauri::command]
fn teach_chat_cancel(state: tauri::State<AppState>) -> Result<(), String> {
    teach::cancel(&state.teach)
}

#[tauri::command]
fn teach_chat_confirm(state: tauri::State<AppState>, yes: bool) -> Result<(), String> {
    teach::confirm(&state.teach, yes)
}

#[tauri::command]
fn teach_chat_status(state: tauri::State<AppState>) -> Result<TeachStatusDto, String> {
    teach::request_status(&state.teach)
}

#[tauri::command]
fn teach_chat_stop(state: tauri::State<AppState>) -> Result<(), String> {
    teach::stop(&state.teach)
}

#[tauri::command]
fn job_start(state: tauri::State<AppState>) -> JobStartDto {
    teach::job_start_dto(&state.teach)
}

#[tauri::command]
fn job_cancel(state: tauri::State<AppState>) -> Result<(), String> {
    teach::cancel(&state.teach)
}

#[tauri::command]
fn list_runs(state: tauri::State<AppState>) -> Result<Vec<RunDto>, String> {
    let home = resolved_home(&state)?;
    Ok(runs::list_runs(&home))
}

#[tauri::command]
fn fleet_status(state: tauri::State<AppState>) -> Result<FleetSnapshotDto, String> {
    let home = resolved_home(&state)?;
    Ok(fleet::fleet_status(&home))
}

#[tauri::command]
fn fleet_submit(
    state: tauri::State<AppState>,
    spec: FleetSubmitSpec,
) -> Result<FleetSubmitResult, String> {
    let home = resolved_home(&state)?;
    fleet::fleet_submit(&home, spec)
}

#[tauri::command]
fn fleet_submit_batch(
    state: tauri::State<AppState>,
    spec: FleetBatchSpec,
) -> Result<Vec<FleetSubmitResult>, String> {
    let home = resolved_home(&state)?;
    fleet::fleet_submit_batch(&home, spec)
}

#[tauri::command]
fn fleet_sync(
    state: tauri::State<AppState>,
    client_id: String,
    skill_id: String,
    version: Option<String>,
) -> Result<serde_json::Value, String> {
    let home = resolved_home(&state)?;
    fleet::fleet_sync(&home, client_id, skill_id, version)
}

#[tauri::command]
fn fleet_config(
    state: tauri::State<AppState>,
    spec: FleetConfigSpec,
) -> Result<serde_json::Value, String> {
    let home = resolved_home(&state)?;
    fleet::fleet_config(&home, spec)
}

#[tauri::command]
fn fleet_park(
    state: tauri::State<AppState>,
    job_id: String,
    reason: Option<String>,
) -> Result<serde_json::Value, String> {
    let home = resolved_home(&state)?;
    fleet::fleet_park(&home, job_id, reason)
}

#[tauri::command]
fn fleet_retry(state: tauri::State<AppState>, job_id: String) -> Result<FleetSubmitResult, String> {
    let home = resolved_home(&state)?;
    fleet::fleet_retry(&home, job_id)
}

#[tauri::command]
fn list_ledgers(state: tauri::State<AppState>) -> Result<Vec<LedgerPartitionDto>, String> {
    let home = resolved_home(&state)?;
    Ok(fleet::list_ledgers(&home))
}

#[tauri::command]
fn llm_status(state: tauri::State<AppState>) -> Result<LlmStatusDto, String> {
    let home = resolved_home(&state)?;
    Ok(runs::llm_status(&home))
}

#[tauri::command]
fn teach_resume_hint(state: tauri::State<AppState>) -> Result<ResumeHintDto, String> {
    let home = resolved_home(&state)?;
    Ok(runs::resume_hint(&home))
}

#[tauri::command]
fn ops_status(state: tauri::State<AppState>) -> OpsStatus {
    let exe = current_exe().ok();
    let stored = paths::load_stored_config(&state.config_dir);

    let (binary, binary_error) = match exe.as_ref().map(|p| paths::resolve_bin(p)) {
        Some(Ok(path)) => (Some(path.display().to_string()), None),
        Some(Err(err)) => (None, Some(err)),
        None => (None, Some("cannot resolve current executable".into())),
    };

    let (home, home_error) = match exe.as_ref() {
        Some(exe) => match paths::resolve_home(stored.cloakcli_home.as_deref(), exe) {
            Ok(path) => (Some(path), None),
            Err(err) => (None, Some(err)),
        },
        None => (None, Some("cannot resolve current executable".into())),
    };

    catalog::ops_status(
        home.as_deref(),
        home_error,
        binary,
        binary_error,
        pty::is_running(&state.pty),
    )
}

fn terminate_pty(app: &AppHandle) {
    if let Some(state) = app.try_state::<AppState>() {
        let _ = pty::stop(&state.pty, Some(app));
        let _ = teach::stop(&state.teach);
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            // Release builds compile without the `devtools` Cargo feature, so
            // WebView inspector is off. Debug (`tauri dev`) keeps DevTools.
            let menu = Menu::new(app.handle())?;
            app.set_menu(menu)?;

            let config_dir = app
                .path()
                .app_config_dir()
                .unwrap_or_else(|_| std::env::temp_dir().join("cloakcli-desktop"));
            app.manage(AppState {
                pty: Arc::new(Mutex::new(None)),
                teach: Arc::new(Mutex::new(None)),
                config_dir,
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            shell_status,
            set_home,
            pty_start,
            pty_write,
            pty_resize,
            pty_stop,
            list_profiles,
            list_skills,
            ops_status,
            teach_chat_start,
            teach_chat_send,
            teach_chat_cancel,
            teach_chat_confirm,
            teach_chat_status,
            teach_chat_stop,
            job_start,
            job_cancel,
            list_runs,
            llm_status,
            teach_resume_hint,
            fleet_status,
            fleet_submit,
            fleet_submit_batch,
            fleet_sync,
            fleet_config,
            fleet_park,
            fleet_retry,
            list_ledgers
        ])
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                terminate_pty(window.app_handle());
                let _ = window.destroy();
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building CloakCLI desktop")
        .run(|app, event| {
            if matches!(
                event,
                tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit
            ) {
                terminate_pty(app);
            }
        });
}

#[cfg(test)]
mod capability_tests {
    #[test]
    fn capabilities_are_minimal() {
        let raw = include_str!("../capabilities/default.json");
        let v: serde_json::Value = serde_json::from_str(raw).expect("capabilities json");
        let perms = v["permissions"].as_array().expect("permissions");
        let joined = perms
            .iter()
            .filter_map(|p| p.as_str())
            .collect::<Vec<_>>()
            .join(",");
        assert!(!joined.contains("shell:"), "{joined}");
        assert!(!joined.contains("fs:"), "{joined}");
        assert!(!joined.contains("os:"), "{joined}");
        assert!(!joined.contains("opener"), "{joined}");
        assert!(joined.contains("allow-list-runs"), "{joined}");
        assert!(joined.contains("allow-llm-status"), "{joined}");
        assert!(joined.contains("allow-teach-resume-hint"), "{joined}");
        assert!(joined.contains("allow-teach-chat-start"), "{joined}");
        assert!(joined.contains("allow-fleet-submit"), "{joined}");
        assert!(joined.contains("allow-fleet-status"), "{joined}");
        assert!(joined.contains("allow-list-ledgers"), "{joined}");
    }
}
