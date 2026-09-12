mod env_inherit;
mod paths;
mod pty;

use pty::SharedPty;
use serde::Serialize;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use tauri::menu::Menu;
use tauri::{AppHandle, Manager, WindowEvent};

struct AppState {
    pty: SharedPty,
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

fn terminate_pty(app: &AppHandle) {
    if let Some(state) = app.try_state::<AppState>() {
        let _ = pty::stop(&state.pty, Some(app));
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let menu = Menu::new(app.handle())?;
            app.set_menu(menu)?;

            let config_dir = app
                .path()
                .app_config_dir()
                .unwrap_or_else(|_| std::env::temp_dir().join("cloakcli-desktop"));
            app.manage(AppState {
                pty: Arc::new(Mutex::new(None)),
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
            pty_stop
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
