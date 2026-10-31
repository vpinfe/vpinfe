"""The devices this install knows about, and what it can be asked to do to them.

A device is another machine - a second VPinFE install, or a phone running VPX Mobile.
`common/devices/device_ops.py` holds the registry side; `common/games/mobile_ops.py` holds what a
phone is carrying, because which games are on it is a question about games.

The one thing only a request can answer is where an announcement came from: the socket's
address, which an install announcing itself is not allowed to name for itself.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Request, Response
from starlette.concurrency import run_in_threadpool

from common.devices import device_ops
from common.games import mobile_ops

from . import jobs as jobs_api
from . import models, scopes
from .auth import requires

router = APIRouter(prefix="/devices", tags=["devices"])


@router.get("", summary="The devices this install knows",
            dependencies=[requires(scopes.DEVICES_READ)])
def list_devices() -> models.DeviceList:
    return models.DeviceList.model_validate(device_ops.listing())


# Ahead of `/{device_id}`, which would otherwise match the word.
@router.get("/discovered", summary="Installs announcing themselves on this network",
            dependencies=[requires(scopes.DEVICES_READ)])
def discovered_installs() -> models.DiscoveredList:
    """What mDNS has heard, as it stands. Not the registry: these are announcements, so
    nothing here has been recorded or decided about."""
    return models.DiscoveredList.model_validate(device_ops.discovered())


@router.get("/{device_id}", summary="One device",
            dependencies=[requires(scopes.DEVICES_READ)])
def get_device(device_id: str) -> models.DeviceResource:
    return models.DeviceResource(**device_ops.resource(device_id))


@router.put("", summary="Record an install", status_code=200,
            dependencies=[requires(scopes.DEVICES_WRITE)])
def announce(request: Request,
             payload: models.DeviceAnnouncement = Body(...)) -> models.DeviceResource:
    """Idempotent by `device_id`: announcing twice is one device, heard from twice.

    The socket's address is read here and handed over, because it is the one fact only
    this layer has - and an install announcing itself is recorded at where it was heard
    from, never at an address it named. A VPX Mobile device is refused: it is listed in
    `PUT /config/mobile_devices`.
    """
    client = getattr(request, "client", None)
    return models.DeviceResource(**device_ops.announce(
        payload.device_id, payload.kind, payload.display_name, payload.features,
        payload.port,
        heard_from=getattr(client, "host", "") or ""))


@router.post("/probe", summary="Ask every device whether it is there",
             dependencies=[requires(scopes.DEVICES_WRITE)])
async def probe_devices() -> models.DeviceProbeList:
    """Dial each device and report what answered, recording the ones that did.

    A write, because it advances each answering device's `last_reachable` - the pull half
    of that timestamp, where an announcement is the push half.
    """
    probes = [await run_in_threadpool(device_ops.probe_one, device)
              for device in device_ops.all_devices()]
    return models.DeviceProbeList.model_validate({"probes": probes})


@router.post("/{device_id}/probe", summary="Ask one device whether it is there",
             dependencies=[requires(scopes.DEVICES_WRITE)])
async def probe_device(device_id: str) -> models.DeviceProbe:
    """The same question as above, of one device, recording it if it answered."""
    device = device_ops.device_or_refuse(device_id)
    return models.DeviceProbe(**await run_in_threadpool(device_ops.probe_one, device))


@router.delete("/{device_id}", summary="Forget an install", status_code=204,
               dependencies=[requires(scopes.DEVICES_WRITE)])
def forget(device_id: str) -> Response:
    """Forgetting one that is still running only means it announces itself again."""
    device_ops.forget(device_id)
    return Response(status_code=204)


@router.get("/{device_id}/games", summary="What a VPX Mobile device is carrying",
            dependencies=[requires(scopes.DEVICES_READ)])
async def device_games(device_id: str) -> models.DeviceGameList:
    """Asked of the device, never remembered. A mobile device is taken away, filled up and
    emptied by hand, so anything this end recorded about it would be a claim."""
    return models.DeviceGameList.model_validate(
        await run_in_threadpool(mobile_ops.carried_by, device_id))


@router.post("/{device_id}/games", summary="Send games to a VPX Mobile device",
             status_code=202, dependencies=[requires(scopes.DEVICES_WRITE)])
def send_games(device_id: str, response: Response,
               payload: models.DeviceSendRequest = Body(...)) -> models.JobResource:
    """A job, not an action: a multi-file transfer over a mobile device's wifi is slow work, and
    slow work has a shape here already."""
    job = mobile_ops.send(device_id, payload.games, payload.everything, payload.settings)
    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    return models.JobResource(**jobs_api.resource(job))


@router.post("/{device_id}/games/plan", summary="What sending these games would do",
             dependencies=[requires(scopes.DEVICES_READ)])
async def plan_send(device_id: str,
                    payload: models.DeviceSendRequest = Body(...)) -> models.DeviceSendPlan:
    """A read, though it is a POST for the body: which of the games would send a mobile
    settings file, so a client can say so before it asks for the send."""
    return models.DeviceSendPlan.model_validate(
        await run_in_threadpool(mobile_ops.plan, device_id, payload.games,
                                payload.everything))


@router.delete("/{device_id}/games/{name}", summary="Remove a game from a device",
               status_code=204, dependencies=[requires(scopes.DEVICES_WRITE)])
async def remove_game(device_id: str, name: str) -> Response:
    await run_in_threadpool(mobile_ops.remove, device_id, name)
    return Response(status_code=204)
