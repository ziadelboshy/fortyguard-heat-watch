// Code node "Build state": the new data/seen-alerts.json content.
const previous = $('Extract seen').first().json.data ?? {};
const items = $('New alerts').all().map((i) => i.json);
return [{ json: nextState(previous, items) }];
