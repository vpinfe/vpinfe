def register(ctx):
    ctx.ui.settings([{"key": "boot", "type": "select", "default": "play",
                      "choices": [["play", "Play"], ["desktop", "Desktop"]]}])
