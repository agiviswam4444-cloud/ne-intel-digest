"""Local web UI server. Run: python run.py serve  ->  http://127.0.0.1:8642"""
import json, os, sys, subprocess, threading, datetime, time
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from . import db, pipeline, classifier, geo, actors

app = FastAPI(title="NE Intel Digest")
CFG = pipeline.load_config(os.environ.get("DIGEST_CONFIG", "config.yaml"))
UI_DIR = os.path.join(os.path.dirname(__file__), "..", "ui")
UI = os.path.join(UI_DIR, "index.html")
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
# Bundled, offline map assets (Leaflet + NE states GeoJSON) — no external tiles.
app.mount("/vendor", StaticFiles(directory=os.path.join(UI_DIR, "vendor")), name="vendor")
_run_lock = threading.Lock()


def _run_pipeline():
    """Run a collection in a SEPARATE process. Collection drives a headless
    browser (Playwright), which can crash; isolating it in a child process
    means a crash can never take down this web server."""
    if not _run_lock.acquire(blocking=False):
        return False
    try:
        subprocess.run([sys.executable, os.path.join(ROOT, "run.py"), "collect"],
                       cwd=ROOT, timeout=1500)
    except Exception as e:
        print("collect subprocess error:", e)
    finally:
        _run_lock.release()
    return True


def _scheduler():
    """Re-run collection every `auto_refresh_minutes` (incremental)."""
    interval = int(CFG.get("auto_refresh_minutes", 0)) * 60
    if interval <= 0:
        return
    while True:
        _run_pipeline()
        time.sleep(interval)


if int(CFG.get("auto_refresh_minutes", 0)) > 0:
    threading.Thread(target=_scheduler, daemon=True).start()


def _severity(row):
    return classifier.severity((row.get("headline") or "") + " " + (row.get("summary") or ""))


def _prev_day(d):
    try:
        return (datetime.date.fromisoformat(d) - datetime.timedelta(days=1)).isoformat()
    except Exception:
        return d


def _latest_pub_date(con):
    r = con.execute("SELECT MAX(pub_date) d FROM stories").fetchone()
    return r["d"] if r and r["d"] else ""


def _con():
    return db.connect(CFG["paths"]["db"])


@app.get("/")
def index():
    return FileResponse(UI)


@app.get("/api/dates")
def dates():
    """Distinct publication dates (window anchors), newest first."""
    con = _con()
    rows = [r["pub_date"] for r in con.execute(
        "SELECT DISTINCT pub_date FROM stories WHERE pub_date IS NOT NULL "
        "ORDER BY pub_date DESC LIMIT 30")]
    con.close()
    return rows


@app.get("/api/digest")
def digest(date: str = ""):
    """The digest is a rolling window keyed on PUBLICATION date: stories
    published on `date` or the day before, pulled across all collection runs
    (a story stays visible for its 2-day window regardless of which run first
    stored it). Source Status / Audit reflect the most recent collection run."""
    con = _con()
    if not date:
        date = _latest_pub_date(con)
    prev = _prev_day(date) if date else ""
    stories = [dict(r) for r in con.execute(
        "SELECT * FROM stories WHERE pub_date IN (?,?) "
        "ORDER BY pub_date DESC, pub_time_ist DESC, first_seen DESC", (date, prev))]
    for s in stories:
        s["severity"] = _severity(s)
    run = con.execute(
        "SELECT * FROM runs ORDER BY run_date DESC, started DESC LIMIT 1").fetchone()
    rrd = run["run_date"] if run else ""
    sources = [dict(r) for r in con.execute(
        "SELECT source,step,status,candidates FROM source_status WHERE run_date=? "
        "ORDER BY step, source", (rrd,))]
    excluded = [dict(r) for r in con.execute(
        "SELECT url,reason FROM excluded WHERE run_date=?", (rrd,))]
    con.close()
    sections = {int(k): v["name"] for k, v in CFG["sections"].items()}
    return {"date": date, "stories": stories,
            "audit": json.loads(run["stats"]) if run and run["stats"] else {},
            "run": dict(run) if run else {}, "sources": sources,
            "excluded": excluded, "sections": sections,
            "auto_refresh_minutes": int(CFG.get("auto_refresh_minutes", 0)),
            "collecting": _run_lock.locked()}


