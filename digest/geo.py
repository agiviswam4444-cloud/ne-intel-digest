"""Deterministic geolocation for the map tab. No AI.

A gazetteer of NE India places (towns / district HQs / known hotspots) mapped to
lat/lon + state. geolocate() scans a story's headline+summary for the most
specific place name mentioned and returns its coordinates. "Precise places
only": a story with no recognized place returns None and is left off the map.
"""
import re

# name -> (lat, lon, state_code).  Names are matched case-insensitively as whole
# words; multi-word and longer names win over shorter ones (most specific plot).
# Deliberately excludes bare state names and very short/ambiguous tokens
# (e.g. "Mon", "Along") to avoid false positives.
GAZETTEER = {
    # --- Manipur (MN) ---
    "imphal east": (24.79, 94.00, "MN"), "imphal west": (24.80, 93.90, "MN"),
    "imphal": (24.817, 93.937, "MN"), "churachandpur": (24.33, 93.67, "MN"),
    "kangpokpi": (25.15, 93.98, "MN"), "moreh": (24.25, 94.30, "MN"),
    "thoubal": (24.63, 94.01, "MN"), "bishnupur": (24.63, 93.77, "MN"),
    "ukhrul": (25.10, 94.36, "MN"), "senapati": (25.27, 94.02, "MN"),
    "tamenglong": (24.98, 93.50, "MN"), "jiribam": (24.80, 93.12, "MN"),
    "kakching": (24.50, 93.98, "MN"), "chandel": (24.32, 94.02, "MN"),
    "tengnoupal": (24.30, 94.905, "MN"), "kamjong": (24.98, 94.48, "MN"),
    "pherzawl": (24.30, 93.20, "MN"), "noney": (24.83, 93.55, "MN"),
    "sugnu": (24.42, 93.99, "MN"), "kwakta": (24.45, 93.75, "MN"),
    # --- Nagaland (NL) ---
    "kohima": (25.67, 94.11, "NL"), "dimapur": (25.91, 93.72, "NL"),
    "mokokchung": (26.32, 94.52, "NL"), "tuensang": (26.28, 94.83, "NL"),
    "wokha": (26.10, 94.26, "NL"), "zunheboto": (26.01, 94.52, "NL"),
    "phek": (25.67, 94.47, "NL"), "kiphire": (25.90, 94.83, "NL"),
    "longleng": (26.42, 94.80, "NL"), "peren": (25.52, 93.73, "NL"),
    "chumukedima": (25.77, 93.80, "NL"), "mon district": (26.72, 95.03, "NL"),
    "mon town": (26.72, 95.03, "NL"), "noklak": (26.20, 95.02, "NL"),
    # --- Assam (AS) ---
    "guwahati": (26.14, 91.73, "AS"), "dibrugarh": (27.47, 94.91, "AS"),
    "tinsukia": (27.49, 95.36, "AS"), "silchar": (24.83, 92.78, "AS"),
    "jorhat": (26.75, 94.22, "AS"), "nagaon": (26.35, 92.68, "AS"),
    "tezpur": (26.63, 92.80, "AS"), "kokrajhar": (26.40, 90.27, "AS"),
    "diphu": (25.84, 93.43, "AS"), "karbi anglong": (25.90, 93.50, "AS"),
    "bongaigaon": (26.47, 90.55, "AS"), "barpeta": (26.32, 91.00, "AS"),
    "golaghat": (26.51, 93.96, "AS"), "sivasagar": (26.98, 94.64, "AS"),
    "sibsagar": (26.98, 94.64, "AS"), "dhubri": (26.02, 89.98, "AS"),
    "cachar": (24.83, 92.78, "AS"), "hailakandi": (24.68, 92.56, "AS"),
    "karimganj": (24.87, 92.35, "AS"), "lakhimpur": (27.23, 94.10, "AS"),
    "nalbari": (26.44, 91.44, "AS"), "goalpara": (26.17, 90.62, "AS"),
    "morigaon": (26.25, 92.34, "AS"), "biswanath": (26.73, 93.15, "AS"),
    "dhemaji": (27.48, 94.58, "AS"), "darrang": (26.45, 92.03, "AS"),
    "baksa": (26.70, 91.10, "AS"), "udalguri": (26.75, 92.10, "AS"),
    "chirang": (26.58, 90.60, "AS"), "hojai": (26.00, 92.86, "AS"),
    # --- Mizoram (MZ) ---
    "aizawl": (23.73, 92.72, "MZ"), "lunglei": (22.88, 92.73, "MZ"),
    "champhai": (23.47, 93.33, "MZ"), "kolasib": (24.22, 92.68, "MZ"),
    "serchhip": (23.30, 92.85, "MZ"), "lawngtlai": (22.53, 92.90, "MZ"),
    "mamit": (23.92, 92.49, "MZ"), "saiha": (22.49, 92.98, "MZ"),
    "zokhawthar": (23.38, 93.40, "MZ"), "siaha": (22.49, 92.98, "MZ"),
    # --- Meghalaya (ML) ---
    "shillong": (25.57, 91.88, "ML"), "tura": (25.51, 90.20, "ML"),
    "jowai": (25.44, 92.20, "ML"), "nongstoin": (25.52, 91.27, "ML"),
    "williamnagar": (25.49, 90.62, "ML"), "baghmara": (25.19, 90.63, "ML"),
    "nongpoh": (25.91, 91.87, "ML"), "resubelpara": (25.72, 90.64, "ML"),
    "khliehriat": (25.35, 92.36, "ML"),
    # --- Arunachal Pradesh (AR) ---
    "itanagar": (27.08, 93.60, "AR"), "tawang": (27.59, 91.86, "AR"),
    "ziro": (27.63, 93.83, "AR"), "pasighat": (28.07, 95.33, "AR"),
    "tezu": (27.92, 96.16, "AR"), "bomdila": (27.26, 92.42, "AR"),
    "changlang": (27.13, 95.73, "AR"), "khonsa": (27.02, 95.57, "AR"),
    "longding": (26.90, 95.34, "AR"), "anini": (28.80, 95.90, "AR"),
    "namsai": (27.67, 95.87, "AR"), "roing": (28.14, 95.83, "AR"),
    "seppa": (27.28, 92.92, "AR"), "yingkiong": (28.63, 95.02, "AR"),
    "daporijo": (27.98, 94.22, "AR"), "aalo": (28.17, 94.80, "AR"),
    # --- Sikkim (SK) ---
    "gangtok": (27.33, 88.61, "SK"), "namchi": (27.17, 88.35, "SK"),
    "gyalshing": (27.28, 88.26, "SK"), "geyzing": (27.28, 88.26, "SK"),
    "mangan": (27.51, 88.53, "SK"), "pakyong": (27.23, 88.59, "SK"),
    "singtam": (27.23, 88.50, "SK"), "rangpo": (27.17, 88.53, "SK"),
    "jorethang": (27.11, 88.32, "SK"), "soreng": (27.20, 88.24, "SK"),
    # --- Tripura (TR) ---
    "agartala": (23.83, 91.28, "TR"), "udaipur": (23.53, 91.48, "TR"),
    "dharmanagar": (24.37, 92.17, "TR"), "kailashahar": (24.33, 92.01, "TR"),
    "ambassa": (23.93, 91.85, "TR"), "khowai": (24.06, 91.60, "TR"),
    "belonia": (23.25, 91.45, "TR"), "sabroom": (23.00, 91.72, "TR"),
    "sonamura": (23.47, 91.27, "TR"), "dhalai": (23.93, 91.90, "TR"),
    "kamalpur": (24.17, 91.83, "TR"), "teliamura": (23.83, 91.62, "TR"),
}

# Precompiled longest-first so the most specific place wins.
_ENTRIES = sorted(GAZETTEER.items(), key=lambda kv: -len(kv[0]))
_PATTERNS = [(re.compile(r"\b" + re.escape(name) + r"\b"), name, coord)
             for name, coord in _ENTRIES]


def geolocate(text):
    """Return (lat, lon, place_display, state) for the most specific place named
    in `text`, or None. Longest gazetteer name wins; ties broken by position."""
    if not text:
        return None
    low = text.lower()
    best = None   # (name_len, position, name, coord)
    for pat, name, coord in _PATTERNS:
        m = pat.search(low)
        if m:
            # longest-first iteration: first (longest) hit that exists wins,
            # but prefer an earlier-positioned equally-long name.
            cand = (len(name), m.start(), name, coord)
            if best is None or len(name) > best[0]:
                best = cand
                # a strictly longer name can't be beaten by shorter ones
                break
    if best is None:
        return None
    name, (lat, lon, state) = best[2], best[3]
    return lat, lon, name.title(), state
