const fs = require('node:fs');

const { parseGameListXml, parseGameMoreXml } = require('../src/parsers');

function readText(filePath) {
  return fs.readFileSync(filePath, 'utf8');
}

function main() {
  const gameListPath = 'captures/20260208-030747/responses/transform--p-get_game_list--r122.txt';
  const gameMorePath = 'captures/20260208-030747/responses/transform--p-get_game_more--r184.txt';

  if (!fs.existsSync(gameListPath) || !fs.existsSync(gameMorePath)) {
    console.log('跳过：本地没有示例抓包文件（captures 目录可能被清理）');
    process.exit(0);
  }

  const list = parseGameListXml(readText(gameListPath));
  console.log('[get_game_list] games =', list.games.length);

  const more = parseGameMoreXml(readText(gameMorePath));
  console.log('[get_game_more] boards =', more.boards.length, 'FT_gid =', more.ftGids.length);
  const main = more.boards.find((b) => b && b.attrs && b.attrs.master === 'Y') || more.boards[0] || null;
  console.log('[main board]', {
    gid: main && main.meta ? main.meta.gid : null,
    league: main && main.meta ? main.meta.league : null,
    team_h: main && main.meta ? main.meta.team_h : null,
    team_c: main && main.meta ? main.meta.team_c : null,
    score:
      main && main.meta && main.meta.score_h != null && main.meta.score_c != null
        ? `${main.meta.score_h}:${main.meta.score_c}`
        : null,
    re_time: main && main.meta ? main.meta.re_time : null,
    markets: main ? (main.markets || []).length : 0
  });
}

main();
