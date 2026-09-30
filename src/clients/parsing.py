from datetime import date, datetime


def parse_iso_date(raw: str | None) -> date | None:
    """Parses an ISO-8601 timestamp or date to a date, returning None for anything unparsable."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None
