"""The web surfaces an install serves: the Manager at / and /manager, the remote at
/remote.

What the Manager contains depends on the install - one curating a library and one
running games do not need the same screens. The remote is the same install seen from a
phone, and it is a second shell rather than the same one narrowed: a workbench is a list
beside an inspector, and one hand cannot hold two panes.
"""

from __future__ import annotations


def register() -> None:
    """Put the Manager on the app: its routes, and the stylesheet they all link.

    The mount is not under /manager, which is a page. A mount there would match every
    path below it and sit beside a route of the same name, and which of the two answered
    would depend on the order they were added.
    """
    from nicegui import app as nicegui_app

    from manager import edit_page, page, report_page  # noqa: F401
    from manager.kit import acting, appearance  # noqa: F401
    from manager.remote import remote  # noqa: F401
    from manager.security import sign_in_gate  # noqa: F401

    acting.install()

    nicegui_app.add_static_files(appearance.BASE_MOUNT, str(appearance.BASE_CSS.parent))
    nicegui_app.add_api_route(sign_in_gate.PAGE, sign_in_gate.sign_in_page, methods=["GET"],
                              include_in_schema=False)
