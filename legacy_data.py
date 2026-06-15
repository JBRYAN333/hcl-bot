"""
legacy_data.py — Fetch and parse historical seasons (S1, S2)
via public TSV export (no auth required).
"""

import aiohttp

S1_SHEET_ID = "1km25wEYzGdfdZAIgyqa8BiM93pFoKM4GSZbSoINRLMM"
S2_SHEET_ID = "1W966tVa_qPbUNN34PzKy22uxWTQCnCip_EPyH-Ns3aU"

# GIDs confirmados (extraídos do HTML da planilha)
S1_TABS = {
    "SA": (S1_SHEET_ID, 0),
    "NA": (S1_SHEET_ID, 471360635),
    "EU": (S1_SHEET_ID, 1277203885),
}
S1_REGION_ORDER = ["SA", "NA", "EU"]

S2_TABS = {
    "GLBL": (S2_SHEET_ID, 1648622354),
    "SA": (S2_SHEET_ID, 1814390317),
    "NA": (S2_SHEET_ID, 1491833090),
    "EU": (S2_SHEET_ID, 532517278),
    "S1 Ranks": (S2_SHEET_ID, 590728060),  # empty
}
S2_REGION_ORDER = ["GLBL", "SA", "NA", "EU"]

HEADER_KEYWORDS = {
    "FIGHTER", "RATING", "NON TITLE MATCHES", "TITLE MATCHES",
    "BATTLE ROYALS", "STATS", "1ST", "2ND", "3RD", "4TH",
    "SA RANKINGS S1", "GLOBAL RANKINGS S2",
}

def _is_header_row(non_empty: list[str]) -> bool:
    """Returns True if the row appears to be a header (checked by first non-empty value)."""
    return bool(non_empty) and non_empty[0].strip().upper() in HEADER_KEYWORDS


async def fetch_tsv(sheet_id: str, gid: int) -> str:
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=tsv&gid={gid}"
    async with aiohttp.ClientSession() as s:
        async with s.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
            if r.status == 200:
                return await r.text()
    return ""


def _parse_rows(raw: str, s2: bool = False) -> list[dict]:
    """
    Parse TSV from either season.
    Skips header rows, extracts name (first non-empty value)
    and stats (last N non-empty numeric values).
    S1: last 6 = RATING, W, L, K, D, MP
    S2: last 7 = RATING, W, L, T, K, D, MP
    """
    lines = raw.strip().split("\n")
    if not lines:
        return []
    n_stats = 7 if s2 else 6
    out = []
    for line in lines:
        cols = [c.strip() for c in line.split("\t")]
        if len(cols) < 3:
            continue
        non_empty = [c for c in cols if c]
        if _is_header_row(non_empty):
            continue
        if len(non_empty) < n_stats + 1:
            continue
        name = non_empty[0]
        if name.upper() in HEADER_KEYWORDS:
            continue
        stats_raw = non_empty[-n_stats:]
        try:
            stats = [int(v) for v in stats_raw]
        except (ValueError, IndexError):
            continue
        if s2:
            rating, w, l, t, k, d, mp = stats
            entry = {"name": name, "season": "S2", "rating": rating,
                     "w": w, "l": l, "t": t, "k": k, "d": d, "mp": mp}
        else:
            rating, w, l, k, d, mp = stats
            entry = {"name": name, "season": "S1", "rating": rating,
                     "w": w, "l": l, "k": k, "d": d, "mp": mp}
        out.append(entry)
    return out


async def fetch_season1_tab(tab: str):
    info = S1_TABS.get(tab)
    if not info:
        return []
    sheet_id, gid = info
    raw = await fetch_tsv(sheet_id, gid)
    return _parse_rows(raw, s2=False)


async def fetch_season2_tab(tab: str):
    info = S2_TABS.get(tab)
    if not info:
        return []
    sheet_id, gid = info
    raw = await fetch_tsv(sheet_id, gid)
    return _parse_rows(raw, s2=True)


async def fetch_all_season1():
    all_fighters = []
    for tab in S1_REGION_ORDER:
        fighters = await fetch_season1_tab(tab)
        for f in fighters:
            f["region"] = tab
        all_fighters.extend(fighters)
    return all_fighters


async def fetch_all_season2():
    all_fighters = []
    for tab in S2_REGION_ORDER:
        fighters = await fetch_season2_tab(tab)
        for f in fighters:
            f["region"] = tab
        all_fighters.extend(fighters)
    return all_fighters


async def fetch_alltime():
    s1 = await fetch_all_season1()
    s2 = await fetch_all_season2()
    combined = {}
    for f in s1 + s2:
        name = f["name"].upper()
        if name not in combined:
            combined[name] = {"name": name, "w": 0, "l": 0, "k": 0, "d": 0, "seasons": set()}
        combined[name]["w"] += f.get("w", 0)
        combined[name]["l"] += f.get("l", 0)
        combined[name]["k"] += f.get("k", 0)
        combined[name]["d"] += f.get("d", 0)
        combined[name]["seasons"].add(f.get("season", "?"))
    result = list(combined.values())
    result.sort(key=lambda x: (x["w"] - x["l"]), reverse=True)
    return result
