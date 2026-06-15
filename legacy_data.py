"""
legacy_data.py — Fetch e parse das temporadas anteriores (S1, S2)
via Google Sheets API (chave de API simples, sem OAuth).

Uso:
  pip install google-api-python-client (OU usa TSV export direto, sem dep)

Fluxo:
  1. Tenta Google Sheets API (requer GOOGLE_SHEETS_API_KEY)
  2. Fallback: export TSV (público, sem chave)
"""

import os
import re
import aiohttp

S1_SHEET_ID = "1km25wEYzGdfdZAIgyqa8BiM93pFoKM4GSZbSoINRLMM"
S2_SHEET_ID = "1W966tVa_qPbUNN34PzKy22uxWTQCnCip_EPyH-Ns3aU"
API_KEY = os.environ.get("GOOGLE_SHEETS_API_KEY", "")

# Mapeamento de aba -> GID conhecidos
S1_TABS = {
    "SA": (S1_SHEET_ID, 0),
    "NA": (S1_SHEET_ID, 1553052514),
    "EU": (S1_SHEET_ID, 181075464),
}

S2_TABS = {
    "GLBL": (S2_SHEET_ID, 1023941459),
    "SA": (S2_SHEET_ID, 1599910090),
    "NA": (S2_SHEET_ID, 1770080623),
    "EU": (S2_SHEET_ID, 203710074),
    "S1 Ranks": (S2_SHEET_ID, 1808427919),
}

S2_TAB_ORDER = ["GLBL", "SA", "NA", "EU"]

# Headers de cada formato
S1_HEADERS = [
    "FIGHTER",
    "3-2","3-1","3-0","2-3","1-3","0-3",
    "5-4","5-3","5-2","5-1","5-0","4-5","3-5","2-5","1-5","0-5",
    "RATING","W","L","K","D","MP",
]
S2_HEADERS = [
    "FIGHTER",
    "3-0","3-1","3-2","2-0","2-1","1-0","2-2","1-1","0-0","0-2","1-2","2-3","1-3","0-3",
    "5-4","5-3","5-2","5-1","5-0","4-5","3-5","2-5","1-5","0-5",
    "1st","2nd","3rd","4th",
    "RATING","W","L","T","K","D","MP",
]

async def fetch_tsv(sheet_id: str, gid: int) -> str:
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=tsv&gid={gid}"
    async with aiohttp.ClientSession() as s:
        async with s.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
            if r.status == 200:
                return await r.text()
    return ""

async def fetch_via_api(sheet_id: str, tab_name: str) -> str:
    if not API_KEY:
        return ""
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{tab_name}?key={API_KEY}"
    async with aiohttp.ClientSession() as s:
        async with s.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status == 200:
                data = await r.json()
                values = data.get("values", [])
                if values:
                    return "\n".join("\t".join(str(c) for c in row) for row in values)
    return ""

def parse_s1_rows(raw: str):
    """Parse TSV da S1 → lista de dicts."""
    lines = raw.strip().split("\n")
    if not lines:
        return []
    # Pula header row (linha 1)
    fighters = []
    for line in lines[1:]:
        cols = line.strip().split("\t")
        if len(cols) < 2:
            continue
        name = cols[0].strip()
        if not name:
            continue
        try:
            rating = int(cols[16]) if len(cols) > 16 and cols[16].strip() else 0
            w = int(cols[17]) if len(cols) > 17 and cols[17].strip() else 0
            l = int(cols[18]) if len(cols) > 18 and cols[18].strip() else 0
            k = int(cols[19]) if len(cols) > 19 and cols[19].strip() else 0
            d = int(cols[20]) if len(cols) > 20 and cols[20].strip() else 0
            mp = int(cols[21]) if len(cols) > 21 and cols[21].strip() else 0
        except (ValueError, IndexError):
            rating = w = l = k = d = mp = 0
        fighters.append({
            "name": name,
            "season": "S1",
            "rating": rating,
            "w": w, "l": l, "k": k, "d": d, "mp": mp,
        })
    return fighters

def parse_s2_rows(raw: str):
    """Parse TSV da S2 → lista de dicts."""
    lines = raw.strip().split("\n")
    if not lines:
        return []
    fighters = []
    for line in lines[1:]:
        cols = line.strip().split("\t")
        if len(cols) < 2:
            continue
        name = cols[0].strip()
        if not name:
            continue
        try:
            rating = int(cols[-7]) if len(cols) > 7 and cols[-7].strip() else 0
            w = int(cols[-6]) if len(cols) > 6 and cols[-6].strip() else 0
            l = int(cols[-5]) if len(cols) > 5 and cols[-5].strip() else 0
            t = int(cols[-4]) if len(cols) > 4 and cols[-4].strip() else 0
            k = int(cols[-3]) if len(cols) > 3 and cols[-3].strip() else 0
            d = int(cols[-2]) if len(cols) > 2 and cols[-2].strip() else 0
            mp = int(cols[-1]) if len(cols) > 1 and cols[-1].strip() else 0
        except (ValueError, IndexError):
            rating = w = l = t = k = d = mp = 0
        fighters.append({
            "name": name,
            "season": "S2",
            "rating": rating,
            "w": w, "l": l, "t": t, "k": k, "d": d, "mp": mp,
        })
    return fighters

async def fetch_season1_tab(tab: str):
    """Fetch e parse de uma aba da S1."""
    info = S1_TABS.get(tab)
    if not info:
        return []
    sheet_id, gid = info
    raw = await fetch_via_api(sheet_id, tab)
    if not raw:
        raw = await fetch_tsv(sheet_id, gid)
    return parse_s1_rows(raw)

async def fetch_season2_tab(tab: str):
    """Fetch e parse de uma aba da S2 (exceto S1 Ranks)."""
    info = S2_TABS.get(tab)
    if not info or tab == "S1 Ranks":
        return []
    sheet_id, gid = info
    raw = await fetch_via_api(sheet_id, tab)
    if not raw:
        raw = await fetch_tsv(sheet_id, gid)
    return parse_s2_rows(raw)

async def fetch_all_season1():
    """Todas as abas da S1 combinadas."""
    all_fighters = []
    for tab in ["SA", "NA", "EU"]:
        fighters = await fetch_season1_tab(tab)
        for f in fighters:
            f["region"] = tab
        all_fighters.extend(fighters)
    return all_fighters

async def fetch_all_season2():
    """Todas as abas da S2 combinadas (exceto S1 Ranks)."""
    all_fighters = []
    for tab in S2_TAB_ORDER:
        fighters = await fetch_season2_tab(tab)
        for f in fighters:
            f["region"] = tab
        all_fighters.extend(fighters)
    return all_fighters

async def fetch_alltime():
    """Soma todas as temporadas → total agregado por fighter."""
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
    result.sort(key=lambda x: x["w"], reverse=True)
    return result
