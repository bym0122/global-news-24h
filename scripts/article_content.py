"""Fetch and truncate article body for news items."""
from __future__ import annotations

import re
from typing import Any

import requests

CONTENT_MAX_CHARS = 600
CONTENT_FETCH_TIMEOUT = 12
USER_AGENT = "global-news-24h/1.0 (+https://github.com/bym0122/global-news-24h)"


def resolve_url(url: str) -> str:
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=CONTENT_FETCH_TIMEOUT,
            allow_redirects=True,
        )
        return resp.url or url
    except Exception:
        return url


def html_to_text(html: str) -> str:
    if not html:
        return ""
    html = re.sub(r"(?is)<(script|style|noscript|svg|iframe)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"(?i)</p>", "\n", html)
    html = re.sub(r"(?i)</div>", "\n", html)
    html = re.sub(r"(?i)</h[1-6]>", "\n", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    text = re.sub(r"[ \t]+", " ", text)
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if len(ln) > 40]
    return "\n".join(lines)


def fetch_article_content(url: str) -> str:
    try:
        final_url = url
        if "news.google.com" in url:
            final_url = resolve_url(url)
        resp = requests.get(
            final_url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.8",
            },
            timeout=CONTENT_FETCH_TIMEOUT,
            allow_redirects=True,
        )
        if resp.status_code != 200:
            return ""
        ctype = (resp.headers.get("content-type") or "").lower()
        if "html" not in ctype and "text" not in ctype:
            return ""
        body = html_to_text(resp.text)
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        if len(body) < 80:
            return ""
        if len(body) > CONTENT_MAX_CHARS:
            body = body[:CONTENT_MAX_CHARS].rstrip() + "…"
        return body
    except Exception:
        return ""


def enrich_with_content(items: list[dict[str, Any]]) -> None:
    total = len(items)
    for i, it in enumerate(items, 1):
        link = it.get("link") or ""
        title = (it.get("title") or "")[:50]
        print(f"  content [{i}/{total}] {title}…")
        content = fetch_article_content(link) if link else ""
        it["content"] = content
        summary = (it.get("summary") or "").strip()
        full_title = (it.get("title") or "").strip()
        if content and (
            not summary
            or summary.lower() in full_title.lower()
            or full_title.lower() in summary.lower()
        ):
            it["summary"] = content[: min(len(content), 400)]
