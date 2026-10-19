"""Tests for promote_admin: rights presets, the caller's own rights, chat kinds and rank."""

import inspect
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp.utilities.func_metadata import func_metadata
from pydantic import ValidationError
from telethon.errors.rpcerrorlist import (
    ChatAdminRequiredError,
    RightForbiddenError,
    UserNotParticipantError,
)
from telethon.tl import functions, types
from telethon.tl.types import ChatAdminRights

from telegram_mcp.tools import groups

CHANNEL_ID = -1001234567890
BROADCAST_ID = -1009876543210
USER_ID = 7

LAYER_RIGHTS = [name for name in inspect.signature(ChatAdminRights.__init__).parameters][1:]
CHANNEL_ONLY = {"post_messages", "edit_messages", "manage_direct_messages"}
GROUP_ONLY = {"pin_messages", "manage_topics", "manage_ranks"}
DEFAULT_OFF = {"add_admins", "anonymous"}
# The field report: an admin without topics, welcome messages, linked peers and DMs.
FIELD_LACKS = {"manage_topics", "manage_welcome_messages", "manage_linked_peers"}


def _channel(megagroup=True):
    return types.Channel(
        id=1234567890 if megagroup else 9876543210,
        title="Supergroup" if megagroup else "Channel",
        photo=types.ChatPhotoEmpty(),
        date=None,
        megagroup=megagroup,
        broadcast=not megagroup,
    )


def _rights(names):
    return ChatAdminRights(**{name: True for name in names})


# Telegram reports the creator's own flags; a creator who is not anonymous lacks that one
# but can still grant it.
CREATOR = types.ChannelParticipantCreator(
    user_id=1, admin_rights=_rights(set(LAYER_RIGHTS) - {"anonymous"})
)


def _caller_admin(lacks=(), add_admins=True):
    held = set(LAYER_RIGHTS) - set(lacks) - {"anonymous"}
    if not add_admins:
        held.discard("add_admins")
    return types.ChannelParticipantAdmin(
        user_id=1, promoted_by=99, date=None, admin_rights=_rights(held), is_self=True
    )


class FakeClient:
    """Answers the caller's own GetParticipant with `me`, records every request."""

    def __init__(self, me=CREATOR, me_error=None, edit_error=None):
        self.me = me
        self.me_error = me_error
        self.edit_error = edit_error
        self.requests = []

    async def __call__(self, request):
        self.requests.append(request)
        if isinstance(request, functions.channels.GetParticipantRequest):
            assert isinstance(request.participant, types.InputPeerSelf)
            if self.me_error:
                raise self.me_error
            return SimpleNamespace(participant=self.me)
        if self.edit_error:
            raise self.edit_error
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
            if identifier == BROADCAST_ID:
                return _channel(megagroup=False)
            return SimpleNamespace(id=identifier)

        monkeypatch.setattr(groups, "get_client", lambda account=None: client)
        monkeypatch.setattr(groups, "resolve_entity", _resolve)
        return client

    return _install


def _granted(request):
    return {name for name in LAYER_RIGHTS if getattr(request.admin_rights, name)}


def test_known_rights_are_every_flag_of_the_installed_layer():
    # A Telethon layer bump that adds or removes a right must fail here.
    assert list(groups.ADMIN_RIGHTS) == LAYER_RIGHTS


def test_docstring_names_every_right():
    doc = groups.promote_admin.__doc__
    assert [name for name in LAYER_RIGHTS if name not in doc] == []


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
async def test_omitted_rank_leaves_the_rank_unchanged(install):
    # channels.editAdmin leaves the rank (an admin's title or a member's tag) alone
    # when the flag is unset, which Telethon does only for rank=None.
    client = install(FakeClient())

    result = await groups.promote_admin(CHANNEL_ID, USER_ID, account=None)

    assert result.startswith("Successfully promoted")
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


# --- rights the caller can grant ---------------------------------------------


@pytest.mark.asyncio
async def test_creator_in_a_supergroup_skips_channel_only_rights(install):
    client = install(FakeClient())

    result = await groups.promote_admin(CHANNEL_ID, USER_ID, account=None)

    (lookup,) = client.lookups()
    assert isinstance(lookup.participant, types.InputPeerSelf)
    (edit,) = client.edits()
    assert _granted(edit) == set(LAYER_RIGHTS) - DEFAULT_OFF - CHANNEL_ONLY
    assert "channels only" in result
    for name in CHANNEL_ONLY:
        assert name in result


@pytest.mark.asyncio
async def test_creator_in_a_channel_skips_group_only_rights(install):
    client = install(FakeClient())

    result = await groups.promote_admin(BROADCAST_ID, USER_ID, account=None)

    (edit,) = client.edits()
    assert _granted(edit) == set(LAYER_RIGHTS) - DEFAULT_OFF - GROUP_ONLY
    assert "groups and supergroups only" in result


