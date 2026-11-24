import json
import urllib.error
import urllib.request

from common.extensions.contract import Unavailable


class PostScore:
    def __init__(self, ctx):
        self.ctx = ctx

    def _send(self, key, path, body=None):
        request = urllib.request.Request(
            self.ctx.config.get("site") + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"X-API-Key": key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10):
                return True
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                return False
            raise Unavailable(self.ctx.why(exc, at=request.full_url)) from exc
        except OSError as exc:
            raise Unavailable(self.ctx.why(exc, at=request.full_url)) from exc

    def ask(self, step):
        if step == "score":
            return {"step": "score", "title": self.ctx.t("dialog.score.title"),
                    "fields": [
                        {"key": "player", "label": self.ctx.t("dialog.player"),
                         "type": "string"},
                        {"key": "table", "label": self.ctx.t("dialog.table"),
                         "type": "string"},
                        {"key": "score", "label": self.ctx.t("dialog.points"),
                         "type": "number"}]}
        return {"step": "key", "title": self.ctx.t("dialog.key.title"),
                "help": self.ctx.t("dialog.key.help"),
                "fields": [{"key": "key", "label": self.ctx.t("dialog.key"),
                            "type": "secret"}]}

    def check(self, values, step):
        if step == "key":
            if not self._send(values.get("key", ""), "/check"):
                return {**self.ask("key"),
                        "errors": {"key": self.ctx.t("dialog.key.refused")}}
            return self.ask("score")
        return {"step": "sure", "title": self.ctx.t("dialog.sure.title"),
                "confirm": self.ctx.t("dialog.sure.confirm"),
                "summary": [[self.ctx.t("dialog.player"), values["player"]],
                            [self.ctx.t("dialog.table"), values["table"]],
                            [self.ctx.t("dialog.points"), f"{int(values['score']):,}"]]}

    def run(self, values):
        self._send(values["key"], "/scores", {
            "player": values["player"], "table": values["table"],
            "score": int(values["score"])})
        return self.ctx.t("dialog.posted", table=values["table"])


def register(ctx):
    ctx.ui.settings([{"key": "site", "type": "string",
                      "default": "http://127.0.0.1:8790"}])
    ctx.ui.action("post", PostScore(ctx))
