"""AEL Telemetry Logging - Structured logging with OTEL integration.

Provides structured JSON logging with automatic trace context injection.
Logs can be exported to Loki via OTLP when configured.

Usage:
    from ploston_core.telemetry.logging import get_logger
    
    logger = get_logger("workflow_engine")
    logger.info("Workflow started", workflow_id="wf-123")
"""

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from opentelemetry import trace


class StructuredLogFormatter(logging.Formatter):
    """JSON formatter with trace context injection.
    
    Formats log records as JSON with:
    - timestamp (ISO 8601)
    - level
    - component (logger name)
    - message
    - trace_id (if available)
    - span_id (if available)
    - Additional fields from extra
    """
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON.
        
        Args:
            record: Log record to format
            
        Returns:
            JSON-formatted log string
        """
        log_data: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "component": record.name,
            "message": record.getMessage(),
        }
        
        # Add trace context if available
        span = trace.get_current_span()
        if span and span.is_recording():
            ctx = span.get_span_context()
            if ctx.is_valid:
                log_data["trace_id"] = format(ctx.trace_id, "032x")
                log_data["span_id"] = format(ctx.span_id, "016x")
        
        # Add extra fields
        for key, value in record.__dict__.items():
            if key not in (
                "name", "msg", "args", "created", "filename", "funcName",
                "levelname", "levelno", "lineno", "module", "msecs",
                "pathname", "process", "processName", "relativeCreated",
                "stack_info", "exc_info", "exc_text", "thread", "threadName",
                "message", "taskName",
            ):
                log_data[key] = value
        
        return json.dumps(log_data)


class AELLogger:
    """Structured logger with trace context support.
    
    Wraps Python logging with:
    - Automatic trace context injection
    - Structured JSON output
    - Convenience methods for common log patterns
    """
    
    def __init__(self, name: str, level: int = logging.INFO):
        """Initialize logger.
        
        Args:
            name: Logger name (component name)
            level: Logging level
        """
        self._logger = logging.getLogger(f"ael.{name}")
        self._logger.setLevel(level)
        
        # Add JSON handler if not already configured
        if not self._logger.handlers:
            handler = logging.StreamHandler(sys.stderr)
            handler.setFormatter(StructuredLogFormatter())
            self._logger.addHandler(handler)
    
    def _log(self, level: int, message: str, **kwargs: Any) -> None:
        """Log message with extra fields.
        
        Args:
            level: Log level
            message: Log message
            **kwargs: Additional fields to include
        """
        self._logger.log(level, message, extra=kwargs)
    
    def debug(self, message: str, **kwargs: Any) -> None:
        """Log debug message."""
        self._log(logging.DEBUG, message, **kwargs)
    
    def info(self, message: str, **kwargs: Any) -> None:
        """Log info message."""
        self._log(logging.INFO, message, **kwargs)
    
    def warning(self, message: str, **kwargs: Any) -> None:
        """Log warning message."""
        self._log(logging.WARNING, message, **kwargs)
    
    def error(self, message: str, **kwargs: Any) -> None:
        """Log error message."""
        self._log(logging.ERROR, message, **kwargs)
    
    def exception(self, message: str, **kwargs: Any) -> None:
        """Log exception with traceback."""
        self._logger.exception(message, extra=kwargs)


# Logger cache
_loggers: dict[str, AELLogger] = {}


def get_logger(name: str, level: int = logging.INFO) -> AELLogger:
    """Get or create a structured logger.
    
    Args:
        name: Logger name (component name)
        level: Logging level
        
    Returns:
        AELLogger instance
    """
    if name not in _loggers:
        _loggers[name] = AELLogger(name, level)
    return _loggers[name]


def reset_loggers() -> None:
    """Reset logger cache (for testing)."""
    global _loggers
    _loggers = {}
