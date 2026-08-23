import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles.css";

if ("__TAURI_INTERNALS__" in window) {
  document.documentElement.classList.add("tauri-runtime");
}

createRoot(document.getElementById("root")!).render(
  <StrictMode><App /></StrictMode>,
);
