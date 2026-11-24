def register(ctx):
    ctx.ui.settings([{"key": "greeting", "type": "string", "default": "Hello"}])
    ctx.logger.info("%s", ctx.config.get("greeting"))
