"""Fetch and truncate article body for news items (parallel + Google News aware)."""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import unquote

import requests

CONTENT_MAX_CHARS = 600
CONTENT_FETCH_TIMEOUT = 10
MAX_WORKERS = 12
USER_AGENT = (
    "Mozilla/5.0 (compatible; global-news-24h/1.1; "
    "+https://github.com/bym0122/global-news-24h)"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def resolve_google_news(url: str, sess: requests.Session) -> str:
    """Best-effort unwrap of news.google.com/rss/articles/... to publisher URL."""
    if "news.google.com" not in url:
        return url
    try:
        resp = sess.get(url, timeout=CONTENT_FETCH_TIMEOUT, allow_redirects=True)
        final = resp.url or url
        if "news.google.com" not in final:
            return final

        text = resp.text or ""
        patterns = [
            r'data-n-au="(https?://[^"]+)"',
            r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\'](https?://[^"\']+)["\']',
            r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\'](https?://[^"\']+)["\']',
            r'url=(https?://(?!news\.google\.com)[^&\s"\']+)',
            r'["\'](https?://(?!news\.google\.com|www\.google\.com|gstatic\.com)[^"\'\s<>]{20,})["\']',
        ]
        for pat in patterns:
            for m in re.finditer(pat, text, re.I):
                cand = unquote(m.group(1)).split("&")[0].rstrip("\\")
                if (
                    cand.startswith("http")
                    and "google.com" not in cand
                    and "gstatic.com" not in cand
                    and len(cand) > 20
                ):
                    return cand
        return final
    except Exception:
        return url


def html_to_text(html: str) -> str:
    if not html:
        return ""
    article_m = re.search(r"(?is)<(article|main)[^>]*>(.*?)</\1>", html)
    if article_m and len(article_m.group(2)) > 400:
        html = article_m.group(2)

    html = re.sub(
        r"(?is)<(script|style|noscript|svg|iframe|nav|footer|header|aside)[^>]*>.*?</\1>",
        " ",
        html,
    )
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"(?i)</(p|div|h[1-6]|li|tr|section)>", "\n", html)
    text = re.sub(r"(?s)<[^>]+", " ", html)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&rsquo;", "'")
        .replace("&lsquo;", "'")
        .replace("&rdquo;", '"')
        .replace("&ldquo;", '"')
        .replace("&#8217;", "'")
        .replace("&#8220;", '"')
        .replace("&#8221;", '"')
    )
    text = re.sub(r"[ \t]+", " ", text)
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if len(ln) > 50]
    skip = re.compile(
        r"(?i)^(subscribe|sign in|log in|cookie|privacy|terms of|advertisement|"
        r"share this|follow us|related stories|read more|skip to|menu$)"
    )
    lines = [ln for ln in lines if not skip.search(ln[:40])]
    return "\n".join(lines)


def fetch_article_content(url: str, sess: requests.Session | None = None) -> str:
    sess = sess or _session()
    try:
        final_url = url
        if "news.google.com" in url:
            final_url = resolve_google_news(url, sess)
            if "news.google.com" in final_url:
                return ""

        resp = sess.get(
            final_url,
            timeout=CONTENT_FETCH_TIMEOUT,
            allow_redirects=True,
        )
        if resp.status_code != 200:
            return ""
        ctype = (resp.headers.get("content-type") or "").lower()
        if "html" not in ctype and "text" not in ctype and "xml" not in ctype:
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
    if total == 0:
        return

    def work(idx_item: tuple[int, dict[str, Any]]) -> tuple[int, str]:
        idx, it = idx_item
        link = it.get("link") or ""
        sess = _session()
        content = fetch_article_content(link, sess) if link else ""
        return idx, content

    results: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = {pool.submit(work, (i, it)): i for i, it in enumerate(items)}
        done = 0
        for fut in as_completed(futs):
            done += 1
            try:
                idx, content = fut.result()
                results[idx] = content
            except Exception:
                pass
            if done % 10 == 0 or done == total:
                print(f"  content progress {done}/{total}")

    ok = 0
    for i, it in enumerate(items):
        content = results.get(i, "")
        it["content"] = content
        if content:
            ok += 1
            summary = (it.get("summary") or "").strip()
            full_title = (it.get("title") or "").strip()
            if (
                not summary
                or summary.lower() in full_title.lower()
                or full_title.lower() in summary.lower()
                or len(summary) < 80
            ):
                it["summary"] = content[: min(len(content), 400)]
    print(f"  content fetched: {ok}/{total}")
