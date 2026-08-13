"""SQLite storage for the NE intel digest."""
import sqlite3, os, json, datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS stories (
  id INTEGER PRIMARY KEY,
  url TEXT UNIQUE,
  headline TEXT,
  summary TEXT,             -- one-line summary (RSS description 1st sentence, or headline)
  image_url TEXT,           -- best-effort thumbnail URL (RSS media / og:image), or null
  outlet TEXT,
  outlets TEXT,             -- json list, for same story on multiple outlets
  state TEXT,
  section INTEGER,
  pub_date TEXT,            -- YYYY-MM-DD (IST)
  pub_time_ist TEXT,        -- HH:MM or 'unknown'
  date_source TEXT,         -- rss-pubdate | x-api | head-meta | body-text | url-slug-reliable-outlet
  verification TEXT,        -- pre-verified | pass1 | pass2
  run_date TEXT,            -- digest date (TODAY_DATE) this story belongs to
  first_seen TEXT
);
CREATE TABLE IF NOT EXISTS excluded (
  id INTEGER PRIMARY KEY, run_date TEXT, url TEXT, reason TEXT
);
CREATE TABLE IF NOT EXISTS source_status (
  id INTEGER PRIMARY KEY, run_date TEXT, source TEXT, step TEXT,
  status TEXT, candidates INTEGER
);
CREATE TABLE IF NOT EXISTS runs (
  run_date TEXT PRIMARY KEY, started TEXT, finished TEXT,
  stats TEXT               -- json audit blob
);
CREATE INDEX IF NOT EXISTS idx_stories_run ON stories(run_date);
"""


def connect(path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    # WAL lets the web server keep reading while a collection writes. Without
    # it the two collide: the dashboard's Source Status momentarily blanks and
    # a concurrent write fails outright with "database is locked". `timeout`
    # makes a writer wait for a busy lock instead of erroring immediately.
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass                     # non-fatal: fall back to default journalling
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    _migrate(con)
    return con


def _migrate(con):
    """Add columns introduced after a DB was first created."""
    cols = {r["name"] for r in con.execute("PRAGMA table_info(stories)")}
    if "summary" not in cols:
        con.execute("ALTER TABLE stories ADD COLUMN summary TEXT")
    if "image_url" not in cols:
        con.execute("ALTER TABLE stories ADD COLUMN image_url TEXT")
    # Persisted coordinates: geolocating on every API call does not scale for
    # radius search, and the body-enrichment pass needs somewhere to write back.
    # geo_src records HOW the point was found (headline / body / dateline).
    if "lat" not in cols:
        con.execute("ALTER TABLE stories ADD COLUMN lat REAL")
        con.execute("ALTER TABLE stories ADD COLUMN lon REAL")
        con.execute("ALTER TABLE stories ADD COLUMN place TEXT")
        con.execute("ALTER TABLE stories ADD COLUMN geo_src TEXT")
        con.execute("CREATE INDEX IF NOT EXISTS idx_stories_geo ON stories(lat,lon)")
    con.commit()


def upsert_story(con, s):
    """Insert story; on URL clash merge outlet names. Returns True if new."""
    cur = con.execute("SELECT id, outlets FROM stories WHERE url=?", (s["url"],))
    row = cur.fetchone()
    if row:
        outlets = set(json.loads(row["outlets"] or "[]"))
        if s["outlet"] not in outlets:
            outlets.add(s["outlet"])
            con.execute("UPDATE stories SET outlets=? WHERE id=?",
                        (json.dumps(sorted(outlets)), row["id"]))
        return False
    con.execute(
        """INSERT INTO stories(url,headline,summary,image_url,outlet,outlets,state,section,
           pub_date,pub_time_ist,date_source,verification,run_date,first_seen,
           lat,lon,place,geo_src)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (s["url"], s["headline"], s.get("summary") or s["headline"], s.get("image_url"),
         s["outlet"], json.dumps([s["outlet"]]),
         s["state"], s["section"], s["pub_date"], s.get("pub_time_ist", "unknown"),
         s["date_source"], s["verification"], s["run_date"],
         datetime.datetime.now().isoformat(timespec="seconds"),
         s.get("lat"), s.get("lon"), s.get("place"), s.get("geo_src")))
    return True


def set_geo(con, url, lat, lon, place, src):
    """Write back a location found later (body-enrichment pass)."""
    con.execute("UPDATE stories SET lat=?, lon=?, place=?, geo_src=? WHERE url=?",
                (lat, lon, place, src, url))


def log_excluded(con, run_date, url, reason):
    con.execute("INSERT INTO excluded(run_date,url,reason) VALUES(?,?,?)",
                (run_date, url, reason))


def log_source(con, run_date, source, step, status, candidates):
    con.execute(
        "INSERT INTO source_status(run_date,source,step,status,candidates) VALUES(?,?,?,?,?)",
        (run_date, source, step, status, candidates))