@pytest.mark.asyncio
async def test_preset_all_by_the_creator_includes_add_admins_and_anonymous(install):
    client = install(FakeClient())

    await groups.promote_admin(CHANNEL_ID, USER_ID, preset="all", account=None)

    assert _granted(client.edits()[0]) == set(LAYER_RIGHTS) - CHANNEL_ONLY


@pytest.mark.asyncio
async def test_rights_override_the_preset(install):
    client = install(FakeClient())

    await groups.promote_admin(
        CHANNEL_ID,
        USER_ID,
        rights={"anonymous": True, "post_stories": False},
        account=None,
    )

    granted = _granted(client.edits()[0])
    assert "anonymous" in granted and "post_stories" not in granted
    assert "add_admins" not in granted and "manage_ranks" in granted


@pytest.mark.asyncio
async def test_admin_caller_grants_only_the_rights_they_hold(install):
    client = install(FakeClient(me=_caller_admin(lacks=FIELD_LACKS)))

    result = await groups.promote_admin(CHANNEL_ID, USER_ID, account=None)

    (edit,) = client.edits()
    assert _granted(edit) == set(LAYER_RIGHTS) - DEFAULT_OFF - CHANNEL_ONLY - FIELD_LACKS
    assert result.startswith("Successfully promoted")
    granted_part, skipped_part = result.split("Skipped", 1)
    assert "post_stories" in granted_part and "manage_ranks" in granted_part
    assert "you don't have" in skipped_part
    for name in FIELD_LACKS | CHANNEL_ONLY:
        assert name in skipped_part
    assert "post_stories" not in skipped_part


@pytest.mark.asyncio
async def test_admin_caller_without_add_admins_is_refused(install):
    client = install(FakeClient(me=_caller_admin(add_admins=False)))

    result = await groups.promote_admin(CHANNEL_ID, USER_ID, account=None)

    assert "add_admins" in result
    assert client.edits() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "client",
    [
        FakeClient(me=types.ChannelParticipant(user_id=1, date=None)),
        FakeClient(me_error=UserNotParticipantError(None)),
        FakeClient(me_error=ChatAdminRequiredError(None)),
    ],
)
async def test_caller_who_is_not_an_admin_is_refused(install, client):
    install(client)

    result = await groups.promote_admin(CHANNEL_ID, USER_ID, account=None)

    assert "not an admin" in result
    assert client.edits() == []


@pytest.mark.asyncio
async def test_explicit_rights_the_caller_lacks_are_refused_by_name(install):
    client = install(FakeClient(me=_caller_admin(lacks=FIELD_LACKS)))

    result = await groups.promote_admin(
        CHANNEL_ID,
        USER_ID,
        rights={"manage_topics": True, "post_messages": True, "ban_users": True},
        account=None,
    )

    assert client.edits() == []
    assert "manage_topics" in result and "post_messages" in result
    assert "ban_users" not in result
    assert "manage_welcome_messages" not in result  # not asked for explicitly


@pytest.mark.asyncio
async def test_explicit_false_for_a_right_the_caller_lacks_is_fine(install):
    client = install(FakeClient(me=_caller_admin(lacks=FIELD_LACKS)))

    result = await groups.promote_admin(
        CHANNEL_ID, USER_ID, rights={"manage_topics": False}, account=None
    )

    assert result.startswith("Successfully promoted")
    assert "manage_topics" not in _granted(client.edits()[0])


@pytest.mark.asyncio
async def test_nothing_left_to_grant_is_refused_instead_of_demoting(install):
    only = types.ChannelParticipantAdmin(
        user_id=1, promoted_by=99, date=None, admin_rights=_rights(["add_admins"])
    )
    client = install(FakeClient(me=only))

    result = await groups.promote_admin(CHANNEL_ID, USER_ID, account=None)

    assert "nothing" in result.lower()
    assert "admin with no rights" in result and 'rights={"other": true}' in result
    assert "demote" not in result
    assert client.edits() == []


@pytest.mark.asyncio
async def test_creator_with_every_right_false_but_other_gets_the_no_rights_hint(install):
    client = install(FakeClient())
    rights = {name: False for name in LAYER_RIGHTS if name != "other"}

    result = await groups.promote_admin(CHANNEL_ID, USER_ID, rights=rights, account=None)

    assert "admin with no rights" in result and "demote" not in result
    assert client.edits() == []


@pytest.mark.asyncio
async def test_other_named_in_rights_makes_an_admin_without_rights(install):
    only = types.ChannelParticipantAdmin(
        user_id=1, promoted_by=99, date=None, admin_rights=_rights(["add_admins"])
    )
    client = install(FakeClient(me=only))

    result = await groups.promote_admin(CHANNEL_ID, USER_ID, rights={"other": True}, account=None)

    assert result.startswith("Successfully promoted")
    (edit,) = client.edits()
    assert _granted(edit) == {"other"}


