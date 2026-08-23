function extractBetween(text, startTag, endTag) {
  const start = text.indexOf(startTag);
  if (start === -1) return null;
  const end = text.indexOf(endTag, start + startTag.length);
  if (end === -1) return null;
  return text.slice(start + startTag.length, end);
}

function parseGameListXml(xmlText) {
  const jsonStr = extractBetween(xmlText, '<original>', '</original>');
  if (!jsonStr) {
    throw new Error('get_game_list 响应里没有找到 <original> JSON（站点可能改版）');
  }

  let parsed;
  try {
    parsed = JSON.parse(jsonStr);
  } catch {
    throw new Error('get_game_list 的 <original> 不是合法 JSON（站点可能改版）');
  }

  const games = Object.values(parsed).filter(Boolean);
  return { games, original: parsed };
}

function parseLeafTags(xmlText) {
  /** @type {Record<string, string>} */
  const tags = {};

  // 只抓“叶子节点”（不包含子标签），够我们提取盘口/水位字段。
  const re = /<([A-Za-z0-9_]+)>([^<]*)<\/\1>/g;
  let m;
  while ((m = re.exec(xmlText))) {
    const key = m[1];
    const value = m[2];
    // get_game_more 里可能有多个 <game> 节点（不同附加盘/玩法）。为了不被后面的重复字段覆盖，
    // 这里用“首次出现优先”的策略。每个 <game> 会单独解析，所以不会影响赔率字段收集。
    if (tags[key] === undefined) tags[key] = value;
  }

  return tags;
}

function parseFtGidList(xmlText) {
  const ft = extractBetween(xmlText, '<FT_gid>', '</FT_gid>');
  if (!ft) return [];

  /** @type {Array<{gid: string, master: 'Y'|'N'}>} */
  const out = [];
  const re = /<gid\s+id="(\d+)"\s+master="([YN])"\s*\/>/g;
  let m;
  while ((m = re.exec(ft))) {
    out.push({ gid: m[1], master: /** @type {'Y'|'N'} */ (m[2]) });
  }
  return out;
}

function marketName(code) {
  const map = {
    RE: '让球',
    ROU: '大小',
    RM: '独赢',
    REO: '单双（Odd/Even）',
    ROUH: '主队大小',
    ROUC: '客队大小',
    RDC: '波胆？(RDC)',
    RWM: '胜分差？(RWM)'
  };
  return map[code] || '未知玩法';
}

function sortMarkets(markets) {
  const order = ['RE', 'ROU', 'RM', 'REO', 'ROUH', 'ROUC'];
  const rank = new Map(order.map((c, i) => [c, i]));
  return markets.slice().sort((a, b) => {
    const ra = rank.has(a.code) ? rank.get(a.code) : 999;
    const rb = rank.has(b.code) ? rank.get(b.code) : 999;
    if (ra !== rb) return ra - rb;
    return a.code.localeCompare(b.code);
  });
}

function extractFirstTagValue(xmlText, tag) {
  const re = new RegExp(`<${tag}>([^<]*)</${tag}>`);
  const m = xmlText.match(re);
  return m ? m[1] : null;
}

function parseGameAttributes(gameXml) {
  const head = gameXml.match(/<game\b([^>]*)>/);
  const attrs = head ? head[1] : '';

  /** @type {Record<string, string>} */
  const out = {};
  const re = /([A-Za-z0-9_:-]+)="([^"]*)"/g;
  let m;
  while ((m = re.exec(attrs))) {
    out[m[1]] = m[2];
  }
  return out;
}

function buildMarketsFromTags(tags) {
  const markets = [];
  for (const [key, value] of Object.entries(tags)) {
    if (!key.startsWith('sw_')) continue;
    if (value !== 'Y') continue;

    const code = key.slice('sw_'.length);
    const codeLower = code.toLowerCase();

    /** @type {Record<string, string>} */
    const ratios = {};
    /** @type {Record<string, string>} */
    const iors = {};

    for (const [k, v] of Object.entries(tags)) {
      if (k.startsWith(`ratio_${codeLower}`)) ratios[k] = v;
      if (k.startsWith(`ior_${code}`)) iors[k] = v;
    }

    if (Object.keys(ratios).length === 0 && Object.keys(iors).length === 0) continue;
    markets.push({ code, name: marketName(code), ratios, iors });
  }
  return sortMarkets(markets);
}

function parseGameMoreXml(xmlText) {
  const ftGids = parseFtGidList(xmlText);

  const code = extractFirstTagValue(xmlText, 'code');
  const ecid = extractFirstTagValue(xmlText, 'ecid');
  const systime = extractFirstTagValue(xmlText, 'systime');

  const gameSegRe = /<game\b[^>]*>[\s\S]*?<\/game>/g;
  /** @type {Array<any>} */
  const boards = [];
  let m;
  while ((m = gameSegRe.exec(xmlText))) {
    const gameXml = m[0];
    const attrs = parseGameAttributes(gameXml);
    const tags = parseLeafTags(gameXml);

    const meta = {
      gid: tags.gid || null,
      lid: tags.lid || null,
      league: tags.league || null,
      team_h: tags.team_h || null,
      team_c: tags.team_c || null,
      score_h: tags.score_h || null,
      score_c: tags.score_c || null,
      re_time: tags.re_time || null,
      systime: tags.systime || null,
      dangerous: tags.dangerous || null,
      strong: tags.strong || null,
      datetime: tags.datetime || null
    };

    boards.push({
      attrs: {
        id: attrs.id || null,
        master: attrs.master || null,
        mode: attrs.mode || null,
        ptype: attrs.ptype || null
      },
      meta,
      markets: buildMarketsFromTags(tags),
      raw: tags
    });
  }

  return { code: code || null, ecid: ecid || null, systime: systime || null, ftGids, boards };
}

module.exports = {
  parseGameListXml,
  parseGameMoreXml
};
