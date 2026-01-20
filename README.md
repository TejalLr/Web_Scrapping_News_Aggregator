# Sports News Aggregator

**Version:** 1.3  
**Built with:** [FastAPI](https://fastapi.tiangolo.com/) | [Python 3.10+](https://www.python.org/) | [httpx](https://www.python-httpx.org/) | [Feedparser](https://feedparser.readthedocs.io/)  

A **fast, async, and de-duplicated aggregator** for sports news from multiple RSS sources. It collects news from popular sports (soccer, basketball, cricket, tennis, NFL, MLB, F1, UFC, golf, college sports, and more), removes duplicates, and exposes them via a **JSON API** and a **beautiful HTML interface**.  

---

## Features

- Aggregates news from multiple RSS feeds per sport.
- De-duplicates articles based on:
  - Exact link
  - Similar titles (using [RapidFuzz](https://github.com/maxbachmann/RapidFuzz) if available, fallback to exact match)
- Converts published dates to **ISO 8601 UTC**.
- Async fetching for high performance.
- Optional saving of aggregated results as JSON.
- HTML front-end to browse news with lightweight, responsive styling.
- Health check endpoint for all configured RSS feeds.
- Configurable fuzzy title match threshold, per-feed limits, and debug options.

---

## Sports Covered

- Soccer, Basketball, Baseball, Cricket, Tennis
- NFL, NHL, F1, Formula E, UFC, Golf
- College Sports: NCAA Football (NCAAF), NCAA Basketball (NCAAB)

> All sports and their RSS feeds are configurable in `SPORT_FEEDS`.

---

## Installation


# Clone the repo
git clone https://github.com/yourusername/sports-news-aggregator.git
cd sports-news-aggregator

# Create virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # Linux / macOS
venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt
