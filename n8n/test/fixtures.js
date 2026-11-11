// Synthetic test data in the shapes FortyGuard's MCP returns. No real IDs or account data.
'use strict';

const SETTINGS = {
  telegram_chat_id: '123',
  monitor_name_prefix: 'Heat Watch - ',
  heat_threshold_c: 35,
  attach_report: 'heat_only',
  max_reports_per_run: 2,
  work_day_start_hour: 6,
};

/** One MCP tools/call answer as Streamable HTTP sends it: a single SSE data line. */
function sse(value) {
  const text = JSON.stringify({ ok: true, result: { value } });
  return `event: message\ndata: ${JSON.stringify({ jsonrpc: '2.0', id: 1, result: { content: [{ type: 'text', text }] } })}\n\n`;
}

function monitor(overrides = {}) {
  return {
    monitor_id: 'm1',
    site: 'Phoenix site',
    aoi_name: 'Warehouse District',
    timezone: 'America/Phoenix',
    rules: [
      { rule_id: 'r35', rule_number: 1, temporal_aggregation: 'maximum', spatial_aggregation: 'maximum', operator: 'above', threshold: 35, unit: 'celsius' },
      { rule_id: 'r15', rule_number: 2, temporal_aggregation: 'maximum', spatial_aggregation: 'maximum', operator: 'above', threshold: 15, unit: 'celsius' },
    ],
    ...overrides,
  };
}

function alert(id, { hoursAhead = 6, temp = 29.2, status = 'COMPLETED', reportReady = true } = {}) {
  return {
    alert_id: id,
    status,
    predicted_breach_at: new Date(Date.now() + hoursAhead * 3600e3).toISOString(),
    predicted_breach_temperature: temp,
    predicted_breach_tile_id: 44,
    breached_rules: [{ rule_id: temp >= 35 ? 'r35' : 'r15', rule_number: 1 }],
    report: { ready: reportReady, viewable: reportReady },
  };
}

/** The monitors list as list_monitors returns it (before selectMonitors). */
function rawMonitors() {
  const m = monitor();
  return [
    { monitor_id: m.monitor_id, name: `Heat Watch - ${m.site}`, enabled: true, aoi_name: m.aoi_name, timezone: m.timezone, rules: m.rules },
    { monitor_id: 'other', name: 'Not watched', enabled: true, timezone: 'UTC', rules: [] },
  ];
}

module.exports = { SETTINGS, sse, monitor, alert, rawMonitors };
