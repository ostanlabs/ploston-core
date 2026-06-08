"""Specification tests for ploston_core.native_tools.kafka.

These tests assert the intended contract of the Kafka native-tool client.
Only the external boundary -- the `kafka` client library -- is mocked
(via a fake module injected into sys.modules). The arg-wiring, message
encoding, and result-shaping logic under test runs for real.

The kafka-python library is NOT installed in this environment, so the
missing-library ImportError path is exercised against the real import.
"""

import json
import sys
import types
from typing import Any

import pytest

from ploston_core.native_tools import kafka as kafka_tool

# ---------------------------------------------------------------------------
# Fake kafka library installed into sys.modules
# ---------------------------------------------------------------------------


class _FakeRecordMetadata:
    def __init__(self, partition=0, offset=42, timestamp=1234567890):
        self.partition = partition
        self.offset = offset
        self.timestamp = timestamp


class _FakeFuture:
    def __init__(self, metadata):
        self._metadata = metadata

    def get(self, timeout=None):
        return self._metadata


class _FakeMessage:
    def __init__(self, value: bytes, key=None, partition=0, offset=0, timestamp=0):
        self.value = value
        self.key = key
        self.partition = partition
        self.offset = offset
        self.timestamp = timestamp


@pytest.fixture
def fake_kafka(monkeypatch):
    """Inject a fake `kafka` package recording constructor args and calls."""
    calls: dict[str, Any] = {
        "producer_config": None,
        "send_args": None,
        "admin_config": None,
        "consumer_topic": None,
        "consumer_config": None,
        "created_topics": None,
        "flushed": False,
        "producer_closed": False,
        "admin_closed": False,
        "consumer_closed": False,
    }

    state = {
        "list_topics_return": {"topic-b", "topic-a", "topic-c"},
        "consumer_messages": [],
    }

    class FakeKafkaProducer:
        def __init__(self, **config):
            calls["producer_config"] = config

        def send(self, topic, value=None, key=None):
            calls["send_args"] = {"topic": topic, "value": value, "key": key}
            return _FakeFuture(_FakeRecordMetadata())

        def flush(self):
            calls["flushed"] = True

        def close(self):
            calls["producer_closed"] = True

    class FakeKafkaAdminClient:
        def __init__(self, **config):
            calls["admin_config"] = config

        def list_topics(self):
            return state["list_topics_return"]

        def create_topics(self, new_topics):
            calls["created_topics"] = new_topics

        def close(self):
            calls["admin_closed"] = True

    class FakeKafkaConsumer:
        def __init__(self, topic, **config):
            calls["consumer_topic"] = topic
            calls["consumer_config"] = config

        def __iter__(self):
            return iter(state["consumer_messages"])

        def close(self):
            calls["consumer_closed"] = True

    class FakeNewTopic:
        def __init__(self, name, num_partitions, replication_factor):
            self.name = name
            self.num_partitions = num_partitions
            self.replication_factor = replication_factor

    kafka_mod = types.ModuleType("kafka")
    kafka_mod.KafkaProducer = FakeKafkaProducer
    kafka_mod.KafkaAdminClient = FakeKafkaAdminClient
    kafka_mod.KafkaConsumer = FakeKafkaConsumer

    admin_mod = types.ModuleType("kafka.admin")
    admin_mod.NewTopic = FakeNewTopic
    kafka_mod.admin = admin_mod

    monkeypatch.setitem(sys.modules, "kafka", kafka_mod)
    monkeypatch.setitem(sys.modules, "kafka.admin", admin_mod)

    return {"calls": calls, "state": state, "NewTopic": FakeNewTopic}


@pytest.fixture
def no_kafka(monkeypatch):
    """Ensure importing `kafka` raises ImportError."""
    monkeypatch.setitem(sys.modules, "kafka", None)
    monkeypatch.setitem(sys.modules, "kafka.admin", None)


# ---------------------------------------------------------------------------
# publish_message_kafka
# ---------------------------------------------------------------------------


async def test_publish_string_message_wiring(fake_kafka):
    calls = fake_kafka["calls"]

    result = await kafka_tool.publish_message_kafka(
        topic="events",
        message="hello",
        bootstrap_servers="broker:9092",
        client_id="cid",
        security_protocol="PLAINTEXT",
        key="k1",
        timeout=30,
        retry_attempts=5,
    )

    # Producer config wiring.
    cfg = calls["producer_config"]
    assert cfg["bootstrap_servers"] == "broker:9092"
    assert cfg["client_id"] == "cid"
    assert cfg["security_protocol"] == "PLAINTEXT"
    assert cfg["request_timeout_ms"] == 30 * 1000
    assert cfg["retries"] == 5

    # Message + key are utf-8 encoded.
    assert calls["send_args"]["topic"] == "events"
    assert calls["send_args"]["value"] == b"hello"
    assert calls["send_args"]["key"] == b"k1"

    # Lifecycle.
    assert calls["flushed"] is True
    assert calls["producer_closed"] is True

    # Result envelope.
    assert result["success"] is True
    assert result["topic"] == "events"
    assert result["partition"] == 0
    assert result["offset"] == 42
    assert result["message_size"] == len(b"hello")
    assert result["key"] == "k1"


async def test_publish_dict_message_is_json_encoded(fake_kafka):
    calls = fake_kafka["calls"]
    payload = {"b": 1, "a": 2}

    result = await kafka_tool.publish_message_kafka(
        topic="t",
        message=payload,
        bootstrap_servers="b:9092",
        client_id="c",
        security_protocol="PLAINTEXT",
    )

    sent = calls["send_args"]["value"]
    assert json.loads(sent.decode("utf-8")) == payload
    assert result["message_size"] == len(sent)
    assert calls["send_args"]["key"] is None


