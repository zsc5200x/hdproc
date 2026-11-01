/** Keep the deadline active until the JSON body has been consumed. */
const APP_BASE = (() => {
  if (typeof window === "undefined" || !window.location) return "";
  const path = String(window.location.pathname || "/");
  return path === "/ps3838" || path.startsWith("/ps3838/") ? "/ps3838" : "";
})();

function withAppBase(url) {
  if (typeof url !== "string") return url;
  if (APP_BASE && url.startsWith("/api/")) return `${APP_BASE}${url}`;
  return url;
}

export async function fetchJson(url, init = {}, { timeoutMs = 8000, allowInvalidJson = false } = {}) {
  const controller = new AbortController();
  const signal = init.signal ? AbortSignal.any([init.signal, controller.signal]) : controller.signal;
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(withAppBase(url), { ...init, signal });
    let payload;
    try {
      payload = await response.json();
    } catch (error) {
      if (!allowInvalidJson || error.name === "AbortError") throw error;
      payload = {};
    }
    return { response, payload };
  } finally {
    clearTimeout(timer);
  }
}
