"""Date verification — Subagent G, in deterministic code.

Two-pass protocol, exactly per spec:
  PASS 1: <head> metadata in priority order:
    a. meta property=article:published_time
    b. meta name=publishdate / name=date
    c. meta property=og:article:published_time
    d. JSON-LD datePublished
    e. <time datetime=...> in first 500 chars of body
    f. URL slug date — last resort, reliable outlets only
  PASS 2: first 1500 chars of body text, date near byline.
Never infer. No date found -> EXCLUDE.
"""
import re, datetime
import requests
from bs4 import BeautifulSoup
from dateutil import parser as dtp

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
TIMEOUT = 20
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

_SLUG_PATTERNS = [
    re.compile(r"/((?:19|20)\d{2})[/-](\d{1,2})[/-](\d{1,2})(?:/|$)"),
    re.compile(r"[-/](\d{1,2})-(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*-((?:19|20)\d{2})", re.I),
]
_BODY_DATE = re.compile(
    r"\b(?:(\d{1,2})\s*(?:st|nd|rd|th)?\s+)?"
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+"
    r"(\d{1,2})?,?\s*((?:19|20)\d{2})|"
    r"\b(\d{1,2})[/-](\d{1,2})[/-]((?:19|20)\d{2})|"
    r"\b((?:19|20)\d{2})-(\d{1,2})-(\d{1,2})", re.I)


def _norm(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt


def _try_parse(s):
    try:
        return _norm(dtp.parse(s, fuzzy=False))
    except Exception:
        try:
            return _norm(dtp.parse(s, fuzzy=True))
        except Exception:
            return None


def _pass1(soup, html_head_and_start, url, reliable_outlets):
    # a-c: meta tags
    metas = [
        ("property", "article:published_time"),
        ("name", "publishdate"), ("name", "date"),
        ("property", "og:article:published_time"),
        ("itemprop", "datePublished"), ("name", "parsely-pub-date"),
    ]
    for attr, val in metas:
        tag = soup.find("meta", attrs={attr: val})
        if tag and tag.get("content"):
            d = _try_parse(tag["content"])
            if d:
                return d, "head-meta"
    # d: JSON-LD
    for sc in soup.find_all("script", type="application/ld+json"):
        m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', sc.string or "")
        if m:
            d = _try_parse(m.group(1))
            if d:
                return d, "head-meta"
    # e: <time datetime> in first 500 chars of body
    body = soup.find("body")
    if body:
        first500 = str(body)[:500]
        m = re.search(r'<time[^>]+datetime="([^"]+)"', first500)
        if m:
            d = _try_parse(m.group(1))
            if d:
                return d, "head-meta"
    # f: URL slug — reliable outlets only
    host = re.sub(r"^www\.", "", re.search(r"https?://([^/]+)", url).group(1).lower())
    if any(host.endswith(o) for o in reliable_outlets):
        for pat in _SLUG_PATTERNS:
            m = pat.search(url)
            if m:
                d = _try_parse("-".join(g for g in m.groups() if g))
                if d:
                    return d, "url-slug-reliable-outlet"
    return None, None


def _pass2(soup):
    body = soup.find("body")
    if not body:
        return None
    text = re.sub(r"\s+", " ", body.get_text(" "))[:1500]
    m = _BODY_DATE.search(text)
    if m:
        return _try_parse(m.group(0))
    return None


def verify(candidate, start, end, reliable_outlets):
    """Returns (verdict, detail). verdict in {verified, outside, no-date, fetch-failed}.
    On verified: detail = (pub_dt, date_source, pass_name)."""
    try:
        r = requests.get(candidate["url"], headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        html = r.text
    except Exception:
        return "fetch-failed", None
    soup = BeautifulSoup(html, "html.parser")
    d, src = _pass1(soup, html, candidate["url"], reliable_outlets)
    passname = "pass1"
    if d is None:
        d = _pass2(soup)
        src, passname = "body-text", "pass2"
    if d is None:
        return "no-date", None
    if start <= d < end:
        return "verified", (d, src, passname)
    return "outside", d.astimezone(IST).strftime("%Y-%m-%d")
