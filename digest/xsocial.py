"""X (Twitter) collection via YOUR OWN logged-in browser session.

X removed public/logged-out access and the free API tier is ~100 posts/month,
so a logged-in session is the only no-cost route. This module drives a real
Chromium profile that YOU log into once:

    python run.py login-x        # opens a window; you sign in manually

Your credentials are never seen, typed, stored or transmitted by this code —
only the resulting browser profile (cookies) is kept on your machine, in
data/x_profile/, which is gitignored and never leaves the computer.

READ THIS BEFORE ENABLING (config: x_scrape.enabled)
  Automated access is against X's terms of service. The realistic consequence
  is rate-limiting, restriction or suspension OF THE ACCOUNT WHOSE SESSION IS
  USED. Use a throwaway account created for this purpose, never your main one.
  Polling is deliberately modest (a few targets per run) to stay low-impact.

Everything degrades gracefully: if the profile is missing, the session has
expired, or X changes its markup, the run logs a status and continues.
"""
import os
import re
import datetime

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
PROFILE_DIR = os.path.join("data", "x_profile")

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def profile_exists():
    return os.path.isdir(PROFILE_DIR) and os.listdir(PROFILE_DIR)


def login(timeout_minutes=5):
    """Open a visible browser so the user can sign in to X once.

    This function types nothing: it navigates to the login page and waits while
    the human completes sign-in. The session then persists in PROFILE_DIR.
    """
    from playwright.sync_api import sync_playwright
    os.makedirs(PROFILE_DIR, exist_ok=True)
    print("Opening a browser window. Sign in to X yourself — use a THROWAWAY "
          "account, not your main one.\nThis window closes automatically once "
          "you are signed in (or after %d minutes)." % timeout_minutes)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE_DIR, headless=False, user_agent=_UA,
            viewport={"width": 1280, "height": 900})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        # A transient network blip (ERR_NETWORK_CHANGED on a wifi/VPN switch)
        # would otherwise abort the whole login before the user sees anything.
        for attempt in range(4):
            try:
                page.goto("https://x.com/login", wait_until="domcontentloaded",
                          timeout=45000)
                break
            except Exception as ex:
                if attempt == 3:
                    ctx.close()
                    print(f"Could not reach x.com ({type(ex).__name__}). "
                          "Check the connection and re-run.")
                    return False
                print(f"  network hiccup ({type(ex).__name__}), retrying...")
                page.wait_for_timeout(4000)
        deadline = datetime.datetime.now() + datetime.timedelta(minutes=timeout_minutes)
        ok = False
        while datetime.datetime.now() < deadline:
            page.wait_for_timeout(3000)
            try:
                if "/home" in page.url or page.locator(
                        '[data-testid="SideNav_AccountSwitcher_Button"]').count():
                    ok = True
                    break
            except Exception:
                pass
        ctx.close()
    print("Signed in — session saved." if ok else
          "Did not detect a completed sign-in. Re-run to try again.")
    return ok


def _parse_tweets(page, cap, label, state, start, end):
    """Extract up to `cap` tweets from the current search results page."""
    out = []
    try:
        cards = page.locator('article[data-testid="tweet"]')
        n = min(cards.count(), cap * 3)      # over-scan: some cards are ads
    except Exception:
        return out
    for i in range(n):
        if len(out) >= cap:
            break
        c = cards.nth(i)
        try:
            text = c.locator('[data-testid="tweetText"]').first.inner_text(timeout=2000)
            ts = c.locator("time").first.get_attribute("datetime", timeout=2000)
            href = c.locator('a[href*="/status/"]').first.get_attribute("href", timeout=2000)
        except Exception:
            continue
        if not text or not ts:
            continue
        try:
            d = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            continue
        if not (start <= d < end):
            continue
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 25:
            continue
        url = f"https://x.com{href}" if href and href.startswith("/") else (href or "")
        head = text[:150].rsplit(" ", 1)[0] if len(text) > 150 else text
        out.append({
            "headline": head, "summary": text[:300], "url": url,
            "outlet": f"X / {label}", "image_url": None, "pub_dt": d,
            "date_source": "x-scrape", "state": state, "trusted": True,
        })
    return out


