"""Local web UI server. Run: python run.py serve  ->  http://127.0.0.1:8642"""
import json, os, sys, subprocess, threading, datetime, time
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from . import db, pipeline, classifier, geo

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


@app.post("/api/run")
def run_now():
    if _run_lock.locked():
        return JSONResponse({"status": "already-running"}, status_code=409)
    threading.Thread(target=_run_pipeline, daemon=True).start()
    return {"status": "started"}


@app.get("/api/run/status")
def run_status():
    return {"running": _run_lock.locked()}
