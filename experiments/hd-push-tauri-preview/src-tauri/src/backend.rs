use crate::health;
use serde::Serialize;
use std::env;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Mutex, OnceLock};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const DEFAULT_READY_TIMEOUT: Duration = Duration::from_secs(60);
const DIAGNOSTIC_MAX_BYTES: u64 = 256 * 1024;
const DIAGNOSTIC_BACKUPS: u8 = 3;
static BACKEND_START_LOCK: OnceLock<Mutex<()>> = OnceLock::new();

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BackendStatus {
    pub running: bool,
    pub started_by_shell: bool,
    pub configured: bool,
    pub target_count: usize,
    pub runtime_root: String,
    pub diagnostic_log: String,
    pub stage: String,
    pub message: String,
}

#[derive(Clone, Debug)]
struct BackendLaunch {
    program: PathBuf,
    args: Vec<String>,
    cwd: PathBuf,
    bundled: bool,
}

fn diagnostic_path(runtime_root: &Path) -> PathBuf {
    runtime_root.join(".run").join("tauri-backend-launch.log")
}

fn rotate_diagnostic_log(path: &Path) {
    let Ok(metadata) = fs::metadata(path) else {
        return;
    };
    if metadata.len() < DIAGNOSTIC_MAX_BYTES {
        return;
    }
    for index in (1..=DIAGNOSTIC_BACKUPS).rev() {
        let source = if index == 1 {
            path.to_path_buf()
        } else {
            path.with_extension(format!("log.{}", index - 1))
        };
        let target = path.with_extension(format!("log.{index}"));
        if source.exists() {
            let _ = fs::rename(source, target);
        }
    }
}

fn diagnostic_line(runtime_root: &Path, message: impl AsRef<str>) {
    let path = diagnostic_path(runtime_root);
    rotate_diagnostic_log(&path);
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(path) {
        let timestamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|duration| duration.as_secs())
            .unwrap_or_default();
        let _ = writeln!(file, "[{timestamp}] {}", message.as_ref());
    }
}

fn config_summary(runtime_root: &Path) -> (bool, usize, String) {
    let path = runtime_root.join("config.json");
    let Ok(text) = fs::read_to_string(&path) else {
        return (false, 0, "尚未初始化配置".to_string());
    };
    let Ok(value) = serde_json::from_str::<serde_json::Value>(&text) else {
        return (false, 0, "配置文件格式无效".to_string());
    };
    let Some(object) = value.as_object() else {
        return (false, 0, "配置根节点必须是对象".to_string());
    };
    let Some(targets) = object.get("targets") else {
        return (false, 0, "尚未配置监听目标".to_string());
    };
    let Some(items) = targets.as_array() else {
        return (false, 0, "配置 targets 必须是数组".to_string());
    };
    let count = items.len();
    if count == 0 {
        (true, 0, "尚未配置监听目标".to_string())
    } else {
        (true, count, "已配置监听目标".to_string())
    }
}

fn set_private_permissions(path: &Path) {
    #[cfg(unix)]
    if let Ok(mut permissions) = fs::metadata(path).map(|metadata| metadata.permissions()) {
        use std::os::unix::fs::PermissionsExt;
        permissions.set_mode(0o600);
        let _ = fs::set_permissions(path, permissions);
    }
}

fn write_config_value(value: &serde_json::Value) -> Result<PathBuf, String> {
    let runtime_root = resolve_runtime_root();
    let object = value
        .as_object()
        .ok_or_else(|| "配置根节点必须是 JSON 对象".to_string())?;
    if let Some(targets) = object.get("targets") {
        if !targets.is_array() {
            return Err("配置 targets 必须是数组".to_string());
        }
    }

    let target = runtime_root.join("config.json");
    fs::create_dir_all(&runtime_root).map_err(|error| format!("创建运行目录失败: {error}"))?;
    if target.exists() {
        let backup_dir = runtime_root.join(".run").join("config-backups");
        fs::create_dir_all(&backup_dir)
            .map_err(|error| format!("创建配置备份目录失败: {error}"))?;
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|duration| duration.as_millis())
            .unwrap_or_default();
        let backup = backup_dir.join(format!("config-{stamp}-{}.json", std::process::id()));
        fs::copy(&target, &backup).map_err(|error| format!("备份现有配置失败: {error}"))?;
        set_private_permissions(&backup);
    }

    let temp = target.with_file_name(format!(".config.json.{}.tmp", std::process::id()));
    fs::write(
        &temp,
        serde_json::to_vec_pretty(&value).map_err(|error| error.to_string())?,
    )
    .map_err(|error| format!("写入新配置失败: {error}"))?;
    set_private_permissions(&temp);
    if let Err(error) = fs::rename(&temp, &target) {
        let _ = fs::remove_file(&temp);
        return Err(format!("原子替换配置失败: {error}"));
    }
    set_private_permissions(&target);
    Ok(target)
}

