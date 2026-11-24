import json
import urllib.error
import urllib.request

from common.extensions.contract import Unavailable


def register(ctx):
    ctx.logger.info("%s", ctx.t("log.started"))

    def check(values):
        if not values["site"].startswith(("http://", "https://")):
            raise ValueError(ctx.t("error.address"))

    ctx.ui.settings([
        {"key": "site", "type": "string", "default": "http://127.0.0.1:8790"},
        {"key": "key", "type": "secret"},
    ], check=check)

    def call(path, method="GET", body=None):
        request = urllib.request.Request(
            ctx.config.get("site").rstrip("/") + path, method=method,
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

    def rows():
        return call("/top")

    columns = [
        {"field": "player"},
        {"field": "table"},
        {"field": "score", "kind": "number"},
    ]

    ctx.ui.list("scores", rows, area="community", columns=columns)
