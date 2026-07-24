"""Optional Claude API helpers (claude-haiku-4-5).

Two uses, both opt-in and both degrade gracefully when ANTHROPIC_API_KEY is
unset or the SDK is missing:
  - extract_from_screenshot: TIER 4 of the fetch cascade — read a full-page
    screenshot and return visible headlines/links/dates as JSON.
  - summarize_batch: when config `ai_summaries: true`, rewrite every story's
    one-liner in a single batched call per run.

Usage (input+output tokens) is accumulated in USAGE so the pipeline can log it
in the run audit.
"""
import os, re, json, base64

MODEL = "claude-haiku-4-5"
USAGE = {"tier4_calls": 0, "tier4_input_tokens": 0, "tier4_output_tokens": 0,
         "summary_calls": 0, "summary_input_tokens": 0, "summary_output_tokens": 0}


def have_key():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _client():
    import anthropic
    return anthropic.Anthropic()


def _first_json(text):
    """Pull the first JSON array/object out of a model reply."""
    m = re.search(r"(\[.*\]|\{.*\})", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def extract_from_screenshot(png_bytes, outlet, page_url):
    """TIER 4. Send a PNG to claude-haiku-4-5, ask for headlines/links/dates.
    Returns a list of candidate dicts (may be empty). Raises on API failure so
    the caller can fall through to 'exhausted-all-tiers'."""
    b64 = base64.standard_b64encode(png_bytes).decode()
    prompt = (
        "This is a screenshot of a news outlet's homepage or a security/"
        "conflict news listing. Extract every distinct news headline you can "
        "read. Return ONLY a JSON array; each item: "
        '{"headline": "...", "url": "... or null", "date": "... or null"}. '
        "Use the exact visible text for headline. Include a url only if a full "
        "link is visibly rendered. Include date only if visibly shown next to "
        "the headline. No prose, no code fences."
    )
    resp = _client().messages.create(
        model=MODEL, max_tokens=2048,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64",
             "media_type": "image/png", "data": b64}},
            {"type": "text", "text": prompt},
        ]}],
    )
    USAGE["tier4_calls"] += 1
    USAGE["tier4_input_tokens"] += resp.usage.input_tokens
    USAGE["tier4_output_tokens"] += resp.usage.output_tokens
    text = next((b.text for b in resp.content if b.type == "text"), "")
    items = _first_json(text) or []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        head = (it.get("headline") or "").strip()
        if not head:
            continue
        url = (it.get("url") or "").strip() or page_url
        out.append({"headline": head, "url": url, "outlet": outlet,
                    "pub_dt": None, "date_source": "news-rss-unverified",
                    "summary": head})
    return out


def summarize_batch(stories):
    """ai_summaries=true. One batched call: rewrite each story into a <=160-char
    one-liner. Mutates stories in place (sets s['summary']). Returns True if the
    call ran, False if skipped (no key / SDK / failure)."""
    if not stories or not have_key():
        return False
    numbered = "\n".join(f"{i}. {s['headline']}" for i, s in enumerate(stories))
    prompt = (
        "For each numbered headline below, write a single neutral one-line "
        "summary (max 160 characters, no trailing period needed). Return ONLY a "
        "JSON object mapping the number (as a string) to the summary string. "
        "No prose, no code fences.\n\n" + numbered
    )
    try:
        resp = _client().messages.create(
            model=MODEL, max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception:
        return False
    USAGE["summary_calls"] += 1
    USAGE["summary_input_tokens"] += resp.usage.input_tokens
    USAGE["summary_output_tokens"] += resp.usage.output_tokens
    text = next((b.text for b in resp.content if b.type == "text"), "")
    mapping = _first_json(text)
    if not isinstance(mapping, dict):
        return False
    for i, s in enumerate(stories):
        line = mapping.get(str(i)) or mapping.get(i)
        if isinstance(line, str) and line.strip():
            s["summary"] = line.strip()[:160]
    return True
