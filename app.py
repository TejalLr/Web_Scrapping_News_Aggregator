import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import feedparser
import httpx
from dateutil import parser as dtparser
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel
import html

# ----- Optional fuzzy matching (graceful fallback) -----
try:
    from rapidfuzz import fuzz

    def title_similarity_score(a: str, b: str) -> int:
        return fuzz.token_set_ratio(a, b)
    FUZZY_AVAILABLE = True
except Exception:
    def title_similarity_score(a: str, b: str) -> int:
        return 100 if a.strip().lower() == b.strip().lower() else 0
    FUZZY_AVAILABLE = False

APP_NAME = "Sports News Aggregator"
app = FastAPI(title=APP_NAME, version="1.3")

# ---- Logging ----
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
log = logging.getLogger("sports-agg")

# ---- Configure sources (RSS) ----
SPORT_FEEDS: Dict[str, List[str]] = {
    "soccer": [
        "https://www.espn.com/espn/rss/soccer/news",
        "https://www.skysports.com/rss/12040",
        "https://www.goal.com/feeds/en/news",
        "https://www.bbc.co.uk/sport/football/rss.xml",
    ],
    "basketball": [
        "https://www.espn.com/espn/rss/nba/news",
        "https://www.skysports.com/rss/12040-11683",
        "https://www.cbssports.com/rss/headlines/nba/",
    ],
    "baseball": [
        "https://www.espn.com/espn/rss/mlb/news",
        "https://www.cbssports.com/rss/headlines/mlb/",
        "https://www.mlbtraderumors.com/feed",
    ],
    "cricket": [
        "https://www.espncricinfo.com/rss/content/story/feeds/0.xml",
        "https://www.skysports.com/rss/12040-12341",
        "https://www.icc-cricket.com/rss/news",
    ],
    "tennis": [
        "https://www.atptour.com/en/media/rss-feed/xml-feed",
        "https://www.wtatennis.com/rss",
        "https://www.skysports.com/rss/12040-13835",
        "https://www.bbc.co.uk/sport/tennis/rss.xml",
    ],
    # --- Added sports ---
    "nfl": [
        "https://www.espn.com/espn/rss/nfl/news",
        "https://www.cbssports.com/rss/headlines/nfl/",
        "https://feeds.bbci.co.uk/sport/american-football/rss.xml",
    ],
    "nhl": [
        "https://www.espn.com/espn/rss/nhl/news",
        "https://www.cbssports.com/rss/headlines/nhl/",
        "https://feeds.bbci.co.uk/sport/ice-hockey/rss.xml",
    ],
    "f1": [
        "https://www.espn.com/espn/rss/f1/news",
        "https://feeds.bbci.co.uk/sport/formula1/rss.xml",
        "https://www.autosport.com/rss/f1/all.xml",
    ],
    "ufc": [
        "https://www.espn.com/espn/rss/mma/news",
        "https://www.cbssports.com/rss/headlines/mma/",
        "https://www.bloodyelbow.com/rss/index.xml",
    ],
    "golf": [
        "https://www.espn.com/espn/rss/golf/news",
        "https://www.cbssports.com/rss/headlines/golf/",
        "https://feeds.bbci.co.uk/sport/golf/rss.xml",
    ],
    "ncaaf": [
        "https://www.espn.com/espn/rss/ncf/news",
        "https://www.cbssports.com/rss/headlines/ncaaf/",
    ],
    "ncaab": [
        "https://www.espn.com/espn/rss/ncb/news",
        "https://www.cbssports.com/rss/headlines/ncaab/",
    ],
    "formulae": [
        "https://www.fiaformulae.com/en/news?format=rss",
        "https://www.autosport.com/rss/formula-e/all.xml",
    ],
}

DEFAULT_SPORTS = sorted(SPORT_FEEDS.keys())

# ---- Models ----
class Article(BaseModel):
    title: str
    link: str
    published: Optional[str] = None   # ISO 8601 UTC (Z)
    source: Optional[str] = None
    summary: Optional[str] = None
    sport: Optional[str] = None

# ---- Helpers ----
UA = "Mozilla/5.0 (compatible; SportsNewsAggregator/1.3; +https://example.com)"
HTTP_TIMEOUT = 12.0

