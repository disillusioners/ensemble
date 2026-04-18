"""Time tool for getting current date and time information."""

from datetime import datetime, timezone
from langchain_core.tools import tool
from typing import Optional

from ._tool_registry import register_tool_category

CATEGORY_NAME = "Time"
CATEGORY_DOC = """\
Get current date and time.

Use `format_type="iso"` for ISO format, or provide a custom format string.
"""


@register_tool_category("time")
@tool
def time(
    timezone_str: Optional[str] = None,
    format_type: Optional[str] = "iso"
) -> str:
    """Get current date and time. Use tool_help("time") for details."""
    try:
        # Get current time
        if timezone_str:
            import zoneinfo
            try:
                tz = zoneinfo.ZoneInfo(timezone_str)
                now = datetime.now(tz)
            except Exception:
                return f"ERROR: Invalid timezone '{timezone_str}'. Use IANA timezone names like 'America/New_York', 'Europe/London', etc."
        else:
            now = datetime.now(timezone.utc)
            tz = timezone.utc
        
        result_parts = []
        
        if format_type == "iso" or format_type == "all":
            iso_time = now.isoformat()
            result_parts.append(f"ISO: {iso_time}")
        
        if format_type == "human" or format_type == "all":
            human_time = now.strftime("%A, %B %d, %Y at %I:%M %p")
            tz_name = now.tzname() or "UTC"
            result_parts.append(f"HUMAN: {human_time} ({tz_name})")
        
        if format_type == "unix" or format_type == "all":
            unix_time = int(now.timestamp())
            result_parts.append(f"UNIX: {unix_time}")
        
        if format_type == "all":
            # Add extra context for "all" format
            date_info = now.strftime("%Y-%m-%d")
            time_info = now.strftime("%H:%M:%S")
            weekday = now.strftime("%A")
            result_parts.append(f"DATE: {date_info}")
            result_parts.append(f"TIME: {time_info}")
            result_parts.append(f"WEEKDAY: {weekday}")
        
        if not result_parts:
            return f"ERROR: Invalid format_type '{format_type}'. Use 'iso', 'human', 'unix', or 'all'."
        
        return "\n".join(result_parts)
        
    except Exception as e:
        return f"ERROR: {str(e)}"

time._full_doc_ = """Get current date and time information.
    
Args:
    timezone_str: IANA timezone name (e.g., 'America/New_York', 'Europe/London', 'Asia/Tokyo').
                  If not provided, uses UTC.
    format_type: Output format - 'iso' (default), 'human', 'unix', or 'all'

Returns:
    Current time information in the requested format
    
Examples:
    time()                          -> Current time in ISO format (UTC)
    time(format_type="human")       -> Human-readable format like "Monday, January 15, 2024 at 10:30 AM"
    time(format_type="unix")        -> Unix timestamp like "1705315800"
    time(format_type="all")         -> All formats combined
    time(timezone_str="America/New_York") -> Current time in New York timezone
"""
