#!/usr/bin/env python3
"""
Zero-cost 24h global news pipeline (pure Python rule engine, no AI).

- Free public RSS feeds
- Last-24h filter
- Event clustering (entities + action verbs + title similarity)
- Multi-source confirmation (keep all outlets reporting same event)
- Source weight + impact + market relevance + confirmation scoring
- Hypothetical / exercise down-weighting
- Rule-based asset detection
- 10-category classification
- Structured Markdown + JSON output (Chinese labels)
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import feedparser
import requests
from dateutil import parser as date_parser

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "data/news"))
HOURS_WINDOW = 24
MAX_ITEMS_PER_FEED = 40
MAX_FINAL_ITEMS = 60
USER_AGENT = "global-news-24h/1.0 (+https://github.com/bym0122/global-news-24h)"

# Free public RSS sources (no API key required)
FEEDS: list[dict[str, Any]] = [
    # ============================================================
    # Tier 1 — Primary / major international news
    # ============================================================
    {
        "name": "Reuters World",
        "url": "https://news.google.com/rss/search?q=when:24h+site:reuters.com/world&ceid=US:en&hl=en-US&gl=US",
        "weight": 1.0,
    },
    {
        "name": "Reuters Markets",
        "url": "https://news.google.com/rss/search?q=when:24h+site:reuters.com/markets+-site:reuters.com/markets/companies+-site:reuters.com/markets/quote&ceid=US:en&hl=en-US&gl=US",
        "weight": 0.98,
    },
    {
        "name": "Reuters Technology",
        "url": "https://news.google.com/rss/search?q=when:24h+site:reuters.com/technology&ceid=US:en&hl=en-US&gl=US",
        "weight": 0.90,
    },
    {
        "name": "AP World",
        "url": "https://news.google.com/rss/search?q=when:24h+site:apnews.com&hl=en-US&gl=US&ceid=US:en",
        "weight": 0.95,
    },
    {
        "name": "BBC World",
        "url": "https://feeds.bbci.co.uk/news/world/rss.xml",
        "weight": 0.95,
    },
    {
        "name": "BBC Business",
        "url": "https://feeds.bbci.co.uk/news/business/rss.xml",
        "weight": 0.90,
    },
    # ============================================================
    # Tier 2 — Topic / market coverage
    # ============================================================
    {
        "name": "GN Top",
        "url": "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",
        "weight": 0.82,
    },
    {
        "name": "GN Geopolitics",
        "url": "https://news.google.com/rss/search?q=geopolitics+OR+war+OR+taiwan+OR+ukraine+OR+iran+OR+korea&hl=en-US&gl=US&ceid=US:en",
        "weight": 0.85,
    },
    {
        "name": "GN Energy",
        "url": "https://news.google.com/rss/search?q=oil+OR+OPEC+OR+natural+gas+OR+Hormuz&hl=en-US&gl=US&ceid=US:en",
        "weight": 0.85,
    },
    {
        "name": "GN Fed",
        "url": "https://news.google.com/rss/search?q=Federal+Reserve+OR+interest+rate+OR+inflation&hl=en-US&gl=US&ceid=US:en",
        "weight": 0.85,
    },
    {
        "name": "GN China",
        "url": "https://news.google.com/rss/search?q=China+economy+OR+China+policy+OR+property&hl=en-US&gl=US&ceid=US:en",
        "weight": 0.85,
    },
    {
        "name": "GN AI",
        "url": "https://news.google.com/rss/search?q=OpenAI+OR+NVIDIA+OR+AI+chip+OR+semiconductor&hl=en-US&gl=US&ceid=US:en",
        "weight": 0.85,
    },
    {
        "name": "GN Markets",
        "url": "https://news.google.com/rss/search?q=stock+market+OR+S%26P+500+OR+Nasdaq&hl=en-US&gl=US&ceid=US:en",
        "weight": 0.80,
    },
    # ============================================================
    # Tier 3 — Additional reputable international sources
    # ============================================================
    {
        "name": "CNBC Top",
        "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "weight": 0.80,
    },
    {
        "name": "DW World",
        "url": "https://rss.dw.com/rdf/rss-en-all",
        "weight": 0.82,
    },
    {
        "name": "Guardian World",
        "url": "https://www.theguardian.com/world/rss",
        "weight": 0.82,
    },
    {
        "name": "NPR World",
        "url": "https://feeds.npr.org/1004/rss.xml",
        "weight": 0.78,
    },
    {
        "name": "France24 World",
        "url": "https://www.france24.com/en/france/rss",
        "weight": 0.78,
    },
    {
        "name": "Al Jazeera",
        "url": "https://www.aljazeera.com/xml/rss/all.xml",
        "weight": 0.75,
    },
]

# Category definitions (Chinese label + keywords for scoring)
CATEGORIES: dict[str, dict[str, Any]] = {
    "geopolitics": {
        "emoji": "🌍",
        "name_zh": "地缘政治",
        "keywords": [
            "war", "ukraine", "russia", "taiwan", "china", "iran", "israel",
            "north korea", "korea", "nato", "sanction", "military", "conflict",
            "hormuz", "gaza", "hezbollah",
        ],
    },
    "energy": {
        "emoji": "🛢️",
        "name_zh": "能源",
        "keywords": [
            "oil", "crude", "opec", "gas", "lng", "pipeline", "hormuz",
            "refinery", "energy", "petroleum", "brent", "wti",
        ],
    },
    "finance": {
        "emoji": "💰",
        "name_zh": "全球金融",
        "keywords": [
            "federal reserve", "fed", "interest rate", "inflation", "dollar",
            "treasury", "bond", "gold", "central bank", "ecb", "boj",
            "rate cut", "rate hike",
        ],
    },
    "china": {
        "emoji": "🇨🇳",
        "name_zh": "中国",
        "keywords": [
            "china", "beijing", "shanghai", "property", "real estate",
            "export", "yuan", "xi jinping", "pboc", "evergrande",
        ],
    },
    "us": {
        "emoji": "🇺🇸",
        "name_zh": "美国",
        "keywords": [
            "white house", "biden", "trump", "congress", "senate",
            "gdp", "jobs report", "cpi", "washington", "treasury secretary",
        ],
    },
    "europe": {
        "emoji": "🇪🇺",
        "name_zh": "欧洲",
        "keywords": [
            "europe", "eu", "germany", "france", "ecb", "euro",
            "uk", "britain", "macron", "scholz",
        ],
    },
    "asia": {
        "emoji": "🇯🇵",
        "name_zh": "亚洲",
        "keywords": [
            "japan", "korea", "india", "southeast asia", "asean",
            "tokyo", "seoul", "modi", "yen",
        ],
    },
    "ai_tech": {
        "emoji": "🤖",
        "name_zh": "AI/科技",
        "keywords": [
            "ai", "openai", "nvidia", "chip", "semiconductor",
            "google", "meta", "microsoft", "apple", "tsmc", "asml", "llm",
        ],
    },
    "commodities": {
        "emoji": "🚢",
        "name_zh": "大宗商品",
        "keywords": [
            "copper", "aluminum", "aluminium", "coal", "iron ore",
            "shipping", "freight", "commodity", "mining", "baltic",
        ],
    },
    "markets": {
        "emoji": "📈",
        "name_zh": "市场",
        "keywords": [
            "stock", "s&p", "nasdaq", "dow", "a-share", "hang seng",
            "market", "rally", "selloff", "earnings",
        ],
    },
}

# High-impact boost keywords
IMPACT_KEYWORDS = {
    "war": 2.0,
    "invasion": 2.0,
    "missile": 1.8,
    "sanctions": 1.6,
    "rate cut": 1.8,
    "rate hike": 1.8,
    "emergency": 1.7,
    "crash": 1.9,
    "default": 2.0,
    "collapse": 1.8,
    "opec": 1.5,
    "hormuz": 2.0,
    "blockade": 1.9,
    "tariff": 1.6,
    "ceasefire": 1.5,
    "nuclear": 1.8,
}

STOPWORDS = {
    "the", "a", "an", "to", "of", "in", "on", "for", "and", "with",
    "from", "after", "before", "says", "said", "new", "news", "over",
    "as", "at", "by", "its", "is", "are", "was", "were", "be", "been",
}

EVENT_ENTITIES = {
    "iran": ["iran", "iranian", "tehran"],
    "israel": ["israel", "israeli", "tel aviv"],
    "us": ["united states", "u.s.", "u.s", " us ", "american", "trump", "white house"],
    "russia": ["russia", "russian", "moscow", "putin"],
    "ukraine": ["ukraine", "ukrainian", "kyiv", "kiev"],
    "china": ["china", "chinese", "beijing"],
    "taiwan": ["taiwan", "taipei"],
    "korea": ["north korea", "south korea", "korean", "pyongyang", "seoul"],
    "hormuz": ["hormuz", "strait of hormuz"],
    "oil": ["oil", "crude", "brent", "wti"],
    "fed": ["federal reserve", "fed ", "powell"],
    "aluminum": ["aluminum", "aluminium"],
    "gold": ["gold"],
    "opec": ["opec"],
    "nato": ["nato"],
    "gaza": ["gaza", "hamas"],
}

# Action verbs help separate different events about the same entities
EVENT_ACTIONS = {
    "close": ["close", "closed", "closure", "shut", "block", "blockade"],
    "open": ["open", "reopen", "reopening", "resume", "resumed"],
    "attack": ["attack", "strike", "missile", "bomb", "assault", "raid"],
    "negotiate": ["negotiat", "talks", "deal", "agreement", "diplomacy"],
    "sanction": ["sanction", "embargo", "ban"],
    "ceasefire": ["ceasefire", "truce", "peace"],
    "rate": ["rate cut", "rate hike", "interest rate", "hike", "cut rates"],
    "invade": ["invas", "invade", "occupation"],
    "export": ["export", "shipment", "supply"],
}

HYPOTHETICAL_MARKERS = [
    "wargame", "war game", "military exercise", "military drill",
    "simulation", "hypothetical", "scenario", "could", "might",
    "possible", "speculation", "what if", "prepare for", "preparing for",
    "contingency", "drill",
]

MARKET_KEYWORDS = [
    "oil", "crude", "gold", "aluminum", "aluminium", "copper",
    "shipping", "freight", "stock", "bond", "yield", "dollar",
    "fed", "interest rate", "opec", "hormuz", "tanker", "vlcc",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_date(entry: dict) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        val = entry.get(key)
        if val:
            try:
                return datetime(*val[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    for key in ("published", "updated"):
        val = entry.get(key)
        if val:
            try:
                dt = date_parser.parse(val)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except Exception:
                pass
    return None


def normalize_title(title: str) -> str:
    t = title.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    tokens = [w for w in t.split() if w not in STOPWORDS and len(w) > 1]
    return " ".join(tokens)


def title_similarity(a: str, b: str) -> float:
    ta = set(normalize_title(a).split())
    tb = set(normalize_title(b).split())
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def extract_entities(text: str) -> set[str]:
    text = f" {text.lower()} "
    result: set[str] = set()
    for canonical, aliases in EVENT_ENTITIES.items():
        if any(alias in text for alias in aliases):
            result.add(canonical)
    return result


def extract_actions(text: str) -> set[str]:
    text = text.lower()
    result: set[str] = set()
    for canonical, aliases in EVENT_ACTIONS.items():
        if any(alias in text for alias in aliases):
            result.add(canonical)
    return result


def event_similarity(a: dict, b: dict) -> float:
    """Combine entity overlap + action overlap + title similarity."""
    ta = f"{a['title']} {a.get('summary', '')}".lower()
    tb = f"{b['title']} {b.get('summary', '')}".lower()

    ea = extract_entities(ta)
    eb = extract_entities(tb)
    aa = extract_actions(ta)
    ab = extract_actions(tb)

    entity_score = 0.0
    if ea and eb:
        entity_score = len(ea & eb) / len(ea | eb)

    action_score = 0.0
    if aa and ab:
        action_score = len(aa & ab) / len(aa | ab)
    elif not aa and not ab:
        action_score = 0.5  # neutral when neither has clear action

    title_score = title_similarity(a["title"], b["title"])

    # Weight: entities most important, then actions, then title
    if ea and eb:
        return entity_score * 0.50 + action_score * 0.30 + title_score * 0.20
    return title_score


def fetch_feed(feed: dict) -> list[dict]:
    url = feed["url"]
    name = feed["name"]
    weight = feed.get("weight", 0.7)
    items: list[dict] = []
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
        for entry in parsed.entries[:MAX_ITEMS_PER_FEED]:
            title = (entry.get("title") or "").strip()
            link = (entry.get("link") or "").strip()
            if not title or not link:
                continue
            published = parse_date(entry)
            summary = (entry.get("summary") or entry.get("description") or "")[:500]
            items.append({
                "title": title,
                "link": link,
                "summary": re.sub(r"<[^>]+>", "", summary).strip(),
                "published": published.isoformat() if published else None,
                "published_dt": published,
                "source": name,
                "source_weight": weight,
                "sources": [name],
                "source_count": 1,
                "links": [link],
            })
    except Exception as e:
        print(f"[warn] feed failed {name}: {e}", file=sys.stderr)
    return items


def detect_assets(text: str) -> list[str]:
    text = text.lower()
    assets: list[str] = []

    if any(x in text for x in ["hormuz", "oil", "crude", "opec", "oil supply", "oil export"]):
        assets += ["原油", "能源股"]

    if any(x in text for x in ["hormuz", "tanker", "shipping", "freight", "vlcc", "oil tanker"]):
        assets.append("油运")

    if any(x in text for x in ["gold", "gold price"]):
        assets.append("黄金")

    if any(x in text for x in ["fed", "federal reserve", "rate cut", "rate hike", "interest rate"]):
        assets += ["美债", "美元", "美股"]

    if any(x in text for x in ["aluminum", "aluminium"]):
        assets += ["铝", "有色金属"]

    if any(x in text for x in ["copper"]):
        assets += ["铜", "有色金属"]

    if any(x in text for x in ["china", "chinese", "beijing"]):
        assets += ["A股", "港股", "人民币"]

    if any(x in text for x in ["nvidia", "semiconductor", "chip", "tsmc", "ai "]):
        assets += ["科技股", "半导体"]

    if any(x in text for x in ["taiwan", "taipei"]):
        assets += ["台积电", "半导体", "亚太市场"]

    return list(dict.fromkeys(assets))[:6]


def score_item(item: dict) -> tuple[float, str, list[str], int]:
    text = f"{item['title']} {item.get('summary', '')}".lower()

    base = item.get("source_weight", 0.7) * 10

    # Category
    cat_scores: dict[str, float] = {}
    for cat_id, cat in CATEGORIES.items():
        hits = sum(1 for kw in cat["keywords"] if kw in text)
        if hits:
            cat_scores[cat_id] = hits

    best_cat = max(cat_scores, key=cat_scores.get) if cat_scores else "geopolitics"
    cat_boost = min(cat_scores.get(best_cat, 0) * 1.2, 5.0)

    # Impact keywords
    impact = 0.0
    for kw, mult in IMPACT_KEYWORDS.items():
        if kw in text:
            impact += mult

    # Hypothetical / exercise / speculation down-weight
    is_hypothetical = any(x in text for x in HYPOTHETICAL_MARKERS)
    if is_hypothetical:
        impact *= 0.35

    # Market relevance
    market_relevance = sum(1 for x in MARKET_KEYWORDS if x in text)
    market_boost = min(market_relevance * 0.5, 3.0)

    # Multi-source confirmation
    source_count = item.get("source_count", 1)
    confirmation_boost = min((source_count - 1) * 1.2, 4.0)

    score = base + cat_boost + impact + market_boost + confirmation_boost

    if score >= 20:
        stars = 5
    elif score >= 16:
        stars = 4
    elif score >= 12:
        stars = 3
    elif score >= 8:
        stars = 2
    else:
        stars = 1

    assets = detect_assets(text)
    item["_is_hypothetical"] = is_hypothetical

    return score, best_cat, assets, stars


def cluster_events(items: list[dict], threshold: float = 0.55) -> list[dict]:
    """
    Cluster similar stories into one event.
    Keep the highest-weighted title as primary; accumulate sources.
    """
    # Pre-score lightly so better sources win as cluster head
    for it in items:
        it["_pre_score"] = it.get("source_weight", 0.7) * 10

    sorted_items = sorted(items, key=lambda x: x["_pre_score"], reverse=True)
    clusters: list[dict] = []

    for item in sorted_items:
        matched = None
        for cluster in clusters:
            if event_similarity(item, cluster) >= threshold:
                matched = cluster
                break

        if matched is None:
            # New cluster head
            head = dict(item)
            head["sources"] = list(dict.fromkeys(item.get("sources", [item["source"]])))
            head["source_count"] = len(head["sources"])
            head["links"] = list(dict.fromkeys(item.get("links", [item["link"]])))
            # Prefer highest source_weight as primary source label
            head["source"] = head["sources"][0]
            clusters.append(head)
        else:
            # Merge into existing cluster
            for src in item.get("sources", [item["source"]]):
                if src not in matched["sources"]:
                    matched["sources"].append(src)
            matched["source_count"] = len(matched["sources"])

            for link in item.get("links", [item["link"]]):
                if link not in matched["links"]:
                    matched["links"].append(link)

            # Keep higher-weight source as primary if better
            if item.get("source_weight", 0) > matched.get("source_weight", 0):
                matched["title"] = item["title"]
                matched["summary"] = item.get("summary") or matched.get("summary")
                matched["link"] = item["link"]
                matched["source"] = item["source"]
                matched["source_weight"] = item["source_weight"]
                if item.get("published_dt") and (
                    matched.get("published_dt") is None
                    or item["published_dt"] > matched["published_dt"]
                ):
                    matched["published"] = item.get("published")
                    matched["published_dt"] = item.get("published_dt")

    return clusters


def build_why_important(item: dict, cat: str, stars: int) -> str:
    src_count = item.get("source_count", 1)
    hypo = item.get("_is_hypothetical", False)

    if hypo:
        return "含假设 / 演习 / 推测成分，实际落地前影响力有限，已降权。"
    if src_count >= 3 and stars >= 4:
        return f"被 {src_count} 家主流媒体同时报道，且命中高影响关键词，属当日核心事件。"
    if src_count >= 2:
        return f"多源确认（{src_count} 家），可信度较高，值得纳入观察。"
    if stars >= 4:
        return "高权重来源 + 高影响关键词，可能对市场或地缘产生连锁反应。"
    if cat == "energy":
        return "能源相关事件通常直接影响油价、航运与通胀预期。"
    if cat == "finance":
        return "央行与利率相关信息会快速传导到全球资产定价。"
    if cat == "ai_tech":
        return "科技与半导体龙头动向常带动整个板块情绪。"
    if cat == "geopolitics":
        return "地缘冲突与制裁可能改变贸易、能源与风险偏好。"
    return "具备一定市场关注度，纳入观察清单。"


def build_impact(item: dict, assets: list[str]) -> str:
    if assets:
        return f"可能影响：{' / '.join(assets)} 及相关产业链。"
    return "可能影响风险偏好、相关板块或区域市场情绪。"


def render_markdown(date_str: str, items: list[dict], by_cat: dict) -> str:
    lines = [
        f"# 全球要闻 · {date_str}",
        "",
        f"> 自动生成 · 过去 {HOURS_WINDOW} 小时 · 零成本纯规则引擎（无 AI）  ",
        f"> 生成时间 (UTC): {utc_now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "---",
        "",
        "## 今日重点（按重要性）",
        "",
    ]

    top = sorted(items, key=lambda x: (-x["stars"], -x["_score"]))[:15]
    for i, it in enumerate(top, 1):
        cat = CATEGORIES[it["category"]]
        stars = "★" * it["stars"] + "☆" * (5 - it["stars"])
        src_count = it.get("source_count", 1)
        sources_str = " / ".join(it.get("sources", [it["source"]])[:5])

        lines.append(f"### {i}. {stars} {it['title']}")
        lines.append("")
        lines.append(f"- **分类**: {cat['emoji']} {cat['name_zh']}")
        lines.append(f"- **来源**: {sources_str}")
        lines.append(f"- **多源确认**: {src_count}")
        if it.get("_is_hypothetical"):
            lines.append("- **假设性事件**: 是（已降权）")
        lines.append(f"- **时间**: {it.get('published') or '未知'}")
        lines.append(f"- **发生了什么**: {it.get('summary') or it['title']}")
        lines.append(f"- **为什么重要**: {it['why_important']}")
        lines.append(f"- **可能影响**: {it['possible_impact']}")
        if it.get("assets"):
            lines.append(f"- **涉及资产**: {', '.join(it['assets'])}")
        lines.append(f"- **链接**: {it['link']}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 分类浏览")
    lines.append("")

    for cat_id, cat in CATEGORIES.items():
        cat_items = by_cat.get(cat_id, [])
        if not cat_items:
            continue
        lines.append(f"### {cat['emoji']} {cat['name_zh']} ({len(cat_items)})")
        lines.append("")
        for it in cat_items[:8]:
            stars = "★" * it["stars"]
            src_n = it.get("source_count", 1)
            suffix = f" · {src_n}源" if src_n > 1 else ""
            lines.append(
                f"- {stars} [{it['title']}]({it['link']}) — {it['source']}{suffix}"
            )
        lines.append("")

    lines.append("---")
    lines.append("*本报告由 GitHub Actions 纯规则引擎自动生成，仅供参考，不构成投资建议。*")
    return "\n".join(lines)


def main() -> None:
    print(f"[{utc_now().isoformat()}] Starting global-news-24h pipeline…")
    cutoff = utc_now() - timedelta(hours=HOURS_WINDOW)

    raw_items: list[dict] = []
    for feed in FEEDS:
        print(f"  fetching {feed['name']}…")
        raw_items.extend(fetch_feed(feed))

    print(f"  raw items: {len(raw_items)}")

    recent = []
    for it in raw_items:
        dt = it.get("published_dt")
        if dt is None or dt >= cutoff:
            recent.append(it)

    print(f"  after 24h filter: {len(recent)}")

    # Event clustering (replaces simple dedup)
    clustered = cluster_events(recent)
    print(f"  after event clustering: {len(clustered)}")

    # Score after clustering so source_count is known
    for it in clustered:
        score, cat, assets, stars = score_item(it)
        it["_score"] = score
        it["category"] = cat
        it["assets"] = assets
        it["stars"] = stars
        it["why_important"] = build_why_important(it, cat, stars)
        it["possible_impact"] = build_impact(it, assets)

    unique = sorted(clustered, key=lambda x: (-x["stars"], -x["_score"]))[:MAX_FINAL_ITEMS]

    by_cat: dict[str, list] = defaultdict(list)
    for it in unique:
        by_cat[it["category"]].append(it)

    date_str = utc_now().strftime("%Y-%m-%d")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    json_items = []
    for it in unique:
        json_items.append({
            "title": it["title"],
            "link": it["link"],
            "summary": it.get("summary"),
            "published": it.get("published"),
            "source": it["source"],
            "sources": it.get("sources", [it["source"]]),
            "source_count": it.get("source_count", 1),
            "category": it["category"],
            "category_zh": CATEGORIES[it["category"]]["name_zh"],
            "stars": it["stars"],
            "score": round(it["_score"], 2),
            "is_hypothetical": it.get("_is_hypothetical", False),
            "why_important": it["why_important"],
            "possible_impact": it["possible_impact"],
            "assets": it.get("assets", []),
        })

    json_path = OUTPUT_DIR / f"{date_str}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": utc_now().isoformat(),
            "window_hours": HOURS_WINDOW,
            "count": len(json_items),
            "items": json_items,
        }, f, ensure_ascii=False, indent=2)

    md_path = OUTPUT_DIR / f"{date_str}.md"
    md = render_markdown(date_str, unique, by_cat)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"Wrote {json_path} and {md_path}")
    print(f"Top stories: {len(unique)}")
    for it in unique[:8]:
        src_n = it.get("source_count", 1)
        hypo = " [假设]" if it.get("_is_hypothetical") else ""
        print(
            f"  {'★' * it['stars']} [{CATEGORIES[it['category']]['name_zh']}] "
            f"({src_n}源){hypo} {it['title'][:65]}"
        )


if __name__ == "__main__":
    main()
