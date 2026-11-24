def register(ctx):
    def flagged():
        return set((ctx.config.get("flagged") or "").split(",")) - {""}

    def keep(ids):
        ctx.config.set("flagged", ",".join(sorted(ids)))

    def flag(who):
        keep(flagged() | {one["game_id"] for one in who})
        return ctx.t("message.flagged", count=len(who))

    def unflag(who):
        keep(flagged() - {one["game_id"] for one in who})
        return ctx.t("message.unflagged", count=len(who))

    def version(who):
        return ctx.t("message.version", version=who[0]["version"] or "-")

    ctx.ui.verb("game", "flag", flag)
    ctx.ui.verb("game", "unflag", unflag, danger=True)
    ctx.ui.verb("table", "version", version, one=True)
