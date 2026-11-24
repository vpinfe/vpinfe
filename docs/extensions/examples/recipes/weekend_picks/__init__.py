def register(ctx):
    ctx.ui.settings([{"key": "collection", "type": "string", "default": "Weekend Picks"}])

    def exists(name):
        listed = ctx.collections.list_collections()["collections"]
        return any(one["name"] == name for one in listed)

    def holds(name):
        return ctx.t("message.holds", name=name,
                     count=len(ctx.collections.collection_members(name)["members"]))

    def pick(who):
        name = ctx.config.get("collection")
        if not exists(name):
            ctx.collections.create_collection(name, description=ctx.t("collection.about"))
        for one in who:
            ctx.collections.add_to_collection(name, one["game_id"])
        return holds(name)

    def unpick(who):
        name = ctx.config.get("collection")
        if not exists(name):
            return ctx.t("message.none", name=name)
        named = {one["game"] for one in ctx.collections.collection_members(name)["members"]
                 if one["origin"] == "named"}
        for one in who:
            if one["game_id"] in named:
                ctx.collections.remove_from_collection(name, one["game_id"])
        return holds(name)

    ctx.ui.verb("game", "pick", pick)
    ctx.ui.verb("game", "unpick", unpick)
