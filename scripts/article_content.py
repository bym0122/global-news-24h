"""Fetch and truncate article body for news items (parallel + Google News batchexecute unwrap)."""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests

CONTENT_MAX_CHARS = 600
CONTENT_FETCH_TIMEOUT = 12
MAX_WORKERS = 12
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}

BATCHEXECUTE_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
_SG = re.compile(r'data-n-a-sg="([^"]+)"')
_TS = re.compile(r'data-n-a-ts="([^"]+)"')
_GARTURLREQ_CTX = [
    ["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1],
    "X",
    "X",
    1,
    [1, 1, 1],
    1,
    1,
    None,
    0,
    0,
    None,
    0,
]


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def _article_id(url: str) -> str | None:
    try:
        p = urlparse(url)
        parts = p.path.split("/")
        if (
            p.hostname == "news.google.com"
            and len(parts) > 1
            and parts[-2] in ("articles", "read")
        ):
            return parts[-1] or None
    except Exception:
        return None
    return None


def _params_page_url(art_id: str, source_url: str | None = None) -> str:
    hl, gl, ceid = "en-US", "US", "US:en"
    if source_url:
        qs = parse_qs(urlparse(source_url).query)
        if qs.get("hl"):
            hl = qs["hl"][0]
        if qs.get("gl"):
            gl = qs["gl"][0]
        if qs.get("ceid"):
            ceid = qs["ceid"][0]
    return (
        f"https://news.google.com/rss/articles/{art_id}"
        f"?hl={quote(hl, safe='')}&gl={quote(gl, safe='')}&ceid={quote(ceid, safe='')}"
    )


def _parse_signature(html: str) -> tuple[str, str] | None:
    sg_m = _SG.search(html)
    ts_m = _TS.search(html)
    if sg_m and ts_m:
        return sg_m.group(1), ts_m.group(1)
    return None


def _build_batchexecute_body(items: list[tuple[str, str, str, str]]) -> str:
    envelopes = []
    for req_id, art_id, ts, sig in items:
        inner = [
            "garturlreq",
            _GARTURLREQ_CTX,
            art_id,
            int(ts) if str(ts).isdigit() else ts,
            sig,
        ]
        envelopes.append(
            ["Fbv4je", json.dumps(inner, separators=(",", ":")), None, str(req_id)]
        )
    return f"f.req={quote(json.dumps([envelopes], separators=(',', ':')))}"


def _parse_batchexecute(text: str) -> list[tuple[str | None, str]]:
    body = text
    if "\n\n" in body:
        body = body.split("\n\n", 1)[1]
    body = body.lstrip()
    if body.startswith(")]}'"):
        body = body.split("\n", 1)[1] if "\n" in body else body[4:]
        body = body.lstrip()
    try:
        rows = json.loads(body)
    except Exception:
        return []
    if rows and rows[-1] and isinstance(rows[-1], list) and rows[-1][0] == "di":
        rows = rows[:-1]
    if rows and isinstance(rows[-1], list) and rows[-1] and rows[-1][0] == "e":
        rows = rows[:-1]

    pairs: list[tuple[str | None, str]] = []
    for row in rows:
        if not (isinstance(row, list) and len(row) >= 3):
            continue
        payload = row[2]
        if row[0] == "wrb.fr" or row[1] == "Fbv4je":
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    continue
            if isinstance(payload, list) and payload and payload[0] == "garturlres":
                req_id = None
                for cell in reversed(row[3:]):
                    if cell is not None:
                        req_id = str(cell)
                        break
                pairs.append((req_id, payload[1]))
    return pairs


def resolve_google_news_batch(urls: list[str], sess: requests.Session) -> dict[str, str]:
    """Resolve many Google News URLs via batchexecute. Returns {original_url: publisher_url}."""
    out: dict[str, str] = {}
    pending: list[tuple[str, str, str, str, str]] = []  # (req_id, art_id, ts, sig, orig)

    for i, url in enumerate(urls):
        if "news.google.com" not in url:
            out[url] = url
            continue
        art_id = _article_id(url)
        if not art_id:
            out[url] = url
            continue
        try:
            page_url = _params_page_url(art_id, url)
            resp = sess.get(page_url, timeout=CONTENT_FETCH_TIMEOUT, allow_redirects=True)
            if resp.status_code != 200:
                out[url] = _fallback_extract(resp.text, url)
                continue
            sig_ts = _parse_signature(resp.text)
            if not sig_ts:
                out[url] = _fallback_extract(resp.text, url)
                continue
            sg, ts = sig_ts
            pending.append((str(i), art_id, ts, sg, url))
        except Exception:
            out[url] = url

    if not pending:
        return out

    for start in range(0, len(pending), 20):
        chunk = pending[start : start + 20]
        try:
            body = _build_batchexecute_body(
                [(rid, aid, ts, sg) for rid, aid, ts, sg, _ in chunk]
            )
            resp = sess.post(
                BATCHEXECUTE_URL,
                data=body,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"
                },
                timeout=CONTENT_FETCH_TIMEOUT,
            )
            pairs = _parse_batchexecute(resp.text)
            by_id = {rid: u for rid, u in pairs if rid is not None}
            ordered = [u for _, u in pairs]
            for offset, (rid, _aid, _ts, _sg, orig) in enumerate(chunk):
                decoded = by_id.get(rid)
                if decoded is None and not by_id and offset < len(ordered):
                    decoded = ordered[offset]
                if decoded and "news.google.com" not in decoded:
                    out[orig] = decoded
                else:
                    out[orig] = orig
        except Exception:
            for *_, orig in chunk:
                out.setdefault(orig, orig)

    return out


