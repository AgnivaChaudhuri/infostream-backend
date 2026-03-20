//! Desktop shell runtime for Infostream.
//!
//! This file handles backend bootstrap/autostart and frontend logging sinks.
//! It exposes Tauri commands used by the webview:
//! - `bootstrap_backend`
//! - `pick_source_directory`
//! - `emit_frontend_log`

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use chrono::SecondsFormat;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

const LOG_ROTATION_MAX_BYTES: u64 = 20 * 1024 * 1024;
const LOG_ROTATION_BACKUPS: usize = 5;
const REDACTED: &str = "***REDACTED***";
const MAX_STRING_LEN: usize = 4000;

#[derive(Deserialize)]
struct Lockfile {
    api_version: String,
    port: u16,
    pid: i32,
    started_at: String,
}

#[derive(Deserialize)]
struct FrontendLogPayload {
    timestamp: String,
    level: String,
    component: String,
    event: String,
    message: String,
    #[serde(default)]
    context: Value,
}

#[derive(Serialize)]
struct LogRecord {
    timestamp: String,
    level: String,
    component: String,
    event: String,
    message: String,
    context: Value,
}

struct RotatingFileWriter {
    path: PathBuf,
    max_bytes: u64,
    backups: usize,
    file: File,
}

impl RotatingFileWriter {
    fn new(path: PathBuf, max_bytes: u64, backups: usize) -> Result<Self, String> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)
                .map_err(|e| format!("failed to create log directory: {e}"))?;
        }
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)
            .map_err(|e| format!("failed to open log file {}: {e}", path.display()))?;
        Ok(Self {
            path,
            max_bytes,
            backups,
            file,
        })
    }

    fn write_line(&mut self, line: &str) -> Result<(), String> {
        self.rotate_if_needed(line.len() as u64)?;
        self.file
            .write_all(line.as_bytes())
            .map_err(|e| format!("failed to write log file {}: {e}", self.path.display()))?;
        self.file
            .flush()
            .map_err(|e| format!("failed to flush log file {}: {e}", self.path.display()))
    }

    fn rotate_if_needed(&mut self, incoming_bytes: u64) -> Result<(), String> {
        let current_size = self.file.metadata().map(|meta| meta.len()).unwrap_or(0);
        if current_size.saturating_add(incoming_bytes) <= self.max_bytes {
            return Ok(());
        }

        if self.backups > 0 {
            let oldest = rotated_path(&self.path, self.backups);
            if oldest.exists() {
                let _ = fs::remove_file(&oldest);
            }

            for index in (1..self.backups).rev() {
                let source = rotated_path(&self.path, index);
                if source.exists() {
                    let target = rotated_path(&self.path, index + 1);
                    let _ = fs::rename(&source, &target);
                }
            }

            if self.path.exists() {
                let _ = fs::rename(&self.path, rotated_path(&self.path, 1));
            }
        } else if self.path.exists() {
            let _ = fs::remove_file(&self.path);
        }

        self.file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)
            .map_err(|e| format!("failed to reopen rotated log file {}: {e}", self.path.display()))?;
        Ok(())
    }
}

struct DesktopLogger {
    human_writer: Option<Mutex<RotatingFileWriter>>,
    json_writer: Option<Mutex<RotatingFileWriter>>,
}

impl DesktopLogger {
    fn new(logs_dir: &Path) -> Self {
        let human_path = logs_dir.join("frontend.log");
        let json_path = logs_dir.join("frontend.jsonl");

        let human_writer = match RotatingFileWriter::new(
            human_path,
            LOG_ROTATION_MAX_BYTES,
            LOG_ROTATION_BACKUPS,
        ) {
            Ok(writer) => Some(Mutex::new(writer)),
            Err(error) => {
                eprintln!("failed to initialize human frontend logger: {error}");
                None
            }
        };

        let json_writer = match RotatingFileWriter::new(
            json_path,
            LOG_ROTATION_MAX_BYTES,
            LOG_ROTATION_BACKUPS,
        ) {
            Ok(writer) => Some(Mutex::new(writer)),
            Err(error) => {
                eprintln!("failed to initialize JSON frontend logger: {error}");
                None
            }
        };

        Self {
            human_writer,
            json_writer,
        }
    }