@pytest.mark.asyncio
async def test_failed_self_lookup_falls_back_to_the_preset(install):
    client = install(FakeClient(me_error=TimeoutError()))

    result = await groups.promote_admin(CHANNEL_ID, USER_ID, account=None)

    (edit,) = client.edits()
    assert _granted(edit) == set(LAYER_RIGHTS) - DEFAULT_OFF - CHANNEL_ONLY
    assert result.startswith("Successfully promoted")
    assert "could not check your own rights" in result
    assert "as is" not in result


@pytest.mark.asyncio
async def test_flood_wait_on_the_self_lookup_is_reported_and_nothing_is_sent(install):
    from telethon.errors import FloodWaitError

    client = install(FakeClient(me_error=FloodWaitError(None, capture=30)))

    result = await groups.promote_admin(CHANNEL_ID, USER_ID, account=None)

    assert "FloodWait" in result and "30 seconds" in result
    assert client.edits() == []


@pytest.mark.asyncio
async def test_right_forbidden_with_unknown_own_rights_names_no_list_tail(install):
    client = install(FakeClient(me_error=TimeoutError(), edit_error=RightForbiddenError(None)))

    result = await groups.promote_admin(CHANNEL_ID, USER_ID, account=None)

    assert len(client.edits()) == 1
    assert "error occurred" not in result
    assert "could not check your own rights" in result
    requested = result.split("Requested: ", 1)[1].split(". ", 1)[0].split(", ")
    assert set(requested) == set(LAYER_RIGHTS) - DEFAULT_OFF - CHANNEL_ONLY
    assert '"manage_linked_peers": false' not in result  # no guessed example
    assert '"<right>": false' in result


@pytest.mark.asyncio
async def test_right_forbidden_with_known_own_rights_says_they_are_held(install):
    client = install(
        FakeClient(me=_caller_admin(lacks=FIELD_LACKS), edit_error=RightForbiddenError(None))
    )

    result = await groups.promote_admin(CHANNEL_ID, USER_ID, account=None)

    assert len(client.edits()) == 1
    assert "you hold all of them" in result
    for name in FIELD_LACKS:
        assert name in result.split("Requested: ", 1)[0]  # named as skipped, not requested


@pytest.mark.asyncio
async def test_creator_who_is_not_anonymous_can_grant_anonymous(install):
    client = install(FakeClient())

    result = await groups.promote_admin(
        CHANNEL_ID, USER_ID, rights={"anonymous": True}, account=None
    )

    assert result.startswith("Successfully promoted")
    assert "anonymous" in _granted(client.edits()[0])


@pytest.mark.asyncio
async def test_all_rights_false_is_refused_without_an_empty_skip_clause(install):
    client = install(FakeClient())

    result = await groups.promote_admin(
        CHANNEL_ID, USER_ID, rights={name: False for name in LAYER_RIGHTS}, account=None
    )

    assert "nothing" in result.lower()
    assert "skipping" not in result and ";" not in result
    assert "demote" in result
    assert client.edits() == []


@pytest.mark.asyncio
async def test_other_is_implied_by_any_right_the_caller_holds(install):
    me = types.ChannelParticipantAdmin(
        user_id=1,
        promoted_by=99,
        date=None,
        admin_rights=_rights(["add_admins", "ban_users"]),  # Telegram may leave other unset
    )
    client = install(FakeClient(me=me))

    result = await groups.promote_admin(CHANNEL_ID, USER_ID, account=None)

    assert _granted(client.edits()[0]) == {"ban_users", "other"}
    assert "other (you don't have it)" not in result


@pytest.mark.asyncio
async def test_lookup_failure_says_what_was_sent(install):
    client = install(FakeClient(me_error=TimeoutError()))

    result = await groups.promote_admin(
        CHANNEL_ID, USER_ID, rights={"post_stories": False}, account=None
    )

    assert "post_stories" not in _granted(client.edits()[0])
    assert "not checked against your own" in result


@pytest.mark.asyncio
async def test_right_forbidden_mentions_rights_set_by_someone_else(install):
    install(FakeClient(edit_error=RightForbiddenError(None)))

    result = await groups.promote_admin(CHANNEL_ID, USER_ID, account=None)

    assert "set by someone else" in result


def test_docstring_says_the_whole_rights_set_is_replaced():
    doc = " ".join(groups.promote_admin.__doc__.split())
    assert "replaces the user's whole set of admin rights" in doc
    assert "other counts as held whenever you hold any right" in doc
    assert "other alone is granted only when named in rights" in doc
    assert "removed" in doc
    assert "as is" not in doc