async def test_publish_sasl_config_applied(fake_kafka):
    calls = fake_kafka["calls"]

    await kafka_tool.publish_message_kafka(
        topic="t",
        message="m",
        bootstrap_servers="b:9092",
        client_id="c",
        security_protocol="SASL_PLAINTEXT",
        sasl_mechanism="PLAIN",
        sasl_username="user",
        sasl_password="pass",
    )

    cfg = calls["producer_config"]
    assert cfg["sasl_mechanism"] == "PLAIN"
    assert cfg["sasl_plain_username"] == "user"
    assert cfg["sasl_plain_password"] == "pass"


async def test_publish_missing_library_raises_importerror(no_kafka):
    with pytest.raises(ImportError):
        await kafka_tool.publish_message_kafka(
            topic="t",
            message="m",
            bootstrap_servers="b",
            client_id="c",
            security_protocol="PLAINTEXT",
        )


# ---------------------------------------------------------------------------
# list_topics_kafka
# ---------------------------------------------------------------------------


async def test_list_topics_sorted_and_counted(fake_kafka):
    result = await kafka_tool.list_topics_kafka(
        bootstrap_servers="b:9092", client_id="c", security_protocol="PLAINTEXT"
    )
    assert result["success"] is True
    assert result["topics"] == ["topic-a", "topic-b", "topic-c"]
    assert result["topic_count"] == 3
    assert fake_kafka["calls"]["admin_closed"] is True


async def test_list_topics_missing_library_raises(no_kafka):
    with pytest.raises(ImportError):
        await kafka_tool.list_topics_kafka(
            bootstrap_servers="b", client_id="c", security_protocol="PLAINTEXT"
        )


# ---------------------------------------------------------------------------
# create_topic_kafka
# ---------------------------------------------------------------------------


async def test_create_topic_wiring(fake_kafka):
    calls = fake_kafka["calls"]

    result = await kafka_tool.create_topic_kafka(
        topic="new-topic",
        bootstrap_servers="b:9092",
        client_id="c",
        security_protocol="PLAINTEXT",
        num_partitions=3,
        replication_factor=2,
    )

    created = calls["created_topics"]
    assert len(created) == 1
    nt = created[0]
    assert nt.name == "new-topic"
    assert nt.num_partitions == 3
    assert nt.replication_factor == 2

    assert result["success"] is True
    assert result["topic"] == "new-topic"
    assert result["num_partitions"] == 3
    assert result["replication_factor"] == 2
    assert calls["admin_closed"] is True


async def test_create_topic_missing_library_raises(no_kafka):
    with pytest.raises(ImportError):
        await kafka_tool.create_topic_kafka(
            topic="t", bootstrap_servers="b", client_id="c", security_protocol="PLAINTEXT"
        )


# ---------------------------------------------------------------------------
# consume_messages_kafka
# ---------------------------------------------------------------------------


async def test_consume_decodes_json_and_string(fake_kafka):
    fake_kafka["state"]["consumer_messages"] = [
        _FakeMessage(value=b'{"a": 1}', key=b"k1", partition=0, offset=10, timestamp=111),
        _FakeMessage(value=b"plain text", key=None, partition=1, offset=11, timestamp=222),
    ]

    result = await kafka_tool.consume_messages_kafka(
        topic="t",
        bootstrap_servers="b:9092",
        client_id="c",
        security_protocol="PLAINTEXT",
        max_messages=10,
        group_id="grp",
    )

    assert result["success"] is True
    assert result["topic"] == "t"
    assert result["message_count"] == 2

    m0, m1 = result["messages"]
    assert m0["value"] == {"a": 1}  # JSON decoded
    assert m0["key"] == "k1"
    assert m0["offset"] == 10
    assert m1["value"] == "plain text"  # fallback to string
    assert m1["key"] is None

    assert fake_kafka["calls"]["consumer_config"]["group_id"] == "grp"
    assert fake_kafka["calls"]["consumer_closed"] is True


async def test_consume_respects_max_messages(fake_kafka):
    fake_kafka["state"]["consumer_messages"] = [
        _FakeMessage(value=b"%d" % i, partition=0, offset=i, timestamp=i) for i in range(10)
    ]

    result = await kafka_tool.consume_messages_kafka(
        topic="t",
        bootstrap_servers="b:9092",
        client_id="c",
        security_protocol="PLAINTEXT",
        max_messages=3,
    )

    # Contract: "max_messages: Maximum number of messages to consume".
    # No more than max_messages should be returned.
    assert result["message_count"] <= 3, (
        f"consume returned {result['message_count']} messages for max_messages=3"
    )


async def test_consume_missing_library_raises(no_kafka):
    with pytest.raises(ImportError):
        await kafka_tool.consume_messages_kafka(
            topic="t", bootstrap_servers="b", client_id="c", security_protocol="PLAINTEXT"
        )


# ---------------------------------------------------------------------------
# check_health_kafka
# ---------------------------------------------------------------------------


async def test_health_reports_healthy(fake_kafka):
    result = await kafka_tool.check_health_kafka(
        bootstrap_servers="broker:9092", client_id="c", security_protocol="PLAINTEXT"
    )
    assert result["success"] is True
    assert result["status"] == "healthy"
    assert result["bootstrap_servers"] == "broker:9092"
    assert result["topic_count"] == 3
    assert fake_kafka["calls"]["admin_closed"] is True


async def test_health_missing_library_raises(no_kafka):
    with pytest.raises(ImportError):
        await kafka_tool.check_health_kafka(
            bootstrap_servers="b", client_id="c", security_protocol="PLAINTEXT"
        )
