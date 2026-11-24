def register(ctx):
    def game_group(who):
        return {
            "facts": [{"label": ctx.t("group.about"), "value": who["name"]}],
            "fields": [{"key": "note", "type": "string", "label": ctx.t("group.note"),
                        "value": ctx.config.get(f"note.{who['game_id']}")}],
        }

    def save_game(who, values):
        ctx.config.set(f"note.{who['game_id']}", values["note"] or "")

    def table_group(who):
        return {"facts": [
            {"label": ctx.t("group.version"), "value": who["version"] or "-"},
            {"label": ctx.t("group.file"), "value": who["vps_file_id"] or "-"},
        ]}

    ctx.ui.panel_group("game", game_group, section="play", save=save_game)
    ctx.ui.panel_group("table", table_group, section="play")
