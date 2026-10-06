#!/usr/bin/env python3
"""Bootstrap: load known-good pipeline from git history, then enrich with article content."""
from __future__ import annotations

import runpy
import sys
import tempfile
from pathlib import Path

import requests

# Known-good full pipeline (pre content-enrichment)
RAW_URL = (
    "https://raw.githubusercontent.com/bym0122/global-news-24h/"
    "a2d7b65e8bc587084bad71639c2846c5241c4eb3/scripts/fetch_news.py"
)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from article_content import enrich_with_content  # noqa: E402
import article_content as _article_content  # noqa: E402

# Near-full article text (was 600 chars)
_article_content.CONTENT_MAX_CHARS = 50000


def main() -> None:
    print("[bootstrap] downloading base pipeline…")
    print(f"[bootstrap] CONTENT_MAX_CHARS={_article_content.CONTENT_MAX_CHARS}")
    resp = requests.get(RAW_URL, timeout=30)
    resp.raise_for_status()
    code = resp.text

    # Inject import
    needle = "from dateutil import parser as date_parser\n"
    inject = needle + "from article_content import enrich_with_content\n"
    if needle not in code:
        raise SystemExit("base script structure changed; cannot inject import")
    code = code.replace(needle, inject, 1)

    # Inject enrich call after unique selection
    old = (
        '    unique = sorted(clustered, key=lambda x: (-x["stars"], -x["_score"]))[:MAX_FINAL_ITEMS]\n'
        "\n"
        "    by_cat: dict[str, list] = defaultdict(list)\n"
    )
    new = (
        '    unique = sorted(clustered, key=lambda x: (-x["stars"], -x["_score"]))[:MAX_FINAL_ITEMS]\n'
        "\n"
        '    print(f"  fetching article bodies for top {len(unique)}…")\n'
        "    enrich_with_content(unique)\n"
        "\n"
        "    by_cat: dict[str, list] = defaultdict(list)\n"
    )
    if old not in code:
        raise SystemExit("base script structure changed; cannot inject enrich call")
    code = code.replace(old, new, 1)

    # Inject content field in JSON
    old_j = (
        '        json_items.append({\n'
        '            "title": it["title"],\n'
        '            "link": it["link"],\n'
        '            "summary": it.get("summary"),\n'
        '            "published": it.get("published"),\n'
    )
    new_j = (
        '        json_items.append({\n'
        '            "title": it["title"],\n'
        '            "link": it["link"],\n'
        '            "summary": it.get("summary"),\n'
        '            "content": it.get("content") or "",\n'
        '            "published": it.get("published"),\n'
    )
    if old_j not in code:
        raise SystemExit("base script structure changed; cannot inject content field")
    code = code.replace(old_j, new_j, 1)

    # Inject markdown content display (full text, collapse only runs of spaces)
    old_m = '        lines.append(f"- **发生了什么**: {it.get(\'summary\') or it[\'title\']}")\n'
    new_m = (
        '        what = it.get("content") or it.get("summary") or it["title"]\n'
        '        what = __import__("re").sub(r"[ \\t]+", " ", what).strip()\n'
        '        lines.append(f"- **发生了什么**: {what}")\n'
    )
    if old_m not in code:
        raise SystemExit("base script structure changed; cannot inject markdown")
    code = code.replace(old_m, new_m, 1)

    # Remove 15-item cap in 今日重点 — show ALL stories
    old_top = (
        '    top = sorted(items, key=lambda x: (-x["stars"], -x["_score"]))[:15]\n'
    )
    new_top = (
        '    top = sorted(items, key=lambda x: (-x["stars"], -x["_score"]))\n'
    )
    if old_top not in code:
        raise SystemExit("base script structure changed; cannot remove [:15] cap")
    code = code.replace(old_top, new_top, 1)

    # Remove 8-item cap in category browse — show ALL in each category
    old_cat = "        for it in cat_items[:8]:\n"
    new_cat = "        for it in cat_items:\n"
    if old_cat not in code:
        raise SystemExit("base script structure changed; cannot remove [:8] cap")
    code = code.replace(old_cat, new_cat, 1)

    # Write temp module and execute
    tmp = Path(tempfile.gettempdir()) / "global_news_24h_pipeline.py"
    tmp.write_text(code, encoding="utf-8")
    print(f"[bootstrap] running patched pipeline ({tmp})…")
    sys.path.insert(0, str(HERE))
    runpy.run_path(str(tmp), run_name="__main__")


if __name__ == "__main__":
    main()
