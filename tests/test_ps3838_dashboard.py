"""只读仪表盘集成；离线构造流水，不访问平博、不发送 TG。"""

import json
from unittest.mock import Mock

import pytest

from hdzz.api.webui import server


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')


@pytest.fixture
def dashboard(tmp_path):
    handler = object.__new__(server.Handler)
    handler._data_dir = tmp_path / 'data'
    handler._orders_dir = tmp_path / 'orders'
    handler._orders_dir.mkdir()
    handler._config_path = tmp_path / 'config.json'
    handler._config_path.write_text(json.dumps({'ps3838_accounts': [
        {'name': 'ps.demo', 'username': 'monitor', 'enabled': True},
        {'name': 'ps_two', 'username': 'monitor2', 'enabled': False},
    ]}), encoding='utf-8')
    handler._send_json = Mock()
    order = {'orderId': '1001', 'target': 'ps.demo', 'ts': 100, 'event': 'new',
             'stake': 150, 'toRisk': 150, 'toWin': 132.6, 'currency': 'THB',
             'line': 'AWAY -0.25', 'status': '', 'account': 'member',
             'choices': [{'inplay': True, 'btName': 'HDP'}]}
    write_rows(handler._data_dir / 'ps3838/orders/ps.demo.jsonl', [
        order, {**order, 'ts': 300, 'event': 'update', 'status': 'settled', 'win': 132.6},
        {**order, 'ts': 200, 'orderId': '1002'},
    ])
    with (handler._data_dir / 'ps3838/orders/ps.demo.jsonl').open('a') as f:
        f.write('{"unfinished":')
    write_rows(handler._data_dir / 'ps3838/orders/ps_two.jsonl', [{**order, 'ts': 250}])
    write_rows(handler._data_dir / 'ps3838/orders/deleted_account.jsonl', [{**order, 'ts': 400}])
    write_rows(handler._orders_dir / 'crown.jsonl', [{**order, 'ts': 150, 'target': 'crown'}])
    write_rows(handler._data_dir / 'follow_results.jsonl', [
        {'orderId': '1001', 'ok': True, 'ts_ms': 150000, 'submit_params': {'ioratio': '0.80'}},
    ])
    return handler


def get(handler, path):
    handler.path = path
    handler.do_GET()
    code, payload = handler._send_json.call_args.args
    assert code == 200
    return payload


def test_dashboard_merges_latest_events_and_isolates_colliding_ticket_ids(dashboard):
    response = get(dashboard, '/api/orders')
    rows = response['orders']
    assert [row['ts'] for row in rows] == [300, 250, 200, 150]
    ps = rows[0]
    assert ps['orderId'] == '1001'
    assert ps['status'] == 'settled'
    assert ps['win'] == 132.6
    assert ps['event'] == 'update'
    assert ps['provider'] == 'ps3838' and ps['read_only'] is True
    assert ps['display_id'] != rows[1]['display_id']
    assert ps['target'] == 'ps.demo'  # 来自当前账号，不信任文件里的 target。
    assert ps['orderType'] == '滚球' and ps['marketLabel'] == '让球'
    assert ps['follow_avg_odds'] is None
    assert not ps['display_reverse_applied']
    assert rows[-1]['follow_avg_odds'] == 0.8
    assert response['targets'] == ['crown']
    assert get(dashboard, '/api/targets')['targets'] == ['crown']
    assert sorted(p.name for p in dashboard._orders_dir.iterdir()) == ['crown.jsonl']


def test_target_since_and_limit_apply_after_latest_event_selection(dashboard):
    rows = get(dashboard, '/api/orders?target=ps.demo&since=210&limit=1')['orders']
    assert [(row['orderId'], row['ts']) for row in rows] == [('1001', 300)]
    assert len(get(dashboard, '/api/orders?target=crown')['orders']) == 1
    assert get(dashboard, '/api/orders?target=deleted_account')['orders'] == []
    assert [row['ts'] for row in get(dashboard, '/api/orders?limit=2')['orders']] == [300, 250]


def test_crown_local_deletion_does_not_hide_same_ps_ticket(dashboard):
    server.save_deleted_order_ids(dashboard._data_dir, {'1001'})
    rows = get(dashboard, '/api/orders')['orders']
    assert len(rows) == 3
    assert all(row['read_only'] for row in rows)