pub fn import_config(source: &Path) -> Result<PathBuf, String> {
    let source = source
        .canonicalize()
        .map_err(|error| format!("无法读取配置文件: {error}"))?;
    let text = fs::read_to_string(&source).map_err(|error| format!("无法读取配置文件: {error}"))?;
    let value = serde_json::from_str::<serde_json::Value>(&text)
        .map_err(|_| "配置文件不是有效 JSON".to_string())?;
    write_config_value(&value)
}

pub fn import_config_content(content: &str) -> Result<PathBuf, String> {
    let value = serde_json::from_str::<serde_json::Value>(content)
        .map_err(|_| "配置文件不是有效 JSON".to_string())?;
    write_config_value(&value)
}

pub fn restart_workers_with_resource(resource_dir: Option<&Path>) -> Result<(), String> {
    let launch = resolve_backend_launch(resource_dir)?;
    let runtime_root = resolve_runtime_root();
    let stdout_path = runtime_root.join(".run").join("tauri-backend.stdout.log");
    let stderr_path = runtime_root.join(".run").join("tauri-backend.stderr.log");
    let stdout = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&stdout_path)
        .map_err(|error| format!("打开 backend 日志失败: {error}"))?;
    let stderr = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&stderr_path)
        .map_err(|error| format!("打开 backend 日志失败: {error}"))?;
    let mut command = Command::new(&launch.program);
    command
        .arg("--restart-workers")
        .current_dir(&runtime_root)
        .env("HDZZ_RUNTIME_ROOT", &runtime_root)
        .env("HDZZ_APP_DIR", &runtime_root)
        .env("HDZZ_BACKEND_OWNER", "tauri-bundled-backend")
        .stdin(Stdio::null())
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(stderr));
    let mut child = command
        .spawn()
        .map_err(|error| format!("重启 worker 失败: {error}"))?;
    let deadline = Instant::now() + Duration::from_secs(45);
    while Instant::now() < deadline {
        if let Ok(Some(status)) = child.try_wait() {
            if status.success() {
                return Ok(());
            }
            return Err(format!("worker 重启失败: {status}"));
        }
        thread::sleep(Duration::from_millis(250));
    }
    let _ = child.kill();
    Err("worker 重启超时".to_string())
}

fn log_excerpt(path: &Path) -> String {
    const MAX_BYTES: usize = 8192;
    let Ok(bytes) = fs::read(path) else {
        return String::new();
    };
    let start = bytes.len().saturating_sub(MAX_BYTES);
    String::from_utf8_lossy(&bytes[start..]).into_owned()
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

    #[cfg(debug_assertions)]
    {
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
    }
    None
}

pub fn resolve_runtime_root() -> PathBuf {
    if let Some(value) = env::var_os("HDZZ_RUNTIME_ROOT") {
        let path = PathBuf::from(value);
        if !path.as_os_str().is_empty() {
            return path;
        }
    }
    let home = env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
    home.join("Library")
        .join("Application Support")
        .join("HD跟单系统")
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

fn bundled_backend_candidates(resource_dir: &Path) -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    if let Ok(value) = env::var("HDZZ_BUNDLED_BACKEND") {
        let path = PathBuf::from(value);
        if !path.as_os_str().is_empty() {
            candidates.push(path);
        }
    }
    let backend_dir = resource_dir.join("backend");
    candidates.extend([
        backend_dir.join("hdzz-backend").join("hdzz-backend"),
        backend_dir.join("hdzz-backend"),
        backend_dir
            .join("HD跟单系统 Backend")
            .join("HD跟单系统 Backend"),
    ]);
    candidates
}

