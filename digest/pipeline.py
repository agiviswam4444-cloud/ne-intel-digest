"""Run orchestrator: compute window -> collect -> verify -> classify -> store -> JSON."""
import os, json, datetime, concurrent.futures as cf
import yaml

from . import db, collector, verifier, classifier, ai

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def load_config(path="config.yaml"):
    with open(path) as f:
        return yaml.safe_load(f)


def compute_window(cfg, today=None):
    """Calendar-day rule: accept stories published (IST) TODAY or YESTERDAY.
    start = yesterday 00:00 IST, end = tomorrow 00:00 IST, so the existing
    `start <= d < end` checks admit exactly those two calendar days. Computed
    at runtime from the clock, never hardcoded. run_date = today."""
    now = datetime.datetime.now(IST)
    today = today or now.date()
    yesterday = today - datetime.timedelta(days=1)
    start = datetime.datetime.combine(yesterday, datetime.time(0), tzinfo=IST)
    end = datetime.datetime.combine(
        today + datetime.timedelta(days=1), datetime.time(0), tzinfo=IST)
    return start, end, today.isoformat()


def run(config_path="config.yaml", verbose=True):
    cfg = load_config(config_path)
    start, end, run_date = compute_window(cfg)
    log = print if verbose else (lambda *a, **k: None)
    log(f"Window: {start:%Y-%m-%d %H:%M} IST -> {end:%Y-%m-%d %H:%M} IST")

    con = db.connect(cfg["paths"]["db"])
    now_ist = datetime.datetime.now(IST)
    con.execute("INSERT OR REPLACE INTO runs(run_date,started) VALUES(?,?)",
                (run_date, now_ist.isoformat(timespec="seconds")))
    # Incremental across the day's 30-min runs: keep existing stories (upsert
    # dedups by URL); refresh only the per-run logs.
    con.execute("DELETE FROM excluded WHERE run_date=?", (run_date,))
    con.execute("DELETE FROM source_status WHERE run_date=?", (run_date,))
    con.commit()

    collected = []
    stats = dict(sources=0, src_ok=0, src_err=0, x_tweets=0, candidates=0)
    tier_counts = {}

    def _tier(status):
        tier_counts[status] = tier_counts.get(status, 0) + 1

    # Source list: state-tagged papers (from the uploaded list) + pan-NE
    # aggregators. Each (url, state, label); aggregators have state=None.
    sources = [(p["url"], p.get("state"), p.get("name") or p["url"])
               for p in cfg.get("papers", [])]
    sources += [(u, None, u) for u in cfg.get("aggregators", [])]

    shot_state = {"used": 0, "max": cfg.get("tier4_max_sites", 5)}
    crawl_cap = cfg.get("crawl_max_items", 14)
    slow = []   # feedless papers -> browser tiers

    # Phase 1 — feed tiers 0-1 (parallel, thread-safe).
    def _fast(src):
        return src, collector.fetch_fast(src[0], start, end)
    with cf.ThreadPoolExecutor(8) as ex:
        for src, (in_win, unknown, status) in ex.map(_fast, sources):
            url, state, label = src
            if status == "fast-exhausted":
                slow.append(src)
                continue
            db.log_source(con, run_date, label, state or "AGG", status,
                          len(in_win) + len(unknown))
            _tier(status)
            stats["src_ok"] += 1
            for c in in_win + unknown:
                if state:
                    c["state"] = state
                collected.append(c)

    # Phase 2 — browser tiers 2-3 for feedless papers (serial, main thread;
    # Playwright is not thread-safe). Crawled/screenshot items are stamped with
    # the capture time so they land in the window.
    for src in slow:
        url, state, label = src
        cands, status = collector.fetch_slow(url, now_ist, crawl_cap, shot_state)
        db.log_source(con, run_date, label, state or "AGG", status, len(cands))
        _tier(status)
        stats["src_ok" if status.startswith("ok:") else "src_err"] += 1
        for c in cands:
            if state:
                c["state"] = state
            collected.append(c)

    stats["sources"] = len(sources)
    log(f"Sources: {stats['src_ok']} ok / {stats['src_err']} failed | tiers {tier_counts}")

    # ---- X API (official handles, always in-scope) ----
    if cfg.get("x_api", {}).get("enabled"):
        tweets, statuses = collector.fetch_x_handles(cfg["x_api"], start, end)
        for h, st in statuses.items():
            db.log_source(con, run_date, f"@{h}", "X", st, 0)
        stats["x_tweets"] = len(tweets)
        collected += tweets
        log(f"X API: {len(tweets)} tweets")

    # ---- Dedup by URL ----
    seen, uniq = set(), []
    for c in collected:
        if c["url"] in seen:
            continue
        seen.add(c["url"])
        uniq.append(c)

    # ---- Date handling: trust each item's own date (feed pubDate, x created_at,
    #      or crawl/screenshot capture time); keep the today/yesterday window. ----
    if cfg.get("trust_feed_dates", True):
        verified, gstats = [], dict(kept=0, nodate=0, outside=0)
        for c in uniq:
            d = c.get("pub_dt")
            if d is None:
                gstats["nodate"] += 1
                db.log_excluded(con, run_date, c["url"], "no-date")
            elif start <= d < end:
                gstats["kept"] += 1
                verified.append(c)
            else:
                gstats["outside"] += 1
                db.log_excluded(con, run_date, c["url"],
                                f"outside-window:{d.astimezone(IST):%Y-%m-%d}")
        log(f"In-window: {gstats['kept']} | dropped no-date={gstats['nodate']} "
            f"outside={gstats['outside']}")
    else:
        # Legacy path (trust_feed_dates: false).
        to_verify = [c for c in uniq if classifier.is_relevant(c["headline"], cfg)]
        verified, gstats = [], dict(pass1=0, pass2=0, outside=0, nodate=0, fetchfail=0)
        rel = cfg.get("slug_reliable_outlets", [])

        def _verify(c):
            return c, verifier.verify(c, start, end, rel)
        with cf.ThreadPoolExecutor(10) as ex:
            for c, (verdict, detail) in ex.map(_verify, to_verify):
                if verdict == "verified":
                    d, src, passname = detail
                    c["pub_dt"], c["date_source"] = d, src
                    gstats[passname] += 1
                    verified.append(c)
                elif verdict == "outside":
                    gstats["outside"] += 1
                    db.log_excluded(con, run_date, c["url"], f"outside-window:{detail}")
                elif verdict == "no-date":
                    gstats["nodate"] += 1
                    db.log_excluded(con, run_date, c["url"], "no-date-found")
                else:
                    gstats["fetchfail"] += 1
                    db.log_excluded(con, run_date, c["url"], "fetch-failed")
        log(f"Verified: {len(verified)}")

    # ---- NE-scope gate: state-tagged papers & X are always kept; aggregator
    #      stories must mention an NE place/state (drops national-only news). ----
    kept, out_of_scope = [], 0
    for c in verified:
        text = (c["headline"] or "") + " " + (c.get("summary") or "")
        if (not c.get("state") and c.get("date_source") != "x-api"
                and not classifier.in_ne_scope(text, cfg)):
            out_of_scope += 1
            continue
        kept.append(c)
    stats["candidates"] = len(kept)
    log(f"In NE-scope: {len(kept)} (dropped {out_of_scope} out-of-scope aggregator items)")

    # ---- Classify -> (optional AI summaries) -> store ----
    to_store = []
    for c in kept:
        d_ist = c["pub_dt"].astimezone(IST)
        to_store.append({
            "headline": c["headline"],
            "summary": c.get("summary") or c["headline"],
            "image_url": c.get("image_url"),
            "url": c["url"], "outlet": c["outlet"],
            "state": c.get("state") or classifier.detect_state(c["headline"], cfg),
            "section": c.get("section") or classifier.classify_section(c["headline"], cfg),
            "pub_date": d_ist.strftime("%Y-%m-%d"),
            "pub_time_ist": d_ist.strftime("%H:%M"),
            "date_source": c.get("date_source", "feed-date"),
            "verification": c.get("verification", "feed-trusted"),
            "run_date": run_date,
        })

    ai_used = False
    if cfg.get("ai_summaries"):
        ai_used = ai.summarize_batch(to_store)   # mutates summary in place
        log(f"AI summaries: {'generated' if ai_used else 'skipped (no key / failed)'}")

    final = 0
    for s in to_store:
        if db.upsert_story(con, s):
            final += 1

    audit = {**stats, **{f"g_{k}": v for k, v in gstats.items()},
             "out_of_scope": out_of_scope, "tiers": tier_counts,
             "ai_summaries": bool(ai_used), "tier4": dict(ai.USAGE),
             "final_verified": final}
    con.execute("UPDATE runs SET finished=?, stats=? WHERE run_date=?",
                (datetime.datetime.now(IST).isoformat(timespec="seconds"),
                 json.dumps(audit), run_date))
    con.commit()

    # ---- Fallback JSON ----
    os.makedirs(cfg["paths"]["json_out"], exist_ok=True)
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM stories WHERE run_date=? ORDER BY section, pub_date DESC, pub_time_ist DESC",
        (run_date,))]
    out = os.path.join(cfg["paths"]["json_out"], f"ne-intel-digest-{run_date}.json")
    with open(out, "w") as f:
        json.dump({"run_date": run_date, "window": [start.isoformat(), end.isoformat()],
                   "audit": audit, "stories": rows}, f, indent=1)
    log(f"Done. {final} stories -> {out}")
    con.close()
    return audit
