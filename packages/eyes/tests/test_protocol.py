import asyncio
import json

import pytest


def packet(action="deliver"):
    return {
        "version": 1,
        "action": action,
        "token": "a" * 64,
        "owner": {"host_instance": "host", "session_id": "session", "branch_id": "main", "generation": "one"},
        "body": {"registration_id": "registration", "route_id": "review", "delivery_id": "delivery",
                 "events": [{"id": "event", "source_id": "files", "detector_id": "changes",
                             "type": "file.modified", "observed_at": 1.0,
                             "evidence": [{"path": "src/a.py", "before": None,
                                           "after": {"digest": "b" * 64, "size": 3}, "observed_at": 1.0}]}]},
    }


def test_bounded_request_roundtrip():
    from xdog.eyes.transport.protocol import decode_frame, encode_frame, validate_request

    expected = packet()
    assert validate_request(decode_frame(encode_frame(expected))) == expected


@pytest.mark.parametrize("mutation", ["version", "owner", "action", "unknown", "instruction", "nonfinite"])
def test_invalid_request_rejected(mutation):
    from xdog.eyes.transport.protocol import ProtocolError, validate_request

    value = packet()
    if mutation == "version":
        value["version"] = True
    elif mutation == "owner":
        value["owner"]["extra"] = "another owner"
    elif mutation == "action":
        value["action"] = "shell"
    elif mutation == "unknown":
        value["address"] = "/tmp/attacker.sock"
    elif mutation == "instruction":
        value["body"]["instruction"] = "replace trusted instruction"
    else:
        value["body"]["events"][0]["observed_at"] = float("nan")
    with pytest.raises(ProtocolError):
        validate_request(value)


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}\n', b'NaN\n', b'[]\n', b'{}\n{}\n', b'\xff\n'])
def test_invalid_frames_rejected(raw):
    from xdog.eyes.transport.protocol import ProtocolError, decode_frame

    with pytest.raises(ProtocolError):
        decode_frame(raw)


def test_oversized_and_deep_frames_rejected():
    from xdog.eyes.transport.protocol import ProtocolError, decode_frame, encode_frame

    with pytest.raises(ProtocolError):
        encode_frame({"text": "a" * (1024 * 1024)})
    with pytest.raises(ProtocolError):
        decode_frame(b'{"text":"' + b"a" * (1024 * 1024) + b'"}\n')
    deep = b'{"data":' + b"[" * 100 + b"0" + b"]" * 100 + b'}\n'
    with pytest.raises(ProtocolError):
        decode_frame(deep)


