//! Updates from GitHub Releases: `latest.json` (written by the release workflow, signed with the key whose public
//! part is in `tauri.conf.json`) is checked a little after the start and then once a day. The bridge reports the
//! state to the web app, which shows it under Einstellungen → Desktop-App and offers „Update installieren“; the
//! install runs the NSIS updater in passive mode and restarts the app.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde::Serialize;
use tauri::AppHandle;
use tauri_plugin_updater::UpdaterExt;

const CHECK_AFTER: Duration = Duration::from_secs(45);
const CHECK_EVERY: Duration = Duration::from_secs(24 * 3600);

#[derive(Clone, Serialize, Default, Debug)]
pub struct UpdateState {
    /// unknown | none | available | downloading | installing | error | unconfigured
    pub state: String,
    pub current: String,
    pub version: Option<String>,
    pub notes: Option<String>,
    pub progress: Option<u8>,
    pub error: Option<String>,
    pub checked: Option<u64>,
}

#[derive(Default, Clone)]
pub struct Updates(pub Arc<Mutex<UpdateState>>);

pub fn snapshot(u: &Updates) -> UpdateState {
    u.0.lock().unwrap().clone()
}

fn set(u: &Updates, f: impl FnOnce(&mut UpdateState)) {
    f(&mut u.0.lock().unwrap());
}

fn now() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

/// Check in the background; the result lands in the state.
pub fn check(app: AppHandle, updates: Updates) {
    let current = app.package_info().version.to_string();
    set(&updates, |s| s.current = current);
    tauri::async_runtime::spawn(async move {
        let updater = match app.updater() {
            Ok(u) => u,
            Err(e) => {
                set(&updates, |s| {
                    s.state = "unconfigured".into();
                    s.error = Some(e.to_string());
                    s.checked = Some(now());
                });
                return;
            }
        };
        match updater.check().await {
            Ok(Some(update)) => set(&updates, |s| {
                s.state = "available".into();
                s.version = Some(update.version.clone());
                s.notes = update.body.clone();
                s.error = None;
                s.checked = Some(now());
            }),
            Ok(None) => set(&updates, |s| {
                s.state = "none".into();
                s.version = None;
                s.notes = None;
                s.error = None;
                s.checked = Some(now());
            }),
            Err(e) => set(&updates, |s| {
                s.state = "error".into();
                s.error = Some(e.to_string());
                s.checked = Some(now());
            }),
        }
    });
}

/// Download and install the announced update. On Windows the app exits once the installer runs and is restarted
/// by it afterwards.
pub fn install(app: AppHandle, updates: Updates) {
    {
        let st = updates.0.lock().unwrap();
        if st.state == "downloading" || st.state == "installing" {
            return;
        }
    }
    tauri::async_runtime::spawn(async move {
        let result: Result<(), String> = async {
            let update = app
                .updater()
                .map_err(|e| e.to_string())?
                .check()
                .await
                .map_err(|e| e.to_string())?
                .ok_or_else(|| "Es gibt gerade kein Update.".to_string())?;
            set(&updates, |s| {
                s.state = "downloading".into();
                s.progress = Some(0);
                s.version = Some(update.version.clone());
                s.error = None;
            });
            let (on_chunk_state, on_done_state) = (updates.clone(), updates.clone());
            let mut got: usize = 0;
            update
                .download_and_install(
                    move |chunk, total| {
                        got += chunk;
                        if let Some(t) = total.filter(|t| *t > 0) {
                            let p = (got as f64 / t as f64 * 100.0).min(100.0) as u8;
                            set(&on_chunk_state, |s| s.progress = Some(p));
                        }
                    },
                    move || {
                        set(&on_done_state, |s| {
                            s.state = "installing".into();
                            s.progress = Some(100);
                        })
                    },
                )
                .await
                .map_err(|e| e.to_string())
        }
        .await;
        if let Err(e) = result {
            set(&updates, |s| {
                s.state = "error".into();
                s.error = Some(e);
                s.progress = None;
            });
        }
    });
}

/// Periodic checks: a little after the start, then daily.
pub fn start(app: AppHandle, updates: Updates) {
    std::thread::spawn(move || {
        std::thread::sleep(CHECK_AFTER);
        loop {
            check(app.clone(), updates.clone());
            std::thread::sleep(CHECK_EVERY);
        }
    });
}