def _fallback_extract(html: str, default: str) -> str:
    patterns = [
        r'data-n-au="(https?://[^"]+)"',
        r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\'](https?://[^"\']+)["\']',
        r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\'](https?://[^"\']+)["\']',
        r'url=(https?://(?!news\.google\.com)[^&\s"\']+)',
        r'["\'](https?://(?!news\.google\.com|www\.google\.com|gstatic\.com)[^"\'\s<>]{20,})["\']',
    ]
    for pat in patterns:
        for m in re.finditer(pat, html or "", re.I):
            cand = unquote(m.group(1)).split("&")[0].rstrip("\\")
            if (
                cand.startswith("http")
                and "google.com" not in cand
                and "gstatic.com" not in cand
                and len(cand) > 20
            ):
                return cand
    return default


def resolve_google_news(url: str, sess: requests.Session) -> str:
    """Single-URL convenience wrapper."""
    if "news.google.com" not in url:
        return url
    m = resolve_google_news_batch([url], sess)
    return m.get(url, url)


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
    text = re.sub(r"(?s)<[^>]+>", " ", html)
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

    sess = _session()
    gn_urls = [it.get("link") or "" for it in items if "news.google.com" in (it.get("link") or "")]
    print(f"  resolving {len(gn_urls)} Google News links…")
    resolved_map = resolve_google_news_batch(gn_urls, sess) if gn_urls else {}
    resolved_ok = sum(1 for u, r in resolved_map.items() if "news.google.com" not in r)
    print(f"  resolved to publisher: {resolved_ok}/{len(gn_urls)}")

    def work(idx_item: tuple[int, dict[str, Any]]) -> tuple[int, str]:
        idx, it = idx_item
        link = it.get("link") or ""
        if not link:
            return idx, ""
        real = resolved_map.get(link, link)
        if "news.google.com" in real:
            return idx, ""
        local = _session()
        try:
            resp = local.get(real, timeout=CONTENT_FETCH_TIMEOUT, allow_redirects=True)
            if resp.status_code != 200:
                return idx, ""
            ctype = (resp.headers.get("content-type") or "").lower()
            if "html" not in ctype and "text" not in ctype and "xml" not in ctype:
                return idx, ""
            body = html_to_text(resp.text)
            body = re.sub(r"\n{3,}", "\n\n", body).strip()
            if len(body) < 80:
                return idx, ""
            if len(body) > CONTENT_MAX_CHARS:
                body = body[:CONTENT_MAX_CHARS].rstrip() + "…"
            return idx, body
        except Exception:
            return idx, ""

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
