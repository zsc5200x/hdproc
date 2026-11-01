export const PAGE_IDS = ["my-events"];

export function resolvePage(value) {
  return PAGE_IDS.includes(value) ? value : "my-events";
}
