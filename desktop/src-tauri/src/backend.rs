//! The Python backend as a supervised child process.
//!
//! The packaged backend (`resources/backend/mtgdeck-backend[.exe]`, a PyInstaller folder build) is started with
//! its own data folder, a free port and a fresh access token. It prints one line `MTGDECK_URL=…`; the window
//! navigates there. If it dies, it is restarted with a short backoff (1, 2, 5, 5, 5 s); after five failures in a
//! row the start page shows what went wrong (the log's tail) and offers a retry. Its stdout/stderr go to
//! `<data>/logs/backend.log`.

use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};

pub const APP_FOLDER: &str = "MTG Deckbuilder";
const BACKOFF: [u64; 5] = [1, 2, 5, 5, 5];
const HEALTHY_AFTER: Duration = Duration::from_secs(300);

#[derive(Clone, Serialize, Default, Debug)]
pub struct Status {
    pub state: String, // starting | ready | restarting | failed | stopped
    pub url: Option<String>,
    pub attempt: u32,
    pub detail: String,
    pub log: String,
}

#[derive(Default)]
pub struct Inner {
    pub status: Status,
    child: Option<Child>,
    stopping: bool,
    generation: u64,
}

#[derive(Default, Clone)]
pub struct Backend(pub Arc<Mutex<Inner>>);

pub struct Dirs {
    pub data: PathBuf,
    pub cache: PathBuf,
    pub log: PathBuf,
}

pub fn dirs(app: &AppHandle) -> Dirs {
    let data = std::env::var_os("MTG_HOME").map(PathBuf::from).unwrap_or_else(|| {
        app.path().data_dir().map(|d| d.join(APP_FOLDER)).unwrap_or_else(|_| PathBuf::from("."))
    });
    let cache = app
        .path()
        .cache_dir()
        .map(|d| d.join(APP_FOLDER).join("cache"))
        .unwrap_or_else(|_| data.join("cache"));
    let log = data.join("logs").join("backend.log");
    Dirs { data, cache, log }
}

/// The backend executable: `MTGDECK_BACKEND` (development), the bundled resource, or the PyInstaller output
/// next to the repository (`dist/mtgdeck-backend/`) when running from `cargo tauri dev`.
pub fn executable(app: &AppHandle) -> Option<PathBuf> {
    let name = if cfg!(windows) { "mtgdeck-backend.exe" } else { "mtgdeck-backend" };
    if let Some(p) = std::env::var_os("MTGDECK_BACKEND") {
        return Some(PathBuf::from(p));
    }
    let mut candidates = Vec::new();
    if let Ok(res) = app.path().resource_dir() {
        candidates.push(res.join("backend").join(name));
    }
    if let Ok(exe) = std::env::current_exe() {
        let mut dir = exe.parent().map(Path::to_path_buf);
        for _ in 0..5 {
            if let Some(d) = dir {
                candidates.push(d.join("dist").join("mtgdeck-backend").join(name));
                candidates.push(d.join("resources").join("backend").join(name));
                dir = d.parent().map(Path::to_path_buf);
            }
        }
    }
    candidates.into_iter().find(|p| p.is_file())
}

fn set_status(app: &AppHandle, backend: &Backend, f: impl FnOnce(&mut Status)) {
    let snapshot = {
        let mut inner = backend.0.lock().unwrap();
        f(&mut inner.status);
        inner.status.clone()
    };
    let _ = app.emit("backend-status", snapshot);
}

pub fn status(backend: &Backend) -> Status {
    backend.0.lock().unwrap().status.clone()
}

fn log_tail(path: &Path, lines: usize) -> String {
    let Ok(mut f) = File::open(path) else { return String::new() };
    let mut s = String::new();
    if f.read_to_string(&mut s).is_err() {
        return String::new();
    }
    let all: Vec<&str> = s.lines().collect();
    all[all.len().saturating_sub(lines)..].join("\n")
}

/// Start (or restart after `stop`) the supervisor thread.
pub fn start(app: AppHandle, backend: Backend) {
    let generation = {
        let mut inner = backend.0.lock().unwrap();
        inner.stopping = false;
        inner.generation += 1;
        inner.generation
    };
    thread::spawn(move || supervise(app, backend, generation));
}

fn supervise(app: AppHandle, backend: Backend, generation: u64) {
    let d = dirs(&app);
    let _ = fs::create_dir_all(&d.data);
    let _ = fs::create_dir_all(d.log.parent().unwrap());
    let mut attempt: u32 = 0;
    loop {
        {
            let inner = backend.0.lock().unwrap();
            if inner.stopping || inner.generation != generation {
                return;
            }
        }
        set_status(&app, &backend, |s| {
            s.state = if attempt == 0 { "starting".into() } else { "restarting".into() };
            s.attempt = attempt;
            s.url = None;
        });
        let started = Instant::now();
        match run_once(&app, &backend, &d, generation) {
            Ok(()) => {}
            Err(e) => {
                let _ = append_log(&d.log, &format!("[shell] {e}\n"));
            }
        }
        if backend.0.lock().unwrap().stopping {
            set_status(&app, &backend, |s| s.state = "stopped".into());
            return;
        }
        if started.elapsed() >= HEALTHY_AFTER {
            attempt = 0; // a long healthy run: an occasional crash does not use up the budget
        }
        if attempt as usize >= BACKOFF.len() {
            let tail = log_tail(&d.log, 40);
            set_status(&app, &backend, |s| {
                s.state = "failed".into();
                s.detail = "Der Hintergrunddienst startet nicht.".into();
                s.log = tail;
            });
            return;
        }
        thread::sleep(Duration::from_secs(BACKOFF[attempt as usize]));
        attempt += 1;
    }
}

