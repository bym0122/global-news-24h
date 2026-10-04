# global-news-24h

**Zero-cost 24-hour global news bot** powered purely by GitHub Actions + free RSS sources.

No paid APIs, no TinyFish, no OpenAI quota required.

## What it does

Every few hours GitHub Actions:

1. Fetches free public RSS feeds (Reuters, BBC, AP, Google News topic feeds, etc.)
2. Filters to the **last 24 hours**
3. Deduplicates by title similarity + URL
4. Scores importance by source weight + keyword hits
5. Classifies into 10 categories
6. Generates structured digests (Markdown + JSON)
7. Commits results back to `data/news/`

## Categories

| Emoji | Category (中文) | Focus |
|-------|-----------------|-------|
| 🌍 | 地缘政治 | 美伊、中美、俄乌、台湾、朝鲜 |
| 🛢️ | 能源 | 原油、天然气、OPEC、霍尔木兹 |
| 💰 | 全球金融 | 美联储、美元、美债、黄金 |
| 🇨🇳 | 中国 | 政策、经济、房地产、出口 |
| 🇺🇸 | 美国 | 白宫、经济数据、科技政策 |
| 🇪🇺 | 欧洲 | 欧盟、法国、德国、ECB |
| 🇯🇵 | 亚洲 | 日本、韩国、印度、东南亚 |
| 🤖 | AI/科技 | NVIDIA、OpenAI、Google、Meta、芯片 |
| 🚢 | 大宗商品 | 铝、铜、煤炭、铁矿、航运 |
| 📈 | 市场 | 美股、A股、港股重要事件 |

Each story includes:

- 重要性 ★★★★★
- 事件
- 发生了什么
- 为什么重要
- 可能影响
- 涉及资产

## Project structure

```
global-news-24h/
├── .github/workflows/news.yml
├── scripts/
│   └── fetch_news.py          # main pipeline
├── data/news/                 # daily outputs (auto-committed)
├── requirements.txt
└── README.md
```

## Schedule

Default (UTC):

- `0 */2 * * *` → every 2 hours
- Manual trigger available in Actions tab

You can change the cron in `.github/workflows/news.yml`.

Suggested later:
- 01:00 UTC → 早报
- 06:00 UTC → 午间
- 12:00 UTC → 晚间
- 16:10 UTC → 过去24小时最终版

## Local run

```bash
pip install -r requirements.txt
python scripts/fetch_news.py
```

Output appears in `data/news/YYYY-MM-DD.md` and `.json`.

## Design principles (v1)

- **Pure free**: only public RSS + Python stdlib + feedparser
- Source weighting (Reuters/AP/BBC/Bloomberg higher score)
- Keyword boost for high-impact terms (war, rate cut, oil, tariff…)
- Title similarity dedup (simple Jaccard / token overlap)
- No LLM required for first version (structured templates + Chinese labels)
- Later you can plug free models for better summarization

## Next upgrades (optional)

1. Better Chinese summarization with free models
2. Notion sync
3. Telegram / email push
4. Asset impact mapping expansion
5. Historical trend charts

## License

MIT
