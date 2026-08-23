const fs = require('node:fs/promises');
const path = require('node:path');
const readline = require('node:readline');
const { chromium } = require('playwright');

function nowStamp() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return [
    d.getFullYear(),
    pad(d.getMonth() + 1),
    pad(d.getDate())
  ].join('') + '-' + [pad(d.getHours()), pad(d.getMinutes()), pad(d.getSeconds())].join('');
}

function safeUrl(url) {
  try {
    const u = new URL(url);
    return `${u.protocol}//${u.host}${u.pathname}`;
  } catch {
    return String(url);
  }
}

function redactObject(value) {
  if (!value || typeof value !== 'object') return value;
  if (Array.isArray(value)) return value.map(redactObject);

  const out = {};
  for (const [k, v] of Object.entries(value)) {
    if (/pass|pwd|password|token|secret|authorization|cookie/i.test(k)) {
      out[k] = '***';
    } else {
      out[k] = redactObject(v);
    }
  }
  return out;
}

function summarizePostData(request) {
  const raw = request.postData();
  if (!raw) return null;

  // Avoid storing plaintext passwords. Keep only safe info.
  const contentType = request.headers()['content-type'] || '';

  if (contentType.includes('application/json')) {
    try {
      const parsed = JSON.parse(raw);
      return { type: 'json', value: redactObject(parsed) };
    } catch {
      return { type: 'json', value: { _invalidJson: true, _length: raw.length } };
    }
  }

  if (contentType.includes('application/x-www-form-urlencoded')) {
    try {
      const params = new URLSearchParams(raw);
      const obj = {};
      for (const [k, v] of params.entries()) {
        obj[k] = /pass|pwd|password|token|secret/i.test(k) ? '***' : v;
      }
      return { type: 'form', value: obj };
    } catch {
      return { type: 'form', value: { _length: raw.length } };
    }
  }

  return { type: 'raw', value: { _length: raw.length } };
}

function hasLikelyAuthFields(postData) {
  if (!postData || !postData.value || typeof postData.value !== 'object') return false;

  const keys = Object.keys(postData.value).map((k) => k.toLowerCase());
  const hasUser =
    keys.some((k) => k.includes('user')) ||
    keys.some((k) => k.includes('username')) ||
    keys.some((k) => k.includes('account')) ||
    keys.some((k) => k.includes('login'));
  const hasPass =
    keys.some((k) => k.includes('pass')) ||
    keys.some((k) => k.includes('pwd'));

  return hasUser && hasPass;
}

function isLikelyLoginRequest(entry) {
  if (entry.kind !== 'request') return false;
  if (entry.method !== 'POST') return false;
  if (!/(xhr|fetch)/i.test(entry.resourceType || '')) return false;

  const url = entry.url.toLowerCase();
  if (
    url.includes('login') ||
    url.includes('signin') ||
    url.includes('auth') ||
    url.includes('token') ||
    url.includes('session')
  ) {
    return true;
  }

  return hasLikelyAuthFields(entry.postData);
}

async function waitForEnter(promptText) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  await new Promise((resolve) => rl.question(promptText, () => resolve()));
  rl.close();
}

async function main() {
  const targetUrl = process.env.TARGET_URL || 'https://hga027.com/';
  const saveHar = /^(1|true|yes)$/i.test(String(process.env.SAVE_HAR || ''));
  const outDir = path.join(process.cwd(), 'captures', nowStamp());
  await fs.mkdir(outDir, { recursive: true });

  const harPath = path.join(outDir, 'login.har.zip');
  const summaryPath = path.join(outDir, 'network-summary.json');
  const candidatesPath = path.join(outDir, 'candidates.json');

  console.log('目标网址:', targetUrl);
  console.log('输出目录:', outDir);
  if (saveHar) {
    console.log('注意：HAR 可能包含敏感信息（包含登录请求），请勿外传。');
  } else {
    console.log('默认不保存 HAR（更安全）。如需保存：运行前设置 SAVE_HAR=1');
  }
  console.log('');

  const browser = await chromium.launch({ headless: false });
  const context = await browser.newContext(
    saveHar
      ? {
          recordHar: { path: harPath }
        }
      : {}
  );
  const page = await context.newPage();

  /** @type {Array<any>} */
  const entries = [];
  const requestToId = new WeakMap();
  let seq = 0;

  page.on('request', (request) => {
    const id = `r${++seq}`;
    requestToId.set(request, id);

    let resourceType = 'unknown';
    try {
      resourceType = request.resourceType();
    } catch {}

    entries.push({
      id,
      kind: 'request',
      ts: Date.now(),
      method: request.method(),
      url: request.url(),
      safeUrl: safeUrl(request.url()),
      resourceType,
      postData: summarizePostData(request)
    });
  });

  page.on('response', async (response) => {
    const request = response.request();
    const id = requestToId.get(request) || `r?-${++seq}`;
    entries.push({
      id,
      kind: 'response',
      ts: Date.now(),
      status: response.status(),
      url: response.url(),
      safeUrl: safeUrl(response.url())
    });
  });

  console.log('正在打开页面…');
  await page.goto(targetUrl, { waitUntil: 'domcontentloaded' });

  await waitForEnter('请在打开的浏览器里手动登录完成后，回到这里按回车继续…');

  console.log('正在保存抓包文件…');
  await context.close(); // flush HAR
  await browser.close();

  await fs.writeFile(summaryPath, JSON.stringify(entries, null, 2), 'utf8');

  const candidates = entries
    .filter(isLikelyLoginRequest)
    .map((e) => ({
      method: e.method,
      url: e.url,
      resourceType: e.resourceType,
      postData: e.postData
    }));
  await fs.writeFile(candidatesPath, JSON.stringify(candidates, null, 2), 'utf8');

  console.log('');
  console.log('完成！文件已保存：');
  if (saveHar) {
    console.log('-', harPath);
  }
  console.log('-', summaryPath);
  console.log('-', candidatesPath);
  console.log('');

  if (candidates.length) {
    console.log('疑似登录接口（候选）：');
    for (const c of candidates.slice(0, 20)) {
      console.log(`- ${c.method} ${c.url}`);
    }
  } else {
    console.log('未自动识别到“明显像登录”的接口。你可以打开 HAR 进一步查看所有请求。');
  }
}

main().catch((error) => {
  console.error('抓包失败：', error && error.message ? error.message : error);
  process.exitCode = 1;
});
