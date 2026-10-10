//! The desktop shell: one window showing the local web app, the Python backend as a supervised child, a tray
//! icon that keeps the app alive when the window is closed (sync and questions from the phone go on), one
//! instance, remembered window state, optional autostart. `--hidden` starts into the tray, `--selftest` starts
//! the backend, checks `/api/health` and exits (used by CI).

mod backend;
mod bridge;
mod updates;

use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use backend::Backend;
use updates::Updates;
use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, RunEvent, WindowEvent};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt as _};
use tauri_plugin_notification::NotificationExt;
use tauri_plugin_window_state::{StateFlags, WindowExt};

static HIDE_HINT_SHOWN: AtomicBool = AtomicBool::new(false);

fn show_main(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_focus();
    }
}

pub(crate) fn open_folder(path: &std::path::Path) {
    let _ = std::fs::create_dir_all(path);
    #[cfg(windows)]
    let _ = std::process::Command::new("explorer").arg(path).spawn();
    #[cfg(target_os = "macos")]
    let _ = std::process::Command::new("open").arg(path).spawn();
    #[cfg(all(unix, not(target_os = "macos")))]
    let _ = std::process::Command::new("xdg-open").arg(path).spawn();
}

#[tauri::command]
fn backend_status(backend: tauri::State<'_, Backend>) -> backend::Status {
    backend::status(&backend)
}

#[tauri::command]
fn restart_backend(app: AppHandle, backend: tauri::State<'_, Backend>) {
    backend::stop(&backend);
    backend::start(app, backend.inner().clone());
}

#[tauri::command]
fn open_log(app: AppHandle) {
    let d = backend::dirs(&app);
    if let Some(folder) = d.log.parent() {
        open_folder(folder);
    }
}

/// The tray icon with its menu; returns the „Beim Anmelden starten“ item so the bridge can keep it in step.
fn build_tray(app: &AppHandle) -> tauri::Result<CheckMenuItem<tauri::Wry>> {
    let open = MenuItem::with_id(app, "open", "Öffnen", true, None::<&str>)?;
    let data = MenuItem::with_id(app, "data", "Datenordner öffnen", true, None::<&str>)?;
    let autostart_on = app.autolaunch().is_enabled().unwrap_or(false);
    let autostart = CheckMenuItem::with_id(app, "autostart", "Beim Anmelden starten", true, autostart_on, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Beenden", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &data, &PredefinedMenuItem::separator(app)?, &autostart, &PredefinedMenuItem::separator(app)?, &quit])?;
    let autostart_item = autostart.clone();
    TrayIconBuilder::with_id("main")
        .icon(app.default_window_icon().cloned().expect("app icon"))
        .tooltip("MTG Deckbuilder")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(move |app, event| match event.id.as_ref() {
            "open" => show_main(app),
            "data" => open_folder(&backend::dirs(app).data),
            "autostart" => {
                let m = app.autolaunch();
                let now = m.is_enabled().unwrap_or(false);
                let _ = if now { m.disable() } else { m.enable() };
                let _ = autostart_item.set_checked(m.is_enabled().unwrap_or(!now));
            }
            "quit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                show_main(tray.app_handle());
            }
        })
        .build(app)?;
    Ok(autostart)
}

/// `--selftest`: start the backend, wait for it, read `/api/health`, print it and exit (0 = fine).
fn selftest(app: AppHandle, backend: Backend) {
    std::thread::spawn(move || {
        let until = Instant::now() + Duration::from_secs(90);
        let url = loop {
            let st = backend::status(&backend);
            if let Some(u) = st.url.clone() {
                break Some(u);
            }
            if st.state == "failed" || Instant::now() > until {
                eprintln!("selftest: backend not ready: {:?}", st);
                break None;
            }
            std::thread::sleep(Duration::from_millis(200));
        };
        let code = match url.as_deref().map(|u| backend::http_get(u, "/api/health")) {
            Some(Ok(body)) if body.contains("\"ok\":true") && body.contains("\"frozen\":true") => {
                println!("selftest ok: {body}");
                0
            }
            Some(Ok(body)) => {
                eprintln!("selftest: unexpected health: {body}");
                1
            }
            Some(Err(e)) => {
                eprintln!("selftest: health failed: {e}");
                1
            }
            None => 1,
        };
        backend::stop(&backend);
        app.exit(code);
    });
}

pub fn run() {
    let args: Vec<String> = std::env::args().collect();
    let hidden = args.iter().any(|a| a == "--hidden");
    let is_selftest = args.iter().any(|a| a == "--selftest");
    let backend = Backend::default();
    let updates = Updates::default();

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| show_main(app)))
        .plugin(tauri_plugin_window_state::Builder::default().with_state_flags(StateFlags::SIZE | StateFlags::POSITION | StateFlags::MAXIMIZED).build())
        .plugin(tauri_plugin_autostart::init(MacosLauncher::LaunchAgent, Some(vec!["--hidden"])))
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(backend.clone())
        .manage(updates.clone())
        .invoke_handler(tauri::generate_handler![backend_status, restart_backend, open_log])
        .setup(move |app| {
            let handle = app.handle().clone();
            let autostart_item = build_tray(&handle)?;
            backend::start(handle.clone(), backend.clone());
            if !is_selftest {
                bridge::start(handle.clone(), backend.clone(), autostart_item, updates.clone());
                updates::start(handle.clone(), updates.clone());
            }
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.restore_state(StateFlags::SIZE | StateFlags::POSITION | StateFlags::MAXIMIZED);
                if !hidden && !is_selftest {
                    let _ = w.show();
                }
            }
            if is_selftest {
                selftest(handle, backend.clone());
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                // the app lives on in the tray: sync and questions from the phone keep working
                api.prevent_close();
                let _ = window.hide();
                if !HIDE_HINT_SHOWN.swap(true, Ordering::SeqCst) {
                    let _ = window
                        .app_handle()
                        .notification()
                        .builder()
                        .title("MTG Deckbuilder läuft weiter")
                        .body("Im Infobereich (neben der Uhr) findest du die App wieder. Beenden: Rechtsklick → Beenden.")
                        .show();
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("Fehler beim Start der Desktop-App");

    app.run(|app, event| {
        if let RunEvent::Exit = event {
            if let Some(b) = app.try_state::<Backend>() {
                backend::stop(&b);
            }
        }
    });
}
