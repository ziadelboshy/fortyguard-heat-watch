// Code node "Reports to attach": alerts whose PDF report should be fetched (capped per run).
const items = $('New alerts').all().map((i) => i.json);
return selectReports(items, $('Settings').first().json).map((json) => ({ json }));
