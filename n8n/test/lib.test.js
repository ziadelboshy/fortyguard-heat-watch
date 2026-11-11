// Unit tests for n8n/src/lib.js. Run: npm test
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const lib = require('../src/lib.js');
const { sse, monitor, alert, SETTINGS } = require('./fixtures.js');

test('escapeHtml escapes the three characters Telegram HTML cares about', () => {
  assert.equal(lib.escapeHtml('a < b & c > d'), 'a &lt; b &amp; c &gt; d');
  assert.equal(lib.escapeHtml(null), '');
});

test('parseMcpResponse unwraps the SSE line and the FortyGuard envelope', () => {
  assert.deepEqual(lib.parseMcpResponse(sse({ monitors: [] })), { monitors: [] });
});

test('parseMcpResponse fails loudly on every error shape', () => {
  assert.throws(() => lib.parseMcpResponse('event: message\n\n'), /no "data:" line/);
  assert.throws(() => lib.parseMcpResponse(`data: ${JSON.stringify({ error: { code: -32602, message: 'bad' } })}`), /MCP error -32602/);
  const toolError = { result: { isError: true, content: [{ type: 'text', text: 'boom' }] } };
  assert.throws(() => lib.parseMcpResponse(`data: ${JSON.stringify(toolError)}`), /MCP tool error: boom/);
  const notOk = { result: { content: [{ type: 'text', text: JSON.stringify({ ok: false, error: { code: 'X' } }) }] } };
  assert.throws(() => lib.parseMcpResponse(`data: ${JSON.stringify(notOk)}`), /FortyGuard error/);
});

test('validateSettings accepts the defaults and rejects bad values', () => {
  assert.equal(lib.validateSettings(SETTINGS).thresholdC, 35);
  assert.throws(() => lib.validateSettings({ ...SETTINGS, attach_report: 'sometimes' }), /attach_report/);
  assert.throws(() => lib.validateSettings({ ...SETTINGS, work_day_start_hour: 24 }), /work_day_start_hour/);
  assert.throws(() => lib.validateSettings({ ...SETTINGS, heat_threshold_c: 'hot' }), /heat_threshold_c/);
  assert.throws(() => lib.validateSettings({ ...SETTINGS, max_reports_per_run: -1 }), /max_reports_per_run/);
});

test('selectMonitors keeps enabled monitors with the prefix and derives the site name', () => {
  const raw = [
    { monitor_id: 'a', name: 'Heat Watch - Phoenix site', enabled: true, timezone: 'America/Phoenix' },
    { monitor_id: 'b', name: 'Heat Watch - Old yard', enabled: false },
    { monitor_id: 'c', name: 'Something else', enabled: true },
  ];
  const out = lib.selectMonitors(raw, 'Heat Watch - ');
  assert.deepEqual(out.map((m) => [m.monitor_id, m.site, m.timezone]), [['a', 'Phoenix site', 'America/Phoenix']]);
});

test('localTime converts to the monitor timezone and falls back to UTC', () => {
  const t = lib.localTime('2026-11-11T22:00:00Z', 'America/Phoenix');
  assert.deepEqual([t.hhmm, t.day, t.hour, t.timeZone], ['15:00', 'Wed 11 Nov', 15, 'America/Phoenix']);
  assert.equal(lib.localTime('2026-11-11T22:00:00Z', 'Not/AZone').timeZone, 'UTC');
  assert.equal(lib.localTime('not a date', 'UTC'), null);
});

test("breachedRuleText describes the monitor's rule, matched by rule_id", () => {
  const monitorRules = [
    { rule_id: 'r35', rule_number: 1, temporal_aggregation: 'maximum', spatial_aggregation: 'maximum', operator: 'above', threshold: 35, unit: 'celsius' },
    { rule_id: 'r15', rule_number: 2, temporal_aggregation: 'maximum', spatial_aggregation: 'maximum', operator: 'above', threshold: 15, unit: 'celsius' },
  ];
  // The alert renumbers the breached rule as #1; the monitor calls it #2.
  const text = lib.breachedRuleText([{ rule_id: 'r15', rule_number: 1 }], monitorRules);
  assert.equal(text, '#2: maximum temperature at the hottest tile above 15 °C');
});

