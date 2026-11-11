// Heat Watch: shared logic for the n8n Code nodes.
//
// Pure functions only: no n8n globals, no network, no dependencies. The same file runs in
// n8n (inlined into each Code node by scripts/build_workflows.py) and under `node --test`.
'use strict';

const RISK_WINDOW_HOURS = 4;
const EVENING_HOUR = 20;
const MAX_SEEN_IDS = 1000;
const ATTACH_MODES = ['heat_only', 'always', 'never'];
const WHERE = {
  maximum: 'at the hottest tile',
  minimum: 'at the coolest tile',
  mean: 'averaged over the area',
  average: 'averaged over the area',
};

/** Escape text for Telegram's HTML parse mode. */
function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

/**
 * Unwrap one MCP `tools/call` answer sent over Streamable HTTP (a single SSE `data:` line)
 * and FortyGuard's `{ok, result: {value}}` envelope. Throws on any error so the run fails loudly.
 */
function parseMcpResponse(text) {
  const body = String(text ?? '').trim();
  let payload = body;
  if (!body.startsWith('{')) {
    // Server-sent event: one JSON-RPC message, possibly split over several `data:` lines.
    const data = body.split(/\r?\n/).filter((l) => l.startsWith('data:')).map((l) => l.slice(5).trim());
    if (!data.length) throw new Error('MCP: no "data:" line in the response');
    payload = data.join('\n');
  }
  const rpc = JSON.parse(payload);
  if (rpc.error) throw new Error(`MCP error ${rpc.error.code}: ${rpc.error.message}`);
  const content = rpc.result?.content?.[0]?.text;
  if (typeof content !== 'string') throw new Error('MCP: unexpected result shape');
  if (rpc.result.isError) throw new Error(`MCP tool error: ${content.slice(0, 300)}`);
  const envelope = JSON.parse(content);
  if (!envelope.ok) throw new Error(`FortyGuard error: ${JSON.stringify(envelope.error)}`);
  if (!envelope.result || !('value' in envelope.result)) throw new Error('FortyGuard: response has no result value');
  return envelope.result.value;
}

/** Check the Settings node once per run; a clear error beats a silent wrong message. */
function validateSettings(settings) {
  const s = settings ?? {};
  const problems = [];
  // Number('') is 0, so blank values must be rejected explicitly.
  const blank = (v) => v === undefined || v === null || String(v).trim() === '';
  for (const key of ['heat_threshold_c', 'work_day_start_hour', 'max_reports_per_run']) {
    if (blank(s[key])) problems.push(`${key} is empty`);
  }
  if (!Number.isFinite(Number(s.heat_threshold_c))) problems.push('heat_threshold_c must be a number');
  if (!ATTACH_MODES.includes(s.attach_report)) problems.push(`attach_report must be one of ${ATTACH_MODES.join(', ')}`);
  const start = Number(s.work_day_start_hour);
  if (!Number.isInteger(start) || start < 0 || start > 23) problems.push('work_day_start_hour must be 0-23');
  const max = Number(s.max_reports_per_run);
  if (!Number.isInteger(max) || max < 0) problems.push('max_reports_per_run must be a whole number >= 0');
  if (problems.length) throw new Error(`Invalid Settings: ${problems.join('; ')}`);
  return {
    prefix: String(s.monitor_name_prefix ?? ''),
    thresholdC: Number(s.heat_threshold_c),
    attachMode: s.attach_report,
    startHour: start,
    maxReports: max,
  };
}

/** Enabled monitors whose name starts with the prefix; the rest of the name is the site name. */
function selectMonitors(monitors, prefix = '') {
  const watched = (monitors ?? [])
    .filter((m) => m.enabled && String(m.name ?? '').startsWith(prefix))
    .map((m) => ({
      monitor_id: m.monitor_id,
      site: String(m.name ?? '').slice(prefix.length).trim() || m.aoi_name || 'Site',
      aoi_name: m.aoi_name ?? null,
      timezone: m.timezone || 'UTC',
      rules: m.rules ?? [],
    }));
  // A typo in the prefix must not look like a quiet day: fail so the error workflow reports it.
  if (!watched.length) throw new Error(`No enabled FortyGuard monitors match the prefix "${prefix}"`);
  return watched;
}

