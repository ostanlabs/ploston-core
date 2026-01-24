"""Native tools implementations for Ploston workflows.

This module provides core tool implementations that can be exposed via MCP servers
or used directly in workflow steps. Tools are organized by category:

- filesystem: File read/write/list/delete operations
- network: HTTP requests, ping, DNS lookup, port checking
- data: JSON/CSV/XML transformations, schema validation
- extraction: Text extraction from various formats
- kafka: Kafka message publishing/consuming (requires kafka-python)
- ml: Text embeddings, similarity, classification (requires Ollama)
- firecrawl: Web scraping via Firecrawl API
"""

# Filesystem tools
# Data transformation tools
from .data import (
    transform_csv_to_json,
    transform_json_to_csv,
    transform_json_to_xml,
    transform_xml_to_json,
    validate_data_schema,
)

# Extraction tools
from .extraction import (
    extract_metadata,
    extract_structured_data,
    extract_text_content,
)
from .filesystem import (
    delete_file_or_directory,
    list_directory_content,
    read_file_content,
    write_file_content,
)

# Firecrawl tools (optional - requires Firecrawl API)
from .firecrawl import (
    check_health_firecrawl,
    extract_data_firecrawl,
    map_website_firecrawl,
    search_web_firecrawl,
)

# Health management
from .health import (
    DEPENDENCY_TOOLS,
    DependencyHealth,
    DependencyStatus,
    DependencyUnavailableError,
    HealthManager,
    OverallStatus,
    get_health_manager,
    reset_health_manager,
)

# Kafka tools (optional - requires kafka-python)
from .kafka import (
    check_health_kafka,
    consume_messages_kafka,
    create_topic_kafka,
    list_topics_kafka,
    publish_message_kafka,
)

# ML tools (optional - requires Ollama)
from .ml import (
    analyze_sentiment,
    calculate_text_similarity,
    classify_text,
    generate_text_embedding,
)

# Network tools
from .network import (
    check_port,
    dns_lookup,
    make_http_request,
    ping_host,
)

__all__ = [
    # Filesystem
    "read_file_content",
    "write_file_content",
    "list_directory_content",
    "delete_file_or_directory",
    # Network
    "make_http_request",
    "ping_host",
    "dns_lookup",
    "check_port",
    # Data
    "validate_data_schema",
    "transform_json_to_csv",
    "transform_csv_to_json",
    "transform_json_to_xml",
    "transform_xml_to_json",
    # Extraction
    "extract_text_content",
    "extract_structured_data",
    "extract_metadata",
    # Kafka
    "publish_message_kafka",
    "list_topics_kafka",
    "create_topic_kafka",
    "consume_messages_kafka",
    "check_health_kafka",
    # ML
    "generate_text_embedding",
    "calculate_text_similarity",
    "classify_text",
    "analyze_sentiment",
    # Firecrawl
    "search_web_firecrawl",
    "map_website_firecrawl",
    "extract_data_firecrawl",
    "check_health_firecrawl",
    # Health management
    "HealthManager",
    "DependencyHealth",
    "DependencyStatus",
    "DependencyUnavailableError",
    "OverallStatus",
    "DEPENDENCY_TOOLS",
    "get_health_manager",
    "reset_health_manager",
]
