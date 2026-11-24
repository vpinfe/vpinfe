import json
import urllib.error
import urllib.request

from common.extensions.contract import Unavailable


def register(ctx):
    ctx.ui.settings([
        {"key": "site", "type": "string", "default": "http://127.0.0.1:8790"},
        {"key": "key", "type": "secret"},
    ])

    def rows():
        request = urllib.request.Request(ctx.config.get("site") + "/top",
                                         headers={"X-API-Key": ctx.config.get("key")})
        try:
            with urllib.request.urlopen(request, timeout=10) as answer:
                return json.loads(answer.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise Unavailable(ctx.t("error.refused")) from exc
            raise Unavailable(ctx.why(exc, at=request.full_url)) from exc
        except OSError as exc:
            raise Unavailable(ctx.why(exc, at=request.full_url)) from exc

    ctx.ui.list("top", rows, area="community", columns=[
        {"field": "player"},
        {"field": "table"},
        {"field": "score", "kind": "number"},
    ])
