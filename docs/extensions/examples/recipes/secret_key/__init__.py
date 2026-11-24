class Connect:
    def __init__(self, ctx):
        self.ctx = ctx

    def ask(self, step):
        return {"fields": [{"key": "key", "label": self.ctx.t("dialog.key"),
                            "type": "secret",
                            "set": bool(self.ctx.config.get("key"))}]}

    def run(self, values):
        typed = values.get("key")
        if typed:
            self.ctx.config.set("key", typed)
            return self.ctx.t("dialog.saved")
        return self.ctx.t("dialog.kept")


def register(ctx):
    ctx.ui.settings([{"key": "key", "type": "secret"}])
    ctx.ui.action("connect", Connect(ctx))
