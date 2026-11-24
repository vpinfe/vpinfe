import os

from common.extensions.contract import PROTECTED

REBOOT_FILE = "/run/reboot-required"


class Updates:
    def __init__(self, ctx):
        self.ctx = ctx
        self.upgraded = False

    def says(self, method):
        return next(one["says"] for one in self.ctx.os.methods if one["name"] == method)

    def ask(self, step):
        if not self.ctx.os.available:
            return {"ready": False, "reason": self.ctx.t("dialog.no_helper"),
                    "confirm": self.ctx.t("dialog.install")}
        waiting = self.ctx.os.call("updates.check")
        count = waiting.get("count", 0) if isinstance(waiting, dict) else 0
        return {"summary": [[self.ctx.t("dialog.waiting"), str(count)],
                            [self.ctx.t("dialog.will"), self.says("updates.apply")]],
                "confirm": self.ctx.t("dialog.install")}

    def run(self, values):
        def install(job):
            done = self.ctx.os.call("updates.apply")
            if isinstance(done, dict) and done.get("vpinfe_upgraded") is True:
                self.upgraded = True
            return done

        job = self.ctx.jobs.submit("install", install)
        return {"job_id": job.id}


class Restart:
    def __init__(self, ctx, updates):
        self.ctx = ctx
        self.updates = updates

    def ask(self, step):
        wording = "dialog.reboot" if os.path.exists(REBOOT_FILE) else "dialog.restart"
        return {"summary": [[self.ctx.t("dialog.will"), self.ctx.t(wording)]],
                "confirm": self.ctx.t("dialog.restart_now")}

    def run(self, values):
        if os.path.exists(REBOOT_FILE):
            self.ctx.system.reboot("system updates")
        else:
            self.ctx.system.restart_vpinfe("system updates")
        self.updates.upgraded = False
        return self.ctx.t("dialog.restarting")


def register(ctx):
    updates = Updates(ctx)
    ctx.ui.action("updates", updates, tier=PROTECTED)
    ctx.ui.action("restart", Restart(ctx, updates), tier=PROTECTED)

    def waiting():
        if os.path.exists(REBOOT_FILE) or updates.upgraded:
            return [{"kind": "restart", "count": 1,
                     "token": "reboot" if os.path.exists(REBOOT_FILE) else "vpinfe"}]
        return []

    ctx.ui.needs({"restart": {"action": "restart", "tone": "warm"}}, waiting)
