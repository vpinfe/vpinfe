MACHINE = {"boot": "play", "checked": "never"}


class Check:
    def __init__(self, ctx):
        self.ctx = ctx

    def ask(self, step):
        return {"summary": [[self.ctx.t("section.state.checked"), MACHINE["checked"]]],
                "confirm": self.ctx.t("action.check.confirm")}

    def run(self, values):
        MACHINE["checked"] = self.ctx.t("section.state.checked.now")
        return self.ctx.t("section.state.checked.said")


def register(ctx):
    def read_state():
        return {"facts": [{"label": ctx.t("section.state.checked"),
                           "value": MACHINE["checked"]}]}

    def read_boot():
        return {"fields": [
            {"key": "boot", "type": "select", "label": ctx.t("section.boot.mode"),
             "value": MACHINE["boot"],
             "choices": [["play", ctx.t("section.boot.play")],
                         ["desk", ctx.t("section.boot.desk")]]},
        ]}

    def save(values):
        MACHINE.update(values)

    ctx.ui.section("state", read_state)
    ctx.ui.section("boot", read_boot, save=save)
    ctx.ui.action("check", Check(ctx), section="state")
    ctx.ui.panel(area="system")
