WEEK = {
    "mm": {"name": "Medieval Madness", "week": 41, "status": "open",
           "tags": ["Williams", "Classic"], "page": "https://scores.example.com/weeks/41",
           "note": "", "remind": False, "level": "normal", "goal": 2},
    "afm": {"name": "Attack from Mars", "week": 40, "status": "closed",
            "tags": ["Bally"], "page": "https://scores.example.com/weeks/40",
            "note": "Beat the saucer first", "remind": False, "level": "high", "goal": 5},
}

TIERS = {"open": "on", "closed": "off"}


def register(ctx):
    def rows():
        return [{"id": key, "name": one["name"]} for key, one in WEEK.items()]

    def detail(row_id):
        one = WEEK.get(row_id)
        if one is None:
            return None
        return {
            "title": one["name"],
            "under": ctx.t("pane.under", week=one["week"]),
            "sections": [
                {"key": "about", "label": ctx.t("pane.about"), "facts": [
                    {"label": ctx.t("pane.status"), "kind": "state",
                     "value": ctx.t(f"pane.status.{one['status']}"),
                     "tier": TIERS[one["status"]]},
                    {"label": ctx.t("pane.tags"), "kind": "chips", "value": one["tags"]},
                    {"label": ctx.t("pane.rules"), "kind": "link",
                     "value": ctx.t("pane.rules.open"), "to": one["page"]},
                ]},
                {"key": "mine", "label": ctx.t("pane.mine"), "fields": [
                    {"key": "note", "type": "string", "label": ctx.t("pane.note"),
                     "value": one["note"]},
                    {"key": "remind", "type": "switch", "label": ctx.t("pane.remind"),
                     "value": one["remind"], "help": ctx.t("pane.remind.help")},
                    {"key": "level", "type": "select", "label": ctx.t("pane.level"),
                     "value": one["level"],
                     "choices": [["normal", ctx.t("pane.level.normal")],
                                 ["high", ctx.t("pane.level.high")]]},
                    {"key": "goal", "type": "number", "label": ctx.t("pane.goal"),
                     "value": one["goal"], "min": 0, "max": 10},
                ]},
            ],
        }

    def save(row_id, values):
        WEEK[row_id].update(values)

    ctx.ui.list("board", rows, area="community", columns=[{"field": "name"}],
                detail=detail, save=save)
