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
import fetch_enhance  # noqa: E402
fetch_enhance.patch()  # curl_cffi + meta/json-ld + multi-cand


def main() -> None:
    print("[bootstrap] downloading base pipeline…")
    resp = requests.get(RAW_URL, timeout=30)
    resp.raise_for_status()
    code = resp.text

    # Inject import
    needle = "from dateutil import parser as date_parser\n"
    inject = (
        needle
        + "from article_content import enrich_with_content\n"
        + "import fetch_enhance\n"
        + "fetch_enhance.patch()\n"
    )
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
        "    # Prefer non-Google-News publisher links from cluster for content fetch\n"
        "    for _it in unique:\n"
        '        _links = list(_it.get("links") or [])\n'
        '        _pri = (_it.get("link") or "").strip()\n'
        "        if _pri and _pri not in _links:\n"
        "            _links.insert(0, _pri)\n"
        '        _non = [u for u in _links if u and "news.google.com" not in u]\n'
        "        if _non:\n"
        '            _it["link"] = _non[0]\n'
        '            _it["links"] = list(dict.fromkeys(_non + _links))\n'
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

    # Inject markdown content display
    old_m = '        lines.append(f"- **发生了什么**: {it.get(\'summary\') or it[\'title\']}")\n'
    new_m = (
        '        what = it.get("content") or it.get("summary") or it["title"]\n'
        '        what = __import__("re").sub(r"\\s+", " ", what).strip()\n'
        '        lines.append(f"- **发生了什么**: {what}")\n'
    )
    if old_m not in code:
        raise SystemExit("base script structure changed; cannot inject markdown")
    code = code.replace(old_m, new_m, 1)

    # Write temp module and execute
    tmp = Path(tempfile.gettempdir()) / "global_news_24h_pipeline.py"
    tmp.write_text(code, encoding="utf-8")
    print(f"[bootstrap] running patched pipeline ({tmp})…")
    sys.path.insert(0, str(HERE))
    runpy.run_path(str(tmp), run_name="__main__")


if __name__ == "__main__":
    main()