    fn log_internal(&self, level: &str, component: &str, event: &str, message: &str, context: Value) {
        let record = LogRecord {
            timestamp: now_timestamp(),
            level: normalize_level(level).to_string(),
            component: component.to_string(),
            event: event.to_string(),
            message: message.to_string(),
            context: sanitize_json(context, None),
        };
        self.write_record(&record);
    }

    fn log_frontend(&self, payload: FrontendLogPayload) {
        let timestamp = if payload.timestamp.trim().is_empty() {
            now_timestamp()
        } else {
            payload.timestamp
        };
        let record = LogRecord {
            timestamp,
            level: normalize_level(&payload.level).to_string(),
            component: payload.component,
            event: payload.event,
            message: payload.message,
            context: sanitize_json(payload.context, None),
        };
        self.write_record(&record);
    }

    fn write_record(&self, record: &LogRecord) {
        let context_json = serde_json::to_string(&record.context).unwrap_or_else(|_| "{}".to_string());
        let human_line = format!(
            "{} {} [{}] {} {} {}\n",
            record.timestamp,
            record.level.to_uppercase(),
            record.component,
            record.event,
            record.message,
            context_json,
        );

        if record.level == "error" || record.level == "warn" {
            eprint!("{human_line}");
        } else {
            print!("{human_line}");
        }

        if let Some(writer_mutex) = &self.human_writer {
            if let Ok(mut writer) = writer_mutex.lock() {
                if let Err(error) = writer.write_line(&human_line) {
                    eprintln!("failed to write human frontend log: {error}");
                }
            }
        }

        if let Some(writer_mutex) = &self.json_writer {
            if let Ok(mut writer) = writer_mutex.lock() {
                match serde_json::to_string(record) {
                    Ok(serialized) => {
                        let line = format!("{serialized}\n");
                        if let Err(error) = writer.write_line(&line) {
                            eprintln!("failed to write JSON frontend log: {error}");
                        }
                    }
                    Err(error) => {
                        eprintln!("failed to serialize frontend log record: {error}");
                    }
                }
            }
        }
    }
}

struct BackendState {
    children: Mutex<Vec<Child>>,
    logger: DesktopLogger,
}

impl BackendState {
    fn new(logs_dir: &Path) -> Self {
        Self {
            children: Mutex::new(Vec::new()),
            logger: DesktopLogger::new(logs_dir),
        }
    }

    fn log_internal(&self, level: &str, event: &str, message: &str, context: Value) {
        self.logger
            .log_internal(level, "desktop.tauri", event, message, context);
    }
}

#[tauri::command]
fn bootstrap_backend(
    app: tauri::AppHandle,
    state: tauri::State<'_, BackendState>,
) -> Result<String, String> {
    state.log_internal(
        "info",
        "bootstrap_backend_started",
        "bootstrap_backend command started",
        json!({}),
    );

    let system_root = resolve_system_root().map_err(|error| {
        state.log_internal(
            "error",
            "bootstrap_backend_system_root_failed",
            "failed to resolve system root",
            json!({ "error": error }),
        );
        error
    })?;
    fs::create_dir_all(&system_root).map_err(|error| {
        let message = format!("failed to create system root: {error}");
        state.log_internal(
            "error",
            "bootstrap_backend_system_root_create_failed",
            "failed to create system root directory",
            json!({ "error": message, "system_root": system_root.to_string_lossy() }),
        );
        message
    })?;
    let lockfile_path = system_root.join("daemon.lock");

    if let Some(base_url) = read_healthy_base_url(&lockfile_path) {
        state.log_internal(
            "info",
            "bootstrap_backend_reused",
            "reused healthy backend from existing lockfile",
            json!({ "base_url": base_url }),
        );
        return Ok(base_url);
    }

    if lockfile_path.exists() {
        let _ = fs::remove_file(&lockfile_path);
        state.log_internal(
            "warn",
            "bootstrap_backend_removed_stale_lockfile",
            "removed stale lockfile before backend spawn",
            json!({ "lockfile": lockfile_path.to_string_lossy() }),
        );
    }

    spawn_backend(&app, &state, &system_root).inspect_err(|error| {
        state.log_internal(
            "error",
            "bootstrap_backend_spawn_failed",
            "failed to spawn backend process",
            json!({ "error": error }),
        );
    })?;
    let base_url = wait_for_lockfile(&lockfile_path).inspect_err(|error| {
        state.log_internal(
            "error",
            "bootstrap_backend_wait_failed",
            "backend did not become healthy",
            json!({ "error": error }),
        );
    })?;
    state.log_internal(
        "info",
        "bootstrap_backend_completed",
        "bootstrap_backend command completed",
        json!({ "base_url": base_url }),
    );
    Ok(base_url)
}

