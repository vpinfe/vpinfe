"""The routes core mounts for what an extension declared through `ctx.ui`.

One router per extension, built at startup for every extension that has a manifest, so a
switched-off one keeps its paths and refuses on them. A route looks the extension up when
it is asked, so what answers is whatever the extension registered last. The work is in
`common.extensions.serving`; this module carries it over HTTP and words the refusals.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from common import extensions
from common.extensions import reach, serving
from common.extensions.host import Record
from common.games import game_lens, table_ops
from common.i18n import t

from . import models, responses, scopes
from .auth import require, requires
from .errors import CODE_UPSTREAM_UNAVAILABLE, ApiError, InvalidRequestError, NotFoundError


def _envelope(exc: serving.ExtensionError) -> ApiError:
    """What an extension's failure looks like to the caller: an unreachable service is the
    upstream's, anything else is the request's, with the setting it belongs under."""
    if exc.unreachable:
        return ApiError(CODE_UPSTREAM_UNAVAILABLE, exc.message, status_code=502)
    return InvalidRequestError(exc.message,
                               details={"field": exc.field} if exc.field else None)


def answering_at(tier: str) -> Any:
    """Marks the request as answered at `tier`, the tier its route is declared at."""
    async def answer() -> None:
        reach.answer_at(tier)

    return Depends(answer)


def router(name: str, running: Any) -> APIRouter:
    """The routes for extension `name`, each behind `running` and the Play tier, which every
    caller of the extension holds. The handler then holds the caller to the tier of the
    list, section, group, dialog or act it reaches, which only the extension's declaration
    knows. Every route answers at Library at most; only an act declared Protected keeps more."""
    floor = requires(scopes.extension_scope(name, reach.PLAY))
    answering = answering_at(reach.LIBRARY)
    found = APIRouter(tags=["extensions"])

    def reading(request: Request, kind: str, key: str) -> None:
        """Hold the caller to what reading a list, section, group or dialog needs."""
        tier = ask(serving.surface_tier, kind, key)
        require(request, scopes.extension_scope(name, reach.for_reading(tier)))

    def library(request: Request) -> None:
        """Hold the caller to Library, for what has no tier of its own."""
        require(request, scopes.extension_scope(name, reach.LIBRARY))

    def changing(request: Request) -> serving.Need:
        """What a change calls with the tier it is made at."""
        def need(tier: str) -> None:
            require(request, scopes.extension_scope(name, tier))
        return need

    def record() -> Record:
        one = extensions.registry().get(name)
        if one is None:
            raise NotFoundError(t("error.extensions.not_found", name=name))
        return one

    def ask(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return call(record(), *args, **kwargs)
        except serving.ExtensionError as exc:
            raise _envelope(exc) from exc

    def get(path: str, summary: str) -> Callable[[Callable[..., Any]], Any]:
        return found.get(path, summary=summary, dependencies=[running, floor, answering])

    def post(path: str, summary: str) -> Callable[[Callable[..., Any]], Any]:
        return found.post(path, summary=summary, dependencies=[running, floor, answering])

    def put(path: str, summary: str) -> Callable[[Callable[..., Any]], Any]:
        return found.put(path, summary=summary, dependencies=[running, floor, answering])

    # -- settings ------------------------------------------------------------------

    @get("/settings", "The settings the extension declared, with their values")
    def get_settings(request: Request) -> dict:
        library(request)
        return ask(serving.settings_read)

    @put("/settings", "Change settings the extension declared")
    def put_settings(request: Request, body: models.ExtensionValues) -> dict:
        library(request)
        return ask(serving.settings_write, body.values)

    # -- lists ---------------------------------------------------------------------

    @get("/lists/{key}", "The rows of a list the extension declared")
    def get_list(request: Request, key: str) -> dict:
        reading(request, "list", key)
        return ask(serving.list_rows, key)

    @get("/lists/{key}/about", "How a list stands, and what it offers to do")
    def get_list_about(request: Request, key: str) -> dict:
        reading(request, "list", key)
        return ask(serving.list_about, key)

    @post("/lists/{key}/about/acts/{act}", "Do one of a list's acts")
    def post_list_act(request: Request, key: str, act: str) -> dict:
        return ask(serving.list_act, key, act, changing(request))

    @post("/lists/{key}/verbs/{verb}", "Press one of a list's verbs on some of its rows")
    def post_list_verb(request: Request, key: str, verb: str,
                       body: models.ExtensionVerb) -> dict:
        return ask(serving.list_verb, key, verb, body.ids, changing(request))

    @get("/lists/{key}/detail/{row_id:path}", "What the pane beside a list shows for a row")
    def get_list_detail(request: Request, key: str, row_id: str) -> dict:
        reading(request, "list", key)
        return ask(serving.list_detail, key, row_id)

    @put("/lists/{key}/detail/{row_id:path}", "Change fields in a row's pane")
    def put_list_detail(request: Request, key: str, row_id: str,
                        body: models.ExtensionValues) -> dict:
        return ask(serving.list_save, key, row_id, body.values, changing(request))

    @get("/pictures/{picture_id}", "A picture one of the extension's rows named")
    def get_picture(picture_id: str, request: Request) -> Response:
        if not ask(serving.reaches_play):
            library(request)
        return responses.media_file(ask(serving.picture, picture_id), request, "")

    # -- sections ------------------------------------------------------------------

    @get("/sections/{key}", "What a section of the extension's panel shows")
    def get_section(request: Request, key: str) -> dict:
        reading(request, "section", key)
        return ask(serving.section_read, key)

    @put("/sections/{key}", "Change fields in a section of the extension's panel")
    def put_section(request: Request, key: str, body: models.ExtensionValues) -> dict:
        return ask(serving.section_save, key, body.values, changing(request))

    # -- panel groups --------------------------------------------------------------

    def subject(game_id: str, table_id: str = "", *, keep_private: bool = False) -> dict:
        """Who a group is asked about, built here so an extension is told only the ids it
        can use. A Private game is `{}`: asking would tell the extension the library holds
        it, unless the entry asking works on Private games, and then `who` says which."""
        game = game_lens.resource_of(game_id)
        if game["private"] and not keep_private:
            return {}
        who = {"game_id": game_id, "vps_id": game["vps_id"], "name": game["name"]}
        if keep_private:
            who["private"] = bool(game["private"])
        if not table_id:
            return who
        row = table_ops.row_or_refuse(game_lens.game_or_refuse(game_id), table_id)
        source = row.get("source") or {}
        return {**who, "table_id": table_id, "vps_file_id": str(source.get("vps_file_id") or ""),
                "version": str(row.get("version") or "")}

    def group_subject(kind: str, game_id: str, table_id: str = "") -> dict:
        """Who the `kind` group is asked about: a Private game only where the group was
        declared to work on one."""
        return subject(game_id, table_id, keep_private=ask(serving.group_keeps_private, kind))

    def _asked(game_id: str, kind: str, table_id: str = "") -> dict:
        who = group_subject(kind, game_id, table_id)
        if not who:
            raise NotFoundError(t("error.extensions.no_group", display=name, subject=kind))
        return who

    def group_read(kind: str, game_id: str, table_id: str = "") -> dict:
        who = group_subject(kind, game_id, table_id)
        if not who:
            return {"title": "", "section": None, "acts": []}
        return ask(serving.group_read, kind, who)

    @get("/groups/game/{game_id}", "What the extension says on a game's panel")
    def get_game_group(request: Request, game_id: str) -> dict:
        reading(request, "group", "game")
        return group_read("game", game_id)

    @put("/groups/game/{game_id}", "Change fields in the group on a game's panel")
    def put_game_group(request: Request, game_id: str,
                       body: models.ExtensionValues) -> dict:
        who = group_subject("game", game_id)
        if not who:
            raise NotFoundError(t("error.extensions.no_group", display=name, subject="game"))
        return ask(serving.group_save, "game", who, body.values, changing(request))

    @post("/groups/game/{game_id}/acts/{act}", "Do one of a game group's acts")
    def post_game_group_act(request: Request, game_id: str, act: str) -> dict:
        return ask(serving.group_act, "game", _asked(game_id, "game"), act, changing(request))

    @post("/verbs/{kind}/{verb}", "Press one of the extension's entries in the Games or "
          "Tables menu on some of them")
    def post_library_verb(request: Request, kind: str, verb: str,
                          body: models.ExtensionLibraryVerb) -> dict:
        if kind not in ("game", "table"):
            raise NotFoundError(t("error.extensions.no_verb", display=name, key=verb))
        keep = bool(ask(serving.declared_library_verb, kind, verb)["private_ok"])
        chosen = dict.fromkeys((one.game_id, one.table_id if kind == "table" else "")
                               for one in body.targets)
        asked = [subject(game_id, table_id, keep_private=keep) for game_id, table_id in chosen
                 if table_id or kind == "game"]
        return ask(serving.library_verb, kind, verb, [who for who in asked if who],
                   changing(request))

    @post("/groups/table/{game_id}/{table_id}/acts/{act}", "Do one of a table group's acts")
    def post_table_group_act(request: Request, game_id: str, table_id: str, act: str) -> dict:
        return ask(serving.group_act, "table", _asked(game_id, "table", table_id), act,
                   changing(request))

    @get("/groups/table/{game_id}/{table_id}", "What the extension says on a table's panel")
    def get_table_group(request: Request, game_id: str, table_id: str) -> dict:
        reading(request, "group", "table")
        return group_read("table", game_id, table_id)

    @put("/groups/table/{game_id}/{table_id}", "Change fields in the group on a table's panel")
    def put_table_group(request: Request, game_id: str, table_id: str,
                        body: models.ExtensionValues) -> dict:
        who = group_subject("table", game_id, table_id)
        if not who:
            raise NotFoundError(t("error.extensions.no_group", display=name, subject="table"))
        return ask(serving.group_save, "table", who, body.values, changing(request))

    # -- dialogs -------------------------------------------------------------------

    @get("/actions/{key}", "The step of a dialog the extension declared")
    def get_action(request: Request, key: str, step: str = "") -> dict:
        reading(request, "action", key)
        return ask(serving.dialog_ask, key, step)

    @post("/actions/{key}/ask", "The step of a dialog opened over some rows or for a game")
    def post_action_ask(request: Request, key: str, body: models.ExtensionAsk) -> dict:
        reading(request, "action", key)
        return ask(serving.dialog_ask, key, body.step, body.rows, body.who.model_dump())

    @post("/actions/{key}/check", "Check a dialog's values")
    def post_action_check(request: Request, key: str, body: models.ExtensionStep) -> dict:
        reading(request, "action", key)
        return ask(serving.dialog_check, key, body.values, body.step, changing(request),
                   body.who.model_dump())

    @post("/actions/{key}/act", "Press one of a dialog's own buttons")
    def post_action_act(request: Request, key: str, body: models.ExtensionAct) -> dict:
        return ask(serving.dialog_act, key, body.act, body.values, body.step,
                   changing(request), body.who.model_dump())

    @post("/actions/{key}/run", "Run what a dialog asked for")
    def post_action_run(request: Request, key: str, body: models.ExtensionStep,
                        response: Response) -> dict:
        answer = ask(serving.dialog_run, key, body.values, body.step, changing(request),
                     body.who.model_dump())
        if answer.get("job_id"):
            response.status_code = 202
        return answer

    @get("/actions/{key}/progress", "How far a dialog's run has got")
    def get_action_progress(request: Request, key: str) -> dict:
        reading(request, "action", key)
        return ask(serving.dialog_progress, key)

    @post("/jobs/{kind}/stop", "Ask a running job of the extension to stop")
    def post_job_stop(request: Request, kind: str) -> dict:
        library(request)
        return ask(serving.job_stop, kind)

    # -- online sources ------------------------------------------------------------

    def source_who(sent: models.ExtensionWho) -> dict:
        """Who a source is asked about. A game Discovery lists that the library does not hold
        has no `game_id`, and is asked about by its VPS id alone; a Private game is `{}`."""
        if not sent.game_id:
            return {"game_id": "", "vps_id": sent.vps_id, "name": sent.name} if sent.vps_id else {}
        return subject(sent.game_id) and sent.model_dump()

    @get("/sources/{key}/offers", "What an online source has of one kind of file for a game")
    def get_source_offers(request: Request, key: str, kind: str, game_id: str = "",
                          vps_id: str = "", name: str = "") -> dict:
        library(request)
        if not source_who(models.ExtensionWho(game_id=game_id, vps_id=vps_id, name=name)):
            return {"rows": [], "note": "", "link": None}
        return ask(serving.source_offers, key, kind,
                   {"game_id": game_id, "vps_id": vps_id, "name": name})

    @post("/sources/{key}/take", "Take one of an online source's rows into a game")
    def post_source_take(request: Request, key: str, body: models.ExtensionSourceTake) -> dict:
        who = source_who(body.who)
        if not who:
            raise NotFoundError(t("error.extensions.verb_none", display=name, key=key))
        return ask(serving.source_take, key, body.kind, who, body.row, changing(request))

    @post("/sources/{key}/take-many", "Take every file ticked in Discovery from an online source")
    def post_source_take_many(request: Request, key: str,
                              body: models.ExtensionSourceTakeMany) -> dict:
        who = source_who(body.who)
        if not who:
            raise NotFoundError(t("error.extensions.verb_none", display=name, key=key))
        return ask(serving.source_take_many, key, who,
                   [one.model_dump() for one in body.picks], changing(request))

    @post("/sources/{key}/act", "Press a button an online source drew on a row or on the pane")
    def post_source_act(request: Request, key: str, body: models.ExtensionSourceAct) -> dict:
        who = source_who(body.who)
        if not who:
            raise NotFoundError(t("error.extensions.verb_none", display=name, key=key))
        return ask(serving.source_act, key, body.kind, who, body.row, body.act,
                   changing(request))

    @get("/sources/{key}/look", "What the page of an online source's file says")
    def get_source_look(request: Request, key: str, kind: str, row: str, game_id: str = "",
                        vps_id: str = "", name: str = "") -> dict:
        library(request)
        who = source_who(models.ExtensionWho(game_id=game_id, vps_id=vps_id, name=name))
        if not who:
            return {"pictures": [], "description": "", "files": [], "note": ""}
        return ask(serving.source_look, key, kind, who, row)

    # -- accounts ------------------------------------------------------------------

    @get("/accounts/{player_id}", "A player's account with the extension's service")
    def get_account(request: Request, player_id: str) -> dict:
        library(request)
        return ask(serving.account_read, player_id)

    @put("/accounts/{player_id}", "Change a player's account")
    def put_account(request: Request, player_id: str, body: models.ExtensionValues) -> dict:
        library(request)
        return ask(serving.account_write, player_id, body.values)

    @post("/accounts/{player_id}/acts/{act}", "Do one of an account's acts")
    def post_account_act(request: Request, player_id: str, act: str) -> dict:
        library(request)
        return ask(serving.account_act, player_id, act)

    @get("/accounts/{player_id}/card", "A player's card, to share")
    def get_account_card(request: Request, player_id: str) -> dict:
        library(request)
        return ask(serving.account_card, player_id)

    @post("/accounts/cards", "Read a card a player pasted")
    def post_account_cards(request: Request, body: models.ExtensionCard) -> dict:
        library(request)
        return ask(serving.account_read_card, body.card)

    @get("/available", "Whether a name is free to take")
    def get_available(request: Request, candidate: str = "") -> dict:
        library(request)
        return ask(serving.account_available, candidate)

    return found