# Common timezone abbreviations seen in RSS feeds
TZINFOS = {
    "UTC": 0, "GMT": 0,
    "EST": -18000, "EDT": -14400,
    "CST": -21600, "CDT": -18000,
    "MST": -25200, "MDT": -21600,
    "PST": -28800, "PDT": -25200,
    "BST": 3600,  "CEST": 7200, "CET": 3600,
}

def get_domain(url: str) -> str:
    try:
        return re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
    except Exception:
        return ""

def normalize_title(t: str) -> str:
    t = t.lower().strip()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[^a-z0-9\s]", "", t)
    return t

def parse_to_dt_utc(dt_str: Optional[str]) -> Optional[datetime]:
    """Parse many date formats and return a timezone-aware UTC datetime."""
    if not dt_str:
        return None
    try:
        dt = dtparser.parse(dt_str, tzinfos=TZINFOS)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

def to_iso8601_utc(dt_str: Optional[str]) -> Optional[str]:
    dt = parse_to_dt_utc(dt_str)
    return dt.isoformat().replace("+00:00", "Z") if dt else None

async def fetch_feed(client: httpx.AsyncClient, url: str) -> feedparser.FeedParserDict:
    t0 = time.perf_counter()
    try:
        resp = await client.get(url, timeout=HTTP_TIMEOUT, headers={"User-Agent": UA})
        resp.raise_for_status()
        parsed = feedparser.parse(resp.text)
        took = (time.perf_counter() - t0) * 1000
        log.info(f"Fetched {url} [{len(parsed.entries)} entries] in {took:.0f} ms")
        return parsed
    except httpx.HTTPError as e:
        took = (time.perf_counter() - t0) * 1000
        log.warning(f"HTTP error for {url} after {took:.0f} ms: {e}")
    except Exception as e:
        took = (time.perf_counter() - t0) * 1000
        log.error(f"Unexpected error for {url} after {took:.0f} ms: {e}")
    return feedparser.parse("")

def entry_to_article(entry, sport: str) -> Article:
    title = entry.get("title", "").strip()
    link = (entry.get("link") or "").strip()
    published_raw = (
        entry.get("published")
        or entry.get("updated")
        or entry.get("pubDate")
        or None
    )
    summary = (entry.get("summary") or entry.get("description") or "").strip()

    source = ""
    if "source" in entry and entry["source"]:
        source = (entry["source"].get("title") or "").strip()
    if not source:
        source = get_domain(link)

    iso_pub = to_iso8601_utc(published_raw)
    if published_raw and not iso_pub:
        log.warning(f"Could not parse date for {link!r}: {published_raw!r}")

    return Article(
        title=title,
        link=link,
        published=iso_pub,
        source=source or None,
        summary=summary or None,
        sport=sport
    )

def dedupe_articles(articles: List[Article], title_similarity: int = 90) -> List[Article]:
    """
    Deduplicate by:
      1) exact link
      2) near-duplicate titles (per domain) using RapidFuzz if available
    Keep the item that has (a) a published date, (b) longer summary as tie-breaker.
    """
    seen_links = set()
    unique: List[Article] = []
    for a in articles:
        if not a.link or a.link in seen_links:
            continue
        seen_links.add(a.link)
        unique.append(a)

    def _length(s: Optional[str]) -> int:
        return len(s or "")

    unique_sorted = sorted(
        unique,
        key=lambda x: (x.published is not None, _length(x.summary)),
        reverse=True,
    )

    result: List[Article] = []
    seen_titles_per_domain: Dict[str, List[str]] = {}

    for a in unique_sorted:
        domain = get_domain(a.link)
        norm_title = normalize_title(a.title)
        dup_found = False

        if domain not in seen_titles_per_domain:
            seen_titles_per_domain[domain] = []

        for t in seen_titles_per_domain[domain]:
            if title_similarity_score(norm_title, t) >= title_similarity:
                dup_found = True
                break

        if not dup_found:
            result.append(a)
            seen_titles_per_domain[domain].append(norm_title)

    def sort_key(x: Article):
        ts = parse_to_dt_utc(x.published) if x.published else None
        fallback = datetime.min.replace(tzinfo=timezone.utc)
        return (ts is not None, ts or fallback, x.title.lower())

    result.sort(key=sort_key, reverse=True)
    return result

# ---- API ----

