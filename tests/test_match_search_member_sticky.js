const test = require('node:test');
const assert = require('node:assert/strict');

let tools = {};
try {
  tools = require('../app/webui/match_search.js');
} catch {
  tools = {};
}

test('creates separate sticky lanes for games odds and events', () => {
  assert.equal(typeof tools.createReadLaneState, 'function');
  const lanes = tools.createReadLaneState();
  assert.deepEqual(lanes, {
    games: { memberId: '', memberLabel: '' },
    odds: { memberId: '', memberLabel: '' },
    events: { memberId: '', memberLabel: '' },
  });
});

test('stores sticky member per lane and keeps other lanes independent', () => {
  assert.equal(typeof tools.setReadLaneMember, 'function');
  const lanes = tools.createReadLaneState();
  tools.setReadLaneMember(lanes, 'games', { id: 'read-1', label: 'A' });
  tools.setReadLaneMember(lanes, 'odds', { id: 'read-2', label: 'B' });
  assert.equal(lanes.games.memberId, 'read-1');
  assert.equal(lanes.odds.memberId, 'read-2');
  assert.equal(lanes.events.memberId, '');
});

test('builds exclude list from other active lanes', () => {
  assert.equal(typeof tools.collectReadLaneExcludes, 'function');
  const lanes = tools.createReadLaneState();
  tools.setReadLaneMember(lanes, 'games', { id: 'read-1', label: 'A' });
  tools.setReadLaneMember(lanes, 'odds', { id: 'read-2', label: 'B' });
  assert.deepEqual(tools.collectReadLaneExcludes(lanes, 'events'), ['read-1', 'read-2']);
  assert.deepEqual(tools.collectReadLaneExcludes(lanes, 'odds'), ['read-1']);
});

test('can reset one lane or all lanes', () => {
  assert.equal(typeof tools.resetReadLaneState, 'function');
  const lanes = tools.createReadLaneState();
  tools.setReadLaneMember(lanes, 'games', { id: 'read-1', label: 'A' });
  tools.setReadLaneMember(lanes, 'odds', { id: 'read-2', label: 'B' });
  tools.resetReadLaneState(lanes, 'games');
  assert.equal(lanes.games.memberId, '');
  assert.equal(lanes.odds.memberId, 'read-2');
  tools.resetReadLaneState(lanes);
  assert.equal(lanes.odds.memberId, '');
  assert.equal(lanes.events.memberId, '');
});