@app.get("/api/archive")
def archive():
    con = _con()
    out = []
    for r in con.execute("SELECT DISTINCT pub_date d FROM stories "
                         "WHERE pub_date IS NOT NULL ORDER BY pub_date DESC LIMIT 7"):
        d = r["d"]
        st = [dict(s) for s in con.execute(
            "SELECT headline,summary,image_url,url,outlet,state,pub_date,pub_time_ist,date_source "
            "FROM stories WHERE pub_date=? ORDER BY section", (d,))]
        for s in st:
            s["severity"] = _severity(s)
        out.append({"date": d, "stories": st})
    con.close()
    return out


@app.get("/api/map")
def map_data(date: str = "", scope: str = "all"):
    """Stories for the digest window, geolocated by place name via the
    deterministic gazetteer, grouped by location. Stories with no recognized
    place are omitted (precise-places-only). scope='all' plots every story;
    scope='security' restricts to sections 1-10."""
    con = _con()
    if not date:
        date = _latest_pub_date(con)
    prev = _prev_day(date) if date else ""
    where = "pub_date IN (?,?)" + (" AND section BETWEEN 1 AND 10" if scope == "security" else "")
    rows = [dict(r) for r in con.execute(
        "SELECT headline,summary,url,outlet,state,section,pub_date,pub_time_ist "
        "FROM stories WHERE " + where +
        " ORDER BY pub_date DESC, pub_time_ist DESC", (date, prev))]
    con.close()
    points, located = {}, 0
    for r in rows:
        loc = geo.geolocate((r["headline"] or "") + " " + (r["summary"] or ""))
        if not loc:
            continue
        located += 1
        lat, lon, place, gstate = loc
        key = (round(lat, 4), round(lon, 4))
        p = points.setdefault(key, {"lat": lat, "lon": lon, "place": place,
                                    "state": gstate, "incidents": []})
        p["incidents"].append({
            "headline": r["headline"], "summary": r["summary"], "url": r["url"],
            "outlet": r["outlet"], "state": r["state"], "section": r["section"],
            "pub_date": r["pub_date"], "pub_time_ist": r["pub_time_ist"],
            "severity": _severity(r)})
    pts = list(points.values())
    for p in pts:                       # worst incident sets the dot colour
        order = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1, "LOW": 0}
        p["severity"] = max((i["severity"] for i in p["incidents"]),
                            key=lambda s: order.get(s, 0))
    return {"date": date, "scope": scope, "points": pts,
            "sections": {int(k): v["name"] for k, v in CFG["sections"].items()},
            "total": len(rows), "located": located,
            "unlocated": len(rows) - located}


def _haversine_km(lat1, lon1, lat2, lon2):
    from math import radians, sin, cos, asin, sqrt
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = (sin(dlat / 2) ** 2
         + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2)
    return 2 * 6371.0 * asin(sqrt(a))


@app.get("/api/geosearch")
def geosearch(q: str = ""):
    """Place-name lookup for centring the radius circle. Searches the curated
    gazetteer first, then the imported GeoNames layer."""
    q = (q or "").strip().lower()
    if len(q) < 3:
        return {"results": []}
    out = []
    for name, (lat, lon, st) in geo.GAZETTEER.items():
        if q in name:
            out.append({"place": name.title(), "lat": lat, "lon": lon,
                        "state": st, "src": "curated"})
    for name, (lat, lon, st) in geo._BULK.items():
        if len(out) >= 40:
            break
        if q in name:
            out.append({"place": name.title(), "lat": lat, "lon": lon,
                        "state": st, "src": "geonames"})
    # exact/prefix matches first, then shortest names
    out.sort(key=lambda r: (not r["place"].lower().startswith(q), len(r["place"])))
    return {"results": out[:25]}


