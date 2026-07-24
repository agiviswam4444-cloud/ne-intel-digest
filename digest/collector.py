"""Collection layer. No AI except the optional TIER 4 screenshot fallback.

Candidate dict:
  headline, summary, url, outlet, pub_dt (aware datetime or None), date_source, state?, section?
date_source in {rss-pubdate, x-api, news-rss-unverified}
Only rss-pubdate and x-api are pre-verified; everything else goes to verifier.

Direct-outlet feeds run through a 5-tier fetch cascade (fetch_direct_fast for
the network tiers 0-2, fetch_slow_tiers for the browser tiers 3-4). The winning
tier is recorded per source in source_status as "ok:tierN".
"""
import os, re, time, json, urllib.parse, datetime
import requests, feedparser
from dateutil import parser as dtp
from bs4 import BeautifulSoup

from . import ai

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
# TIER 1 rotates through these when the browser UA above is blocked.
ALT_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
]
TIMEOUT = 20
BACKOFF = 10   # seconds between TIER 1 user-agent retries


def _outlet_from_url(url):
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
        return re.sub(r"^www\.", "", host)
    except Exception:
        return "unknown"


def _homepage(url):
    p = urllib.parse.urlparse(url)
    return f"{p.scheme}://{p.netloc}/"


def _parse_feed(url):
    r = requests.get(url, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return feedparser.parse(r.content)


def _entry_dt(e):
    for k in ("published", "updated"):
        v = e.get(k)
        if v:
            try:
                d = dtp.parse(v)
                if d.tzinfo is None:
                    d = d.replace(tzinfo=datetime.timezone.utc)
                return d
            except Exception:
                pass
    return None


def _summary_from_entry(e, headline):
    """One-line summary from the RSS/Atom description: HTML stripped, first
    sentence only, max 160 chars. Falls back to the headline."""
    raw = e.get("summary") or e.get("description") or ""
    if not raw:
        # Atom content array
        for c in (e.get("content") or []):
            if c.get("value"):
                raw = c["value"]
                break
    text = BeautifulSoup(raw, "html.parser").get_text(" ") if raw else ""
    text = re.sub(r"\s+", " ", text).strip()
    # Many feeds ship a boilerplate description (just a logo link / site brand).
    # Too short to be a real summary -> fall back to the headline.
    if len(text) < 20:
        return headline
    m = re.match(r"(.+?[.!?])(?:\s|$)", text)
    first = m.group(1) if m else text
    return first[:160].strip()


def _image_from_entry(e):
    """Best-effort thumbnail URL from RSS/Atom media fields or the first <img>."""
    for key in ("media_thumbnail", "media_content"):
        for m in (e.get(key) or []):
            u = m.get("url")
            if u and re.search(r"\.(jpg|jpeg|png|webp|gif)", u, re.I) or (
                    u and m.get("medium") == "image"):
                return u
    for enc in (e.get("enclosures") or []):
        if (enc.get("type") or "").startswith("image") and enc.get("href"):
            return enc["href"]
    raw = e.get("summary") or e.get("description") or ""
    for c in (e.get("content") or []):
        raw = raw or c.get("value") or ""
    m = re.search(r'<img[^>]+src="([^"]+)"', raw)
    return m.group(1) if m else None


def _resolve_gnews(url):
    """Google News wraps article URLs; try to unwrap the real link."""
    if "news.google.com" not in url:
        return url
    try:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        if "url" in q:
            return q["url"][0]
    except Exception:
        pass
    return url


def _split_direct(fp, outlet, start, end):
    """Trusted-pubDate feed -> (in_window, unknown_date) candidate lists."""
    in_win, unknown = [], []
    for e in fp.entries[:50]:
        title = (e.get("title") or "").strip()
        link = (e.get("link") or "").strip()
        if not title or not link:
            continue
        d = _entry_dt(e)
        c = {"headline": title, "summary": _summary_from_entry(e, title),
             "url": link, "outlet": outlet, "pub_dt": d,
             "image_url": _image_from_entry(e)}
        if d is None:
            c["date_source"] = "news-rss-unverified"
            unknown.append(c)
        elif start <= d < end:
            c["date_source"] = "rss-pubdate"
            in_win.append(c)
        # outside window -> discard silently
    return in_win, unknown


def _news_entries(fp):
    """Google/Bing News feed -> UNVERIFIED candidates (verifier gates dates)."""
    out = []
    for e in fp.entries[:20]:
        title = (e.get("title") or "").strip()
        link = _resolve_gnews((e.get("link") or "").strip())
        if not title or not link:
            continue
        outlet = _outlet_from_url(link)
        m = re.match(r"^(.*)\s+-\s+([^-]{2,40})$", title)
        if m and "news.google.com" in link:
            title, outlet = m.group(1).strip(), m.group(2).strip()
        out.append({"headline": title, "summary": _summary_from_entry(e, title),
                    "url": link, "outlet": outlet, "image_url": _image_from_entry(e),
                    "pub_dt": _entry_dt(e), "date_source": "news-rss-unverified"})
    return out


# ---------------------------------------------------------------------------
# TIER 1 — cloudscraper + alternate user-agents + backoff
# ---------------------------------------------------------------------------
def _parse_feed_cloudscraper(url):
    import cloudscraper
    scraper = cloudscraper.create_scraper()
    for i, ua in enumerate(ALT_UAS):
        try:
            r = scraper.get(url, headers={"User-Agent": ua}, timeout=TIMEOUT)
            if r.status_code == 200 and r.content:
                fp = feedparser.parse(r.content)
                if fp.entries:
                    return fp
        except Exception:
            pass
        if i < len(ALT_UAS) - 1:
            time.sleep(BACKOFF)
    return None


# ---------------------------------------------------------------------------
# TIER 2 — headless Chrome (Playwright): render homepage, scrape DOM headlines.
# No feed = no date, so items are stamped with the capture time (`stamp`).
# ---------------------------------------------------------------------------
def _crawl_dom(url, outlet, stamp, cap):
    from playwright.sync_api import sync_playwright
    home = _homepage(url)
    anchors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=UA["User-Agent"])
            page.goto(home, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)   # let JS render
            anchors = page.eval_on_selector_all(
                "a", "els => els.map(e => ({t:(e.innerText||'').trim(), h:e.href}))")
        finally:
            browser.close()
    out, seen = [], set()
    for a in anchors:
        t, h = (a.get("t") or "").strip(), (a.get("h") or "")
        if len(t) < 25 or not h.startswith("http") or h in seen:
            continue
        # skip obvious non-article links (home/section/tag pages)
        if re.search(r"/(category|tag|author|page|about|contact)/", h, re.I):
            continue
        seen.add(h)
        out.append({"headline": t, "summary": t, "url": h, "outlet": outlet,
                    "image_url": None, "pub_dt": stamp, "date_source": "crawl"})
        if len(out) >= cap:
            break
    return out


