"""Actor / entity tracking for the NE information landscape. No AI.

A curated gazetteer of the organisations, forces and figures that drive NE
narratives, matched deterministically against headline + summary text (same
approach as geo.py). Powers the separate "Actors" tab — it does not touch the
collection pipeline or the main dashboard in any way.

Each entry:  key -> (display name, category, [aliases/regex-safe surface forms])
Aliases are matched case-insensitively on word boundaries. Short/ambiguous
tokens are deliberately given tight patterns (see _TIGHT) to avoid false hits.
"""
import re
from collections import Counter, defaultdict

# --- categories -------------------------------------------------------------
ARMED = "Armed group"
CIVIL = "Civil society"
FORCE = "Security force"
GOVT = "Government / political"
PROC = "Peace process"

ACTORS = {
    # ---------------- Armed / insurgent groups ----------------
    "nscn_im":   ("NSCN (I-M)", ARMED, ["nscn-im", "nscn (im)", "nscn(im)", "nscn i-m", "nscn (i-m)"]),
    "nscn_k":    ("NSCN (K)", ARMED, ["nscn-k", "nscn (k)", "nscn(k)", "nscn-kyа", "nscn k-ya"]),
    "nscn":      ("NSCN (unspecified)", ARMED, ["nscn"]),
    "ulfa_i":    ("ULFA-I", ARMED, ["ulfa-i", "ulfa (i)", "ulfa independent", "paresh baruah"]),
    "ulfa":      ("ULFA", ARMED, ["ulfa"]),
    "ndfb":      ("NDFB", ARMED, ["ndfb", "bodoland front"]),
    "unlf":      ("UNLF", ARMED, ["unlf", "united national liberation front"]),
    "prepak":    ("PREPAK", ARMED, ["prepak"]),
    "kykl":      ("KYKL", ARMED, ["kykl"]),
    "kcp":       ("KCP", ARMED, ["kcp"]),
    "pla_rpf":   ("PLA / RPF (Manipur)", ARMED, ["rpf/pla", "pla/rpf", "revolutionary people's front"]),
    "knf":       ("KNF / Kuki militant groups", ARMED, ["knf", "kuki national front", "kna", "kuki national army"]),
    "hnlc":      ("HNLC", ARMED, ["hnlc"]),
    "gnla":      ("GNLA", ARMED, ["gnla"]),
    "nlft":      ("NLFT", ARMED, ["nlft"]),
    "attf":      ("ATTF", ARMED, ["attf"]),
    "zra":       ("ZRA", ARMED, ["zra", "zomi revolutionary"]),
    "mnpf":      ("MNPF", ARMED, ["mnpf"]),
    "arakan":    ("Arakan Army / Myanmar groups", ARMED, ["arakan army", "chin national", "cnf", "tatmadaw", "junta"]),

    # ---------------- Civil society / community bodies ----------------
    "cocomi":    ("COCOMI", CIVIL, ["cocomi", "coordinating committee on manipur integrity"]),
    "kuki_inpi": ("Kuki Inpi", CIVIL, ["kuki inpi"]),
    "itlf":      ("ITLF", CIVIL, ["itlf", "indigenous tribal leaders"]),
    "unc":       ("United Naga Council", CIVIL, ["united naga council", "unc "]),
    "ansam":     ("ANSAM", CIVIL, ["ansam"]),
    "atsum":     ("ATSUM", CIVIL, ["atsum"]),
    "meira":     ("Meira Paibi", CIVIL, ["meira paibi", "meira paibis"]),
    "aasu":      ("AASU", CIVIL, ["aasu", "all assam students"]),
    "amsu":      ("AMSU", CIVIL, ["amsu"]),
    "nsf":       ("Naga Students' Federation", CIVIL, ["naga students' federation", "naga students federation"]),
    "kso":       ("Kuki Students' Organisation", CIVIL, ["kuki students"]),
    "ymа":       ("Young Mizo Association", CIVIL, ["young mizo association", "yma "]),
    "enpo":      ("ENPO", CIVIL, ["enpo", "eastern nagaland peoples"]),
    "hynniewtrep": ("Hynniewtrep / KSU", CIVIL, ["hynniewtrep", "hnym", "ksu "]),
    "nnpg":      ("NNPGs", CIVIL, ["nnpg", "naga national political groups"]),

    # ---------------- Security forces / institutions ----------------
    "assam_rifles": ("Assam Rifles", FORCE, ["assam rifles"]),
    "army":      ("Indian Army", FORCE, ["indian army", "spear corps", "gajraj corps", "goc", "army chief"]),
    "crpf":      ("CRPF", FORCE, ["crpf"]),
    "bsf":       ("BSF", FORCE, ["bsf", "border security force"]),
    "nia":       ("NIA", FORCE, ["nia "]),
    "cbi":       ("CBI", FORCE, ["cbi "]),
    "itbp":      ("ITBP", FORCE, ["itbp"]),
    "ssb":       ("SSB", FORCE, ["ssb "]),
    "ncb":       ("NCB (Narcotics)", FORCE, ["narcotics control bureau", "ncb "]),
    "police":    ("State Police", FORCE, ["manipur police", "assam police", "nagaland police",
                                          "mizoram police", "tripura police", "meghalaya police"]),
    "afspa":     ("AFSPA", FORCE, ["afspa", "armed forces special powers"]),

    # ---------------- Government / political figures ----------------
    "himanta":   ("Himanta Biswa Sarma", GOVT, ["himanta biswa", "himanta"]),
    "biren":     ("N. Biren Singh", GOVT, ["biren singh"]),
    "conrad":    ("Conrad Sangma", GOVT, ["conrad sangma", "conrad k sangma"]),
    "rio":       ("Neiphiu Rio", GOVT, ["neiphiu rio"]),
    "lalduhoma": ("Lalduhoma", GOVT, ["lalduhoma"]),
    "manik":     ("Manik Saha", GOVT, ["manik saha"]),
    "khandu":    ("Pema Khandu", GOVT, ["pema khandu"]),
    "tamang":    ("Prem Singh Tamang", GOVT, ["prem singh tamang", "p s golay"]),
    "amit_shah": ("Amit Shah / MHA", GOVT, ["amit shah", "home ministry", "mha "]),
    "modi":      ("PM Modi", GOVT, ["narendra modi", "pm modi", "prime minister modi"]),
    "governor":  ("Governor", GOVT, ["governor"]),

    # ---------------- Peace processes / frameworks ----------------
    "naga_talks": ("Naga peace talks", PROC, ["naga peace", "framework agreement", "naga political issue"]),
    "soo":       ("Suspension of Operations (SoO)", PROC, ["suspension of operations", "soo pact", "soo agreement"]),
    "bodo":      ("Bodo Accord", PROC, ["bodo accord", "btr accord"]),
    "fmr":       ("Free Movement Regime / fencing", PROC, ["free movement regime", "border fencing", "fmr"]),
}