/** Local wall-clock parts of an ISO instant in an IANA timezone (falls back to UTC). */
function localTime(iso, timeZone) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  const options = { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' };
  let zone = timeZone || 'UTC';
  let format;
  try {
    format = new Intl.DateTimeFormat('en-GB', { ...options, timeZone: zone });
  } catch {
    zone = 'UTC';
    format = new Intl.DateTimeFormat('en-GB', { ...options, timeZone: zone });
  }
  const p = Object.fromEntries(format.formatToParts(date).map((x) => [x.type, x.value]));
  return { date, timeZone: zone, hour: Number(p.hour), hhmm: `${p.hour}:${p.minute}`, day: `${p.weekday} ${p.day} ${p.month}` };
}

function describeRule(rule) {
  const where = WHERE[rule.spatial_aggregation] || `(${rule.spatial_aggregation})`;
  const unit = rule.unit === 'fahrenheit' ? 'F' : 'C';
  return `#${rule.rule_number ?? '?'}: ${rule.temporal_aggregation} temperature ${where} ${rule.operator} ${rule.threshold} °${unit}`;
}

/**
 * Alert payloads may number rules differently from the monitor, so match on rule_id and
 * describe the monitor's own rule.
 */
function breachedRuleText(breached, monitorRules) {
  return (breached ?? [])
    .map((r) => describeRule((monitorRules ?? []).find((m) => m.rule_id === r.rule_id) ?? r))
    .join('; ');
}

const hourLabel = (h) => `${String(h).padStart(2, '0')}:00`;

/** Plain next step for the crew lead. Never says "no change needed": humidity matters too. */
function actionLine({ heat, thresholdC, startHour, breach }) {
  if (!heat) {
    return `Below your ${thresholdC} °C alert threshold. Humidity can still raise the heat index, so check it and follow your heat plan.`;
  }
  if (breach.hour < startHour || breach.hour >= EVENING_HOUR) {
    return `Heat is expected at ${breach.hhmm}, outside normal working hours. Plan water, shade and rest breaks for any work then. Follow your heat plan.`;
  }
  const latest = breach.hour - RISK_WINDOW_HOURS;
  if (latest > startHour) {
    return `Consider moving heavy outdoor work to ${hourLabel(startHour)}–${hourLabel(latest)}, and plan water, shade and rest breaks around ${breach.hhmm}. Follow your heat plan.`;
  }
  return `The heat arrives early (${breach.hhmm}), so plan water, shade and rest breaks from the start of the shift. Follow your heat plan.`;
}

function alertMessage({ site, heat, temp, breach, aoiName, tileId, rule, action, reportReady, alertId }) {
  return [
    `${heat ? '🔥' : '🌤️'} <b>Heat Watch: ${escapeHtml(site)}</b>${heat ? ' <b>(heat alert)</b>' : ''}`,
    `Forecast: <b>${temp} °C</b> at ${breach.hhmm} local, ${breach.day} (${escapeHtml(breach.timeZone)})`,
    `Area: ${escapeHtml(aoiName || '-')}, tile ${escapeHtml(tileId ?? '-')}`,
    `Rule met: ${escapeHtml(rule || '-')}`,
    `Action: ${escapeHtml(action)}`,
    `Report: ${reportReady ? 'ready' : 'not ready'} · alert ${escapeHtml(String(alertId).slice(0, 8))}`,
  ].join('\n');
}

function setupMessage(monitorCount, skippedCount) {
  return [
    '✅ <b>Heat Watch is on</b>',
    `Watching ${monitorCount} monitor(s). ${skippedCount} existing alert(s) were marked as seen and won't be sent. New forecast alerts will arrive here.`,
  ].join('\n');
}

/**
 * Decide what to send this run.
 *   monitors: [{ monitor, alerts }] where monitor comes from selectMonitors().
 *   state:    the parsed data/seen-alerts.json ({ initialized, seen }).
 * First run: send one setup message and mark every existing alert as seen.
 * Later runs: one item per new, completed alert whose forecast time is still ahead.
 * Alerts already in the past are recorded as seen but not sent.
 */
