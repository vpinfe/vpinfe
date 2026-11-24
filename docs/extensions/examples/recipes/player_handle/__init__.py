def register(ctx):
    def read(player_id):
        held = ctx.players.account(player_id)
        return {"fields": [
            {"key": "handle", "type": "string", "label": ctx.t("account.handle"),
             "value": held.get("handle", "")},
            {"key": "api_key", "type": "secret", "label": ctx.t("account.key"),
             "set": bool(held.get("api_key"))}]}

    def write(player_id, values):
        held = dict(ctx.players.account(player_id))
        for key, value in values.items():
            if value is None:
                held.pop(key, None)
            else:
                held[key] = value
        ctx.players.set_account(player_id, held)

    ctx.ui.account(read=read, write=write)
