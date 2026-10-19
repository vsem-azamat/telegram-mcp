"""Tests for promote_admin: rights presets, every right of the layer, and the rank."""

import inspect
from types import SimpleNamespace

import pytest
from telethon.errors.rpcerrorlist import UserNotParticipantError
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
async def test_rank_is_kept_for_an_existing_admin(install):
    client = install(FakeClient(participant=_admin("Moderator")))

    await groups.promote_admin(CHANNEL_ID, USER_ID, account=None)

    assert len(client.lookups()) == 1
    assert client.edits()[0].rank == "Moderator"


@pytest.mark.asyncio
async def test_rank_is_kept_for_the_creator(install):
    creator = types.ChannelParticipantCreator(
        user_id=USER_ID, admin_rights=ChatAdminRights(add_admins=True), rank="Owner"
    )
    client = install(FakeClient(participant=creator))

    await groups.promote_admin(CHANNEL_ID, USER_ID, account=None)

    assert client.edits()[0].rank == "Owner"


@pytest.mark.asyncio
async def test_admin_without_rank_keeps_an_empty_rank(install):
    client = install(FakeClient(participant=_admin(None)))

    await groups.promote_admin(CHANNEL_ID, USER_ID, account=None)

    assert client.edits()[0].rank == ""


@pytest.mark.asyncio
async def test_member_gets_an_empty_rank(install):
    client = install(FakeClient(participant=types.ChannelParticipant(user_id=USER_ID, date=None)))

    await groups.promote_admin(CHANNEL_ID, USER_ID, account=None)

    assert client.edits()[0].rank == ""


@pytest.mark.asyncio
async def test_non_participant_gets_an_empty_rank(install):
    client = install(FakeClient(lookup_error=UserNotParticipantError(None)))

    result = await groups.promote_admin(CHANNEL_ID, USER_ID, account=None)

    assert result.startswith("Successfully promoted")
    assert client.edits()[0].rank == ""


@pytest.mark.asyncio
async def test_explicit_rank_is_used_without_a_lookup(install):
    client = install(FakeClient(participant=_admin("Moderator")))

    await groups.promote_admin(CHANNEL_ID, USER_ID, rank="Bot", account=None)

    assert client.lookups() == []
    assert client.edits()[0].rank == "Bot"


@pytest.mark.asyncio
async def test_explicit_empty_rank_clears_it(install):
    client = install(FakeClient(participant=_admin("Moderator")))

    await groups.promote_admin(CHANNEL_ID, USER_ID, rank="", account=None)

    assert client.lookups() == []
    assert client.edits()[0].rank == ""


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
