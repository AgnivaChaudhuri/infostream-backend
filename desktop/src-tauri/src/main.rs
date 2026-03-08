#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Deserialize;
use std::fs;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

#[derive(Default)]
struct BackendState {
    children: Mutex<Vec<Child>>,
}

#[derive(Deserialize)]
struct Lockfile {
    api_version: String,
    port: u16,
    pid: i32,
    started_at: String,
}

#[tauri::command]
fn bootstrap_backend(
    app: tauri::AppHandle,
    state: tauri::State<'_, BackendState>,
) -> Result<String, String> {
    let system_root = resolve_system_root()?;
    fs::create_dir_all(&system_root).map_err(|e| format!("failed to create system root: {e}"))?;
    let lockfile_path = system_root.join("daemon.lock");

    if let Some(base_url) = read_healthy_base_url(&lockfile_path) {
        return Ok(base_url);
    }

    if lockfile_path.exists() {
        let _ = fs::remove_file(&lockfile_path);
    }

    spawn_backend(&app, &state, &system_root)?;
    wait_for_lockfile(&lockfile_path)
}

fn resolve_system_root() -> Result<PathBuf, String> {
    if cfg!(target_os = "linux") {
        if let Some(config_dir) = dirs::config_dir() {
            return Ok(config_dir.join("infostream"));
        }
    }

    if cfg!(target_os = "macos") {
        if let Some(home) = dirs::home_dir() {
            return Ok(home
                .join("Library")
                .join("Application Support")
                .join("Infostream"));
        }
    }

    if cfg!(target_os = "windows") {
        if let Some(data_dir) = dirs::data_dir() {
            return Ok(data_dir.join("Infostream"));
        }
    }

    dirs::home_dir()
        .map(|home| home.join(".infostream"))
        .ok_or_else(|| "cannot resolve user home directory".to_string())
}

fn read_healthy_base_url(lockfile_path: &Path) -> Option<String> {
    if !lockfile_path.exists() {
        return None;
    }
    let lockfile = read_lockfile(lockfile_path)?;
    let _ = (&lockfile.api_version, &lockfile.started_at);
    if !pid_running(lockfile.pid) {
        return None;
    }
    if !health_check(lockfile.port) {
        return None;
    }
    Some(format!("http://127.0.0.1:{}", lockfile.port))
}

fn read_lockfile(lockfile_path: &Path) -> Option<Lockfile> {
    let body = fs::read_to_string(lockfile_path).ok()?;
    serde_json::from_str::<Lockfile>(&body).ok()
}

fn pid_running(pid: i32) -> bool {
    if pid <= 0 {
        return false;
    }

    if cfg!(target_os = "linux") {
        return Path::new("/proc").join(pid.to_string()).exists();
    }

    true
}

fn health_check(port: u16) -> bool {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let mut stream = match TcpStream::connect_timeout(&addr, Duration::from_millis(500)) {
        Ok(stream) => stream,
        Err(_) => return false,
    };

    let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(500)));

    let request = b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n";
    if stream.write_all(request).is_err() {
        return false;
    }

    let mut buffer = [0u8; 1024];
    match stream.read(&mut buffer) {
        Ok(size) if size > 0 => {
            let response = String::from_utf8_lossy(&buffer[..size]);
            response.contains(" 200 ") || response.contains(" 200\r")
        }
        _ => false,
    }
}

fn spawn_backend(
    _app: &tauri::AppHandle,
    state: &tauri::State<'_, BackendState>,
    system_root: &Path,
) -> Result<(), String> {
    if let Some(sidecar_path) = resolve_sidecar_path() {
        let child = Command::new(sidecar_path)
            .arg("--home")
            .arg(system_root)
            .spawn()
            .map_err(|e| format!("failed to spawn backend sidecar: {e}"))?;
        state
            .children
            .lock()
            .map_err(|_| "state lock poisoned".to_string())?
            .push(child);
        return Ok(());
    }

    for candidate in [
        PathBuf::from("../backend/.venv/bin/infostreamd"),
        PathBuf::from("backend/.venv/bin/infostreamd"),
    ] {
        if candidate.exists() && candidate.is_file() {
            let child = Command::new(candidate)
                .arg("--home")
                .arg(system_root)
                .spawn()
                .map_err(|e| format!("failed to spawn local backend executable: {e}"))?;
            state
                .children
                .lock()
                .map_err(|_| "state lock poisoned".to_string())?
                .push(child);
            return Ok(());
        }
    }

    let uv_project = if Path::new("../backend/pyproject.toml").exists() {
        "../backend"
    } else {
        "backend"
    };
    let uv_directory = uv_project;

    let child = Command::new("uv")
        .arg("run")
        .arg("--project")
        .arg(uv_project)
        .arg("--directory")
        .arg(uv_directory)
        .arg("python")
        .arg("-m")
        .arg("infostream.cli")
        .arg("--home")
        .arg(system_root)
        .spawn()
        .map_err(|e| format!("failed to spawn uv backend fallback: {e}"))?;

    state
        .children
        .lock()
        .map_err(|_| "state lock poisoned".to_string())?
        .push(child);
    Ok(())
}

fn resolve_sidecar_path() -> Option<PathBuf> {
    let mut candidates: Vec<PathBuf> = Vec::new();

    if let Ok(current_exe) = std::env::current_exe() {
        if let Some(exe_dir) = current_exe.parent() {
            candidates.push(exe_dir.join("infostream-backend"));
            candidates.push(exe_dir.join("bin").join("infostream-backend"));
        }
    }

    candidates.push(PathBuf::from("src-tauri/bin/infostream-backend"));
    candidates.push(PathBuf::from("desktop/src-tauri/bin/infostream-backend"));

    candidates
        .into_iter()
        .find(|candidate| candidate.exists() && candidate.is_file())
}

fn wait_for_lockfile(lockfile_path: &Path) -> Result<String, String> {
    let start = Instant::now();
    while start.elapsed() < Duration::from_secs(12) {
        if let Some(base_url) = read_healthy_base_url(lockfile_path) {
            return Ok(base_url);
        }
        thread::sleep(Duration::from_millis(200));
    }
    Err("backend did not become healthy after startup".to_string())
}

fn main() {
    tauri::Builder::default()
        .manage(BackendState::default())
        .invoke_handler(tauri::generate_handler![bootstrap_backend])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
