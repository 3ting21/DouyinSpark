from __future__ import annotations

from urllib.parse import quote, urlparse

DOUYIN_HOME_URL = "https://www.douyin.com/"
DOUYIN_CHAT_URL = "https://www.douyin.com/chat"


def normalize_douyin_id(raw: str) -> str:
    """Accept `abc123`, `@abc123` or a profile link and return the bare id."""
    value = raw.strip()
    if not value:
        return ""
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme in {"http", "https"}:
        if host != "douyin.com" and not host.endswith(".douyin.com"):
            return ""
        path = parsed.path.strip("/")
        if path.startswith("user/"):
            return ""
        if path.startswith("search/"):
            value = path[len("search"):].strip("/")
        else:
            return ""
    if "douyin.com" in value.lower() and "/user/" in value:
        return ""
    return value.lstrip("@").strip()


def douyin_search_url(keyword: str) -> str:
    """Douyin web search filtered to users, used to resolve a 抖音号 to a nickname."""
    value = normalize_douyin_id(keyword)
    if not value:
        return DOUYIN_HOME_URL
    return f"https://www.douyin.com/search/{quote(value, safe='')}?type=user"


def official_douyin_url(url: str) -> str:
    value = url.strip()
    if not value:
        return DOUYIN_HOME_URL
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host in {"0.0.0.0", "0.0.0.1", "localhost"}:
        return DOUYIN_HOME_URL
    if host != "douyin.com" and not host.endswith(".douyin.com"):
        return DOUYIN_HOME_URL
    if parsed.path.rstrip("/") == "/message":
        return DOUYIN_CHAT_URL
    return value