function selectNewAlerts({ monitors, state, settings, now = new Date() }) {
  const cfg = validateSettings(settings);
  const firstRun = state?.initialized !== true;
  const seen = new Set(state?.seen ?? []);
  const sent = [];
  const skipped = [];
  for (const { monitor, alerts } of monitors) {
    for (const a of alerts ?? []) {
      if (a.status !== 'COMPLETED' || seen.has(a.alert_id)) continue;
      seen.add(a.alert_id);
      const breach = localTime(a.predicted_breach_at, monitor.timezone);
      // Number(null) is 0; a missing temperature must never become "0 °C".
      const raw = a.predicted_breach_temperature;
      const temp = raw === null || raw === undefined || raw === '' ? NaN : Math.round(Number(raw) * 10) / 10;
      if (firstRun || !breach || breach.date < now || !Number.isFinite(temp)) {
        skipped.push(a.alert_id);
        continue;
      }
      const heat = temp >= cfg.thresholdC;
      const rule = breachedRuleText(a.breached_rules, monitor.rules);
      const reportReady = Boolean(a.report?.ready);
      const action = actionLine({ heat, thresholdC: cfg.thresholdC, startHour: cfg.startHour, breach });
      sent.push({
        kind: 'alert',
        alert_id: a.alert_id,
        monitor_id: monitor.monitor_id,
        site: monitor.site,
        timezone: breach.timeZone,
        predicted_breach_utc: breach.date.toISOString(),
        predicted_breach_local: `${breach.day} ${breach.hhmm}`,
        predicted_temp_c: temp,
        heat,
        level: heat ? 'HEAT' : 'info',
        rule,
        message: alertMessage({
          site: monitor.site, heat, temp, breach, aoiName: monitor.aoi_name,
          tileId: a.predicted_breach_tile_id, rule, action, reportReady, alertId: a.alert_id,
        }),
        attach: reportReady && (cfg.attachMode === 'always' || (cfg.attachMode === 'heat_only' && heat)),
      });
    }
  }
  if (firstRun) {
    return [{ kind: 'setup', alert_id: null, attach: false, mark_seen: skipped, message: setupMessage(monitors.length, skipped.length) }];
  }
  if (sent.length) sent[0].mark_seen = skipped;
  return sent;
}

/** Alerts whose PDF report should be fetched, capped per run. */
function selectReports(items, settings) {
  const { maxReports } = validateSettings(settings);
  return (items ?? []).filter((i) => i.kind === 'alert' && i.attach).slice(0, maxReports);
}

/** New dedupe state: previous IDs plus everything handled this run, newest last, capped. */
function nextState(previous, items, now = new Date()) {
  const ids = [...(previous?.seen ?? [])];
  for (const i of items ?? []) {
    if (i.alert_id) ids.push(i.alert_id);
    ids.push(...(i.mark_seen ?? []));
  }
  return { initialized: true, seen: [...new Set(ids)].slice(-MAX_SEEN_IDS), updated_at: now.toISOString() };
}

/**
 * One CSV field. Text that a spreadsheet would treat as a formula (= + - @, tab, CR) is
 * prefixed with a quote (OWASP CSV injection guidance); numbers are left as numbers.
 */
function csvField(value) {
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  let text = String(value ?? '');
  if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  return `"${text.replace(/"/g, '""')}"`;
}

const LOG_COLUMNS = ['logged_utc', 'site', 'alert_id', 'predicted_breach_utc', 'timezone', 'predicted_temp_c', 'level', 'rule'];

function alertLogLine(item, loggedAt = new Date()) {
  const row = { ...item, logged_utc: loggedAt.toISOString() };
  return `${LOG_COLUMNS.map((c) => csvField(row[c])).join(',')}\n`;
}

/** Telegram text for the error workflow; tolerates trigger-level errors with no execution. */
function errorNotice(payload) {
  const execution = payload?.execution ?? {};
  const trigger = payload?.trigger ?? {};
  const message = (execution.error ?? trigger.error ?? {}).message ?? 'unknown error';
  const lines = [
    '⚠️ <b>Heat Watch run failed</b>',
    `Workflow: ${escapeHtml(payload?.workflow?.name ?? '-')}`,
    `Node: ${escapeHtml(execution.lastNodeExecuted ?? (trigger.error ? 'trigger' : '-'))}`,
    `Error: ${escapeHtml(String(message).slice(0, 300))}`,
  ];
  if (execution.url) lines.push(`Execution: ${escapeHtml(execution.url)}`);
  lines.push('New alerts may be delayed until the next successful run.');
  return lines.join('\n');
}

// @export-start (removed by the build; lets `node --test` require this file)
if (typeof module === 'object' && module.exports) {
  module.exports = {
    LOG_COLUMNS, actionLine, alertLogLine, alertMessage, breachedRuleText, csvField, describeRule,
    errorNotice, escapeHtml, localTime, nextState, parseMcpResponse, selectMonitors, selectNewAlerts,
    selectReports, setupMessage, validateSettings,
  };
}
// @export-end
