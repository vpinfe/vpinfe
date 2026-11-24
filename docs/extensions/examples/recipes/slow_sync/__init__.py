import time

STEPS = 20


class Sync:
    def __init__(self, ctx):
        self.ctx = ctx

    def ask(self, step):
        return {"fields": [], "summary": [[self.ctx.t("dialog.steps"), str(STEPS)]],
                "confirm": self.ctx.t("dialog.start")}

    def run(self, values):
        def work(job):
            for done in range(1, STEPS + 1):
                if job.halt.is_set():
                    job.stopped = True
                    return {"done": done - 1}
                time.sleep(0.5)
                job.progress(done, STEPS, self.ctx.t("job.step", done=done, total=STEPS))
            return {"done": STEPS}

        job = self.ctx.jobs.submit("sync", work, stoppable=True)
        return {"job_id": job.id}


def register(ctx):
    ctx.ui.action("sync", Sync(ctx))
