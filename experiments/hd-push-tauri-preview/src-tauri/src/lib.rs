use serde::Serialize;

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct DashboardMetrics {
    orders_today: u32,
    pushed_today: u32,
    success_rate: f32,
    failed_today: u32,
    targets_online: u32,
    channels_online: u32,
}

#[derive(Clone, Serialize)]
struct TrendPoint {
    label: String,
    orders: u32,
    success: u32,
}

#[derive(Clone, Serialize)]
struct PushOrder {
    id: String,
    time: String,
    league: String,
    r#match: String,
    phase: String,
    market: String,
    period: String,
    selection: String,
    amount: u32,
    status: String,
    target: String,
    channel: String,
    latency: String,
    message: String,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct TargetAccount {
    id: String,
    name: String,
    alias: String,
    state: String,
    orders: u32,
    last_seen: String,
}

#[derive(Clone, Serialize)]
struct TelegramChannel {
    id: String,
    name: String,
    audience: String,
    state: String,
    deliveries: u32,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct DashboardSnapshot {
    generated_at: String,
    metrics: DashboardMetrics,
    trend: Vec<TrendPoint>,
    orders: Vec<PushOrder>,
    targets: Vec<TargetAccount>,
    channels: Vec<TelegramChannel>,
}

struct OrderInput<'a> {
    id: &'a str,
    time: &'a str,
    league: &'a str,
    match_name: &'a str,
    phase: &'a str,
    market: &'a str,
    period: &'a str,
    selection: &'a str,
    amount: u32,
    status: &'a str,
    target: &'a str,
    channel: &'a str,
    latency: &'a str,
}

fn push_order(input: OrderInput<'_>) -> PushOrder {
    let message = format!(
        "🤣新注单提醒🤣\n⚽️ ⚽️ ⚽️ ⚽️ ⚽️ ⚽️ ⚽️ ⚽️ ⚽️ ⚽️\n🤣{}\n🤣{}\n\n📍 状态：{}\n玩法：{}\n时段：{}\n选择：{}\n\n💰 金额：{}",
        input.league,
        input.match_name,
        input.phase,
        input.market,
        input.period,
        input.selection,
        input.amount
    );

    PushOrder {
        id: input.id.into(),
        time: input.time.into(),
        league: input.league.into(),
        r#match: input.match_name.into(),
        phase: input.phase.into(),
        market: input.market.into(),
        period: input.period.into(),
        selection: input.selection.into(),
        amount: input.amount,
        status: input.status.into(),
        target: input.target.into(),
        channel: input.channel.into(),
        latency: input.latency.into(),
        message,
    }
}

fn dashboard_snapshot() -> DashboardSnapshot {
    DashboardSnapshot {
        generated_at: "2026-07-25T14:08:36+08:00".into(),
        metrics: DashboardMetrics {
            orders_today: 186,
            pushed_today: 183,
            success_rate: 98.7,
            failed_today: 3,
            targets_online: 4,
            channels_online: 2,
        },
        trend: [
            ("08:00", 12, 100),
            ("09:00", 23, 100),
            ("10:00", 18, 94),
            ("11:00", 31, 97),
            ("12:00", 27, 100),
            ("13:00", 36, 98),
            ("14:00", 39, 99),
        ]
        .into_iter()
        .map(|(label, orders, success)| TrendPoint {
            label: label.into(),
            orders,
            success,
        })
        .collect(),
        orders: vec![
            push_order(OrderInput {
                id: "HD-0725-0186",
                time: "14:08:32",
                league: "英格兰超级联赛",
                match_name: "阿森纳 vs 切尔西",
                phase: "滚球",
                market: "让球",
                period: "全场",
                selection: "阿森纳 -0.5 @0.95",
                amount: 100,
                status: "sent",
                target: "主账号 · 8c91a",
                channel: "HD 实时单",
                latency: "0.7s",
            }),
            push_order(OrderInput {
                id: "HD-0725-0185",
                time: "14:06:18",
                league: "西班牙甲级联赛",
                match_name: "巴塞罗那 vs 比利亚雷亚尔",
                phase: "早盘",
                market: "进球大小",
                period: "全场",
                selection: "大 2.5/3 @0.88",
                amount: 300,
                status: "pending",
                target: "主账号 · 8c91a",
                channel: "HD 实时单",
                latency: "排队中",
            }),
            push_order(OrderInput {
                id: "HD-0725-0184",
                time: "14:03:51",
                league: "法国甲级联赛",
                match_name: "巴黎圣日耳曼 vs 里昂",
                phase: "滚球",
                market: "独赢",
                period: "下半场",
                selection: "巴黎圣日耳曼 @0.76",
                amount: 200,
                status: "failed",
                target: "观察账号 · 2fa07",
                channel: "VIP 精选单",
                latency: "重试 2/3",
            }),
            push_order(OrderInput {
                id: "HD-0725-0183",
                time: "13:58:04",
                league: "德国甲级联赛",
                match_name: "拜仁慕尼黑 vs 多特蒙德",
                phase: "滚球",
                market: "进球大小",
                period: "上半场",
                selection: "小 1.5 @0.91",
                amount: 500,
                status: "sent",
                target: "主账号 · 8c91a",
                channel: "HD 实时单",
                latency: "0.9s",
            }),
            push_order(OrderInput {
                id: "HD-0725-0182",
                time: "13:52:27",
                league: "意大利甲级联赛",
                match_name: "国际米兰 vs AC米兰",
                phase: "早盘",
                market: "让球",
                period: "全场",
                selection: "国际米兰 -0/0.5 @0.86",
                amount: 150,
                status: "sent",
                target: "副账号 · b783d",
                channel: "VIP 精选单",
                latency: "0.6s",
            }),
            push_order(OrderInput {
                id: "HD-0725-0181",
                time: "13:47:12",
                league: "日本职业联赛",
                match_name: "川崎前锋 vs 横滨水手",
                phase: "滚球",
                market: "进球大小",
                period: "全场",
                selection: "大 3 @1.02",
                amount: 260,
                status: "sent",
                target: "副账号 · b783d",
                channel: "HD 实时单",
                latency: "0.8s",
            }),
        ],
        targets: vec![
            TargetAccount {
                id: "8c91a".into(),
                name: "主监听账号".into(),
                alias: "AG · 8c91a".into(),
                state: "online".into(),
                orders: 96,
                last_seen: "刚刚".into(),
            },
            TargetAccount {
                id: "b783d".into(),
                name: "副监听账号".into(),
                alias: "AG · b783d".into(),
                state: "online".into(),
                orders: 54,
                last_seen: "3 秒前".into(),
            },
            TargetAccount {
                id: "2fa07".into(),
                name: "观察账号".into(),
                alias: "AG · 2fa07".into(),
                state: "warning".into(),
                orders: 21,
                last_seen: "18 秒前".into(),
            },
            TargetAccount {
                id: "96bd1".into(),
                name: "夜盘账号".into(),
                alias: "AG · 96bd1".into(),
                state: "online".into(),
                orders: 15,
                last_seen: "6 秒前".into(),
            },
        ],
        channels: vec![
            TelegramChannel {
                id: "-1002186001".into(),
                name: "HD 实时单".into(),
                audience: "1,286 位订阅者".into(),
                state: "connected".into(),
                deliveries: 132,
            },
            TelegramChannel {
                id: "-1002186002".into(),
                name: "VIP 精选单".into(),
                audience: "368 位订阅者".into(),
                state: "connected".into(),
                deliveries: 51,
            },
        ],
    }
}

#[tauri::command]
fn get_dashboard_snapshot() -> DashboardSnapshot {
    dashboard_snapshot()
}

#[tauri::command]
fn set_push_engine(enabled: bool) -> bool {
    enabled
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            get_dashboard_snapshot,
            set_push_engine
        ])
        .run(tauri::generate_context!())
        .expect("failed to run HD push terminal preview");
}

#[cfg(test)]
mod tests {
    #[test]
    fn snapshot_contract_matches_frontend() {
        let snapshot = super::dashboard_snapshot();

        assert_eq!(snapshot.metrics.orders_today, 186);
        assert_eq!(snapshot.orders.len(), 6);
        assert_eq!(snapshot.orders[0].status, "sent");
        assert!(snapshot.orders[0].message.contains("新注单提醒"));
    }
}
