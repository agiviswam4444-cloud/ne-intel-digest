"""Run orchestrator: compute window -> collect -> verify -> classify -> store -> JSON."""
import os, re, time, json, datetime, concurrent.futures as cf
import yaml

from . import db, collector, verifier, classifier, ai, geo, enrich, telegram

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
    # Retention: prune rows older than retain_days so the DB stays bounded.
    retain = int(cfg.get("retain_days", 0) or 0)
    if retain > 0:
        cutoff = (now_ist.date() - datetime.timedelta(days=retain)).isoformat()
        con.execute("DELETE FROM stories WHERE pub_date < ?", (cutoff,))
        con.execute("DELETE FROM excluded WHERE run_date < ?", (cutoff,))
        con.execute("DELETE FROM source_status WHERE run_date < ?", (cutoff,))
        con.execute("DELETE FROM runs WHERE run_date < ?", (cutoff,))
        log(f"Retention: pruned rows older than {cutoff} (keep {retain}d)")
    con.commit()

    collected = []
    stats = dict(sources=0, src_ok=0, src_err=0, x_tweets=0, candidates=0)
    tier_counts = {}

    def _tier(status):
        tier_counts[status] = tier_counts.get(status, 0) + 1

    # Source list: state-tagged papers (from the uploaded list) + pan-NE
    # aggregators. Each (url, state, label); aggregators have state=None.
    # (url, state, label, trusted, sections).  `state` may be None for
    # multi-state outlets — those get per-story state from the URL section.
    # `trusted` (papers) bypasses the NE-scope keyword gate; aggregators do not.
    sources = [(p["url"], p.get("state"), p.get("name") or p["url"],
                True, p.get("sections"))
               for p in cfg.get("papers", [])]
    sources += [(u, None, u, False, None) for u in cfg.get("aggregators", [])]
    # Neighbourhood watch (CN/BD/MM). Same cascade, same tuple shape — they are
    # separated from NE only by their state code, and are defence-filtered later.
    sources += [(p["url"], p.get("state"), p.get("name") or p["url"],
                 True, p.get("sections"))
                for p in cfg.get("foreign_papers", [])]

    shot_state = {"used": 0, "max": cfg.get("tier4_max_sites", 5)}
    crawl_cap = cfg.get("crawl_max_items", 14)
    slow = []   # feedless papers -> browser tiers

    # Phase 1 — feed tiers 0-1 (parallel, thread-safe).
    def _fast(src):
        return src, collector.fetch_fast(src[0], start, end)
    with cf.ThreadPoolExecutor(8) as ex:
        for src, (in_win, unknown, status) in ex.map(_fast, sources):
            url, state, label, trusted, _sec = src
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
                c["trusted"] = trusted
                collected.append(c)

    # Phase 2 — browser tiers 2-3 for feedless papers (serial, main thread;
    # Playwright is not thread-safe). Crawled/screenshot items are stamped with
    # the capture time so they land in the window.
    for src in slow:
        url, state, label, trusted, sections = src
        cands, status = collector.fetch_slow(url, now_ist, crawl_cap, shot_state,
                                             sections)
        db.log_source(con, run_date, label, state or "AGG", status, len(cands))
        _tier(status)
        stats["src_ok" if status.startswith("ok:") else "src_err"] += 1
        for c in cands:
            if state:
                c["state"] = state
            c["trusted"] = trusted
            collected.append(c)

    # Phase 3 — ADDITIVE section sweep. Runs *in addition to* Phases 1-2 and
    # changes neither. A paper whose feed succeeded in Phase 1 never reaches the
    # browser, so its state section pages would go unread even though the feed
    # lists only a fraction of them (India Today NE: 11 sitewide, often stale,
    # vs ~90 live across its 8 sections). Papers handled in Phase 2 already
    # crawled their sections, so they are skipped here — no duplicate work.
    slow_urls = {s[0] for s in slow}
    sweep = [s for s in sources if s[4] and s[0] not in slow_urls]
    if sweep:
        n_sweep = 0
        for src in sweep:
            url, state, label, trusted, sections = src
            cands, status = collector.sweep_sections(url, now_ist, crawl_cap, sections)
            db.log_source(con, run_date, f"{label} [sections]", state or "AGG",
                          status, len(cands))
            _tier("sections-ok" if cands else "sections-none")
            n_sweep += len(cands)
            for c in cands:
                if state:
                    c["state"] = state
                c["trusted"] = trusted
                collected.append(c)
        log(f"Section sweep: {len(sweep)} paper(s), {n_sweep} extra candidates")

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

    # ---- Topic discovery net: Google News keyword searches. Catches NE news
    #      from ANY outlet (national / obscure / not in the paper list). Added
    #      AFTER the papers so the direct-outlet copy wins the title-dedup. ----
    disc = cfg.get("discovery_queries", [])
    if disc:
        # Google News rate-limits bursts, so keep concurrency low and retry.
        def _disc(q):
            status = "error:none"
            for attempt in range(3):
                cands, status = collector.fetch_news_rss(collector.google_news_url(q), q)
                if cands:
                    return q, cands, status
                time.sleep(2 * (attempt + 1))
            return q, [], status
        n_disc = 0
        with cf.ThreadPoolExecutor(3) as ex:
            for q, cands, status in ex.map(_disc, disc):
                db.log_source(con, run_date, f"search: {q}", "DISC", status, len(cands))
                collected += cands           # state=None -> scope-gated + detect_state
                n_disc += len(cands)
        log(f"Discovery: {len(disc)} searches, {n_disc} candidates")

    # ---- Neighbourhood-watch discovery (CN/BD/MM). Separate from the NE
    #      discovery net above so NE behaviour is untouched; results carry their
    #      region's state code and are defence-filtered below. ----
    fq = cfg.get("foreign_queries", [])
    if fq:
        def _fq(item):
            cands, status = collector.fetch_news_rss(
                collector.google_news_url(item["q"]), item["q"])
            return item, cands, status
        n_fq = 0
        with cf.ThreadPoolExecutor(6) as ex:
            for item, cands, status in ex.map(_fq, fq):
                db.log_source(con, run_date, f"watch: {item['q']}",
                              item.get("state") or "FGN", status, len(cands))
                for c in cands:
                    c["state"] = item.get("state")
                    c["trusted"] = True      # region is known from the query
                    collected.append(c)
                n_fq += len(cands)
        log(f"Neighbourhood watch: {len(fq)} searches, {n_fq} candidates")

    # ---- Public Telegram channels (no API key / login). Additive phase: it
    #      adds candidates and touches no existing source path. ----
    tg = cfg.get("telegram_channels", [])
    if tg:
        tcands, tstatus = telegram.collect(tg, start, end,
                                           int(cfg.get("telegram_max_per_channel", 20)))
        for name, st in tstatus.items():
            db.log_source(con, run_date, f"TG: {name}", "TG", st, 0)
        collected += tcands
        log(f"Telegram: {len(tg)} channels, {len(tcands)} in-window messages")

    # ---- X via the user's own logged-in browser profile. OFF unless
    #      x_scrape.enabled is set; see the warning in config.yaml. Additive. ----
    xs = cfg.get("x_scrape") or {}
    if xs.get("enabled"):
        from . import xsocial
        xcands, xstatus = xsocial.collect(xs, start, end)
        for key, st in xstatus.items():
            db.log_source(con, run_date, f"X: {key}", "X", st, 0)
        collected += xcands
        log(f"X scrape: {len(xcands)} tweets from {len(xstatus)} query/tab pairs")

    # ---- Dedup by URL, then by normalized headline (collapses the same story
    #      seen via multiple outlets / the Google redirect; first seen wins,
    #      and papers are ordered before discovery so real URLs win). ----
    seen_url, seen_title, uniq = set(), set(), []
    for c in collected:
        if c["url"] in seen_url:
            continue
        tnorm = re.sub(r"[^a-z0-9 ]", "", (c.get("headline") or "").lower())
        tnorm = re.sub(r"\s+", " ", tnorm).strip()
        if tnorm and tnorm in seen_title:
            continue
        seen_url.add(c["url"])
        if tnorm:
            seen_title.add(tnorm)
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
        # NOTE: gate on `trusted`, NOT on `state`. A multi-state NE paper has no
        # single state but is still a known NE outlet — gating on state would
        # silently drop its stories that don't happen to name a place.
        if (not c.get("trusted") and c.get("date_source") != "x-api"
                and not classifier.in_ne_scope(text, cfg)):
            out_of_scope += 1
            continue
        kept.append(c)
    stats["candidates"] = len(kept)
    log(f"In NE-scope: {len(kept)} (dropped {out_of_scope} out-of-scope aggregator items)")

    # ---- Defence-relevance gate — CN/BD/MM ONLY. NE-state stories never enter
    #      this loop (their state code is not in FOREIGN_STATES), so NE columns
    #      behave exactly as before. Those outlets carry mostly economy/culture,
    #      so without this the three new columns would bury the signal. ----
    if cfg.get("defence_keywords"):
        keep2, dropped_fgn = [], 0
        for c in kept:
            if c.get("state") in classifier.FOREIGN_STATES:
                text = (c["headline"] or "") + " " + (c.get("summary") or "")
                if not classifier.is_defence_relevant(text, cfg):
                    dropped_fgn += 1
                    db.log_excluded(con, run_date, c["url"], "not-defence-relevant")
                    continue
            keep2.append(c)
        fgn = sum(1 for c in keep2 if c.get("state") in classifier.FOREIGN_STATES)
        kept = keep2
        stats["candidates"] = len(kept)
        log(f"Defence filter (CN/BD/MM): kept {fgn}, dropped {dropped_fgn} non-defence")

    # ---- Classify -> (optional AI summaries) -> store ----
    to_store = []
    for c in kept:
        d_ist = c["pub_dt"].astimezone(IST)
        to_store.append({
            "headline": c["headline"],
            "summary": c.get("summary") or c["headline"],
            "image_url": c.get("image_url"),
            "url": c["url"], "outlet": c["outlet"],
            # state: configured single-state paper -> URL section -> keywords
            "state": (c.get("state")
                      or classifier.state_from_url(c.get("url"), cfg)
                      or classifier.detect_state(
                          (c["headline"] or "") + " " + (c.get("summary") or ""), cfg)),
            "section": c.get("section") or classifier.classify_section(c["headline"], cfg),
            "pub_date": d_ist.strftime("%Y-%m-%d"),
            "pub_time_ist": d_ist.strftime("%H:%M"),
            "date_source": c.get("date_source", "feed-date"),
            "verification": c.get("verification", "feed-trusted"),
            "run_date": run_date,
        })

    # Geolocate from headline+summary at store time (cheap, no network) so the
    # coordinates are persisted and radius queries never re-run the gazetteer.
    for s in to_store:
        hit = geo.geolocate((s["headline"] or "") + " " + (s["summary"] or ""),
                            s.get("state"))
        if hit:
            s["lat"], s["lon"], s["place"], s["geo_src"] = hit[0], hit[1], hit[2], "headline"

    ai_used = False
    if cfg.get("ai_summaries"):
        ai_used = ai.summarize_batch(to_store)   # mutates summary in place
        log(f"AI summaries: {'generated' if ai_used else 'skipped (no key / failed)'}")

    final = 0
    for s in to_store:
        if db.upsert_story(con, s):
            final += 1

    # Location enrichment — fetch article bodies for stories still unlocated
    # (security sections first). Capped, runs after storage, and any failure
    # here can never affect what was already collected.
    try:
        enrich.enrich_locations(con, cfg, log)
    except Exception as e:
        log(f"Location enrichment skipped: {type(e).__name__}: {e}")

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