#[tauri::command]
fn pick_source_directory(state: tauri::State<'_, BackendState>) -> Option<String> {
    state.log_internal(
        "debug",
        "source_directory_picker_opened",
        "opening native source directory picker",
        json!({}),
    );
    let picked = rfd::FileDialog::new()
        .set_title("Select source directory")
        .pick_folder()
        .map(|path| path.to_string_lossy().to_string());

    if let Some(path) = &picked {
        state.log_internal(
            "info",
            "source_directory_picker_selected",
            "source directory selected",
            json!({ "selected_path": path }),
        );
    } else {
        state.log_internal(
            "info",
            "source_directory_picker_cancelled",
            "source directory picker cancelled",
            json!({}),
        );
    }
    picked
}

#[tauri::command]
fn emit_frontend_log(state: tauri::State<'_, BackendState>, payload: FrontendLogPayload) {
    state.logger.log_frontend(payload);
}

/// Resolve OS-specific app data location used by the backend daemon.
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

/// Read lockfile and return base URL only if PID exists and health check passes.
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

/// Parse backend daemon lockfile JSON.
fn read_lockfile(lockfile_path: &Path) -> Option<Lockfile> {
    let body = fs::read_to_string(lockfile_path).ok()?;
    serde_json::from_str::<Lockfile>(&body).ok()
}

/// Best-effort PID liveness check.
fn pid_running(pid: i32) -> bool {
    if pid <= 0 {
        return false;
    }

    if cfg!(target_os = "linux") {
        return Path::new("/proc").join(pid.to_string()).exists();
    }

    true
}

/// Probe /health on a candidate backend port.
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

/// Spawn sidecar/local/uv backend process with fallback order.
fn spawn_backend(
    _app: &tauri::AppHandle,
    state: &tauri::State<'_, BackendState>,
    system_root: &Path,
) -> Result<(), String> {
    if let Some(sidecar_path) = resolve_sidecar_path() {
        let child = Command::new(&sidecar_path)
            .arg("--home")
            .arg(system_root)
            .spawn()
            .map_err(|e| format!("failed to spawn backend sidecar: {e}"))?;
        state
            .children
            .lock()
            .map_err(|_| "state lock poisoned".to_string())?
            .push(child);
        state.log_internal(
            "info",
            "backend_spawned_sidecar",
            "spawned bundled backend sidecar",
            json!({ "sidecar_path": sidecar_path.to_string_lossy() }),
        );
        return Ok(());
    }

    for candidate in [
        PathBuf::from("../backend/.venv/bin/infostreamd"),
        PathBuf::from("backend/.venv/bin/infostreamd"),
    ] {
        if candidate.exists() && candidate.is_file() {
            let child = Command::new(&candidate)
                .arg("--home")
                .arg(system_root)
                .spawn()
                .map_err(|e| format!("failed to spawn local backend executable: {e}"))?;
            state
                .children
                .lock()
                .map_err(|_| "state lock poisoned".to_string())?
                .push(child);
            state.log_internal(
                "info",
                "backend_spawned_local_binary",
                "spawned local backend executable",
                json!({ "path": candidate.to_string_lossy() }),
            );
            return Ok(());
        }
    }

    let uv_project = if Path::new("../backend/pyproject.toml").exists() {
        "../backend"
    } else {
        "backend"
    };

    let child = Command::new("uv")
        .arg("run")
        .arg("--project")
        .arg(uv_project)
        .arg("--directory")
        .arg(uv_project)
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
    state.log_internal(
        "info",
        "backend_spawned_uv_fallback",
        "spawned backend through uv fallback",
        json!({ "project": uv_project }),
    );
    Ok(())
}