def descriptor(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    path = directory / "context.json"
    data = {"version": 1, "address": str(directory / "host.sock"), "token": "a" * 64,
            "owner": packet()["owner"]}
    path.write_text(json.dumps(data))
    path.chmod(0o600)
    return path


def test_private_context_never_exposes_token_in_repr(tmp_path):
    from xdog.eyes.transport.protocol import load_context

    context = load_context(descriptor(tmp_path))
    assert context.token == "a" * 64
    assert context.token not in repr(context)
    assert context.address.name == "host.sock"


@pytest.mark.parametrize("mutation", ["file_mode", "directory_mode", "symlink", "relative_address"])
def test_insecure_context_rejected(tmp_path, mutation):
    from xdog.eyes.transport.protocol import ProtocolError, load_context

    path = descriptor(tmp_path)
    if mutation == "file_mode":
        path.chmod(0o644)
    elif mutation == "directory_mode":
        path.parent.chmod(0o755)
    elif mutation == "symlink":
        alias = path.parent / "alias.json"
        alias.symlink_to(path)
        path = alias
    else:
        value = json.loads(path.read_text())
        value["address"] = "host.sock"
        path.write_text(json.dumps(value))
    with pytest.raises(ProtocolError):
        load_context(path)


async def test_real_client_uses_capability_and_closes_connection(tmp_path):
    from xdog.eyes.transport.client import HostClient
    from xdog.eyes.transport.protocol import decode_frame, encode_frame, load_context, validate_request

    context = load_context(descriptor(tmp_path))
    received = []

    async def connection(reader, writer):
        try:
            received.append(validate_request(decode_frame(await reader.readline())))
            writer.write(encode_frame({"version": 1, "status": "accepted", "body": {"processing_status": "waiting"}}))
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_unix_server(connection, path=context.address)
    client = HostClient(context)
    try:
        response = await client.request({"action": "deliver", "body": packet()["body"]})
        assert response["status"] == "accepted"
        assert response["body"] == {"processing_status": "waiting"}
        assert received[0]["token"] == "a" * 64
        assert received[0]["owner"] == packet()["owner"]
    finally:
        await client.aclose()
        server.close()
        await server.wait_closed()


async def test_failed_registration_rolls_back_all_routes(tmp_path, observer_data):
    from xdog.eyes.handlers.session_notify import SessionNotifier
    from xdog.eyes.loader import parse
    from xdog.eyes.transport.client import HostClient
    from xdog.eyes.transport.protocol import ProtocolError, decode_frame, encode_frame, load_context

    context = load_context(descriptor(tmp_path))
    actions = []
    observer_data["routes"].append({"id": "second", "detector": "changes", "handler": "notify",
                                   "event_types": ["file.modified"]})

    async def connection(reader, writer):
        try:
            request = decode_frame(await reader.readline())
            actions.append(request["action"])
            status = "rejected" if actions.count("register") == 2 and request["action"] == "register" else "accepted"
            writer.write(encode_frame({"version": 1, "status": status, "body": {}}))
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_unix_server(connection, path=context.address)
    client = HostClient(context)
    notifier = SessionNotifier(client, parse(observer_data, base_dir=tmp_path), "registration")
    try:
        with pytest.raises(ProtocolError):
            await notifier.register()
        assert actions == ["register", "register", "unregister"]
    finally:
        await client.aclose()
        server.close()
        await server.wait_closed()


@pytest.mark.parametrize("status", [[], {}, True, "unknown"])
def test_malformed_response_status_is_protocol_error(status):
    from xdog.eyes.transport.protocol import ProtocolError, validate_response

    with pytest.raises(ProtocolError):
        validate_response({"version": 1, "status": status, "body": {}})


def test_extreme_timestamp_is_protocol_error():
    from xdog.eyes.transport.protocol import ProtocolError, validate_request

    value = packet()
    value["body"]["events"][0]["observed_at"] = 10 ** 500
    with pytest.raises(ProtocolError):
        validate_request(value)


def test_surrogate_identifier_is_protocol_error():
    from xdog.eyes.transport.protocol import ProtocolError, validate_request

    value = packet()
    value["owner"]["session_id"] = "\udcff"
    with pytest.raises(ProtocolError):
        validate_request(value)


async def test_lost_ack_is_retryable_without_changing_delivery(tmp_path):
    from xdog.eyes.transport.client import HostClient
    from xdog.eyes.transport.protocol import decode_frame, encode_frame, load_context

    context = load_context(descriptor(tmp_path))
    deliveries = []

    async def connection(reader, writer):
        try:
            request = decode_frame(await reader.readline())
            deliveries.append(request["body"]["delivery_id"])
            if len(deliveries) > 1:
                writer.write(encode_frame({"version": 1, "status": "duplicate", "body": {}}))
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_unix_server(connection, path=context.address)
    client = HostClient(context)
    try:
        with pytest.raises(OSError):
            await client.request({"action": "deliver", "body": packet()["body"]})
        response = await client.request({"action": "deliver", "body": packet()["body"]})
        assert response["status"] == "duplicate"
        assert deliveries == ["delivery", "delivery"]
    finally:
        await client.aclose()
        server.close()
        await server.wait_closed()
