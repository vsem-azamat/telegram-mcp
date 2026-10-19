"""Tests for promote_admin: rights presets, every right of the layer, and the rank."""

import inspect
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp.utilities.func_metadata import func_metadata
from pydantic import ValidationError
from telethon.errors.rpcerrorlist import RightForbiddenError, UserNotParticipantError
from telethon.tl import functions, types
from telethon.tl.types import ChatAdminRights

from telegram_mcp.tools import groups

CHANNEL_ID = -1001234567890
USER_ID = 7

LAYER_RIGHTS = [name for name in inspect.signature(ChatAdminRights.__init__).parameters][1:]


def _channel():
    return types.Channel(
        id=1234567890,
        title="Supergroup",
        photo=types.ChatPhotoEmpty(),
        date=None,
        megagroup=True,
    )


class FakeClient:
    def __init__(self, participant=None, lookup_error=None):
        self.participant = participant
        self.lookup_error = lookup_error
        self.requests = []

    async def __call__(self, request):
        self.requests.append(request)
        if isinstance(request, functions.channels.GetParticipantRequest):
            if self.lookup_error:
                raise self.lookup_error
            return SimpleNamespace(participant=self.participant)
        return SimpleNamespace()

    def edits(self):
        return [r for r in self.requests if isinstance(r, functions.channels.EditAdminRequest)]

    def lookups(self):
        return [
            r for r in self.requests if isinstance(r, functions.channels.GetParticipantRequest)
        ]


@pytest.fixture
def install(monkeypatch):
    def _install(client):
        async def _resolve(identifier, _client):
            if identifier == CHANNEL_ID:
                return _channel()
            return SimpleNamespace(id=identifier)

        monkeypatch.setattr(groups, "get_client", lambda account=None: client)
        monkeypatch.setattr(groups, "resolve_entity", _resolve)
        return client

    return _install


def _admin(rank):
    return types.ChannelParticipantAdmin(
        user_id=USER_ID,
        promoted_by=99,
        date=None,
        admin_rights=ChatAdminRights(delete_messages=True),
        rank=rank,
    )


def _granted(request):
    return {name: bool(getattr(request.admin_rights, name)) for name in LAYER_RIGHTS}


def test_known_rights_are_every_flag_of_the_installed_layer():
    # A Telethon layer bump that adds or removes a right must fail here.
    assert list(groups.ADMIN_RIGHTS) == LAYER_RIGHTS


def test_docstring_names_every_right():
    doc = groups.promote_admin.__doc__
    assert [name for name in LAYER_RIGHTS if name not in doc] == []


@pytest.mark.asyncio
async def test_default_grants_every_right_except_add_admins_and_anonymous(install):
    client = install(FakeClient(participant=types.ChannelParticipant(user_id=USER_ID, date=None)))

    result = await groups.promote_admin(CHANNEL_ID, USER_ID, account=None)

    assert result.startswith("Successfully promoted")
    (edit,) = client.edits()
    granted = _granted(edit)
    assert granted.pop("add_admins") is False
    assert granted.pop("anonymous") is False
    assert all(granted.values()), granted
    assert {"post_stories", "edit_stories", "delete_stories", "manage_ranks"} <= set(granted)


@pytest.mark.asyncio
async def test_preset_all_grants_every_right(install):
    client = install(FakeClient(participant=None, lookup_error=UserNotParticipantError(None)))

    await groups.promote_admin(CHANNEL_ID, USER_ID, preset="all", account=None)

    (edit,) = client.edits()
    assert all(_granted(edit).values())


@pytest.mark.asyncio
async def test_rights_override_the_preset(install):
    client = install(FakeClient(participant=None, lookup_error=UserNotParticipantError(None)))

    await groups.promote_admin(
        CHANNEL_ID,
        USER_ID,
        preset="all_except_add_admins",
        rights={"anonymous": True, "post_stories": False},
        account=None,
    )

    granted = _granted(client.edits()[0])
    assert granted["anonymous"] is True
    assert granted["post_stories"] is False
    assert granted["add_admins"] is False
    assert granted["manage_ranks"] is True


@pytest.mark.asyncio
async def test_unknown_right_is_refused(install):
    client = install(FakeClient())

    result = await groups.promote_admin(
        CHANNEL_ID, USER_ID, rights={"manage_tags": True}, account=None
    )

    assert "manage_tags" in result and "Unknown" in result
    assert "manage_ranks" in result  # the error lists the known rights
    assert client.requests == []


@pytest.mark.asyncio
async def test_non_bool_right_is_refused(install):
    client = install(FakeClient())

    result = await groups.promote_admin(
        CHANNEL_ID, USER_ID, rights={"ban_users": "yes"}, account=None
    )

    assert "ban_users" in result and "true or false" in result
    assert client.requests == []