test('actionLine never says "no change needed" and handles early heat', () => {
  const below = lib.actionLine({ heat: false, thresholdC: 35, startHour: 6, breach: { hour: 15, hhmm: '15:00' } });
  assert.match(below, /Humidity can still raise the heat index/);
  assert.doesNotMatch(below, /no .*change/i);
  const afternoon = lib.actionLine({ heat: true, thresholdC: 35, startHour: 6, breach: { hour: 15, hhmm: '15:00' } });
  assert.match(afternoon, /06:00–11:00/);
  const early = lib.actionLine({ heat: true, thresholdC: 35, startHour: 6, breach: { hour: 9, hhmm: '09:00' } });
  assert.match(early, /arrives early \(09:00\)/);
});

test('selectNewAlerts: the first run sends one setup message and replays nothing', () => {
  const out = lib.selectNewAlerts({ monitors: [{ monitor: monitor(), alerts: [alert('a1'), alert('a2')] }], state: {}, settings: SETTINGS });
  assert.equal(out.length, 1);
  assert.equal(out[0].kind, 'setup');
  assert.deepEqual(out[0].mark_seen, ['a1', 'a2']);
});

test('selectNewAlerts: later runs send new upcoming alerts and record past ones silently', () => {
  const state = { initialized: true, seen: ['old'] };
  const alerts = [
    alert('old'),
    alert('past', { hoursAhead: -2 }),
    alert('pending', { status: 'RUNNING' }),
    alert('hot', { temp: 38.04 }),
    alert('mild', { temp: 29.2, reportReady: false }),
  ];
  const out = lib.selectNewAlerts({ monitors: [{ monitor: monitor(), alerts }], state, settings: SETTINGS });
  assert.deepEqual(out.map((i) => [i.alert_id, i.level, i.attach]), [['hot', 'HEAT', true], ['mild', 'info', false]]);
  assert.deepEqual(out[0].mark_seen, ['past']);
  assert.equal(out[0].predicted_temp_c, 38);
  assert.match(out[0].message, /🔥 <b>Heat Watch: Phoenix site<\/b> <b>\(heat alert\)<\/b>/);
});

test('selectNewAlerts sends an alert once even if two monitors return it', () => {
  const shared = alert('dup');
  const out = lib.selectNewAlerts({
    monitors: [{ monitor: monitor(), alerts: [shared] }, { monitor: monitor({ monitor_id: 'm2' }), alerts: [shared] }],
    state: { initialized: true, seen: [] },
    settings: SETTINGS,
  });
  assert.equal(out.length, 1);
});

test('selectNewAlerts escapes monitor text in the Telegram message', () => {
  const out = lib.selectNewAlerts({
    monitors: [{ monitor: monitor({ site: '<b>Yard</b> & co' }), alerts: [alert('x')] }],
    state: { initialized: true, seen: [] },
    settings: SETTINGS,
  });
  assert.match(out[0].message, /Heat Watch: &lt;b&gt;Yard&lt;\/b&gt; &amp; co/);
});

test('selectReports honours attach flags and the per-run cap', () => {
  const items = [{ kind: 'alert', attach: true }, { kind: 'alert', attach: true }, { kind: 'alert', attach: false }, { kind: 'alert', attach: true }];
  assert.equal(lib.selectReports(items, SETTINGS).length, 2);
  assert.equal(lib.selectReports(items, { ...SETTINGS, max_reports_per_run: 0 }).length, 0);
});

test('nextState merges, de-duplicates and caps the seen list', () => {
  const now = new Date('2026-11-11T00:00:00Z');
  const state = lib.nextState({ seen: ['a', 'b'] }, [{ alert_id: 'b', mark_seen: ['c'] }, { alert_id: null, mark_seen: ['d'] }], now);
  assert.deepEqual(state, { initialized: true, seen: ['a', 'b', 'c', 'd'], updated_at: '2026-11-11T00:00:00.000Z' });
  const many = Array.from({ length: 1200 }, (_, i) => `id${i}`);
  assert.equal(lib.nextState({ seen: many }, []).seen.length, 1000);
});

test('csvField quotes text, keeps numbers, and neutralises spreadsheet formulas', () => {
  assert.equal(lib.csvField('He said "hi"'), '"He said ""hi"""');
  assert.equal(lib.csvField(-3.5), '-3.5');
  assert.equal(lib.csvField('=HYPERLINK("x")'), '"\'=HYPERLINK(""x"")"');
  assert.equal(lib.csvField('@SUM(A1)'), '"\'@SUM(A1)"');
});

