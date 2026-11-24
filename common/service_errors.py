"""The four ways a service refuses, so a caller can tell one refusal from another.

A caller matches on the type, never on the message: the message is already the sentence a
person reads, and `details` is what a surface needs to name the thing it is about. Four,
because that is how many distinct things a caller does about a refusal - look elsewhere,
fix the request, try again later, or stop offering it here.

A service is free to raise something more specific as long as it derives from one of
these, which is how a caller that only cares about the kind and a caller that cares about
the particular can both be served.

`CertificateChangedError` is the one refusal that is not a service's: it is a failed
request, so it derives from the transport's error and sits here only so that the code which
words failures can name it.
"""

from __future__ import annotations

from typing import Any

import requests

from common.i18n import t


class ServiceError(Exception):
    def __init__(self, message: str = "", *, details: Any = None) -> None:
        super().__init__(message)
        self.details = details


class NotFoundError(ServiceError):
    """Nothing here goes by that name."""


class RefusedError(ServiceError):
    """The request itself is wrong, and sending it again will not help."""


class BlockedError(ServiceError):
    """The request is fine and cannot happen right now - something is in the way."""


class UnavailableError(ServiceError):
    """Exists, but not on this install. The message says what is missing."""


class CertificateChangedError(requests.exceptions.SSLError):
    """The certificate a paired install shows is not the one it showed when paired.

    An SSLError and not a ServiceError, so a caller that already handles a failed request
    handles this one too; it is here so that the code which words failures can name it
    without importing the pinning.
    """

    def __init__(self, host: str, **kwargs: Any) -> None:
        self.host = host
        super().__init__(t("said.why.certificate_changed_at", host=host), **kwargs)