/// Find packaged backend sidecar path in dev and bundled layouts.
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

/// Wait until daemon lockfile appears and backend passes health checks.
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

fn normalize_level(level: &str) -> &'static str {
    match level.to_lowercase().as_str() {
        "debug" => "debug",
        "warn" => "warn",
        "error" => "error",
        _ => "info",
    }
}

fn now_timestamp() -> String {
    chrono::Utc::now().to_rfc3339_opts(SecondsFormat::Millis, true)
}

fn rotated_path(path: &Path, index: usize) -> PathBuf {
    PathBuf::from(format!("{}.{}", path.to_string_lossy(), index))
}

fn is_sensitive_key(key: &str) -> bool {
    let lowered = key.to_lowercase();
    [
        "api_key",
        "apikey",
        "authorization",
        "token",
        "password",
        "secret",
        "cookie",
        "access_key",
        "private_key",
    ]
    .iter()
    .any(|part| lowered.contains(part))
}

fn sanitize_json(value: Value, key_hint: Option<&str>) -> Value {
    if let Some(key) = key_hint {
        if is_sensitive_key(key) {
            return Value::String(REDACTED.to_string());
        }
    }

    match value {
        Value::Object(map) => {
            let mut sanitized = serde_json::Map::new();
            for (key, nested) in map {
                sanitized.insert(key.clone(), sanitize_json(nested, Some(&key)));
            }
            Value::Object(sanitized)
        }
        Value::Array(entries) => Value::Array(
            entries
                .into_iter()
                .map(|entry| sanitize_json(entry, key_hint))
                .collect(),
        ),
        Value::String(text) => {
            let lowered = text.trim().to_lowercase();
            if lowered.starts_with("bearer ") || (lowered.starts_with("sk-") && lowered.len() > 10) {
                return Value::String(REDACTED.to_string());
            }
            if text.len() > MAX_STRING_LEN {
                return Value::String(format!("{}...[truncated]", &text[..MAX_STRING_LEN]));
            }
            Value::String(text)
        }
        other => other,
    }
}

/// Application entrypoint wiring commands and shared state.
fn main() {
    let system_root = resolve_system_root().unwrap_or_else(|_| {
        dirs::home_dir()
            .unwrap_or_else(|| PathBuf::from("."))
            .join(".infostream")
    });
    let logs_dir = system_root.join("logs");
    let state = BackendState::new(&logs_dir);
    state.log_internal(
        "info",
        "desktop_tauri_starting",
        "desktop tauri process starting",
        json!({ "logs_dir": logs_dir.to_string_lossy() }),
    );

    tauri::Builder::default()
        .manage(state)
        .invoke_handler(tauri::generate_handler![
            bootstrap_backend,
            pick_source_directory,
            emit_frontend_log
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(test)]
mod tests {
    use super::{sanitize_json, Value, REDACTED};

    #[test]
    fn sanitize_json_redacts_sensitive_keys() {
        let payload = serde_json::json!({
            "api_key": "sk-1234567890",
            "nested": {
                "authorization": "Bearer token",
                "ok": "value"
            }
        });

        let sanitized = sanitize_json(payload, None);
        assert_eq!(sanitized["api_key"], Value::String(REDACTED.to_string()));
        assert_eq!(sanitized["nested"]["authorization"], Value::String(REDACTED.to_string()));
        assert_eq!(sanitized["nested"]["ok"], Value::String("value".to_string()));
    }
}
