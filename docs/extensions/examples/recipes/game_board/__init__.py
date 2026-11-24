PICKS = [
    {"vps_id": "vps-medieval", "name": "Medieval Madness", "votes": 41},
    {"vps_id": "vps-mars", "name": "Attack from Mars", "votes": 33},
    {"vps_id": "vps-homebrew", "name": "A table nobody catalogued", "votes": 2},
]


def register(ctx):
    ctx.ui.list("picks", lambda: PICKS, area="community", columns=[
        {"field": "vps_id", "kind": "game", "name_from": "name"},
        {"field": "votes", "kind": "number"},
    ])
