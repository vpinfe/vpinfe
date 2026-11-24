"""Writing a launcher through the API the way a caller describes one.

Callers name a launcher by `launcher_id` and `display_name`; `/config/launchers` holds it
as a list item. This translates the one into the other and answers in the caller's terms.
"""

from __future__ import annotations

from typing import Any

from common.devices import launcher_wire


class Answer:
    """A response whose body reads as the launcher a caller wrote."""

    def __init__(self, response: Any) -> None:
        self._response = response
        self.status_code = response.status_code
        self.text = response.text

    def json(self) -> Any:
        body = self._response.json()
        if self.status_code < 300 and isinstance(body, dict) and "id" in body:
            return launcher_wire.from_item(body)
        return body


def put_launcher(client: Any, launcher_id: str, json: dict[str, Any]) -> Answer:
    held = [one for one in client.get("/config/launchers").json() if isinstance(one, dict)]
    return Answer(client.put(f"/config/launchers/{launcher_id}",
                             json=launcher_wire.to_item(launcher_id, json, held)))


def delete_launcher(client: Any, launcher_id: str) -> Any:
    return client.delete(f"/config/launchers/{launcher_id}")