test('alertLogLine writes the documented columns in order', () => {
  const line = lib.alertLogLine(
    { site: 'Phoenix', alert_id: 'a1', predicted_breach_utc: '2026-11-11T22:00:00.000Z', timezone: 'America/Phoenix', predicted_temp_c: 29.2, level: 'info', rule: '#2' },
    new Date('2026-11-11T10:00:00Z'),
  );
  assert.deepEqual(lib.LOG_COLUMNS, ['logged_utc', 'site', 'alert_id', 'predicted_breach_utc', 'timezone', 'predicted_temp_c', 'level', 'rule']);
  assert.equal(line, '"2026-11-11T10:00:00.000Z","Phoenix","a1","2026-11-11T22:00:00.000Z","America/Phoenix",29.2,"info","#2"\n');
});

test('errorNotice handles normal and trigger-level errors and escapes them', () => {
  const normal = lib.errorNotice({
    workflow: { name: 'Heat Watch -> Telegram' },
    execution: { lastNodeExecuted: 'MCP: list monitors (free)', error: { message: '404 <not found>' }, url: 'http://localhost:5678/x' },
  });
  assert.match(normal, /Workflow: Heat Watch -&gt; Telegram/);
  assert.match(normal, /Error: 404 &lt;not found&gt;/);
  assert.match(normal, /Execution: http:\/\/localhost:5678\/x/);
  const trigger = lib.errorNotice({ workflow: { name: 'W' }, trigger: { error: { message: 'cron broke' } } });
  assert.match(trigger, /Node: trigger/);
  assert.match(trigger, /Error: cron broke/);
});

test('parseMcpResponse also accepts plain JSON and multi-line SSE data', () => {
  const rpc = { jsonrpc: '2.0', id: 1, result: { content: [{ type: 'text', text: JSON.stringify({ ok: true, result: { value: 7 } }) }] } };
  assert.equal(lib.parseMcpResponse(JSON.stringify(rpc)), 7);
  // Per the SSE spec, data lines are joined with newlines, so a server splits between JSON tokens.
  const wire = JSON.stringify(rpc);
  const cut = wire.indexOf(',') + 1;
  const [head, tail] = [wire.slice(0, cut), wire.slice(cut)];
  assert.equal(lib.parseMcpResponse(`event: message\ndata: ${head}\ndata: ${tail}\n\n`), 7);
  const noResult = { result: { content: [{ type: 'text', text: JSON.stringify({ ok: true }) }] } };
  assert.throws(() => lib.parseMcpResponse(JSON.stringify(noResult)), /no result value/);
});

test('validateSettings rejects blank numeric settings instead of reading them as 0', () => {
  assert.throws(() => lib.validateSettings({ ...SETTINGS, heat_threshold_c: '' }), /heat_threshold_c is empty/);
  assert.throws(() => lib.validateSettings({ ...SETTINGS, max_reports_per_run: ' ' }), /max_reports_per_run is empty/);
});

test('selectMonitors fails loudly when nothing matches the prefix', () => {
  assert.throws(() => lib.selectMonitors([{ monitor_id: 'a', name: 'Heat Watch – typo', enabled: true }], 'Heat Watch - '), /No enabled FortyGuard monitors/);
});

test('selectNewAlerts never reports a missing temperature as 0 °C', () => {
  const missing = { ...alert('n'), predicted_breach_temperature: null };
  const out = lib.selectNewAlerts({ monitors: [{ monitor: monitor(), alerts: [missing, alert('ok')] }], state: { initialized: true, seen: [] }, settings: SETTINGS });
  assert.deepEqual(out.map((i) => i.alert_id), ['ok']);
  assert.deepEqual(out[0].mark_seen, ['n']);
});

test('actionLine handles heat outside working hours', () => {
  const late = lib.actionLine({ heat: true, thresholdC: 35, startHour: 6, breach: { hour: 23, hhmm: '23:00' } });
  assert.match(late, /outside normal working hours/);
  const night = lib.actionLine({ heat: true, thresholdC: 35, startHour: 6, breach: { hour: 3, hhmm: '03:00' } });
  assert.match(night, /outside normal working hours/);
});

test('scripts/start.sh writes the same CSV header as LOG_COLUMNS', () => {
  const script = fs.readFileSync(path.join(__dirname, '..', '..', 'scripts', 'start.sh'), 'utf8');
  assert.ok(script.includes(lib.LOG_COLUMNS.join(',')), 'start.sh header matches LOG_COLUMNS');
});
