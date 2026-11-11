// Code node "New alerts": one item per alert to send (or one setup item on the first run).
const settings = $('Settings').first().json;
const state = $('Extract seen').first().json.data ?? {};
const monitors = $input.all().map((item, i) => ({
  monitor: $('Monitors').itemMatching(i).json,
  alerts: parseMcpResponse(item.json.sse).alerts ?? [],
}));
return selectNewAlerts({ monitors, state, settings }).map((json) => ({ json }));