@app.get("/api/radius")
def radius(lat: float, lon: float, km: float = 30, days: int = 7,
           scope: str = "all"):
    """Area-of-interest search: every located story within `km` of a point.

    `days` counts back from the newest publication date (1 = today only,
    0 = the entire archive). scope='security' limits to sections 1-10.
    Only stories WITH stored coordinates can participate — roughly a third of
    the archive; the response reports that so the UI can be honest about it."""
    con = _con()
    latest = _latest_pub_date(con)
    args, where = [], ["lat IS NOT NULL"]
    if days and days > 0 and latest:
        start = (datetime.date.fromisoformat(latest)
                 - datetime.timedelta(days=max(0, int(days) - 1))).isoformat()
        where.append("pub_date >= ?")
        args.append(start)
    else:
        start = ""
    if scope == "security":
        where.append("section BETWEEN 1 AND 10")
    rows = [dict(r) for r in con.execute(
        "SELECT headline,summary,url,outlet,state,section,pub_date,pub_time_ist,"
        "lat,lon,place,geo_src FROM stories WHERE " + " AND ".join(where), args)]
    con.close()

    hits = []
    for r in rows:
        d = _haversine_km(lat, lon, r["lat"], r["lon"])
        if d <= km:
            r["distance_km"] = round(d, 1)
            r["severity"] = _severity(r)
            hits.append(r)
    hits.sort(key=lambda r: (r["distance_km"],
                             r["pub_date"] or "", r["pub_time_ist"] or ""))

    # group by location so the map draws one marker per place
    pts = {}
    for h in hits:
        key = (round(h["lat"], 4), round(h["lon"], 4))
        p = pts.setdefault(key, {"lat": h["lat"], "lon": h["lon"],
                                 "place": h["place"], "state": h["state"],
                                 "distance_km": h["distance_km"], "incidents": []})
        p["incidents"].append(h)
    order = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1, "LOW": 0}
    for p in pts.values():
        p["severity"] = max((i["severity"] for i in p["incidents"]),
                            key=lambda s: order.get(s, 0))
    return {"center": {"lat": lat, "lon": lon}, "km": km, "days": days,
            "scope": scope, "from": start, "to": latest,
            "total": len(hits), "locations": len(pts),
            "points": sorted(pts.values(), key=lambda p: p["distance_km"]),
            "stories": hits[:200]}


@app.get("/api/actors")
def actors_data(days: int = 2, state: str = ""):
    """ADDITIVE endpoint for the separate Actors tab. Reads the same stories
    table but changes nothing about the digest/map endpoints or the pipeline.
    `days` = how far back to look (1 = today only; larger values use the
    history now preserved by retain_days)."""
    con = _con()
    latest = _latest_pub_date(con)
    if not latest:
        con.close()
        return {"actors": [], "cooccurrence": [], "days": days, "from": "", "to": ""}
    try:
        start = (datetime.date.fromisoformat(latest)
                 - datetime.timedelta(days=max(0, int(days) - 1))).isoformat()
    except Exception:
        start = latest
    q = ("SELECT headline,summary,url,outlet,state,pub_date,pub_time_ist "
         "FROM stories WHERE pub_date BETWEEN ? AND ?")
    args = [start, latest]
    if state:
        q += " AND state=?"
        args.append(state)
    rows = [dict(r) for r in con.execute(q + " ORDER BY pub_date DESC, pub_time_ist DESC", args)]
    con.close()
    out = actors.analyse(rows)
    out.update({"days": days, "from": start, "to": latest,
                "total_stories": len(rows), "state": state})
    return out


@app.post("/api/run")
def run_now():
    if _run_lock.locked():
        return JSONResponse({"status": "already-running"}, status_code=409)
    threading.Thread(target=_run_pipeline, daemon=True).start()
    return {"status": "started"}


@app.get("/api/run/status")
def run_status():
    return {"running": _run_lock.locked()}