@app.get("/sports", response_model=List[Article], summary="Aggregate sports news into one JSON")
async def get_sports_news(
    sport: Optional[str] = Query(None, description=f"One sport (e.g. {', '.join(DEFAULT_SPORTS)}). If omitted, aggregates all."),
    save: bool = Query(False, description="If true, also writes a JSON file to disk on the server."),
    title_sim_threshold: int = Query(90, ge=0, le=100, description="Fuzzy title match threshold (0-100) for dedupe (higher = stricter)."),
    limit_per_feed: int = Query(50, ge=1, le=200, description="Soft cap per feed to keep responses snappy."),
    debug: bool = Query(False, description="Return meta stats along with items."),
    log_feeds: bool = Query(False, description="Log per-feed entry counts."),
) -> List[Article] | dict:
    if sport:
        sport = sport.lower()
        if sport not in SPORT_FEEDS:
            raise HTTPException(status_code=400, detail=f"Unknown sport '{sport}'. Valid: {', '.join(DEFAULT_SPORTS)}")
        feeds_plan = {sport: SPORT_FEEDS[sport]}
    else:
        feeds_plan = SPORT_FEEDS

    tasks = []
    async with httpx.AsyncClient(follow_redirects=True, headers={"User-Agent": UA}) as client:
        for s, feed_urls in feeds_plan.items():
            for url in feed_urls:
                tasks.append(fetch_feed(client, url))
        feeds = await asyncio.gather(*tasks)

    articles: List[Article] = []
    idx = 0
    for s, feed_urls in feeds_plan.items():
        for _ in feed_urls:
            parsed = feeds[idx]
            idx += 1
            entries = parsed.entries[:limit_per_feed] if parsed and parsed.entries else []
            if log_feeds:
                log.info(f"[{s}] +{len(entries)} entries")
            for e in entries:
                art = entry_to_article(e, sport=s)
                if not art.title or not art.link:
                    continue
                articles.append(art)

    if log_feeds:
        log.info(f"Collected {len(articles)} raw entries before dedupe")

    deduped = dedupe_articles(articles, title_similarity=title_sim_threshold)

    if save:
        filename = f"sports_news_{sport or 'all'}.json"
        with open(filename, "w", encoding="utf-8") as f:
            json.dump([a.model_dump() for a in deduped], f, ensure_ascii=False, indent=2)
        log.info(f"Wrote {len(deduped)} items to {filename}")

    if debug:
        return {
            "items": deduped,
            "meta": {
                "sport": sport or "all",
                "items": len(deduped),
                "raw": len(articles),
                "title_sim_threshold": title_sim_threshold,
                "limit_per_feed": limit_per_feed,
                "fuzzy_available": FUZZY_AVAILABLE,
            },
        }

    return deduped

