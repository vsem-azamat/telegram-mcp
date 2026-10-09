import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from telegram_mcp import runtime
from telegram_mcp.tools import chats, media, messages


class _DownloadClient:
    def __init__(self, message, payload=b"", *, fail=False):
        self.message = message
        self.payload = payload
        self.fail = fail
        self.download_calls = []

    async def get_messages(self, entity, ids):
        return self.message

    async def download_media(self, message, file, progress_callback=None):
        self.download_calls.append(file)
        if progress_callback is not None:
            progress_callback(len(self.payload), len(self.payload))
        Path(file).write_bytes(self.payload)
        if self.fail:
            raise RuntimeError("transfer failed")
        return file


async def _resolve_entity(chat_id, client):
    return "entity"


@pytest.mark.asyncio
async def test_download_media_rejects_declared_oversize_before_transfer(monkeypatch):
    message = SimpleNamespace(
        media=SimpleNamespace(document=SimpleNamespace(size=11)),
        file=SimpleNamespace(size=11),
    )
    client = _DownloadClient(message)
    monkeypatch.setitem(runtime.MAX_FILE_BYTES, "download_media", 10)
    monkeypatch.setattr(media, "get_client", lambda account: client)
    monkeypatch.setattr(media, "resolve_entity", _resolve_entity)

    result = await media.download_media(chat_id=1, message_id=2)

    assert "too large" in result.lower()
    assert client.download_calls == []


