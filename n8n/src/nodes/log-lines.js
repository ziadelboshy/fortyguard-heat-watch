// Code node "Log lines": one CSV line per sent alert for data/alerts-log.csv.
const now = new Date();
return $('New alerts').all()
  .map((i) => i.json)
  .filter((item) => item.kind === 'alert')
  .map((item) => ({ json: { line: alertLogLine(item, now) } }));
