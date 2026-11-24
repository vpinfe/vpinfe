def register(ctx):
    ctx.ui.settings([{"key": "port", "type": "number", "default": 9090, "min": 1, "max": 65535}])

    def console():
        return f"http://{{host}}:{ctx.config.get('port')}/"

    ctx.ui.link("console", console, area="system")
    ctx.ui.link("manual", "https://tool.example.com/manual")
