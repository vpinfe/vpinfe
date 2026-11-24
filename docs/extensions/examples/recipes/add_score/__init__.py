import json
import urllib.error
import urllib.request

from common.extensions.contract import Unavailable


def register(ctx):
    ctx.ui.settings([
        {"key": "site", "type": "string", "default": "http://127.0.0.1:8790"},
        {"key": "key", "type": "secret"},
    ])

    def call(path, body=None):
        request = urllib.request.Request(
            ctx.config.get("site") + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"X-API-Key": ctx.config.get("key"), "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as answer:
                return json.loads(answer.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise Unavailable(ctx.t("error.refused")) from exc
            raise Unavailable(ctx.why(exc, at=request.full_url)) from exc
        except OSError as exc:
            raise Unavailable(ctx.why(exc, at=request.full_url)) from exc

    class Post:
        def ask(self, step):
            return {"step": "score", "title": ctx.t("dialog.post.title"), "fields": [
                {"key": "player", "label": ctx.t("dialog.player"), "type": "string"},
                {"key": "table", "label": ctx.t("dialog.table"), "type": "string"},
                {"key": "score", "label": ctx.t("dialog.points"), "type": "number"}]}

        def run(self, values):
            call("/scores", {"player": values["player"], "table": values["table"],
                             "score": int(values["score"])})
            return ctx.t("dialog.posted", table=values["table"])

    ctx.ui.action("post", Post())
    ctx.ui.list("scores", lambda: call("/top"), area="community", columns=[
        {"field": "player"},
        {"field": "table"},
        {"field": "score", "kind": "number"},
    ], add="post")
