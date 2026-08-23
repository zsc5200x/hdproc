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

function toFilename(value) {
  return String(value || '')
    .replace(/https?:\/\//g, '')
    .replace(/[^\w.-]+/g, '_')
    .replace(/_+/g, '_')
    .slice(0, 180);
}

function redactObject(value) {
  if (!value || typeof value !== 'object') return value;
  if (Array.isArray(value)) return value.map(redactObject);

  const out = {};
  for (const [k, v] of Object.entries(value)) {
    if (/pass|pwd|password|token|secret|authorization|cookie|blackbox/i.test(k)) {
      out[k] = '***';
    } else {
      out[k] = redactObject(v);
    }
  }
  return out;
}

function parseForm(raw) {
  try {
    const params = new URLSearchParams(raw);
    const obj = {};
    for (const [k, v] of params.entries()) {
      obj[k] = /pass|pwd|password|token|secret|blackbox/i.test(k) ? '***' : v;
    }
    return obj;
  } catch {
    return null;
  }
}

function summarizePostData(request) {
  const raw = request.postData();
  if (!raw) return null;

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
    const obj = parseForm(raw);
    return obj ? { type: 'form', value: obj } : { type: 'form', value: { _length: raw.length } };
  }

  return { type: 'raw', value: { _length: raw.length } };
}

function isLikelyLoginRequest(entry) {
  if (entry.kind !== 'request') return false;
  if (entry.method !== 'POST') return false;
  if (!/(xhr|fetch)/i.test(entry.resourceType || '')) return false;

  const url = entry.url.toLowerCase();
  if (url.includes('login') || url.includes('signin') || url.includes('auth') || url.includes('token') || url.includes('session')) {
    return true;
  }

  const post = entry.postData && entry.postData.value;
  if (!post || typeof post !== 'object') return false;
  const keys = Object.keys(post).map((k) => k.toLowerCase());
  const hasUser = keys.some((k) => k.includes('user') || k.includes('username') || k.includes('account') || k.includes('login'));
  const hasPass = keys.some((k) => k.includes('pass') || k.includes('pwd'));
  return hasUser && hasPass;
}

function isTransform(url) {
  return safeUrl(url) === 'https://hga027.com/transform.php';
}

function isGismo(url) {
  return safeUrl(url).includes('/gismo/');
}

async function waitForEnter(promptText) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  await new Promise((resolve) => rl.question(promptText, () => resolve()));
  rl.close();
}

async function writeTextMaybeJson(filePathNoExt, contentType, bodyText) {
  if (contentType.includes('application/json')) {
    try {
      const parsed = JSON.parse(bodyText);
      const redacted = redactObject(parsed);
      const outPath = `${filePathNoExt}.json`;
      await fs.writeFile(outPath, JSON.stringify(redacted, null, 2), 'utf8');
      return outPath;
    } catch {
      // fallthrough to text
    }
  }

  const outPath = `${filePathNoExt}.txt`;
  await fs.writeFile(outPath, bodyText, 'utf8');
  return outPath;
}

async function main() {
  const targetUrl = process.env.TARGET_URL || 'https://hga027.com/';
  const outDir = path.join(process.cwd(), 'captures', nowStamp());
  const responsesDir = path.join(outDir, 'responses');
  await fs.mkdir(responsesDir, { recursive: true });

  const summaryPath = path.join(outDir, 'network-summary.json');
  const candidatesPath = path.join(outDir, 'candidates.json');
  const responseIndexPath = path.join(outDir, 'responses-index.json');

  console.log('目标网址:', targetUrl);
  console.log('输出目录:', outDir);
  console.log('注意：响应文件可能包含敏感信息（cookie/token），请勿外传。');
  console.log('');

  const browser = await chromium.launch({ headless: false });
  const context = await browser.newContext();
  const page = await context.newPage();

  /** @type {Array<any>} */
  const entries = [];
  const requestToId = new WeakMap();
  const requestMeta = new WeakMap();
  let seq = 0;

  /** @type {Array<any>} */
  const responseIndex = [];

  page.on('request', (request) => {
    const id = `r${++seq}`;
    requestToId.set(request, id);

    let resourceType = 'unknown';
    try {
      resourceType = request.resourceType();
    } catch {}

    const postData = summarizePostData(request);
    const meta = {
      id,
      safeUrl: safeUrl(request.url()),
      url: request.url(),
      method: request.method(),
      resourceType,
      postData
    };
    requestMeta.set(request, meta);

    entries.push({
      id,
      kind: 'request',
      ts: Date.now(),
      method: request.method(),
      url: request.url(),
      safeUrl: safeUrl(request.url()),
      resourceType,
      postData
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

    const url = response.url();
    if (!(isTransform(url) || isGismo(url))) {
      return;
    }

    const meta = requestMeta.get(request);
    const p = meta && meta.postData && meta.postData.value && meta.postData.value.p ? String(meta.postData.value.p) : null;
    const gid = meta && meta.postData && meta.postData.value && meta.postData.value.gid ? String(meta.postData.value.gid) : null;

    const fileBaseParts = [];
    fileBaseParts.push(isTransform(url) ? 'transform' : 'gismo');
    if (p) fileBaseParts.push(`p-${p}`);
    if (gid) fileBaseParts.push(`gid-${gid}`);
    fileBaseParts.push(id);

    const fileBase = path.join(responsesDir, toFilename(fileBaseParts.join('--')));

    try {
      const contentType = (await response.headerValue('content-type')) || '';
      const text = await response.text();
      const savedPath = await writeTextMaybeJson(fileBase, contentType, text);
      responseIndex.push({
        id,
        url,
        safeUrl: safeUrl(url),
        status: response.status(),
        contentType,
        p,
        gid,
        savedPath: path.relative(process.cwd(), savedPath)
      });
    } catch (error) {
      responseIndex.push({
        id,
        url,
        safeUrl: safeUrl(url),
        status: response.status(),
        p,
        gid,
        error: error && error.message ? error.message : String(error)
      });
    }
  });

  console.log('正在打开页面…');
  await page.goto(targetUrl, { waitUntil: 'domcontentloaded' });

  await waitForEnter(
    [
      '请在浏览器里完成以下操作后，回到终端按回车：',
      '1) 手动登录',
      '2) 点进：足球 -> 滚球(进行中) -> 打开一场比赛 -> 看到盘口/水位',
      '3) 停留 10 秒让它刷新几次',
      ''
    ].join('\n')
  );

  await context.close();
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
  await fs.writeFile(responseIndexPath, JSON.stringify(responseIndex, null, 2), 'utf8');

  console.log('');
  console.log('完成！文件已保存：');
  console.log('-', path.relative(process.cwd(), summaryPath));
  console.log('-', path.relative(process.cwd(), candidatesPath));
  console.log('-', path.relative(process.cwd(), responseIndexPath));
  console.log('-', path.relative(process.cwd(), responsesDir));
}

main().catch((error) => {
  console.error('抓包失败：', error && error.message ? error.message : error);
  process.exitCode = 1;
});

