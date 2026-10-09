"""Regression tests for local hardening profiles on the upstream runtime."""

import asyncio
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp import FastMCP

from telegram_mcp import runtime

EXPECTED_COMMUNICATION_TOOLS = {
    "list_chats",
    "get_chat",
    "list_topics",
    "get_full_chat",
    "get_message_link",
    "list_contacts",
    "search_contacts",
    "get_direct_chat_by_contact",
    "get_last_interaction",
    "wait_for_new_message",
    "wait_for_settled_message",
    "get_participants",
    "send_file",
    "send_album",
    "download_media",
    "send_voice",
    "transcribe_voice",
    "get_media_info",
    "get_sticker_sets",
    "send_sticker",
    "get_gif_search",
    "send_gif",
    "get_messages",
    "send_message",
    "send_scheduled_message",
    "get_scheduled_messages",
    "delete_scheduled_message",
    "list_inline_buttons",
    "list_messages",
    "get_message_context",
    "forward_message",
    "forward_messages",
    "edit_message",
    "delete_message",
    "mark_as_read",
    "reply_to_message",
    "search_messages",
    "search_global",
    "get_history",
    "get_pinned_messages",
    "pin_message",
    "unpin_message",
    "create_poll",
    "send_reaction",
    "remove_reaction",
    "get_message_reactions",
    "save_draft",
    "get_drafts",
    "clear_draft",
    "get_me",
    "get_full_user",
    "get_user_status",
}

NETWORK_ADMIN_ADDITIONS = {
    "get_admins",
    "invite_to_group",
    "promote_admin",
    "edit_admin_rights",
    "edit_chat_about",
    "get_member_admin_status",
}


def _tool_names(server):
    return {tool.name for tool in server._tool_manager.list_tools()}


def _server_with_tools(names):
    server = FastMCP("profile-contract")
    for name in sorted(names):
        server.tool(name=name)(lambda: None)
    return server


@pytest.mark.parametrize("mode", ["communication+send_message", "network-admin+ban_user"])
def test_exact_profiles_reject_allowlist_suffixes(mode):
    with pytest.raises(SystemExit):
        runtime._get_exposed_tools_mode(mode)


def test_error_log_redacts_content_paths_and_exception_payload(caplog):
    with caplog.at_level("ERROR", logger=runtime.logger.name):
        runtime.log_and_format_error(
            "send_message",
            RuntimeError("exception-secret"),
            chat_id=123,
            message="message-secret",
            file_path="/private/path-secret.txt",
        )

    assert "Telegram MCP operation failed" in caplog.text
    assert "send_message" not in caplog.text
    assert "exception-secret" not in caplog.text
    assert "message-secret" not in caplog.text
    assert "path-secret" not in caplog.text


def test_flood_wait_log_redacts_all_caller_and_provider_payloads(caplog):
    error = runtime.FloodWaitError(request=None, capture=45)
    error.args = ("exception-provider-payload-secret",)

    with caplog.at_level("WARNING", logger=runtime.logger.name):
        result = runtime.log_and_format_error(
            "transcribe_voice",
            error,
            transcript="transcript-secret",
            provider_payload={"text": "provider-secret"},
            file_path="/private/audio-secret.ogg",
        )

    assert "45 seconds" in result
    assert "Telegram FloodWait" in caplog.text
    assert "transcribe_voice" not in caplog.text
    for secret in (
        "exception-provider-payload-secret",
        "transcript-secret",
        "provider-secret",
        "audio-secret",
    ):
        assert secret not in caplog.text
