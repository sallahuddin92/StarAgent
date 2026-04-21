"""
Production utilities: logging, validation, rate limiting, monitoring.
"""

import logging
import time
from typing import Dict, Callable, Any
from functools import wraps
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class RateLimiter:
    """Simple in-memory rate limiter per key (conversation, IP, etc.)."""
    
    def __init__(self, max_requests: int = 100, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: Dict[str, list[float]] = {}
    
    def is_allowed(self, key: str) -> bool:
        """Check if request is within rate limit."""
        now = time.time()
        cutoff = now - self.window_seconds
        
        # Clean old requests
        if key in self.requests:
            self.requests[key] = [t for t in self.requests[key] if t > cutoff]
        else:
            self.requests[key] = []
        
        # Check limit
        if len(self.requests[key]) >= self.max_requests:
            return False
        
        self.requests[key].append(now)
        return True


class RequestValidator:
    """Validate and sanitize incoming requests."""
    
    @staticmethod
    def validate_conversation_id(conv_id: str) -> bool:
        """Validate conversation ID format."""
        if not conv_id:
            return False
        # Allow alphanumeric, hyphens, underscores; max 256 chars
        import re
        return bool(re.match(r"^[a-zA-Z0-9_-]{1,256}$", conv_id))
    
    @staticmethod
    def validate_project_id(proj_id: str) -> bool:
        """Validate project ID format."""
        if not proj_id:
            return False
        import re
        return bool(re.match(r"^[a-zA-Z0-9_-]{1,128}$", proj_id))
    
    @staticmethod
    def validate_message_length(content: str, max_length: int = 16000) -> bool:
        """Validate message length."""
        return len(str(content)) <= max_length
    
    @staticmethod
    def sanitize_string(text: str, max_length: int = 1000) -> str:
        """Sanitize string input."""
        text = str(text).strip()
        # Remove null bytes
        text = text.replace('\0', '')
        # Truncate if needed
        return text[:max_length]


class PerformanceMonitor:
    """Track and log performance metrics."""
    
    def __init__(self):
        self.metrics: Dict[str, list[float]] = {}
    
    def record(self, metric_name: str, value: float):
        """Record a metric value."""
        if metric_name not in self.metrics:
            self.metrics[metric_name] = []
        self.metrics[metric_name].append(value)
        
        # Keep only last 1000 values to prevent memory leak
        if len(self.metrics[metric_name]) > 1000:
            self.metrics[metric_name] = self.metrics[metric_name][-1000:]
    
    def get_average(self, metric_name: str) -> float:
        """Get average value for a metric."""
        if metric_name not in self.metrics or not self.metrics[metric_name]:
            return 0.0
        values = self.metrics[metric_name]
        return sum(values) / len(values)
    
    def get_stats(self, metric_name: str) -> Dict[str, float]:
        """Get statistics for a metric."""
        if metric_name not in self.metrics or not self.metrics[metric_name]:
            return {"count": 0, "avg": 0, "min": 0, "max": 0}
        
        values = self.metrics[metric_name]
        return {
            "count": len(values),
            "avg": sum(values) / len(values),
            "min": min(values),
            "max": max(values)
        }


class RequestLogger:
    """Log request/response details for debugging and monitoring."""
    
    def __init__(self, logger_instance: logging.Logger = None):
        self.logger = logger_instance or logger
    
    def log_request(
        self,
        method: str,
        path: str,
        conversation_id: str,
        project_id: str = "default",
        user: str | None = None,
        extra: Dict[str, Any] | None = None
    ):
        """Log incoming request."""
        msg = f"{method} {path} | conv={conversation_id} | proj={project_id}"
        if user:
            msg += f" | user={user}"
        if extra:
            msg += f" | {extra}"
        self.logger.info(msg)
    
    def log_response(
        self,
        status_code: int,
        conversation_id: str,
        duration_ms: float,
        tokens_prompt: int = 0,
        tokens_completion: int = 0,
        extra: Dict[str, Any] | None = None
    ):
        """Log response details."""
        msg = (
            f"status={status_code} | conv={conversation_id} | "
            f"duration={duration_ms:.0f}ms | "
            f"tokens={tokens_prompt}+{tokens_completion}"
        )
        if extra:
            msg += f" | {extra}"
        self.logger.info(msg)
    
    def log_error(
        self,
        error: Exception,
        conversation_id: str,
        context: str = ""
    ):
        """Log error details."""
        self.logger.error(
            f"Error in {context} | conv={conversation_id} | {type(error).__name__}: {str(error)}",
            exc_info=True
        )


# Global instances
rate_limiter = RateLimiter(max_requests=100, window_seconds=60)
request_validator = RequestValidator()
performance_monitor = PerformanceMonitor()
request_logger = RequestLogger()
