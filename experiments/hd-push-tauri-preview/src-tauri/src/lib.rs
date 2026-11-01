mod backend;
mod health;

use backend::BackendStatus;
use tauri::{Emitter, Manager, Url};

#[tauri::command]
fn backend_status() -> BackendStatus {
    backend::current_status()
}

#[tauri::command]
fn start_backend(app: tauri::AppHandle) -> Result<BackendStatus, String> {
    let resource_dir = app.path().resource_dir().ok();
    let result = backend::ensure_backend_with_resource(resource_dir.as_deref());
    match &result {
        Ok(status) => {
            let _ = app.emit("backend-stage", status.clone());
            if let Some(window) = app.get_webview_window("main") {
                if let Ok(url) = Url::parse(health::WEBUI_URL) {
                    let _ = window.navigate(url);
                }
            }
        }
        Err(error) => {
            let _ = app.emit(
                "backend-stage",
                serde_json::json!({ "stage": "error", "message": error }),
            );
            let _ = app.emit("backend-error", error.clone());
        }
    }
    result
}

#[tauri::command]
fn import_config(path: String) -> Result<String, String> {
    backend::import_config(std::path::Path::new(&path)).map(|value| value.display().to_string())
}

#[tauri::command]
fn import_config_content(app: tauri::AppHandle, content: String) -> Result<String, String> {
    let resource_dir = app.path().resource_dir().ok();
    let target = backend::import_config_content(&content)?;
    backend::restart_workers_with_resource(resource_dir.as_deref())?;
    Ok(target.display().to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .setup(|app| {
            let app_handle = app.handle().clone();
            let resource_dir = app.path().resource_dir().ok();
            let _ = app_handle.emit(
                "backend-stage",
                serde_json::json!({
                    "stage": "starting",
                    "message": "正在启动后台服务",
                }),
            );
            std::thread::spawn(move || {
                match backend::ensure_backend_with_resource(resource_dir.as_deref()) {
                    Ok(status) => {
                        eprintln!("{}", status.message);
                        let _ = app_handle.emit("backend-stage", status.clone());
                        if let Some(window) = app_handle.get_webview_window("main") {
                            if let Ok(url) = Url::parse(health::WEBUI_URL) {
                                if let Err(error) = window.navigate(url) {
                                    eprintln!("导航正式 WebUI 失败: {error}");
                                }
                            }
                        }
                    }
                    Err(error) => {
                        eprintln!("启动正式 WebUI 失败: {error}");
                        let _ = app_handle.emit(
                            "backend-stage",
                            serde_json::json!({
                                "stage": "error",
                                "message": error,
                            }),
                        );
                        if let Some(window) = app_handle.get_webview_window("main") {
                            let _ = window.emit("backend-error", error);
                        }
                    }
                }
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            backend_status,
            start_backend,
            import_config,
            import_config_content
        ])
        .run(tauri::generate_context!())
        .expect("failed to run HD push terminal shell");
}