fn append_log(path: &Path, text: &str) -> std::io::Result<()> {
    let mut f = OpenOptions::new().create(true).append(true).open(path)?;
    f.write_all(text.as_bytes())
}

fn run_once(app: &AppHandle, backend: &Backend, d: &Dirs, generation: u64) -> Result<(), String> {
    let exe = executable(app).ok_or_else(|| "Backend nicht gefunden (resources/backend/mtgdeck-backend)".to_string())?;
    // one log per run, the previous one kept
    if d.log.exists() {
        let _ = fs::rename(&d.log, d.log.with_extension("1.log"));
    }
    let log = File::create(&d.log).map_err(|e| format!("Protokoll {}: {e}", d.log.display()))?;
    let mut cmd = Command::new(&exe);
    cmd.args(["--data", &d.data.to_string_lossy(), "--cache", &d.cache.to_string_lossy(), "--port", "0", "--token", "auto"])
        .env("PYTHONUNBUFFERED", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::from(log.try_clone().map_err(|e| e.to_string())?));
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW: no console flashing up
    }
    let mut child = cmd.spawn().map_err(|e| format!("{}: {e}", exe.display()))?;
    let stdout = child.stdout.take().ok_or("kein stdout")?;
    {
        let mut inner = backend.0.lock().unwrap();
        if inner.stopping || inner.generation != generation {
            let _ = child.kill();
            return Ok(());
        }
        inner.child = Some(child);
    }
    let mut log_w = log;
    for line in BufReader::new(stdout).lines() {
        let Ok(line) = line else { break };
        let _ = writeln!(log_w, "{line}");
        if let Some(url) = line.strip_prefix("MTGDECK_URL=") {
            let url = url.trim().to_string();
            wait_reachable(&url, Duration::from_secs(20));
            set_status(app, backend, |s| {
                s.state = "ready".into();
                s.url = Some(url.clone());
                s.detail.clear();
                s.log.clear();
            });
            let _ = app.emit("backend-ready", serde_json::json!({ "url": url }));
        }
    }
    // stdout closed: the process ended (or is being stopped)
    let child = backend.0.lock().unwrap().child.take();
    if let Some(mut c) = child {
        let code = c.wait().map(|s| s.code()).unwrap_or(None);
        let _ = append_log(&d.log, &format!("[shell] Backend beendet (code {code:?})\n"));
    }
    Ok(())
}

/// Connect until the server accepts connections (the socket is bound before the line is printed, listening
/// starts a few milliseconds later).
fn wait_reachable(url: &str, max: Duration) {
    let Some(addr) = host_port(url) else { return };
    let until = Instant::now() + max;
    while Instant::now() < until {
        if TcpStream::connect_timeout(&addr.parse().unwrap(), Duration::from_millis(300)).is_ok() {
            return;
        }
        thread::sleep(Duration::from_millis(50));
    }
}

fn host_port(url: &str) -> Option<String> {
    let rest = url.strip_prefix("http://")?;
    let end = rest.find('/').unwrap_or(rest.len());
    Some(rest[..end].to_string())
}

/// A tiny HTTP client (no dependency): `GET` or `POST` to `base + path`, with the app's token and a JSON body if
/// given; the body of a 200 answer. Used by the self-test (`/api/health`) and the bridge (`/api/desktop/poll`).
pub fn http_request(base: &str, method: &str, path: &str, token: Option<&str>, body: Option<&str>) -> Result<String, String> {
    let addr = host_port(base).ok_or("keine Adresse")?;
    let mut s = TcpStream::connect_timeout(&addr.parse().map_err(|e| format!("{e}"))?, Duration::from_secs(3)).map_err(|e| e.to_string())?;
    s.set_read_timeout(Some(Duration::from_secs(5))).ok();
    let mut req = format!("{method} {path} HTTP/1.0\r\nHost: {addr}\r\nConnection: close\r\n");
    if let Some(t) = token {
        req.push_str(&format!("Authorization: Bearer {t}\r\n"));
    }
    if let Some(b) = body {
        req.push_str(&format!("Content-Type: application/json\r\nContent-Length: {}\r\n", b.len()));
    }
    req.push_str("\r\n");
    if let Some(b) = body {
        req.push_str(b);
    }
    s.write_all(req.as_bytes()).map_err(|e| e.to_string())?;
    let mut buf = String::new();
    s.read_to_string(&mut buf).map_err(|e| e.to_string())?;
    let (head, body) = buf.split_once("\r\n\r\n").unwrap_or((&buf, ""));
    if !head.starts_with("HTTP/1.") || !head.contains(" 200 ") {
        return Err(head.lines().next().unwrap_or("").to_string());
    }
    Ok(body.to_string())
}

pub fn http_get(base: &str, path: &str) -> Result<String, String> {
    http_request(base, "GET", path, None, None)
}

/// The access token in `http://127.0.0.1:1234/?token=…`.
pub fn token_of(url: &str) -> Option<String> {
    let (_, rest) = url.split_once("?token=")?;
    let token = rest.split('&').next().unwrap_or("");
    (!token.is_empty()).then(|| token.to_string())
}

pub fn stop(backend: &Backend) {
    let child = {
        let mut inner = backend.0.lock().unwrap();
        inner.stopping = true;
        inner.status.state = "stopped".into();
        inner.child.take()
    };
    if let Some(mut c) = child {
        let _ = c.kill();
        let _ = c.wait();
    }
}
