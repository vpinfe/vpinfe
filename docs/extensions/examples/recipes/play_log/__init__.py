def register(ctx):
    def played(game_id="", seconds=0, private=False, **_rest):
        if private:
            return
        ctx.logger.info("%s", ctx.t("log.played", game=game_id, count=round(seconds / 60)))

    ctx.events.subscribe("table.play_recorded", played)
