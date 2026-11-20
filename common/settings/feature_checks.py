"""Whether an install's configuration is enough for the features it has switched on.

Enablement and availability answer different questions and this is the first. A feature
is a deliberate choice; a requirement is what that choice needs in order to work. An
install with `frontend` on and no launcher configured is *misconfigured*, which is not
the same as an install that simply has no peripherals - and only the first is something
a person should be shown and asked to fix.

That distinction is why this is separate from `is_available` on a capability.
`_peripherals_available` returns False on any machine without DOF or a real DMD, which is
most of them and is correctly configured; marking that as a problem would put a warning on
a healthy install, and a mark that appears on everything says nothing.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from common.config import path_state
from common.config.config_access import cfg_get
from common.config.config_store import ConfigStore
from common.devices import install_identity
from common.i18n import t

if TYPE_CHECKING:
    from common.config.config_schema import ConfigOption

# Where the thing that fixes a requirement lives. A settings requirement ends at a field
# on a settings page; a launcher one ends at the Launchers list, which is not a setting
# and has no section and key to name.
WHERE_SETTINGS = "settings"
WHERE_LAUNCHERS = "launchers"
WHERE_EXTENSIONS = "extensions"
# Each is the Manager's view key for that page.
WHERES = (WHERE_SETTINGS, WHERE_LAUNCHERS, WHERE_EXTENSIONS)

# The section a finding about the library folders is filed under. It names no setting: the
# folders are not config keys, and the Folders page is where it is fixed.
FOLDERS = "folders"
# The section a finding about an extension is filed under, with its name as the key.
EXTENSIONS = "extensions"

# What each feature needs before it can do its job. These happen to be paths, which is not
# a rule - a requirement is anything a feature cannot work without.
REQUIREMENTS: dict[str, tuple[tuple[str, str], ...]] = {
    # Curating a library needs somewhere to find the games, which is a location rather
    # than a setting now and is checked below against the ones the install actually has.
    install_identity.LIBRARY: (),
    # Launching needs the games, and something to launch them *with*. Neither is a
    # setting: both are objects, and both are checked below.
    install_identity.FRONTEND: (),
    # Managing other installs needs nothing of its own: it reaches them over the network,
    # and an address it cannot reach is that device's row to report, not a setting here.
    install_identity.DEVICES: (),
    # A rollup reports on whatever the other features hold. It has nothing of its own to
    # be missing, and an empty library is a fact about the library rather than a fault
    # in the page that counts it.
    install_identity.OVERVIEW: (),
}


# Where a frontend is told which library to read. Not a path, which is why it sits beside
# the table above rather than in it: what it names is another install.
LIBRARY_URL = ("network", "library_url")


@dataclass(frozen=True)
class Unmet:
    """One requirement an enabled feature does not have. `reason` is written for the
    person who has to fix it, and names what was found rather than what was wanted."""

    feature: str
    section: str
    key: str
    state: str
    reason: str
    # Which surface resolves it. Defaulted, because every requirement was a setting until
    # the binary moved onto a launcher, and most still are.
    where: str = WHERE_SETTINGS


def _option(section: str, key: str) -> ConfigOption | None:
    from common.config import config_schema

    return next((o for o in config_schema.CONFIG_OPTIONS
                 if o.section == section and o.key == key), None)


# "Nobody asked about a launcher", as against None, which means there is not one. Two
# different answers: a caller that does not hold the launchers is not reporting that the
# install has none.
NOT_ASKED = object()


# `launcher` and `locations` are handed in already resolved, and are Any because this
# module may not import the domain package that would name their types - which is the
# same reason they are passed at all rather than looked up here. NOT_ASKED means the
# caller did not resolve them, which is not the same as resolving them to nothing.
def unmet(config: ConfigStore, features: Iterable[str] | None = None,
          launcher: Any = NOT_ASKED, locations: Any = NOT_ASKED) -> list[Unmet]:
    """Every requirement the enabled features do not satisfy, in feature order.

    One entry per (feature, setting), so a setting two features both need is reported
    against each of them - the person is looking at one feature's page and needs to know
    that page is affected, not that some other feature also is.
    """
    on = list(features) if features is not None else install_identity.features(config)
    found: list[Unmet] = []
    for feature in install_identity.FEATURES:
        if feature not in on:
            continue
        for section, key in REQUIREMENTS.get(feature, ()):
            option = _option(section, key)
            if option is None or option.type == "list":
                continue
            found_state = path_state.look(cfg_get(config, section, key), option.path)
            state, reason = found_state.state, found_state.reason
            if state == path_state.OK:
                continue
            # Unset is a failure here even though it is not one for the setting itself:
            # blank means "use the default" for an optional path, and these have no
            # default to fall back on.
            if state == path_state.UNSET:
                reason = t("error.features.not_set", label=option.label)
            found.append(Unmet(feature=feature, section=section, key=key,
                               state=state, reason=reason))
    for extra in (*_no_reachable_location(on, locations),
                  _no_working_launcher(on, launcher),
                  _no_library_to_read(config, on)):
        if extra is not None:
            found.append(extra)
    return found


def _no_reachable_location(on: list[str], found: Any) -> tuple[Unmet, ...]:
    """A library with nowhere to read, or nowhere it can currently reach.

    Reported against every feature that needs one, because the person is looking at one
    feature's page and needs to know that page is affected. Handed the resolved locations
    for the reason the launcher is: this module may not reach into a domain package, and
    passing the answer keeps the resolution in the one place that owns it.

    One unreachable location among several is that row's business, not a fault in the
    install - the library still has the others. Nothing reachable at all is the fault.
    """
    wants = [f for f in (install_identity.LIBRARY, install_identity.FRONTEND) if f in on]
    if not wants or found is NOT_ASKED:
        return ()

    held = list(found or [])
    if not held:
        reason = t("error.features.no_folder")
    elif not any(state.reachable for state in (one[1] for one in held)):
        reason = t("error.features.none_reachable")
    else:
        return ()
    return tuple(Unmet(feature=feature, section=FOLDERS, key="", state=path_state.UNSET,
                       reason=reason, where=WHERE_SETTINGS)
                 for feature in wants)


def _no_working_launcher(on: list[str], found: Any) -> Unmet | None:
    """A frontend with nothing it can actually run a table with.

    Asked about the launcher the install would use rather than a config key, because that
    is what a launch resolves and the two must not be able to disagree. It names no
    section: the fix is adding or pointing a launcher, which is not a settings field.

    The launcher is handed in already resolved. This module is infrastructure and may not
    reach into a domain package to find one - and passing the answer rather than the
    question keeps the resolution in the one place that owns it.
    """
    if install_identity.FRONTEND not in on or found is NOT_ASKED:
        return None

    if found is None:
        reason, state = t("error.features.no_launcher"), path_state.UNSET
    else:
        configured = str(found.value("bin_path") or "").strip()
        if not configured:
            reason, state = (t("said.no_program", launcher_name=found.display_name),
                             path_state.UNSET)
        else:
            looked = path_state.look(configured, path_state.EXE)
            state = looked.state
            if state == path_state.OK:
                return None
            reason = t("error.features.program_problem",
                       launcher_name=found.display_name, problem=looked.reason)
    return Unmet(feature=install_identity.FRONTEND, section="", key="",
                 state=state, reason=reason, where=WHERE_LAUNCHERS)


def _no_library_to_read(config: ConfigStore, on: list[str]) -> Unmet | None:
    """A frontend that holds no library of its own and has not been told which to read.

    Not guessed at, not even when exactly one install on the network has a library. A
    silent pick is the kind of thing nobody can debug afterwards, and the catalog belongs
    to a different machine.
    """
    if install_identity.FRONTEND not in on or install_identity.LIBRARY in on:
        return None
    if cfg_get(config, *LIBRARY_URL).strip():
        return None
    return Unmet(feature=install_identity.FRONTEND,
                 section=LIBRARY_URL[0], key=LIBRARY_URL[1],
                 state=path_state.UNSET,
                 reason=t("error.features.no_library"))


def features_in_trouble(config: ConfigStore,
                        features: Iterable[str] | None = None) -> set[str]:
    """Just the names, for a caller that only has to decide whether to mark something."""
    return {item.feature for item in unmet(config, features)}