@app.get("/sports/html", response_class=HTMLResponse, summary="View sports news in HTML")
async def sports_html(
    sport: Optional[str] = Query(None, description=f"One sport (e.g. {', '.join(DEFAULT_SPORTS)}). If omitted, aggregates all."),
    title_sim_threshold: int = Query(90, ge=0, le=100, description="Fuzzy title match threshold (0-100) for dedupe."),
    limit_per_feed: int = Query(50, ge=1, le=200, description="Soft cap per feed per source."),
):
    items_or_dict = await get_sports_news(
        sport=sport,
        save=False,
        title_sim_threshold=title_sim_threshold,
        limit_per_feed=limit_per_feed,
        debug=False,
        log_feeds=False,
    )
    articles: List[Article] = items_or_dict  # type: ignore
    title_text = f"{APP_NAME} – {sport.capitalize() if sport else 'All Sports'}"

    parts = []
    parts.append(f"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title_text)}</title>
<style>
  :root {{
    --bg:#0b0f14; --card:#121821; --text:#e8eef6; --muted:#9fb0c3; --accent:#3aa7ff; --chip:#1a2230;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text); font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,"Apple Color Emoji","Segoe UI Emoji"; }}
  header {{
    padding:24px 16px; border-bottom:1px solid #1e2733; position:sticky; top:0;
    background:linear-gradient(180deg, rgba(11,15,20,.98), rgba(11,15,20,.92)); backdrop-filter:blur(6px); z-index:10;
  }}
  .wrap {{ max-width:1100px; margin:0 auto; }}
  h1 {{ margin:0 0 6px; font-size:22px; }}
  .sub {{ color:var(--muted); font-size:14px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:14px; padding:16px; }}
  .card {{
    background:var(--card); border:1px solid #1e2733; border-radius:14px; padding:14px; display:flex; flex-direction:column; gap:10px;
    transition:transform .12s ease, border-color .12s ease;
  }}
  .card:hover {{ transform:translateY(-2px); border-color:#2a3749; }}
  .title {{ font-weight:600; line-height:1.25; font-size:16px; }}
  .meta {{ display:flex; gap:8px; flex-wrap:wrap; color:var(--muted); font-size:12px; }}
  .chip {{ background:var(--chip); padding:2px 8px; border-radius:999px; border:1px solid #202a38; }}
  .summary {{ color:#cfd9e6; font-size:14px; line-height:1.35; }}
  a.link {{ color:var(--accent); text-decoration:none; }}
  a.link:hover {{ text-decoration:underline; }}
  .toolbar {{ margin-top:8px; display:flex; gap:12px; align-items:center; flex-wrap:wrap; }}
  .pill {{
    display:inline-flex; align-items:center; gap:8px; background:var(--chip); border:1px solid #223044; padding:6px 10px; border-radius:999px; color:var(--text); text-decoration:none; font-size:13px;
  }}
  .pill:hover {{ border-color:#2f415a; }}
  footer {{ color:var(--muted); font-size:12px; padding:18px 16px 36px; text-align:center; }}
</style>
</head>
<body>
<header>
  <div class="wrap">
    <h1>{html.escape(title_text)}</h1>
    <div class="sub">De-duplicated headlines from multiple sources. Showing {len(articles)} items.</div>
    <div class="toolbar">
      <a class="pill" href="/sports{('?sport='+sport if sport else '')}">JSON</a>
      <a class="pill" href="/sources">Sources</a>
      <a class="pill" href="/health">Health</a>
    </div>
  </div>
</header>
<main class="wrap">
  <section class="grid">
""")
    for a in articles:
        t = html.escape(a.title or "")
        link = html.escape(a.link or "#")
        pub = html.escape(a.published or "")
        src = html.escape(a.source or "")
        sum_ = html.escape((a.summary or "")[:320] + ("…" if a.summary and len(a.summary) > 320 else ""))
        sport_chip = html.escape(a.sport or "")
        parts.append(f"""
    <article class="card">
      <div class="title"><a class="link" href="{link}" target="_blank" rel="noopener noreferrer">{t}</a></div>
      <div class="meta">
        {'<span class="chip">' + sport_chip + '</span>' if sport_chip else ''}
        {'<span class="chip">' + src + '</span>' if src else ''}
        {'<span class="chip">' + pub + '</span>' if pub else ''}
      </div>
      <div class="summary">{sum_}</div>
      <div><a class="pill" href="{link}" target="_blank" rel="noopener noreferrer">Open article ↗</a></div>
    </article>
""")
    parts.append("""
  </section>
</main>
<footer>
  Built with FastAPI. Switch to <a class="link" href="/sports">JSON</a>.
</footer>
</body>
</html>
""")
    return HTMLResponse("".join(parts))

@app.get("/health", summary="Check feed health and basic service status")
async def health():
    summary = {"service": APP_NAME, "ok": True, "feeds": []}
    async with httpx.AsyncClient(follow_redirects=True, headers={"User-Agent": UA}) as client:
        tasks, plan = [], []
        for sport, urls in SPORT_FEEDS.items():
            for url in urls:
                tasks.append(client.get(url, timeout=HTTP_TIMEOUT))
                plan.append((sport, url))
        results = await asyncio.gather(*tasks, return_exceptions=True)

    ok_count = 0
    for (sport, url), res in zip(plan, results):
        item = {"sport": sport, "url": url}
        if isinstance(res, Exception):
            item.update({"ok": False, "status": None, "error": str(res)})
        else:
            item.update({"ok": res.is_success, "status": res.status_code})
        summary["feeds"].append(item)
        if item["ok"]:
            ok_count += 1

    summary["feed_ok"] = ok_count
    summary["feed_total"] = len(plan)
    summary["ok"] = ok_count == len(plan)
    return JSONResponse(summary, status_code=(200 if summary["ok"] else 207))

@app.get("/sources", summary="List configured sports and feed URLs")
def sources():
    return [{"sport": s, "feeds": SPORT_FEEDS[s]} for s in sorted(SPORT_FEEDS)]