# ---------------------------------------------------------------------------
# TIER 3 — full-page screenshot -> claude-haiku-4-5 vision (budget-capped)
# ---------------------------------------------------------------------------
def _screenshot_ocr(url, outlet, stamp):
    from playwright.sync_api import sync_playwright
    home = _homepage(url)
    png = None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=UA["User-Agent"])
            page.goto(home, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)
            png = page.screenshot(full_page=True)
        finally:
            browser.close()
    cands = ai.extract_from_screenshot(png, outlet, home)
    for c in cands:            # OCR rarely yields a reliable date -> stamp
        c["pub_dt"], c["date_source"] = stamp, "screenshot"
    return cands


# ---------------------------------------------------------------------------
# Cascade entry points.  Order = RSS -> cloudscraper RSS -> crawl -> screenshot.
# ---------------------------------------------------------------------------
def fetch_fast(url, start, end):
    """Feed tiers 0-1 (thread-safe). Returns (in_window, unknown, status).
    status is 'ok:tier0|tier1' or 'fast-exhausted' (feedless -> browser tiers)."""
    outlet = _outlet_from_url(url)
    # TIER 0 — requests + feedparser, browser UA
    try:
        fp = _parse_feed(url)
        if fp.entries:
            iw, un = _split_direct(fp, outlet, start, end)
            return iw, un, "ok:tier0"
    except Exception:
        pass
    # TIER 1 — cloudscraper + alternate UAs + 10s backoff
    try:
        fp = _parse_feed_cloudscraper(url)
        if fp and fp.entries:
            iw, un = _split_direct(fp, outlet, start, end)
            return iw, un, "ok:tier1"
    except Exception:
        pass
    return [], [], "fast-exhausted"


