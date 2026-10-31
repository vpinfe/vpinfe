"""The two keys this install holds in plain, because it must hand them on.

**The Remote pass** is what the Remote Code, the Join code and On your mobile device carry,
so scanning one signs a mobile device in at Play with nothing to type. Renewing it ends every
mobile device that came in with the old one.

**A pairing token** is what another install issued this one when they were paired. This
install sends it with every call to that device.

Both live in the secrets file, which only this user can read. A token this install *issues*
is not here: it is hashed in `common/security/api_tokens.py`, because the one holding it is
someone else.
"""

from __future__ import annotations

import hmac
import secrets
import uuid

from common.security import machine_secrets

PASS = "remote_pass"
PASS_PARAM = "pass"
PAIRED = "paired_devices"


def remote_pass() -> tuple[str, str]:
    """The pass in force as (id, secret), made on first use."""
    held = machine_secrets.get(PASS, lambda: {"id": uuid.uuid4().hex,
                                              "secret": secrets.token_urlsafe(24)})
    return str(held["id"]), str(held["secret"])


def renew_remote_pass() -> tuple[str, str]:
    """A new pass. Returns the id of the one it replaced, for the caller to sign out the
    phones that carry it, and the new secret."""
    old_id, _ = remote_pass()
    machine_secrets.put(PASS, {"id": uuid.uuid4().hex, "secret": secrets.token_urlsafe(24)})
    return old_id, remote_pass()[1]


def pass_id_for(secret: str) -> str:
    """The id of the pass in force if `secret` is it, else ''."""
    pass_id, current = remote_pass()
    return pass_id if secret and hmac.compare_digest(current.encode(), secret.encode()) else ""


def pairing_token(device_id: str) -> str:
    """What to send to this device, or '' when it has not been paired."""
    held = machine_secrets.read().get(PAIRED)
    return str(held.get(device_id, "")) if isinstance(held, dict) else ""


def pairing_header(device_id: str) -> dict[str, str] | None:
    """The request header that presents the token paired with this device, or None."""
    token = pairing_token(device_id) if device_id else ""
    return {"Authorization": "bearer " + token} if token else None


def keep_pairing_token(device_id: str, token: str) -> None:
    machine_secrets.change(
        PAIRED, lambda held: {**(held if isinstance(held, dict) else {}), device_id: token})


def forget_pairing_token(device_id: str) -> None:
    if device_id not in (machine_secrets.read().get(PAIRED) or {}):
        return
    machine_secrets.change(
        PAIRED, lambda held: {key: value for key, value in (held or {}).items()
                              if key != device_id})
