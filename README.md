# NE India Security Intelligence Digest — local app

Replaces the CoWork scheduled task. Zero AI tokens: collection, date
verification (your full Subagent G two-pass protocol), state tagging, and
11-section classification are all deterministic Python.

## Setup (MacBook)

```bash
cd ne-intel-digest
python3 -m pip install -r requirements.txt
python3 run.py collect          # first run (2–5 min)
python3 run.py serve            # open http://127.0.0.1:8642
```

## Install as always-on app + 07:01 daily schedule

```bash
bash scripts/install_macos.sh
```

This creates two launchd agents: a daily collector at 07:01 (launchd fires
missed jobs on wake, matching your flexible-start rule) and the dashboard
server, kept alive at http://127.0.0.1:8642. Bookmark it or make a Safari
"Add to Dock" web app for a one-click icon.

## X API

Put your bearer token in `~/Documents/x_bearer_token.txt` (same path as the
CoWork task). If the file is missing or auth fails, the run continues without
X and logs the reason under Source Status.

## What maps to what

| CoWork spec | This app |
|---|---|
| Step 0 date window | `pipeline.compute_window` (07:00 IST boundary) |
| 1A direct RSS (12) | `collector.fetch_direct_rss`, pubDate trusted |
| 1B/1C Google + Bing News RSS | `collector.fetch_news_rss`, always unverified |
| 30-site web crawls | per-outlet Google News `site:` RSS (stabler than scraping) |
| 1D X API (8 handles) | `collector.fetch_x_handles`, created_at trusted |
| Subagent G two-pass date verification | `verifier.py` — meta tags → JSON-LD → `<time>` → slug (reliable outlets only) → 1500-char body fallback → EXCLUDE |
| 11 sections + state tagging | `classifier.py` keyword rules (edit in `config.yaml`) |
| Fallback JSON | `data/archive/ne-intel-digest-DATE.json` |
| Artifact tabs | Bulletin / All Stories / By Section / Source Status / Audit / Archive |

## Tuning

Everything lives in `config.yaml`: add/remove feeds, outlets, section
keywords, state keywords, relevance gate. No code changes needed for source
management.
