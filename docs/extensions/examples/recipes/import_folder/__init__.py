class ImportFolder:
    def __init__(self, ctx):
        self.ctx = ctx

    def folder(self):
        folder = self.ctx.config.get("folder")
        self.ctx.files.set_roots([folder])
        return folder

    def ask(self, step):
        folder = self.folder()
        coming, notes = [], []
        if folder:
            plan = self.ctx.uploads.plan(folder, allow_new_game=True)
            coming = [[item["label"], item["destination"]] for item in plan["items"]]
            notes = [one["reason"] for one in plan["blocked"]]
        return {"summary": coming, "notes": notes, "ready": bool(coming),
                "reason": self.ctx.t("dialog.nothing" if folder else "dialog.no_folder"),
                "confirm": self.ctx.t("dialog.confirm")}

    def run(self, values):
        folder = self.folder()

        def work(job):
            report = self.ctx.uploads.execute(folder, allow_new_game=True)
            job.problems = len(report["skipped"])
            return {"game_dir": report["game_dir"], "imported": report["imported"]}

        job = self.ctx.jobs.submit("import_folder", work, keep_result=True)
        return {"job_id": job.id}


def register(ctx):
    ctx.ui.settings([{"key": "folder", "type": "path", "wants": "dir"}])
    ctx.ui.action("import_folder", ImportFolder(ctx))
