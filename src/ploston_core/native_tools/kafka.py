"""Core Kafka implementation functions for MCP tools."""

import json
from typing import Any


async def publish_message_kafka(
    topic: str,
    message: str | dict[str, Any],
    bootstrap_servers: str,
    client_id: str,
    security_protocol: str,
    key: str | None = None,
    sasl_mechanism: str | None = None,
    sasl_username: str | None = None,
    sasl_password: str | None = None,
    timeout: int = 30,
    retry_attempts: int = 3,
) -> dict[str, Any]:
    """Core implementation of Kafka message publishing.

    Args:
        topic: Kafka topic name
        message: Message content (string or dict)
        bootstrap_servers: Kafka bootstrap servers
        client_id: Client ID for Kafka connection
        security_protocol: Security protocol (PLAINTEXT, SASL_PLAINTEXT, etc.)
        key: Optional message key for partitioning
        sasl_mechanism: SASL mechanism if using SASL
        sasl_username: SASL username if using SASL
        sasl_password: SASL password if using SASL
        timeout: Request timeout in seconds
        retry_attempts: Number of retry attempts

    Returns:
        Dictionary with publishing results
    """
    try:
        from kafka import KafkaProducer
    except ImportError:
        raise ImportError("Kafka library not available. Install with: pip install kafka-python")

    # Prepare message
    if isinstance(message, dict):
        message_bytes = json.dumps(message, default=str).encode("utf-8")
    elif isinstance(message, str):
        message_bytes = message.encode("utf-8")
    else:
        message_bytes = str(message).encode("utf-8")

    # Prepare key
    key_bytes = None
    if key:
        if isinstance(key, str):
            key_bytes = key.encode("utf-8")
        else:
            key_bytes = str(key).encode("utf-8")

    # Create producer with configurable settings
    producer_config = {
        "bootstrap_servers": bootstrap_servers,
        "client_id": client_id,
        "security_protocol": security_protocol,
        "request_timeout_ms": timeout * 1000,
        "retries": retry_attempts,
    }

    # Add SASL configuration if provided
    if sasl_mechanism:
        producer_config["sasl_mechanism"] = sasl_mechanism
    if sasl_username:
        producer_config["sasl_plain_username"] = sasl_username
    if sasl_password:
        producer_config["sasl_plain_password"] = sasl_password

    producer = KafkaProducer(**producer_config)

    # Send message
    future = producer.send(topic, value=message_bytes, key=key_bytes)
    record_metadata = future.get(timeout=timeout)

    producer.flush()
    producer.close()

    return {
        "success": True,
        "topic": topic,
        "partition": record_metadata.partition,
        "offset": record_metadata.offset,
        "timestamp": record_metadata.timestamp,
        "message_size": len(message_bytes),
        "key": key,
    }


