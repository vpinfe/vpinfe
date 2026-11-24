class Hello:
    def __init__(self, ctx):
        self.ctx = ctx

    def ask(self, step):
        return {"fields": [{"key": "who", "label": self.ctx.t("dialog.who"),
                            "type": "string"}]}

    def run(self, values):
        return self.ctx.t("dialog.done", who=values.get("who") or "world")


def register(ctx):
    ctx.ui.action("hello", Hello(ctx))
