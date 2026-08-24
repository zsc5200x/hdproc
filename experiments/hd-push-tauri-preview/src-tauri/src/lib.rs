mod backend;
mod health;

use backend::BackendStatus;
use tauri::{Manager, Url};

#[tauri::command]
fn backend_status() -> BackendStatus {
    backend::current_status()
}

#[tauri::command]
fn start_backend() -> Result<BackendStatus, String> {
    backend::ensure_backend()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let app_handle = app.handle().clone();
            std::thread::spawn(move || match backend::ensure_backend() {
                Ok(status) => {
                    eprintln!("{}", status.message);
                    if let Some(window) = app_handle.get_webview_window("main") {
                        if let Ok(url) = Url::parse(health::WEBUI_URL) {
                            if let Err(error) = window.navigate(url) {
                                eprintln!("导航正式 WebUI 失败: {error}");
                            }
                        }
                    }
                }
                Err(error) => eprintln!("启动正式 WebUI 失败: {error}"),
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![backend_status, start_backend])
        .run(tauri::generate_context!())
        .expect("failed to run HD push terminal shell");
}
