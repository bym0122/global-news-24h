#!/usr/bin/env python3
"""Loader: assemble base64 chunks and exec the real pipeline."""
from pathlib import Path
import base64

here = Path(__file__).resolve().parent
parts = sorted(
    here.glob("fetch_news.b64.*"),
    key=lambda p: int(p.name.rsplit(".", 1)[-1]),
)
if not parts:
    raise SystemExit("missing fetch_news.b64.* chunks")
data = "".join(p.read_text() for p in parts)
code = base64.b64decode(data).decode("utf-8")
ns = {"__name__": "__main__", "__file__": str(here / "fetch_news.py")}
exec(compile(code, str(here / "fetch_news_impl.py"), "exec"), ns)
