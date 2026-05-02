"""ClickHouse-based telemetry store backend (M-082).

The submodules (``migrate``, ``store``, ``serialization``) are imported lazily
because they require ``clickhouse-connect``, which is an optional dependency
declared in the ``clickhouse`` extra.
"""

__all__ = ["ClickHouseTelemetryStore", "run_migrations"]


def __getattr__(name: str):
    if name == "run_migrations":
        from .migrate import run_migrations

        return run_migrations
    if name == "ClickHouseTelemetryStore":
        from .store import ClickHouseTelemetryStore

        return ClickHouseTelemetryStore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
