"""Custom middleware for the Ensemble Daemon API."""

import logging

logger = logging.getLogger(__name__)


class SelectiveAccessLogMiddleware:
    """Middleware that controls access logging via custom logic."""

    # Exact paths to HIDE (exclude from logging)
    HIDE_PATTERNS = [
        "/api/instances",
    ]

    # ANSI color codes
    RESET = "\033[0m"
    BOLD = "\033[1m"
    
    # Method colors
    COLORS = {
        "GET": "\033[92m",      # Green
        "POST": "\033[96m",     # Cyan
        "PUT": "\033[93m",      # Yellow
        "PATCH": "\033[93m",    # Yellow
        "DELETE": "\033[91m",   # Red
    }
    
    # Status colors
    def status_color(self, code: int) -> str:
        if 200 <= code < 300:
            return "\033[92m"   # Green
        elif 300 <= code < 400:
            return "\033[94m"   # Blue
        elif 400 <= code < 500:
            return "\033[93m"   # Yellow
        else:
            return "\033[91m"   # Red

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Extract request info before processing
        method = scope.get("method", "")
        path = scope.get("path", "")
        client = scope.get("client")
        client_addr = f"{client[0]}:{client[1]}" if client else "unknown"

        status_code = 200  # default

        async def custom_send(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        # Process the request
        await self.app(scope, receive, custom_send)

        # Skip logging if path exactly matches hide patterns
        if path in self.HIDE_PATTERNS:
            return

        # Colorize log output
        method_color = self.COLORS.get(method, self.RESET)
        status_color = self.status_color(status_code)
        
        log_msg = (
            f"{self.BOLD}{client_addr}{self.RESET} "
            f"{method_color}{method}{self.RESET} "
            f"{path} "
            f"{status_color}{status_code}{self.RESET}"
        )
        logger.info(log_msg)
