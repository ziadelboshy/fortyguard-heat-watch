// Code node "Build notice" (error workflow): Telegram text describing the failed run.
return [{ json: { text: errorNotice($input.first().json) } }];