@pytest.mark.asyncio
async def test_download_media_deletes_oversize_after_download(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    message = SimpleNamespace(media=SimpleNamespace(), file=None)
    client = _DownloadClient(message, payload=b"01234567890")
    monkeypatch.setitem(runtime.MAX_FILE_BYTES, "download_media", 10)
    monkeypatch.setattr(runtime, "SERVER_ALLOWED_ROOTS", [root])
    monkeypatch.setattr(media, "get_client", lambda account: client)
    monkeypatch.setattr(media, "resolve_entity", _resolve_entity)

    result = await media.download_media(chat_id=1, message_id=2, file_path="oversize.bin")

    assert "too large" in result.lower()
    assert not (root / "oversize.bin").exists()


@pytest.mark.asyncio
async def test_download_media_cleans_partial_file_on_failure(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    message = SimpleNamespace(media=SimpleNamespace(), file=None)
    client = _DownloadClient(message, payload=b"partial", fail=True)
    monkeypatch.setattr(runtime, "SERVER_ALLOWED_ROOTS", [root])
    monkeypatch.setattr(media, "get_client", lambda account: client)
    monkeypatch.setattr(media, "resolve_entity", _resolve_entity)

    result = await media.download_media(chat_id=1, message_id=2, file_path="partial.bin")

    assert "error occurred" in result.lower()
    assert not (root / "partial.bin").exists()


@pytest.mark.asyncio
async def test_download_media_cleans_extension_appended_partial_on_failure(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    message = SimpleNamespace(media=SimpleNamespace(), file=None)

    class ExtensionAppendingClient(_DownloadClient):
        async def download_media(self, message, file, progress_callback=None):
            Path(f"{file}.pdf").write_bytes(b"partial")
            raise RuntimeError("transfer failed")

    monkeypatch.setattr(runtime, "SERVER_ALLOWED_ROOTS", [root])
    monkeypatch.setattr(media, "get_client", lambda account: ExtensionAppendingClient(message))
    monkeypatch.setattr(media, "resolve_entity", _resolve_entity)

    result = await media.download_media(chat_id=1, message_id=2)

    assert "error occurred" in result.lower()
    assert not any(path.is_file() for path in root.rglob("*"))
    assert not any(path.name.startswith(".telegram-mcp-download-") for path in root.rglob("*"))


@pytest.mark.asyncio
async def test_download_media_preserves_detected_extension_on_success(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    message = SimpleNamespace(media=SimpleNamespace(), file=None)

    class ExtensionAppendingClient(_DownloadClient):
        async def download_media(self, message, file, progress_callback=None):
            actual = Path(f"{file}.pdf")
            actual.write_bytes(b"complete")
            return str(actual)

    monkeypatch.setattr(runtime, "SERVER_ALLOWED_ROOTS", [root])
    monkeypatch.setattr(media, "get_client", lambda account: ExtensionAppendingClient(message))
    monkeypatch.setattr(media, "resolve_entity", _resolve_entity)

    result = await media.download_media(chat_id=1, message_id=2)

    files = [path for path in root.rglob("*") if path.is_file()]
    assert len(files) == 1
    assert files[0].suffix == ".pdf"
    assert str(files[0]) in result
    assert not any(path.name.startswith(".telegram-mcp-download-") for path in root.rglob("*"))


@pytest.mark.asyncio
async def test_download_media_replaces_explicit_suffix_with_detected_suffix(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    message = SimpleNamespace(media=SimpleNamespace(), file=None)

    class ExtensionAppendingClient(_DownloadClient):
        async def download_media(self, message, file, progress_callback=None):
            actual = Path(f"{file}.pdf")
            actual.write_bytes(b"complete")
            return str(actual)

    monkeypatch.setattr(runtime, "SERVER_ALLOWED_ROOTS", [root])
    monkeypatch.setattr(media, "get_client", lambda account: ExtensionAppendingClient(message))
    monkeypatch.setattr(media, "resolve_entity", _resolve_entity)

    result = await media.download_media(
        chat_id=1,
        message_id=2,
        file_path=str(root / "requested.jpg"),
    )

    assert (root / "requested.pdf").read_bytes() == b"complete"
    assert not (root / "requested.jpg").exists()
    assert str(root / "requested.pdf") in result


@pytest.mark.asyncio
async def test_download_media_progress_limit_cleans_partial(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    message = SimpleNamespace(media=SimpleNamespace(), file=None)

    class ProgressClient(_DownloadClient):
        async def download_media(self, message, file, progress_callback=None):
            Path(file).write_bytes(b"partial")
            assert progress_callback is not None
            progress_callback(11, 11)

    monkeypatch.setitem(runtime.MAX_FILE_BYTES, "download_media", 10)
    monkeypatch.setattr(runtime, "SERVER_ALLOWED_ROOTS", [root])
    monkeypatch.setattr(media, "get_client", lambda account: ProgressClient(message))
    monkeypatch.setattr(media, "resolve_entity", _resolve_entity)

    result = await media.download_media(chat_id=1, message_id=2)

    assert "too large" in result.lower()
    assert not any(path.is_file() for path in root.rglob("*"))
    assert not any(path.name.startswith(".telegram-mcp-download-") for path in root.rglob("*"))


@pytest.mark.asyncio
async def test_upload_file_response_does_not_expose_source_path(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    source = root / "private-name.txt"
    source.write_text("payload")

    class Client:
        async def upload_file(self, file):
            assert file == str(source)
            return SimpleNamespace(name=source.name, size=7, md5_checksum=None)

    async def connected(client):
        return None

    monkeypatch.setattr(runtime, "SERVER_ALLOWED_ROOTS", [root])
    monkeypatch.setattr(media, "get_client", lambda account: Client())
    monkeypatch.setattr(media, "ensure_connected", connected)

    payload = json.loads(await media.upload_file(str(source)))

    assert "path" not in payload
    assert str(source) not in json.dumps(payload)
    assert payload["name"] == source.name


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module", "function", "kwargs", "secret"),
    [
        (media, media.get_gif_search, {"query": "gif-query-secret"}, "gif-query-secret"),
        (
            messages,
            messages.create_poll,
            {"chat_id": 1, "question": "poll-question-secret", "options": ["yes", "no"]},
            "poll-question-secret",
        ),
    ],
)
async def test_communication_error_logs_redact_user_content(
    monkeypatch, caplog, module, function, kwargs, secret
):
    def fail_get_client(account):
        raise RuntimeError("exception-payload-secret")

    monkeypatch.setattr(module, "get_client", fail_get_client)

    with caplog.at_level("ERROR", logger=runtime.logger.name):
        await function(**kwargs)

    assert secret not in caplog.text
    assert "exception-payload-secret" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("function", "kwargs"),
    [
        (
            messages.send_scheduled_message,
            {"chat_id": 1, "message": "scheduled-secret", "schedule_date": 1},
        ),
        (messages.get_scheduled_messages, {"chat_id": 1}),
        (messages.delete_scheduled_message, {"chat_id": 1, "message_ids": [2]}),
        (messages.get_pinned_messages, {"chat_id": 1}),
        (messages.send_reaction, {"chat_id": 1, "message_id": 2, "emoji": "👍"}),
        (messages.remove_reaction, {"chat_id": 1, "message_id": 2}),
        (messages.get_message_reactions, {"chat_id": 1, "message_id": 2}),
        (messages.save_draft, {"chat_id": 1, "message": "draft-secret"}),
        (messages.get_drafts, {}),
        (messages.clear_draft, {"chat_id": 1}),
    ],
)
async def test_allowed_message_tool_errors_never_log_exception_payload(
    monkeypatch, caplog, function, kwargs
):
    def fail_get_client(account):
        raise RuntimeError("exception-payload-secret")

    monkeypatch.setattr(messages, "get_client", fail_get_client)

    with caplog.at_level("ERROR", logger=runtime.logger.name):
        await function(**kwargs)

    assert "exception-payload-secret" not in caplog.text


@pytest.mark.asyncio
async def test_get_message_link_error_never_logs_exception_payload(monkeypatch, caplog):
    def fail_get_client(account):
        raise RuntimeError("exception-payload-secret")

    monkeypatch.setattr(chats, "get_client", fail_get_client)

    with caplog.at_level("ERROR", logger=runtime.logger.name):
        await chats.get_message_link(chat_id=1, message_id=2)

    assert "exception-payload-secret" not in caplog.text


@pytest.mark.asyncio
async def test_gif_fallback_error_never_returns_exception_payload(monkeypatch):
    class Client:
        calls = 0

        async def __call__(self, request):
            self.calls += 1
            if self.calls == 1:
                raise AttributeError("force fallback")
            raise RuntimeError("exception-payload-secret")

    async def connected(client):
        return None

    monkeypatch.setattr(
        media.functions.messages,
        "SearchGifsRequest",
        lambda **kwargs: "primary-request",
        raising=False,
    )
    monkeypatch.setattr(media, "get_client", lambda account: Client())
    monkeypatch.setattr(media, "ensure_connected", connected)

    result = await media.get_gif_search(query="gif-query-secret")

    assert "exception-payload-secret" not in result
    assert "gif-query-secret" not in result


@pytest.mark.asyncio
async def test_chat_best_effort_errors_never_log_exception_payload(monkeypatch, caplog):
    entity = chats.User(id=7, first_name="Safe")
    dialog = SimpleNamespace(
        entity=entity,
        unread_count=0,
        unread_mentions_count=0,
        archived=False,
        dialog=None,
    )

    class ListClient:
        async def get_dialogs(self, **kwargs):
            return [dialog]

        async def __call__(self, request):
            raise RuntimeError("exception-payload-secret")

    async def connected(client):
        return None

    monkeypatch.setattr(chats, "ensure_connected", connected)
    monkeypatch.setattr(chats, "get_client", lambda account: ListClient())
    with caplog.at_level("WARNING", logger=runtime.logger.name):
        await chats.list_chats(with_about=True)
    assert "exception-payload-secret" not in caplog.text

    async def resolved(chat_id, client):
        return entity

    class GetClient:
        async def get_input_entity(self, entity):
            raise RuntimeError("exception-payload-secret")

    caplog.clear()
    monkeypatch.setattr(chats, "resolve_entity", resolved)
    monkeypatch.setattr(chats, "get_client", lambda account: GetClient())
    with caplog.at_level("WARNING", logger=runtime.logger.name):
        await chats.get_chat(chat_id=7)
    assert "exception-payload-secret" not in caplog.text
