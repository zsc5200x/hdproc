/** Keep the deadline active until the JSON body has been consumed. */
export async function fetchJson(url, init = {}, { timeoutMs = 8000, allowInvalidJson = false } = {}) {
  const controller = new AbortController();
  const signal = init.signal ? AbortSignal.any([init.signal, controller.signal]) : controller.signal;
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { ...init, signal });
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
