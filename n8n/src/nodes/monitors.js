// Code node "Monitors": the enabled monitors this workflow should watch.
const { prefix } = validateSettings($('Settings').first().json);
const { monitors } = parseMcpResponse($input.first().json.sse);
return selectMonitors(monitors, prefix).map((json) => ({ json }));
