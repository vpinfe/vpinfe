"""A fixture extension whose Community lists derive tags and rank, and nothing real behind
them.

Kept in its own root so the suites that load every extension under `fixtures/extensions`
do not grow lists they never asked for. Its settings say which ids each list holds,
comma-separated, and whether a read fails, so a test sets up the week it wants. The two
ranked lists hold `id=rating` pairs, a rating left empty for one nobody rated.

The Ratings list also says how it stands: `status` is its line and `status_to` where the
line sends somebody, and while `waiting` holds anything its menu offers Post Now before a
link to the week. Posting empties `waiting`. The labels of those two acts are in the
fixture's own `i18n/en.json`.
"""

from __future__ import annotations


def columns(kind: str) -> list[dict]:
    return [{"field": "vps_id", "header": "VPS", "kind": kind, "name_from": "name"},
            {"field": "name", "header": "Table"}]


RATING = {"field": "rating", "header": "Rating", "kind": "number"}
TOP = [{"key": "top", "name": "Top Rated", "ranks": True,
        "sort": [{"field": "rating", "desc": True}]}]
WEEK = "https://challenge.example/week"


def register(ctx) -> None:
    def said(setting: str) -> list[str]:
        if ctx.config.get("fail"):
            raise RuntimeError("the fixture was asked to fail")
        return [one.strip() for one in ctx.config.get(setting).split(",") if one.strip()]

    def rows(setting: str):
        return lambda: [{"name": one, "vps_id": one} for one in said(setting)]

    def rated(setting: str):
        def read() -> list[dict]:
            pairs = [one.partition("=") for one in said(setting)]
            return [{"name": held, "vps_id": held,
                     "rating": float(rating) if rating else None}
                    for held, _, rating in pairs]
        return read

    def line():
        text = ctx.config.get("status")
        return {"text": text, "to": ctx.config.get("status_to")} if text else ""

    def post() -> dict:
        posted = len(ctx.config.get("waiting").split(","))
        ctx.config.set("waiting", "")
        return {"message": f"Posted {posted}"}

    def acts() -> dict:
        offered = {"post": post} if ctx.config.get("waiting") else {}
        return {**offered, "week": WEEK}

    ctx.ui.list("machines", rows("machines"), title="Machines of the Month", area="community",
                columns=columns("game"), tag="Machine of the Month")
    ctx.ui.list("releases", rows("releases"), title="Weekly Challenge", area="community",
                columns=columns("table"), tag="Weekly Challenge")
    ctx.ui.list("ratings", rated("ratings"), title="Ratings", area="community",
                columns=[*columns("game"), RATING], views=TOP, status=line, acts=acts)
    ctx.ui.list("builds", rated("builds"), title="Build Ratings", area="community",
                columns=[*columns("table"), RATING], views=TOP)
