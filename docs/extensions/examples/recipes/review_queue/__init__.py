import json
import urllib.error
import urllib.request

from common.extensions.contract import Unavailable


def register(ctx):
    ctx.ui.settings([
        {"key": "site", "type": "string", "default": "http://127.0.0.1:8790"},
        {"key": "key", "type": "secret"},
    ])

    def scores():
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

    def flagged():
        return [one for one in scores() if one["flagged"]]

    def waiting():
        return [{"kind": "to_review", "ids": [str(one["id"]) for one in flagged()]}]

    ctx.ui.list("queue", flagged, area="community", columns=[
        {"field": "player"},
        {"field": "table"},
        {"field": "score", "kind": "number"},
    ])
    ctx.ui.needs({"to_review": {"list": "queue", "tone": "danger"}}, waiting)
