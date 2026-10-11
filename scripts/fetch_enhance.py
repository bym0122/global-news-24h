"""Runtime patches for article_content: curl_cffi + meta/json-ld + multi-candidate."""
from __future__ import annotations

import re
from typing import Any

try:
    from curl_cffi import requests as cffi_requests

    _HAS_CFFI = True
except Exception:  # pragma: no cover
    cffi_requests = None
    _HAS_CFFI = False

import article_content as ac


def _meta_or_jsonld(html: str) -> str:
    if not html:
        return ""
    patterns = [
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:description["\']',
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']description["\']',
        r'"articleBody"\s*:\s*"((?:[^"\\]|\\.){60,})"',
        r'"description"\s*:\s*"((?:[^"\\]|\\.){60,})"',
    ]
    for pat in patterns:
        m = re.search(pat, html, re.I | re.S)
        if not m:
            continue
        t = m.group(1)
        t = (
            t.replace("\\n", " ")
            .replace("\\u2019", "'")
            .replace("\\u201c", '"')
            .replace("\\u201d", '"')
            .replace("&nbsp;", " ")
            .replace("&amp;", "&")
            .replace("&#39;", "'")
            .replace("&quot;", '"')
        )
        t = re.sub(r"\s+", " ", t).strip()
        if len(t) >= 60:
            return t
    return ""


def _http_get(url: str, timeout: int | None = None):
    timeout = timeout or ac.CONTENT_FETCH_TIMEOUT
    if _HAS_CFFI:
        try:
            return cffi_requests.get(
                url,
                impersonate="chrome124",
                headers=ac.HEADERS,
                timeout=timeout,
                allow_redirects=True,
            )
        except Exception:
            pass
    sess = ac._session()
    return sess.get(url, timeout=timeout, allow_redirects=True)


def _page_title(html: str) -> str:
    if not html:
        return ""
    patterns = [
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
        r'<title[^>]*>(.*?)</title>',
    ]
    for pat in patterns:
        m = re.search(pat, html, re.I | re.S)
        if m:
            t = re.sub(r"\s+", " ", m.group(1)).strip()
            if t:
                return t
    return ""


def _title_tokens(title: str) -> set[str]:
    t = re.sub(r"[^\w\s]", " ", (title or "").lower())
    stop = {"the", "a", "an", "and", "of", "to", "in", "for", "on", "at", "with", "says", "said", "after", "as", "by", "from", "is", "are", "was", "were"}
    return {x for x in t.split() if len(x) > 2 and x not in stop}


def _title_matches(expected: str, actual: str) -> bool:
    if not expected or not actual:
        return False
    a = _title_tokens(expected)
    b = _title_tokens(actual)
    if not a or not b:
        return False
    inter = len(a & b)
    return inter >= 3 and inter / min(len(a), len(b)) >= 0.45


def fetch_article_content(url: str, sess=None, expected_title: str = "") -> str:
    try:
        resp = _http_get(url)
        if resp.status_code >= 400 and len(resp.text or "") < 2000:
            return ""
        ctype = (resp.headers.get("content-type") or "").lower()
        if ctype and not any(x in ctype for x in ("html", "text", "xml", "json")):
            return ""
        raw = resp.text or ""
        if expected_title and not _title_matches(expected_title, _page_title(raw)):
            return ""
        body = ac.html_to_text(raw)
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        if len(body) < 80:
            meta = _meta_or_jsonld(raw)
            if len(meta) >= 60 and (not expected_title or _title_matches(expected_title, _page_title(raw))):
                body = meta
            else:
                return ""
        if len(body) > ac.CONTENT_MAX_CHARS:
            body = body[: ac.CONTENT_MAX_CHARS].rstrip() + "…"
        return body
    except Exception:
        return ""


def enrich_with_content(items: list[dict[str, Any]]) -> None:
    total = len(items)
    if total == 0:
        return

    from concurrent.futures import ThreadPoolExecutor, as_completed

    gn_urls: list[str] = []
    for it in items:
        u = (it.get("link") or "").strip()
        if u and "news.google.com" in u and u not in gn_urls:
            gn_urls.append(u)

    sess = ac._session()
    print(f"  resolving {len(gn_urls)} Google News links…")
    resolved_map = ac.resolve_google_news_batch(gn_urls, sess) if gn_urls else {}
    resolved_ok = sum(1 for _, r in resolved_map.items() if "news.google.com" not in r)
    print(f"  resolved to publisher: {resolved_ok}/{len(gn_urls)}")
    if _HAS_CFFI:
        print("  http client: curl_cffi (chrome124)")
    else:
        print("  http client: requests")

    def work(idx_item: tuple[int, dict[str, Any]]) -> tuple[int, str]:
        idx, it = idx_item
        link = (it.get("link") or "").strip()
        cands: list[str] = []
        for u in [resolved_map.get(link, ""), link]:
            u = (u or "").strip()
            if u and "news.google.com" not in u and u not in cands:
                cands.append(u)
        expected_title = (it.get("title") or "").strip()
        for u in cands:
            body = fetch_article_content(u, expected_title=expected_title)
            if body:
                return idx, body
        return idx, ""

    results: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=ac.MAX_WORKERS) as pool:
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
        link = it.get("link") or ""
        if link in resolved_map and "news.google.com" not in resolved_map[link]:
            it["publisher_link"] = resolved_map[link]
        if content:
            ok += 1
    print(f"  content fetched (title-validated): {ok}/{total}")


def patch() -> None:
    """Monkey-patch article_content entry points used by the bootstrap."""
    ac.fetch_article_content = fetch_article_content  # type: ignore
    ac.enrich_with_content = enrich_with_content  # type: ignore
