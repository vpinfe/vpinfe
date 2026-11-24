"""What the browser is told about input.

One method now - `get_bindings` - handing back each action's whole binding list, rather
than a gamepad map and a keyboard map separately. The split existed only because a stored
value could not say which input it came from; a `key:` or `pad:` selector says it, so the
two halves were one thing written down twice.

`get_joymapping` and `get_keymapping` still answer, projected out of the same lists, for
anything built against them.
"""

from __future__ import annotations

import logging
from typing import Any

from common import apps
from common.config import config_schema, input_registry
from common.config.config_access import ConfigSource, cfg_get
from common.games import launchers
from common.i18n import t

logger = logging.getLogger("vpinfe.frontend.input_api")

PAGING_GROUPS = config_schema.PAGING_GROUPS
PAGING_GROUP_ALIASES = config_schema.PAGING_GROUP_ALIASES
PAGING_GROUP_DEFAULT = config_schema.PAGING_GROUP_DEFAULT
PAGING_SIZE_DEFAULT = 10


def _parser(config: ConfigSource) -> Any:
    return getattr(config, "config", config)


def get_bindings(config: ConfigSource) -> dict[str, list[str]]:
    """Every action and what is bound to it, in order, with any binding two hold said."""
    out = input_registry.bound(config)
    _say_what_collides(out)
    return out


# Said once per process. This is asked on every page the frontend serves, and a warning
# repeated a hundred times is one a reader learns to scroll past.
_said_collisions: set[str] = set()


def _say_what_collides(bound: dict[str, list[str]]) -> None:
    """A binding two actions hold is one that only ever fires the first of them.

    Dispatch resolves a key to the first action listing it, so the loser does nothing
    and says nothing. An install that holds one is told here, because the alternative is
    a player concluding their cabinet is broken.
    """
    found = input_registry.collisions(bound)
    for binding, names in sorted(found.items()):
        if binding in _said_collisions:
            continue
        _said_collisions.add(binding)
        logger.warning(
            "%s is bound to %s. Only %s gets it - the rest do nothing. "
            "Change it under Settings, Input.",
            input_registry.describe(binding), ", ".join(names), names[0])


def apply_cabinet_defaults(config: ConfigSource) -> dict[str, list[str]]:
    """Put this machine's cabinet buttons on the actions nobody has changed, and save.

    Returns the actions that changed, with what each holds now; nothing when there is no
    launcher to ask, or its app wires no cabinet. Setup's Controls step calls this once it
    knows the install is a cabinet: until then a desktop keeps the keyboard layout.
    """
    launcher = launchers.default_launcher()
    app = apps.get(launcher.app) if launcher is not None else None
    if launcher is None or app is None or app.buttons is None:
        return {}
    settings = {declared.key: launcher.value(declared.key) for declared in launcher.fields()}
    changed = input_registry.cabinet_bindings(
        input_registry.bound(config), app.buttons.keys(settings))
    if not changed:
        return {}
    parser = _parser(config)
    if not parser.has_section(input_registry.SECTION):
        parser.add_section(input_registry.SECTION)
    for name, bindings in changed.items():
        parser.set(input_registry.SECTION, name, ",".join(bindings))
    config.save()
    return changed


def get_paging_config(config: ConfigSource) -> tuple[str, int]:
    """Return (paging_group, page_size), normalized to sane values.

    This is the player's default. A collection may override it - see the order block -
    so a caller with a collection in hand resolves against that first.
    """
    paging_group = cfg_get(config, "behavior", "paging_group",
                           PAGING_GROUP_DEFAULT).strip().lower()
    paging_group = PAGING_GROUP_ALIASES.get(paging_group, paging_group)
    if paging_group not in PAGING_GROUPS:
        paging_group = PAGING_GROUP_DEFAULT
    try:
        page_size = int(cfg_get(config, "behavior", "paging_size",
                                str(PAGING_SIZE_DEFAULT)).strip())
    except (TypeError, ValueError):
        page_size = PAGING_SIZE_DEFAULT
    if page_size < 1:
        page_size = PAGING_SIZE_DEFAULT
    return paging_group, page_size


def get_joymapping(config: ConfigSource) -> dict[str, str]:
    """The gamepad half, under the key names it used to have. Projected, never stored."""
    bindings = get_bindings(config)
    return {action.legacy_joy_key:
            ",".join(input_registry.pad_buttons_in(bindings[action.name]))
            for action in input_registry.actions() if action.legacy_joy_key}


def get_keymapping(config: ConfigSource) -> dict[str, str]:
    """The keyboard half, under the key names it used to have."""
    bindings = get_bindings(config)
    return {action.legacy_key_key:
            ",".join(input_registry.keys_in(bindings[action.name]))
            for action in input_registry.actions() if action.legacy_key_key}


def set_button_mapping(iniconfig: ConfigSource, action_name: str,
                       button_index: int) -> dict[str, Any]:
    """Bind a gamepad button to an action, keeping everything else bound to it.

    Accepts an action name or any `[Input]` key that used to be one, so the gamepad
    binding page keeps working while it is repointed.
    """
    name = input_registry.action_for_legacy_key(action_name)
    if not name:
        return {"success": False,
                "message": t("frontend.input_api.no_such_action", action=action_name)}
    try:
        parser = _parser(iniconfig)
        if not parser.has_section(input_registry.SECTION):
            parser.add_section(input_registry.SECTION)
        current = get_bindings(iniconfig)[name]
        # Replace this action's gamepad binding; leave its keys and anything richer.
        kept = [b for b in current if not b.startswith(input_registry.PAD_PREFIX)]
        kept.append(f"{input_registry.PAD_PREFIX}0/button:{button_index}")
        parser.set(input_registry.SECTION, name, ",".join(kept))
        iniconfig.save()
    except Exception:
        logger.exception("Could not save the button mapping for %s", name)
        return {"success": False, "message": t("frontend.input_api.not_saved")}
    label = next(action.label for action in input_registry.actions() if action.name == name)
    return {"success": True,
            "message": t("frontend.input_api.mapped", action=label, button=button_index)}
