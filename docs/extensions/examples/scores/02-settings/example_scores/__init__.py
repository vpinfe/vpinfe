def register(ctx):
    ctx.logger.info("%s", ctx.t("log.started"))

    def check(values):
        if not values["site"].startswith(("http://", "https://")):
            raise ValueError(ctx.t("error.address"))

    ctx.ui.settings([
        {"key": "site", "type": "string", "default": "http://127.0.0.1:8790"},
        {"key": "key", "type": "secret"},
    ], check=check)
