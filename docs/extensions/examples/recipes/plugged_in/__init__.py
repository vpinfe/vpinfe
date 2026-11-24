ACCESS = {True: "yes", False: "no"}


def register(ctx):
    def devices():
        return [{
            "name": one["name"],
            "kind": one["kind"],
            "ids": f"{one['vendor_id']}:{one['product_id']}" if one["vendor_id"] else "",
            "access": ACCESS.get(one["accessible"], "unknown"),
        } for one in ctx.hardware.devices()]

    def status():
        if any(one["access"] == "no" for one in devices()):
            return ctx.hardware.access_help()
        return ""

    ctx.ui.list("devices", devices, area="system", status=status, columns=[
        {"field": "name"},
        {"field": "kind", "kind": "state", "states": {"usb": {}, "serial": {}, "display": {}}},
        {"field": "ids"},
        {"field": "access", "kind": "state", "states": {
            "yes": {}, "no": {"tier": "bad"}, "unknown": {}}},
    ])
