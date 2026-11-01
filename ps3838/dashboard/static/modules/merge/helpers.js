

export function mergeTeams(event) {
  const home = String(event?.home || "").trim();
  const away = String(event?.away || "").trim();
  if (home || away) return [home, away];
  const name = String(event?.name || "");
  const parts = name.split(/\s+(?:VS\.?|V)\s+/i);
  return parts.length > 1 ? [parts[0].trim(), parts.slice(1).join(" vs ").trim()] : [name.trim(), ""];
}

export function mergeFixtureLabel(event) {
  const [home, away] = mergeTeams(event);
  return [home, away].filter(Boolean).join(" vs ") || "赛事名称待补全";
}

export function mergeEventTime(event, source) {
  const normalized = String(event?.normalizedKickoff || "").trim();
  if (normalized) return normalized;
  const raw = source === "crown" ? event?.startLabel : event?.kickoff;
  return String(raw || "时间未知");
}

export function mergeEventTimeLabel(event, source) {
  const normalized = String(event?.normalizedKickoff || "").trim();
  return normalized ? `北京时间 ${normalized}` : mergeEventTime(event, source);
}

export function mergeSourceTimezoneLabel(event, source) {
  const zone = String(event?.sourceTimezone || "").trim();
  if (zone === "Asia/Shanghai") return "北京";
  if (zone === "America/New_York") return "美东";
  return zone || (source === "crown" ? "美东" : "北京");
}

export function mergeEventRawTime(event, source) {
  const raw = source === "crown" ? event?.startLabel : event?.kickoff;
  return String(raw || "").trim();
}

export function mergeCrownOptionValue(event) {
  const id = String(event?.id || "");
  const leagueId = String(event?.leagueId || "");
  const league = String(event?.league || "未命名联赛");
  const kickoff = mergeEventTime(event, "crown");
  return `${mergeFixtureLabel(event)} · ${league} · ${kickoff} · 赛事 ${id} · 联赛 ${leagueId || "--"}`;
}

export function mergePinnacleOptionValue(event) {
  const id = String(event?.id || "");
  const leagueId = String(event?.leagueId || "");
  const league = String(event?.league || "未命名联赛");
  const kickoff = mergeEventTime(event, "pinnacle");
  return `${mergeFixtureLabel(event)} · ${league} · ${kickoff} · 赛事 ${id} · 联赛 ${leagueId || "--"}`;
}

export function mergeMappedByPinnacle(data) {
  return new Map((data?.mappings || []).map((item) => [String(item.pinnacleId), item]));
}

export function mergeCrownById(data) {
  return new Map((data?.crownEvents || []).map((item) => [String(item.id), item]));
}

export function mergeLeagueOptionValue(league) {
  return `${league.name} · 联赛 ID ${league.id} · ${league.eventCount} 场`;
}

export function mergeLeaguesForSource(data, source, events) {
  const provided = Array.isArray(data?.[source === "crown" ? "crownLeagues" : "pinnacleLeagues"])
    ? data[source === "crown" ? "crownLeagues" : "pinnacleLeagues"]
    : [];
  const eventsByLeague = new Map();
  for (const event of events) {
    const id = String(event?.leagueId || "--");
    if (!eventsByLeague.has(id)) eventsByLeague.set(id, []);
    eventsByLeague.get(id).push(event);
  }
  if (provided.length) {
    return provided.map((league) => {
      const id = String(league?.id || "--");
      const groupedEvents = eventsByLeague.get(id) || [];
      return {
        id,
        name: String(league?.name || groupedEvents[0]?.league || "未命名联赛"),
        eventCount: Number(league?.eventCount ?? groupedEvents.length),
        events: groupedEvents,
      };
    }).filter((league) => league.events.length > 0);
  }
  return [...eventsByLeague.entries()].map(([id, groupedEvents]) => ({
    id,
    name: String(groupedEvents[0]?.league || "未命名联赛"),
    eventCount: groupedEvents.length,
    events: groupedEvents,
  }));
}