@pytest.mark.asyncio
async def test_unknown_preset_is_refused(install):
    client = install(FakeClient())

    result = await groups.promote_admin(CHANNEL_ID, USER_ID, preset="everything", account=None)

    assert "preset" in result
    assert client.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "participant",
    [_admin("Moderator"), types.ChannelParticipant(user_id=USER_ID, date=None, rank="VIP")],
)
async def test_omitted_rank_leaves_the_rank_unchanged(install, participant):
    # channels.editAdmin leaves the rank (an admin's title or a member's tag) alone
    # when the flag is unset, which Telethon does only for rank=None.
    client = install(FakeClient(participant=participant))

    result = await groups.promote_admin(CHANNEL_ID, USER_ID, account=None)

    assert result.startswith("Successfully promoted")
    assert client.lookups() == []
    (edit,) = client.edits()
    assert edit.rank is None


def test_telethon_leaves_the_rank_flag_unset_for_none():
    def request(rank):
        return functions.channels.EditAdminRequest(
            channel=types.InputChannel(1, 2),
            user_id=types.InputUser(3, 4),
            admin_rights=ChatAdminRights(),
            rank=rank,
        )._bytes()

    assert request(None)[4:8] == b"\x00\x00\x00\x00"  # flags.0 unset: rank unchanged
    assert request("")[4:8] == b"\x01\x00\x00\x00"  # "" is sent and clears it


@pytest.mark.asyncio
async def test_explicit_rank_is_sent(install):
    client = install(FakeClient())

    await groups.promote_admin(CHANNEL_ID, USER_ID, rank="Bot", account=None)

    assert client.lookups() == []
    assert client.edits()[0].rank == "Bot"


@pytest.mark.asyncio
async def test_explicit_empty_rank_clears_it(install):
    client = install(FakeClient())

    await groups.promote_admin(CHANNEL_ID, USER_ID, rank="", account=None)

    assert client.edits()[0].rank == ""


@pytest.mark.asyncio
async def test_basic_group_is_refused_with_what_to_do(install, monkeypatch):
    client = install(FakeClient())
    basic = types.Chat(
        id=5317263588,
        title="Basic Group",
        photo=types.ChatPhotoEmpty(),
        participants_count=3,
        date=None,
        version=1,
    )

    async def _resolve(identifier, _client):
        return basic if identifier == -5317263588 else SimpleNamespace(id=identifier)

    monkeypatch.setattr(groups, "resolve_entity", _resolve)

    result = await groups.promote_admin(-5317263588, USER_ID, account=None)

    assert "basic group" in result and "upgrade_group_to_supergroup" in result
    assert client.requests == []


@pytest.mark.asyncio
async def test_right_forbidden_names_the_rights_to_drop(install):
    class Forbidden(FakeClient):
        async def __call__(self, request):
            self.requests.append(request)
            raise RightForbiddenError(None)

    install(Forbidden())

    result = await groups.promote_admin(CHANNEL_ID, USER_ID, account=None)

    assert "error occurred" not in result
    assert "rights=" in result and "false" in result
    assert "post_stories" in result  # a granted right the caller may lack is named
    requested = result.split("Requested: ", 1)[1].split(". ", 1)[0].split(", ")
    assert "add_admins" not in requested and "anonymous" not in requested
    assert set(requested) == set(groups.ADMIN_RIGHTS) - {"add_admins", "anonymous"}
    assert "exist only in channels" not in result


@pytest.mark.asyncio
async def test_migrated_basic_group_points_at_its_supergroup(install, monkeypatch):
    client = install(FakeClient())
    migrated = types.Chat(
        id=5317263588,
        title="Old Group",
        photo=types.ChatPhotoEmpty(),
        participants_count=0,
        date=None,
        version=1,
        deactivated=True,
        migrated_to=types.InputChannel(channel_id=1234567890, access_hash=1),
    )

    async def _resolve(identifier, _client):
        return migrated if identifier == -5317263588 else SimpleNamespace(id=identifier)

    monkeypatch.setattr(groups, "resolve_entity", _resolve)

    result = await groups.promote_admin(-5317263588, USER_ID, account=None)

    assert "already upgraded" in result and "-1001234567890" in result
    assert "upgrade_group_to_supergroup" not in result
    assert client.requests == []


@pytest.mark.parametrize("value", ["yes", 1, "true", 0])
def test_mcp_arguments_refuse_non_bool_rights(value):
    model = func_metadata(groups.promote_admin).arg_model
    with pytest.raises(ValidationError):
        model.model_validate(
            {"group_id": CHANNEL_ID, "user_id": USER_ID, "rights": {"ban_users": value}}
        )


def test_mcp_arguments_accept_bool_rights():
    model = func_metadata(groups.promote_admin).arg_model
    args = model.model_validate(
        {"group_id": CHANNEL_ID, "user_id": USER_ID, "rights": {"ban_users": False}}
    )
    assert args.rights == {"ban_users": False}


@pytest.mark.asyncio
async def test_rank_longer_than_16_characters_is_refused(install):
    client = install(FakeClient())

    result = await groups.promote_admin(CHANNEL_ID, USER_ID, rank="x" * 17, account=None)

    assert "16" in result
    assert client.requests == []


@pytest.mark.asyncio
async def test_rank_of_16_characters_is_accepted(install):
    client = install(FakeClient())

    await groups.promote_admin(CHANNEL_ID, USER_ID, rank="Модератор чатов!", account=None)

    assert client.edits()[0].rank == "Модератор чатов!"
