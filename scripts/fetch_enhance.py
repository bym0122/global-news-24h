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


def fetch_article_content(url: str, sess=None) -> str:
    try:
        final_url = url
        if "news.google.com" in url:
            final_url = ac.resolve_google_news(url, sess or ac._session())
            if "news.google.com" in final_url:
                return ""

        candidates = [final_url]
        if "/amp" not in final_url and "amp." not in final_url:
            candidates.append(
                final_url.rstrip("/") + "/amp"
                if not final_url.endswith("/")
                else final_url + "amp"
            )

        for cand in candidates:
            try:
                resp = _http_get(cand)
            except Exception:
                continue
            if resp.status_code >= 400 and len(resp.text or "") < 2000:
                continue
            ctype = (resp.headers.get("content-type") or "").lower()
            if ctype and not any(x in ctype for x in ("html", "text", "xml", "json")):
                continue
            raw = resp.text or ""
            body = ac.html_to_text(raw)
            body = re.sub(r"\n{3,}", "\n\n", body).strip()
            if len(body) < 80:
                meta = _meta_or_jsonld(raw)
                if len(meta) >= 60:
                    body = meta
                else:
                    continue
            if len(body) > ac.CONTENT_MAX_CHARS:
                body = body[: ac.CONTENT_MAX_CHARS].rstrip() + "…"
            return body
        return ""
    except Exception:
        return ""


def enrich_with_content(items: list[dict[str, Any]]) -> None:
    total = len(items)
    if total == 0:
        return

    from concurrent.futures import ThreadPoolExecutor, as_completed

    gn_urls: list[str] = []
    for it in items:
        for u in [it.get("link")] + list(it.get("links") or []):
            u = (u or "").strip()
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
        for u in [resolved_map.get(link, ""), link] + list(it.get("links") or []):
            u = (u or "").strip()
            if not u:
                continue
            if "news.google.com" in u:
                u = resolved_map.get(u, u)
            if u and "news.google.com" not in u and u not in cands:
                cands.append(u)
        for u in cands:
            body = fetch_article_content(u)
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


def patch() -> None:
    """Monkey-patch article_content entry points used by the bootstrap."""
    ac.fetch_article_content = fetch_article_content  # type: ignore
    ac.enrich_with_content = enrich_with_content  # type: ignore
