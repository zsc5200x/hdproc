use crate::health;
use serde::Serialize;
use std::env;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

const DEFAULT_READY_TIMEOUT: Duration = Duration::from_secs(60);

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BackendStatus {
    pub running: bool,
    pub started_by_shell: bool,
    pub message: String,
}

fn existing_directory(value: Option<String>) -> Option<PathBuf> {
    let path = PathBuf::from(value?).canonicalize().ok()?;
    path.is_dir().then_some(path)
}

pub fn resolve_app_dir() -> Option<PathBuf> {
    if let Some(path) = existing_directory(env::var("HDZZ_APP_DIR").ok()) {
        if path.join("app.py").is_file() {
            return Some(path);
        }
    }

    if let Some(repo_root) = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .and_then(Path::parent)
    {
        let app_dir = repo_root.join("app");
        if app_dir.join("app.py").is_file() {
            return Some(app_dir);
        }
    }

    let current_dir = env::current_dir().ok()?;
    for candidate in [current_dir.clone(), current_dir.join("app")] {
        if candidate.join("app.py").is_file() {
            return candidate.canonicalize().ok();
        }
    }
    None
}

fn python_candidates(app_dir: &Path) -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    if let Ok(value) = env::var("HDZZ_PYTHON") {
        if !value.trim().is_empty() {
            candidates.push(PathBuf::from(value));
        }
    }

    let venv_dir = app_dir.join(".venv");
    if cfg!(windows) {
        candidates.push(venv_dir.join("Scripts").join("python.exe"));
    } else {
        candidates.push(venv_dir.join("bin").join("python"));
    }
    candidates.push(PathBuf::from(if cfg!(windows) {
        "python.exe"
    } else {
        "python3"
    }));
    candidates
}

fn resolve_python(app_dir: &Path) -> Option<PathBuf> {
    python_candidates(app_dir)
        .into_iter()
        .find(|candidate| candidate.is_file() || candidate.components().count() == 1)
}

fn ready_timeout() -> Duration {
    env::var("HDZZ_BACKEND_READY_TIMEOUT_SECONDS")
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .filter(|seconds| *seconds > 0)
        .map(Duration::from_secs)
        .unwrap_or(DEFAULT_READY_TIMEOUT)
}

pub fn ensure_backend() -> Result<BackendStatus, String> {
    if health::webui_ready() {
        return Ok(BackendStatus {
            running: true,
            started_by_shell: false,
            message: "已连接现有 Python backend".to_string(),
        });
    }

    let app_dir = resolve_app_dir().ok_or_else(|| {
        "未找到 app/app.py。开发环境请从项目根目录运行，或设置 HDZZ_APP_DIR。".to_string()
    })?;
    let python = resolve_python(&app_dir).ok_or_else(|| {
        "未找到 Python 解释器。请设置 HDZZ_PYTHON，或准备 app/.venv。".to_string()
    })?;
    let app_script = app_dir.join("app.py");

    Command::new(&python)
        .arg(&app_script)
        .arg("start")
        .current_dir(&app_dir)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|error| format!("启动 Python backend 失败: {error}"))?;

    let deadline = Instant::now() + ready_timeout();
    while Instant::now() < deadline {
        if health::webui_ready() {
            return Ok(BackendStatus {
                running: true,
                started_by_shell: true,
                message: "已启动 Python backend 并连接 WebUI".to_string(),
            });
        }
        thread::sleep(Duration::from_millis(500));
    }

    Err(format!(
        "Python backend 已请求启动，但 {url} 在 {seconds} 秒内未就绪",
        url = health::WEBUI_URL,
        seconds = ready_timeout().as_secs()
    ))
}

pub fn current_status() -> BackendStatus {
    let running = health::webui_ready();
    BackendStatus {
        running,
        started_by_shell: false,
        message: if running {
            "WebUI 已就绪".to_string()
        } else {
            "WebUI 尚未就绪".to_string()
        },
    }
}

#[cfg(test)]
mod tests {
    use super::python_candidates;
    use std::path::Path;

    #[test]
    fn python_candidates_keep_project_venv_before_system_python() {
        let candidates = python_candidates(Path::new("/tmp/hdproc/app"));
        assert_eq!(candidates[0], Path::new("/tmp/hdproc/app/.venv/bin/python"));
        assert_eq!(candidates.last().unwrap(), Path::new("python3"));
    }
}
