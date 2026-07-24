"""Relevance gate, state detection, 11-section classification. Pure keywords."""
import re


def _hits(text, keywords):
    t = text.lower()
    return sum(1 for k in keywords if k.lower() in t)


def is_relevant(headline, cfg):
    return _hits(headline, cfg["relevance_keywords"]) > 0


_SCOPE_RE = None


def in_ne_scope(text, cfg):
    """True if the text mentions any of the 7 NE states or a place/entity from
    them (config.states values — state names, towns, districts, ethnic terms).
    This is the inclusion gate: keep NE news (security or normal), drop
    national-only stories. Word-boundary matched to avoid substring hits."""
    global _SCOPE_RE
    if _SCOPE_RE is None:
        terms = sorted({k for kws in cfg["states"].values() for k in kws},
                       key=len, reverse=True)
        _SCOPE_RE = re.compile(r"\b(" + "|".join(re.escape(t) for t in terms) + r")",
                               re.I)
    return bool(_SCOPE_RE.search(text or ""))


def detect_state(headline, cfg):
    # "Assam Rifles" is a force, not a location — don't let it tag state AS.
    t = headline.lower().replace("assam rifles", "").replace("assamrifles", "")
    matched = [code for code, kws in cfg["states"].items()
               if any(k.lower() in t for k in kws)]
    if len(matched) >= 2:
        return "MULTI"
    return matched[0] if matched else "MULTI"


def classify_section(headline, cfg):
    best, best_score = 11, 0
    for num, sec in cfg["sections"].items():
        score = _hits(headline, sec["keywords"])
        # Specific insurgency/border sections outrank generic force sections on ties
        if score > best_score or (score == best_score and score > 0 and int(num) > best):
            best, best_score = int(num), score
    return best if best_score > 0 else 11


# Keyword-based triage for the ECHO-style severity badge. No AI — deterministic
# escalation words. Highest matching tier wins.
SEVERITY_KEYWORDS = {
    "CRITICAL": ["killed", "dead", "death", "encounter", "gunfight", "gun battle",
                 "firing", "ambush", "ied", "blast", "explosion", "bomb", "grenade",
                 "shot", "abduct", "kidnap", "massacre", "clash", "attack"],
    "HIGH": ["militant", "insurgent", "cadre", "arrested", "arrest", "seized",
             "seizure", "arms", "ammunition", "weapon", "extortion", "infiltrat",
             "smuggl", "narcotic", "drugs", "heroin", "curfew", "bandh"],
    "MEDIUM": ["surrender", "peace talk", "ceasefire", "operation", "security",
               "patrol", "protest", "detained", "recovered"],
}


def severity(text):
    """Return 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW' from escalation keywords."""
    t = (text or "").lower()
    for level in ("CRITICAL", "HIGH", "MEDIUM"):
        if any(k in t for k in SEVERITY_KEYWORDS[level]):
            return level
    return "LOW"
