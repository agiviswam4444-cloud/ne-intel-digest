NE INDIA // SECURITY INTEL DIGEST  —  Mac quick start
=====================================================

TO RUN
  Double-click:  start-mac.command

  The first launch collects news (2-3 minutes), then your browser opens at
  http://127.0.0.1:8642 automatically. After that it refreshes itself every
  30 minutes. Keep the black Terminal window open; closing it stops the app.

  If macOS blocks it the first time ("unidentified developer"):
     right-click start-mac.command  ->  Open  ->  Open

  Or from Terminal, from inside this folder:
     ./.venv/bin/python run.py serve

WHAT IS INSIDE
  .venv/         self-contained Python + all dependencies. This is why the
                 app keeps working even if your system python3 changes
                 (e.g. Homebrew/Ollama putting a different one first).
  digest/        the collection pipeline, classifier, gazetteer, map, actors
  ui/            the dashboard (offline Leaflet map assets in ui/vendor)
  config.yaml    every source, filter and setting - edit this, no code needed
  data/          the news database (created on first run; not in git)

THE TABS
  Console        state-wise columns: 8 NE states + China / Bangladesh / Myanmar
  Map            incidents plotted; click or search a place, draw a 10/30/50 km
                 radius, choose the time depth
  Actors         who is driving the narrative (NSCN, Assam Rifles, COCOMI...)
  Source Status  every source and how it was fetched (RSS / crawl / Telegram)
  Audit          run statistics and what was excluded, with reasons

OPTIONAL: X (TWITTER)
  Off by default. To enable:
     ./.venv/bin/python run.py login-x     (a window opens; you sign in)
  then set  x_scrape.enabled: true  in config.yaml.
  WARNING: automated access breaks X's terms of service; the account used can
  be rate-limited or suspended. Use a throwaway account, not your main one.

UPDATING
  git pull

BACKUP THIS FOLDER
  The code lives on GitHub, but data/digest.db (the collected news and its
  history) does NOT. If this folder is deleted, that history is gone. Copy
  data/digest.db somewhere safe if the archive matters to you.