def fetch_slow(url, stamp, cap, shot_state):
    """Browser tiers 2-3 for feedless papers (main thread — Playwright is not
    thread-safe). Items are stamped with `stamp` (capture time). Returns
    (candidates, status)."""
    outlet = _outlet_from_url(url)
    # TIER 2 — headless-Chrome DOM crawl
    try:
        cands = _crawl_dom(url, outlet, stamp, cap)
        if cands:
            return cands, "ok:crawl"
    except Exception:
        pass
    # TIER 3 — screenshot -> Claude vision (needs API key, budget-capped)
    if not ai.have_key():
        return [], "screenshot-skipped-no-key"
    if shot_state["used"] >= shot_state["max"]:
        return [], "exhausted-all-tiers"
    shot_state["used"] += 1
    try:
        cands = _screenshot_ocr(url, outlet, stamp)
        if cands:
            return cands, "ok:screenshot"
    except Exception:
        pass
    return [], "exhausted-all-tiers"


def google_news_url(query):
    q = urllib.parse.quote_plus(query)
    return f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"


def bing_news_url(query):
    q = urllib.parse.quote_plus(query)
    return f"https://www.bing.com/news/search?q={q}&format=RSS"


def fetch_news_rss(feed_url, label):
    """Google/Bing News RSS — all UNVERIFIED. Returns (candidates, status)."""
    try:
        fp = _parse_feed(feed_url)
    except Exception as ex:
        return [], f"error:{type(ex).__name__}"
    return _news_entries(fp), "ok:tier0"


def fetch_x_handles(cfg, start, end):
    """X API v2 recent search per handle. Pre-verified via created_at."""
    results, statuses = [], {}
    token_path = os.path.expanduser(cfg["bearer_token_file"])
    if not os.path.exists(token_path):
        return [], {h: "token-file-missing" for h in cfg["handles"]}
    try:
        token = open(token_path).read().strip()
    except OSError as ex:
        return [], {h: f"token-file-unreadable:{type(ex).__name__}"
                    for h in cfg["handles"]}
    s_iso = start.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    e_iso = end.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for handle, meta in cfg["handles"].items():
        url = ("https://api.twitter.com/2/tweets/search/recent"
               f"?query=from:{handle}&max_results=10"
               f"&tweet.fields=created_at,text&start_time={s_iso}&end_time={e_iso}")
        try:
            r = requests.get(url, headers={"Authorization": f"Bearer {token}"},
                             timeout=TIMEOUT)
            if r.status_code == 429:
                time.sleep(15)
                r = requests.get(url, headers={"Authorization": f"Bearer {token}"},
                                 timeout=TIMEOUT)
            if r.status_code == 401:
                statuses[handle] = "auth-failed"
                continue
            if r.status_code == 429:
                statuses[handle] = "rate-limited"
                continue
            r.raise_for_status()
            data = r.json().get("data", []) or []
            for t in data:
                text = re.sub(r"\s+", " ", t.get("text", "")).strip()
                head = text[:150].rsplit(" ", 1)[0] if len(text) > 150 else text
                created = dtp.parse(t["created_at"])
                results.append({
                    "headline": head, "summary": head,
                    "url": f"https://x.com/{handle}/status/{t['id']}",
                    "outlet": f"X / @{handle}", "pub_dt": created,
                    "date_source": "x-api",
                    "state": meta["state"], "section": meta["section"]})
            statuses[handle] = f"ok:{len(data)}"
        except Exception as ex:
            statuses[handle] = f"error:{type(ex).__name__}"
    return results, statuses