fn resolve_backend_launch(resource_dir: Option<&Path>) -> Result<BackendLaunch, String> {
    let runtime_root = resolve_runtime_root();
    if let Some(resource_dir) = resource_dir {
        if let Some(program) = bundled_backend_candidates(resource_dir)
            .into_iter()
            .find(|path| path.is_file())
        {
            return Ok(BackendLaunch {
                program,
                args: vec![
                    "start".to_string(),
                    "--host".to_string(),
                    health::WEBUI_HOST.to_string(),
                    "--port".to_string(),
                    health::WEBUI_PORT.to_string(),
                ],
                cwd: runtime_root,
                bundled: true,
            });
        }
        if !cfg!(debug_assertions) {
            return Err(format!(
                "未找到 bundled backend resource: {}",
                resource_dir.display()
            ));
        }
    }

    let app_dir = resolve_app_dir().ok_or_else(|| {
        "未找到 app/app.py。开发环境请从项目根目录运行，或设置 HDZZ_APP_DIR。".to_string()
    })?;
    let python = resolve_python(&app_dir).ok_or_else(|| {
        "未找到 Python 解释器。请设置 HDZZ_PYTHON，或准备 app/.venv。".to_string()
    })?;
    Ok(BackendLaunch {
        program: python,
        args: vec![
            app_dir.join("app.py").to_string_lossy().to_string(),
            "start".to_string(),
            "--host".to_string(),
            health::WEBUI_HOST.to_string(),
            "--port".to_string(),
            health::WEBUI_PORT.to_string(),
        ],
        cwd: app_dir,
        bundled: false,
    })
}

pub fn ensure_backend() -> Result<BackendStatus, String> {
    ensure_backend_with_resource(None)
}

pub fn ensure_backend_with_resource(resource_dir: Option<&Path>) -> Result<BackendStatus, String> {
    let _start_guard = BACKEND_START_LOCK
        .get_or_init(|| Mutex::new(()))
        .lock()
        .map_err(|_| "backend 启动锁不可用".to_string())?;
    if health::webui_ready() {
        let runtime_root = resolve_runtime_root();
        let (valid, target_count, summary) = config_summary(&runtime_root);
        return Ok(BackendStatus {
            running: true,
            started_by_shell: false,
            configured: valid && target_count > 0,
            target_count,
            runtime_root: runtime_root.display().to_string(),
            diagnostic_log: diagnostic_path(&runtime_root).display().to_string(),
            stage: "ready".to_string(),
            message: if valid && target_count > 0 {
                "已连接现有 Python backend".to_string()
            } else {
                format!("后台服务已启动，{summary}")
            },
        });
    }

    let launch = resolve_backend_launch(resource_dir)?;
    let runtime_root = resolve_runtime_root();
    std::fs::create_dir_all(&runtime_root)
        .map_err(|error| format!("创建 backend runtime 目录失败: {error}"))?;

    let resolved_path = launch
        .program
        .canonicalize()
        .unwrap_or_else(|_| launch.program.clone());
    let metadata = fs::metadata(&launch.program).ok();
    diagnostic_line(
        &runtime_root,
        format!(
            "spawn_prepare resource_dir={} resolved_backend_path={} exists={} is_file={} size={} readonly={} cwd={} args={:?} PATH={} HDZZ_RUNTIME_ROOT={} owner={}",
            resource_dir.map(|path| path.display().to_string()).unwrap_or_default(),
            resolved_path.display(),
            launch.program.exists(),
            launch.program.is_file(),
            metadata.as_ref().map(|value| value.len()).unwrap_or_default(),
            metadata
                .as_ref()
                .map(|value| value.permissions().readonly())
                .unwrap_or(false),
            launch.cwd.display(),
            launch.args,
            env::var("PATH").unwrap_or_default(),
            runtime_root.display(),
            if launch.bundled {
                "tauri-bundled-backend"
            } else {
                "tauri-dev-backend"
            },
        ),
    );

    let stdout_path = runtime_root.join(".run").join("tauri-backend.stdout.log");
    let stderr_path = runtime_root.join(".run").join("tauri-backend.stderr.log");
    let stdout = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&stdout_path)
        .ok();
    let stderr = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&stderr_path)
        .ok();

    let mut command = Command::new(&launch.program);
    command
        .args(&launch.args)
        .current_dir(&launch.cwd)
        .env("HDZZ_RUNTIME_ROOT", &runtime_root)
        .env(
            "HDZZ_BACKEND_OWNER",
            if launch.bundled {
                "tauri-bundled-backend"
            } else {
                "tauri-dev-backend"
            },
        )
        .stdin(Stdio::null());
    if let Some(stdout) = stdout {
        command.stdout(Stdio::from(stdout));
    } else {
        command.stdout(Stdio::null());
    }
    if let Some(stderr) = stderr {
        command.stderr(Stdio::from(stderr));
    } else {
        command.stderr(Stdio::null());
    }

    let spawn_started_at = Instant::now();
    let mut child = match command.spawn() {
        Ok(child) => {
            diagnostic_line(
                &runtime_root,
                format!(
                    "spawn_result=SPAWN_SUCCEEDED child_pid={} stdout_log={} stderr_log={}",
                    child.id(),
                    stdout_path.display(),
                    stderr_path.display(),
                ),
            );
            child
        }
        Err(error) => {
            diagnostic_line(
                &runtime_root,
                format!(
                    "spawn_result=SPAWN_FAILED error={} raw_os_error={:?}",
                    error,
                    error.raw_os_error(),
                ),
            );
            return Err(format!("启动 backend 失败: {error}"));
        }
    };

    let deadline = Instant::now() + ready_timeout();
    while Instant::now() < deadline {
        if health::webui_ready() {
            let (valid, target_count, summary) = config_summary(&runtime_root);
            diagnostic_line(
                &runtime_root,
                format!(
                    "config_summary valid={} target_count={} summary={}",
                    valid, target_count, summary
                ),
            );
            diagnostic_line(
                &runtime_root,
                format!(
                    "backend_ready child_pid={} lifetime_ms={}",
                    child.id(),
                    spawn_started_at.elapsed().as_millis(),
                ),
            );
            return Ok(BackendStatus {
                running: true,
                started_by_shell: true,
                configured: valid && target_count > 0,
                target_count,
                runtime_root: runtime_root.display().to_string(),
                diagnostic_log: diagnostic_path(&runtime_root).display().to_string(),
                stage: "ready".to_string(),
                message: if valid && target_count > 0 {
                    "已启动 Python backend 并连接 WebUI".to_string()
                } else {
                    format!("后台服务已启动，{summary}")
                },
            });
        }
        if let Ok(Some(status)) = child.try_wait() {
            let stdout_excerpt = log_excerpt(&stdout_path);
            let stderr_excerpt = log_excerpt(&stderr_path);
            diagnostic_line(
                &runtime_root,
                format!(
                    "spawn_result=SUCCEEDED_BUT_EXITED child_pid={} status={:?} lifetime_ms={} stdout={:?} stderr={:?}",
                    child.id(),
                    status,
                    spawn_started_at.elapsed().as_millis(),
                    stdout_excerpt,
                    stderr_excerpt,
                ),
            );
            return Err(format!("backend 启动后退出: {status}"));
        }
        thread::sleep(Duration::from_millis(500));
    }

    diagnostic_line(
        &runtime_root,
        format!(
            "backend_ready_timeout child_pid={} lifetime_ms={}",
            child.id(),
            spawn_started_at.elapsed().as_millis(),
        ),
    );

    Err(format!(
        "Python backend 已请求启动，但 {url} 在 {seconds} 秒内未就绪",
        url = health::WEBUI_URL,
        seconds = ready_timeout().as_secs()
    ))
}

pub fn current_status() -> BackendStatus {
    let runtime_root = resolve_runtime_root();
    let (valid, target_count, summary) = config_summary(&runtime_root);
    let running = health::webui_ready();
    BackendStatus {
        running,
        started_by_shell: false,
        configured: valid && target_count > 0,
        target_count,
        runtime_root: runtime_root.display().to_string(),
        diagnostic_log: diagnostic_path(&runtime_root).display().to_string(),
        stage: if running { "ready" } else { "starting" }.to_string(),
        message: if running {
            if valid && target_count > 0 {
                "WebUI 已就绪".to_string()
            } else {
                format!("WebUI 已就绪，{summary}")
            }
        } else {
            "WebUI 尚未就绪".to_string()
        },
    }
}

#[cfg(test)]
mod tests {
    use super::{config_summary, diagnostic_line, python_candidates, DIAGNOSTIC_MAX_BYTES};
    use std::fs;
    use std::path::Path;

    #[test]
    fn python_candidates_keep_project_venv_before_system_python() {
        let candidates = python_candidates(Path::new("/tmp/hdproc/app"));
        assert_eq!(candidates[0], Path::new("/tmp/hdproc/app/.venv/bin/python"));
        assert_eq!(candidates.last().unwrap(), Path::new("python3"));
    }

    #[test]
    fn empty_targets_are_reported_as_unconfigured() {
        let root =
            std::env::temp_dir().join(format!("hd-follow-shell-test-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        fs::write(root.join("config.json"), br#"{"targets": []}"#).unwrap();
        assert_eq!(
            config_summary(&root),
            (true, 0, "尚未配置监听目标".to_string())
        );
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn launcher_diagnostic_log_rotates_when_it_reaches_limit() {
        let root = std::env::temp_dir().join(format!("hd-follow-shell-log-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(root.join(".run")).unwrap();
        let path = root.join(".run").join("tauri-backend-launch.log");
        fs::write(&path, vec![b'x'; DIAGNOSTIC_MAX_BYTES as usize]).unwrap();
        diagnostic_line(&root, "rotated");
        assert!(path.with_extension("log.1").is_file());
        assert!(fs::read_to_string(&path).unwrap().contains("rotated"));
        let _ = fs::remove_dir_all(root);
    }
}
