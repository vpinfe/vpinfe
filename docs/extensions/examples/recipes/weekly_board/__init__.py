CHALLENGES = [
    {"name": "Medieval Madness", "status": "open", "tags": ["Williams", "Classic"],
     "page": "https://scores.example.com/weeks/41", "art": "art/flipper.png"},
    {"name": "Attack from Mars", "status": "closing", "tags": ["Bally"],
     "page": "https://scores.example.com/weeks/40", "art": "art/flipper.png"},
    {"name": "The Addams Family", "status": "closed", "tags": [],
     "page": "https://scores.example.com/weeks/39", "art": ""},
    {"name": "Twilight Zone", "status": "cancelled", "tags": ["Bally"],
     "page": "", "art": ""},
    {"name": "Theatre of Magic", "status": "planned", "tags": [],
     "page": "", "art": ""},
]


def register(ctx):
    ctx.ui.list("board", lambda: CHALLENGES, area="community", columns=[
        {"field": "name"},
        {"field": "status", "kind": "state", "states": {
            "open": {"tier": "on"},
            "closing": {"tier": "warn"},
            "closed": {"tier": "off"},
            "cancelled": {"icon": "block"},
            "planned": {},
        }},
        {"field": "tags", "kind": "chips"},
        {"field": "page", "kind": "link"},
        {"field": "art", "kind": "picture"},
    ])
