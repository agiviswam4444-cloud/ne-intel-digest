"""Public Telegram channel collection. No API key, no login, no account.

Telegram serves every PUBLIC channel as plain HTML at t.me/s/<channel> — the
same preview page a browser shows. That is a documented public endpoint, so
unlike scraping X or Instagram this needs no credentials and breaks no terms.

MEASURED when adding this: Myanmar coverage is the real prize — Irrawaddy,
Mizzima, DVB, Khit Thit, RFA Burmese and BNI all post within hours, and much of
it never reaches their websites' RSS. NE India Telegram is thin by comparison:
only Assam Tribune and The News Mill are active, and both are already collected
via RSS, so they are not duplicated here.

Messages carry a real ISO timestamp, so they are date-trusted like RSS.
"""
import re
import datetime
import concurrent.futures as cf

import requests

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
TIMEOUT = 15
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

_MSG = re.compile(
    r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S)
_TIME = re.compile(r'<time datetime="([^"]+)"')
_LINK = re.compile(r'data-post="([^"]+)"')
_TAG = re.compile(r"<[^>]+>")
_BR = re.compile(r"<br\s*/?>", re.I)


def _clean(html):
    txt = _BR.sub(" ", html)
    txt = _TAG.sub("", txt)
    for a, b in (("&amp;", "&"), ("&quot;", '"'), ("&#39;", "'"),
                 ("&lt;", "<"), ("&gt;", ">"), ("&nbsp;", " ")):
        txt = txt.replace(a, b)
    return re.sub(r"\s+", " ", txt).strip()


def fetch_channel(channel, label, state, start, end, cap=20):
    """Return (candidates, status) for one public channel."""
    url = f"https://t.me/s/{channel}"
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT)
        if r.status_code != 200:
            return [], f"http{r.status_code}"
        html = r.text
    except Exception as ex:
        return [], f"error:{type(ex).__name__}"

    bodies = _MSG.findall(html)
    times = _TIME.findall(html)
    links = _LINK.findall(html)
    if not bodies:
        return [], "no-messages"

    # The three lists run in message order; pair them from the end so the most
    # recent messages line up even when counts differ slightly.
    out = []
    for i in range(1, min(len(bodies), cap) + 1):
        text = _clean(bodies[-i])
        if len(text) < 25:
            continue
        ts = times[-i] if i <= len(times) else None
        post = links[-i] if i <= len(links) else None
        try:
            d = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None
        except Exception:
            d = None
        if d is None or not (start <= d < end):
            continue
        head = text[:150].rsplit(" ", 1)[0] if len(text) > 150 else text
        out.append({
            "headline": head,
            "summary": text[:300],
            "url": f"https://t.me/{post}" if post else url,
            "outlet": f"TG / {label}",
            "image_url": None,
            "pub_dt": d,
            "date_source": "telegram",
            "state": state,
            "trusted": True,
        })
    return out, f"ok:{len(out)}"


def collect(channels, start, end, cap=20, log=print):
    """Fetch all configured channels in parallel. Returns (candidates, statuses)."""
    results, statuses = [], {}

    def _job(ch):
        return ch, fetch_channel(ch["channel"], ch.get("name") or ch["channel"],
                                 ch.get("state"), start, end, cap)

    with cf.ThreadPoolExecutor(5) as ex:
        for ch, (cands, status) in ex.map(_job, channels):
            statuses[ch.get("name") or ch["channel"]] = status
            results += cands
    return results, statuses
