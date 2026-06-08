"""REST API configuration models."""

from dataclasses import dataclass, field


@dataclass
class APIKeyConfig:
    """API key configuration."""

    name: str
    key: str
    scopes: list[str] = field(default_factory=lambda: ["read", "write", "execute"])


@dataclass
class RESTConfig:
    """REST API configuration."""

    # Server
    host: str = "0.0.0.0"
    port: int = 8022

    # API
    prefix: str = "/api/v1"
    title: str = "AEL REST API"
    version: str = "1.0.0"

    # Documentation
    docs_enabled: bool = True
    docs_path: str = "/docs"
    redoc_path: str = "/redoc"
    openapi_path: str = "/openapi.json"

    # Security
    require_auth: bool = False
    api_keys: list[APIKeyConfig] = field(default_factory=list)

    # Rate limiting
    rate_limiting_enabled: bool = False
    requests_per_minute: int = 100
    # Hosts whose X-Forwarded-For header is trusted for client identification.
    # Empty by default (strict): the direct connection IP is always used unless
    # the immediate client is one of these proxies. Prevents XFF spoofing.
    trusted_proxies: list[str] = field(default_factory=list)

    # CORS
    cors_enabled: bool = True
    cors_origins: list[str] = field(default_factory=lambda: ["*"])