_STAMP = os.path.join("data", ".x_last_run")


def _throttled(minutes):
    """True if X ran less than `minutes` ago. Keeps request volume down — the
    single biggest factor in whether the account gets restricted."""
    if minutes <= 0:
        return False
    try:
        last = os.path.getmtime(_STAMP)
    except OSError:
        return False
    age_min = (datetime.datetime.now().timestamp() - last) / 60
    return age_min < minutes


def _mark_run():
    try:
        os.makedirs(os.path.dirname(_STAMP), exist_ok=True)
        open(_STAMP, "w").write(datetime.datetime.now().isoformat())
    except OSError:
        pass


def _open_context(p, cfg_x):
    """Return (context, cleanup) for either mode.

    CDP mode attaches to a Chrome you are ALREADY logged into, started with
    --remote-debugging-port. Nothing is typed or stored; we just open a tab in
    your existing session. Cleanup closes only our tab, never your browser.
    """
    if cfg_x.get("use_chrome_cdp"):
        url = cfg_x.get("cdp_url") or "http://127.0.0.1:9222"
        browser = p.chromium.connect_over_cdp(url)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        return ctx, (lambda: browser.close())     # detaches; Chrome keeps running
    ctx = p.chromium.launch_persistent_context(
        PROFILE_DIR, headless=True, user_agent=_UA,
        viewport={"width": 1280, "height": 900})
    return ctx, (lambda: ctx.close())


def collect(cfg_x, start, end, log=print):
    """Fetch the configured searches. Returns (candidates, statuses)."""
    queries = cfg_x.get("queries") or []
    if not queries:
        return [], {}
    use_cdp = bool(cfg_x.get("use_chrome_cdp"))
    if not use_cdp and not profile_exists():
        return [], {q.get("q", "?"): "no-session (run: python run.py login-x)"
                    for q in queries}
    wait = int(cfg_x.get("min_interval_minutes", 0) or 0)
    if _throttled(wait):
        return [], {"x-scrape": f"throttled (min_interval_minutes={wait})"}
    _mark_run()
    cap = int(cfg_x.get("per_tab", 10))
    want = [t.lower() for t in (cfg_x.get("tabs") or ["top", "latest"])]
    results, statuses = [], {}
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as p:
            try:
                ctx, cleanup = _open_context(p, cfg_x)
            except Exception as ex:
                hint = (" — is Chrome running with --remote-debugging-port=9222?"
                        if use_cdp else "")
                return [], {"x-scrape": f"connect-failed:{type(ex).__name__}{hint}"}
            page = ctx.new_page() if use_cdp else (
                ctx.pages[0] if ctx.pages else ctx.new_page())
            for item in queries:
                q = item.get("q")
                state = item.get("state")
                for tab, suffix in [t for t in (("top", ""), ("latest", "&f=live"))
                                    if t[0] in want]:
                    url = ("https://x.com/search?q=" +
                           __import__("urllib.parse", fromlist=["quote_plus"]).quote_plus(q) +
                           suffix)
                    key = f"{q} [{tab}]"
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=45000)
                        page.wait_for_timeout(4000)
                        if "/login" in page.url or "/i/flow/login" in page.url:
                            statuses[key] = "session-expired"
                            continue
                        got = _parse_tweets(page, cap, q, state, start, end)
                        results += got
                        statuses[key] = f"ok:{len(got)}"
                    except Exception as ex:
                        statuses[key] = f"error:{type(ex).__name__}"
                    page.wait_for_timeout(2500)   # be gentle between requests
            if use_cdp:
                page.close()          # close ONLY our tab, leave Chrome alone
            cleanup()
    except Exception as ex:
        return results, {"x-scrape": f"error:{type(ex).__name__}"}
    return results, statuses