async def list_topics_kafka(
    bootstrap_servers: str,
    client_id: str,
    security_protocol: str,
    sasl_mechanism: str | None = None,
    sasl_username: str | None = None,
    sasl_password: str | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Core implementation of Kafka topic listing.

    Args:
        bootstrap_servers: Kafka bootstrap servers
        client_id: Client ID for Kafka connection
        security_protocol: Security protocol
        sasl_mechanism: SASL mechanism if using SASL
        sasl_username: SASL username if using SASL
        sasl_password: SASL password if using SASL
        timeout: Request timeout in seconds

    Returns:
        Dictionary with topic list
    """
    try:
        from kafka import KafkaAdminClient
    except ImportError:
        raise ImportError("Kafka library not available. Install with: pip install kafka-python")

    # Create admin client
    admin_config = {
        "bootstrap_servers": bootstrap_servers,
        "client_id": client_id,
        "security_protocol": security_protocol,
        "request_timeout_ms": timeout * 1000,
    }

    # Add SASL configuration if provided
    if sasl_mechanism:
        admin_config["sasl_mechanism"] = sasl_mechanism
    if sasl_username:
        admin_config["sasl_plain_username"] = sasl_username
    if sasl_password:
        admin_config["sasl_plain_password"] = sasl_password

    admin_client = KafkaAdminClient(**admin_config)

    # List topics
    topics = admin_client.list_topics()
    admin_client.close()

    return {"success": True, "topics": sorted(list(topics)), "topic_count": len(topics)}


async def create_topic_kafka(
    topic: str,
    bootstrap_servers: str,
    client_id: str,
    security_protocol: str,
    num_partitions: int = 1,
    replication_factor: int = 1,
    sasl_mechanism: str | None = None,
    sasl_username: str | None = None,
    sasl_password: str | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Core implementation of Kafka topic creation.

    Args:
        topic: Topic name to create
        bootstrap_servers: Kafka bootstrap servers
        client_id: Client ID for Kafka connection
        security_protocol: Security protocol
        num_partitions: Number of partitions
        replication_factor: Replication factor
        sasl_mechanism: SASL mechanism if using SASL
        sasl_username: SASL username if using SASL
        sasl_password: SASL password if using SASL
        timeout: Request timeout in seconds

    Returns:
        Dictionary with creation results
    """
    try:
        from kafka import KafkaAdminClient
        from kafka.admin import NewTopic
    except ImportError:
        raise ImportError("Kafka library not available. Install with: pip install kafka-python")

    # Create admin client
    admin_config = {
        "bootstrap_servers": bootstrap_servers,
        "client_id": client_id,
        "security_protocol": security_protocol,
        "request_timeout_ms": timeout * 1000,
    }

    # Add SASL configuration if provided
    if sasl_mechanism:
        admin_config["sasl_mechanism"] = sasl_mechanism
    if sasl_username:
        admin_config["sasl_plain_username"] = sasl_username
    if sasl_password:
        admin_config["sasl_plain_password"] = sasl_password

    admin_client = KafkaAdminClient(**admin_config)

    # Create topic
    new_topic = NewTopic(
        name=topic, num_partitions=num_partitions, replication_factor=replication_factor
    )

    admin_client.create_topics([new_topic])
    admin_client.close()

    return {
        "success": True,
        "topic": topic,
        "num_partitions": num_partitions,
        "replication_factor": replication_factor,
    }


async def consume_messages_kafka(
    topic: str,
    bootstrap_servers: str,
    client_id: str,
    security_protocol: str,
    group_id: str = "mcp-consumer",
    max_messages: int = 10,
    sasl_mechanism: str | None = None,
    sasl_username: str | None = None,
    sasl_password: str | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Core implementation of Kafka message consumption.

    Args:
        topic: Topic name to consume from
        bootstrap_servers: Kafka bootstrap servers
        client_id: Client ID for Kafka connection
        security_protocol: Security protocol
        group_id: Consumer group ID
        max_messages: Maximum number of messages to consume
        sasl_mechanism: SASL mechanism if using SASL
        sasl_username: SASL username if using SASL
        sasl_password: SASL password if using SASL
        timeout: Request timeout in seconds

    Returns:
        Dictionary with consumed messages
    """
    try:
        from kafka import KafkaConsumer
    except ImportError:
        raise ImportError("Kafka library not available. Install with: pip install kafka-python")

    # Create consumer
    consumer_config = {
        "bootstrap_servers": bootstrap_servers,
        "client_id": client_id,
        "group_id": group_id,
        "security_protocol": security_protocol,
        "auto_offset_reset": "earliest",
        "enable_auto_commit": True,
        "consumer_timeout_ms": timeout * 1000,
    }

    # Add SASL configuration if provided
    if sasl_mechanism:
        consumer_config["sasl_mechanism"] = sasl_mechanism
    if sasl_username:
        consumer_config["sasl_plain_username"] = sasl_username
    if sasl_password:
        consumer_config["sasl_plain_password"] = sasl_password

    consumer = KafkaConsumer(topic, **consumer_config)

    # Consume messages
    messages = []
    for i, message in enumerate(consumer):
        if i >= max_messages:
            break

        # Try to decode as JSON, fallback to string
        try:
            value = json.loads(message.value.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            value = message.value.decode("utf-8")

        messages.append(
            {
                "partition": message.partition,
                "offset": message.offset,
                "timestamp": message.timestamp,
                "key": message.key.decode("utf-8") if message.key else None,
                "value": value,
            }
        )

    consumer.close()

    return {"success": True, "topic": topic, "messages": messages, "message_count": len(messages)}


async def check_health_kafka(
    bootstrap_servers: str,
    client_id: str,
    security_protocol: str,
    sasl_mechanism: str | None = None,
    sasl_username: str | None = None,
    sasl_password: str | None = None,
    timeout: int = 10,
) -> dict[str, Any]:
    """Core implementation of Kafka health check.

    Args:
        bootstrap_servers: Kafka bootstrap servers
        client_id: Client ID for Kafka connection
        security_protocol: Security protocol
        sasl_mechanism: SASL mechanism if using SASL
        sasl_username: SASL username if using SASL
        sasl_password: SASL password if using SASL
        timeout: Request timeout in seconds

    Returns:
        Dictionary with health status
    """
    try:
        from kafka import KafkaAdminClient
    except ImportError:
        raise ImportError("Kafka library not available. Install with: pip install kafka-python")

    # Create admin client
    admin_config = {
        "bootstrap_servers": bootstrap_servers,
        "client_id": client_id,
        "security_protocol": security_protocol,
        "request_timeout_ms": timeout * 1000,
    }

    # Add SASL configuration if provided
    if sasl_mechanism:
        admin_config["sasl_mechanism"] = sasl_mechanism
    if sasl_username:
        admin_config["sasl_plain_username"] = sasl_username
    if sasl_password:
        admin_config["sasl_plain_password"] = sasl_password

    admin_client = KafkaAdminClient(**admin_config)

    # List topics to verify connectivity
    topics = admin_client.list_topics()
    admin_client.close()

    return {
        "success": True,
        "status": "healthy",
        "bootstrap_servers": bootstrap_servers,
        "topic_count": len(topics),
    }