# Surface forms that are too short/ambiguous for a plain substring match.
_TIGHT = {"nia ", "cbi ", "ssb ", "ncb ", "mha ", "unc ", "yma ", "ksu ", "goc"}


def _pattern(alias):
    a = alias.strip()
    if a in _TIGHT or len(a) <= 4:
        return re.compile(r"\b" + re.escape(a.strip()) + r"\b", re.I)
    return re.compile(re.escape(a), re.I)


_COMPILED = [(key, meta[0], meta[1], [_pattern(a) for a in meta[2]])
             for key, meta in ACTORS.items()]

# More specific keys win over their generic parent (NSCN(I-M) beats bare NSCN).
_SUPERSEDES = {"nscn": {"nscn_im", "nscn_k"}, "ulfa": {"ulfa_i"}}


def detect(text):
    """Return the set of actor keys mentioned in `text`."""
    if not text:
        return set()
    found = set()
    for key, _name, _cat, pats in _COMPILED:
        if any(p.search(text) for p in pats):
            found.add(key)
    for generic, specifics in _SUPERSEDES.items():
        if generic in found and (found & specifics):
            found.discard(generic)
    return found


def name_of(key):
    return ACTORS[key][0] if key in ACTORS else key


def category_of(key):
    return ACTORS[key][1] if key in ACTORS else "Other"


def analyse(rows):
    """Build the Actors-tab payload from story rows.

    Returns counts per actor, per-state breakdown, co-occurrence pairs, a daily
    series for trend, and the matching stories per actor.
    """
    counts = Counter()
    per_state = defaultdict(Counter)
    per_day = defaultdict(Counter)
    outlets = defaultdict(Counter)
    stories = defaultdict(list)
    pairs = Counter()

    for r in rows:
        text = (r.get("headline") or "") + " " + (r.get("summary") or "")
        keys = detect(text)
        if not keys:
            continue
        for k in keys:
            counts[k] += 1
            per_state[k][r.get("state") or "?"] += 1
            per_day[k][r.get("pub_date") or "?"] += 1
            outlets[k][r.get("outlet") or "?"] += 1
            if len(stories[k]) < 40:
                stories[k].append({
                    "headline": r.get("headline"), "summary": r.get("summary"),
                    "url": r.get("url"), "outlet": r.get("outlet"),
                    "state": r.get("state"), "pub_date": r.get("pub_date"),
                    "pub_time_ist": r.get("pub_time_ist"),
                })
        for a, b in _upairs(sorted(keys)):
            pairs[(a, b)] += 1

    actors = [{
        "key": k, "name": name_of(k), "category": category_of(k), "count": n,
        "states": dict(per_state[k].most_common()),
        "days": dict(sorted(per_day[k].items())),
        "outlets": dict(outlets[k].most_common(6)),
        "stories": stories[k],
    } for k, n in counts.most_common()]

    co = [{"a": name_of(a), "b": name_of(b), "n": n}
          for (a, b), n in pairs.most_common(25) if n > 1]

    return {"actors": actors, "cooccurrence": co,
            "categories": [ARMED, CIVIL, FORCE, GOVT, PROC]}


def _upairs(keys):
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            yield keys[i], keys[j]
