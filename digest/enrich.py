"""Location enrichment — find WHERE an incident happened. No AI.

Most stories carry no place name in the headline/summary, so ~2/3 cannot be
mapped from the feed alone. This pass fetches the article for stories that are
still unlocated and looks for a place in the *article body*, preferring the
dateline ("IMPHAL, Aug 5:") which by newspaper convention IS the incident
location.

Measured on a real sample: strict article-body extraction recovers a location
for ~14% of unlocated security stories, and those hits are accurate. A naive
whole-page scan appeared to do better (~27%) but roughly half of those were the
NEWSPAPER's own city picked up from site navigation — worse than useless for a
radius search, since it clusters everything at newspaper offices. Hence the
strict selectors and the nav/footer/aside stripping below.

Runs AFTER stories are stored, is capped per run, and never blocks collection.
"""
import re
import concurrent.futures as cf

import requests
from bs4 import BeautifulSoup

from . import geo, db

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
TIMEOUT = 15

# Article-body containers, tried in order. Site chrome (nav/footer/aside) is
# removed first so the newspaper's own address cannot be mistaken for the story.
_SELECTORS = ["article", '[itemprop="articleBody"]', ".entry-content",
              ".post-content", ".story-body", ".article-content",
              ".td-post-content", ".single-content", ".content-body", "main"]
_STRIP = ["script", "style", "nav", "footer", "header", "aside", "form",
          "iframe", "noscript"]

# The dateline zone: the first stretch of the body, where "PLACE, Date:" sits.
_DATELINE_CHARS = 300
_BODY_CHARS = 1500


# Story tag metadata. MEASURED on 16 unlocated stories: meta keywords yielded 3
# new locations (e.g. "Jorhat Health Camp" -> Jorhat, "Biswanath Chariali" ->
# Biswanath) that the body pass missed entirely.
#
# `articleSection` is deliberately NOT used: its values are desk names
# ("National", "Sports", "Videos", "JOCO SERIOUS") and its only geo-resolvable
# value was the newspaper's own city — the exact bias this module avoids.
_META_KEYWORDS = re.compile(
    r'<meta[^>]+name=["\']keywords["\'][^>]+content=["\']([^"\']+)', re.I)


def _meta_keywords(html):
    m = _META_KEYWORDS.search(html)
    return m.group(1) if m else None


def _article_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(_STRIP):
        t.decompose()
    for sel in _SELECTORS:
        el = soup.select_one(sel)
        if el:
            txt = re.sub(r"\s+", " ", el.get_text(" ")).strip()
            if len(txt) > 200:
                return txt
    return None


def locate_one(url, state):
    """Fetch an article and try to locate it. Returns (lat, lon, place, src)."""
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        html = r.text
        txt = _article_text(html)
    except Exception:
        return None
    if txt:
        # 1) dateline zone — most reliable: it names the incident location
        hit = geo.geolocate(txt[:_DATELINE_CHARS], state)
        if hit:
            return hit[0], hit[1], hit[2], "dateline"
        # 2) wider body — still inside the article, so no site chrome
        hit = geo.geolocate(txt[:_BODY_CHARS], state)
        if hit:
            return hit[0], hit[1], hit[2], "body"
    # 3) story tag metadata — last resort. Ordered last on purpose: keywords can
    #    name a person's home city rather than the incident site, so it must
    #    never override a dateline or in-body location.
    kws = _meta_keywords(html)
    if kws:
        hit = geo.geolocate(kws, state)
        if hit:
            return hit[0], hit[1], hit[2], "meta-keywords"
    return None


def enrich_locations(con, cfg, log=print):
    """Locate stored stories that have no coordinates yet.

    Security sections are done first (they are the ones a radius search is
    about). Capped by `enrich_max_per_run` so a slow site can never stall the
    30-minute cycle.
    """
    cap = int(cfg.get("enrich_max_per_run", 60) or 0)
    if cap <= 0:
        return {"attempted": 0, "located": 0}
    days = int(cfg.get("enrich_days", 3) or 3)
    rows = [dict(r) for r in con.execute(
        "SELECT url, state, section FROM stories "
        "WHERE lat IS NULL AND geo_src IS NULL "
        "  AND pub_date >= date('now', ?) "
        "ORDER BY CASE WHEN section BETWEEN 1 AND 10 THEN 0 ELSE 1 END, "
        "         first_seen DESC LIMIT ?", (f"-{days} day", cap))]
    if not rows:
        return {"attempted": 0, "located": 0}

    found = 0

    def _job(r):
        return r, locate_one(r["url"], r["state"])

    # 4, not 8: measured that 8 concurrent fetches against the same outlet get
    # refused (ConnectionError on every request), which silently inflated the
    # "attempted but not located" count. Lower concurrency is slower per run but
    # actually returns pages.
    with cf.ThreadPoolExecutor(4) as ex:
        for r, hit in ex.map(_job, rows):
            if hit:
                db.set_geo(con, r["url"], hit[0], hit[1], hit[2], hit[3])
                found += 1
            else:
                # mark as attempted so the next run does not refetch it forever
                db.set_geo(con, r["url"], None, None, None, "none")
    con.commit()
    log(f"Location enrichment: {found}/{len(rows)} newly located")
    return {"attempted": len(rows), "located": found}
