from common.extensions.contract import PLAY


class Clear:
    def __init__(self, ctx):
        self.ctx = ctx

    def ask(self, step):
        return {"summary": [[self.ctx.t("section.note.field"), self.ctx.config.get("note") or ""]],
                "confirm": self.ctx.t("action.clear.confirm")}

    def run(self, values):
        self.ctx.config.set("note", "")
        return self.ctx.t("section.note.cleared")


def register(ctx):
    def read():
        return {"fields": [
            {"key": "note", "type": "string", "label": ctx.t("section.note.field"),
             "value": ctx.config.get("note") or ""},
        ]}

    def save(values):
        ctx.config.set("note", values["note"])

    ctx.ui.section("note", read, save=save, tier=PLAY)
    ctx.ui.action("clear", Clear(ctx), section="note", tier=PLAY)
    ctx.ui.panel(area="community")
