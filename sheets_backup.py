"""
sheets_backup.py — Exporta dados do Supabase para Google Sheets.

Uso:
  1. Crie uma Service Account no Google Cloud Console
  2. Compartilhe a planilha com o email da service account
  3. Salve o JSON da chave como google_credentials.json
  4. Rode: python sheets_backup.py

Variáveis de ambiente:
  SUPABASE_URL, SUPABASE_KEY (mesmas do bot)
  GOOGLE_CREDENTIALS_PATH (padrão: google_credentials.json)
  SHEET_ID (ID da planilha Google Sheets)
"""

import os
import json
import asyncio
import base64
from datetime import datetime, timezone

import aiohttp
import gspread

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
GOOGLE_CREDS_B64 = os.environ.get("GOOGLE_CREDENTIALS_B64", "")
SHEET_ID = os.environ.get("SHEET_ID", "")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

def get_gc():
    if GOOGLE_CREDS_B64:
        raw = base64.b64decode(GOOGLE_CREDS_B64).decode("utf-8")
        info = json.loads(raw)
        return gspread.service_account_from_dict(info, scopes=SCOPES)
    path = os.environ.get("GOOGLE_CREDENTIALS_PATH", "google_credentials.json")
    return gspread.service_account(filename=path)

async def fetch_table(table: str) -> list[dict]:
    sep = "&" if "?" in table else "?"
    url = f"{SUPABASE_URL}/{table}{sep}limit=5000"
    async with aiohttp.ClientSession() as s:
        async with s.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=120)) as r:
            if r.status == 200:
                return await r.json()
            elif r.status >= 400:
                text = await r.text()
                print(f"⚠️ Sheets fetch {table} — HTTP {r.status}: {text[:200]}")
    return []

def flatten(obj: dict) -> list:
    row = []
    for v in obj.values():
        if isinstance(v, (dict, list)):
            row.append(json.dumps(v, ensure_ascii=False))
        elif v is None:
            row.append("")
        else:
            row.append(str(v))
    return row

def write_sheet(ws, headers: list[str], rows: list[list]):
    ws.clear()
    if rows:
        ws.append_rows([headers] + rows, value_input_option="USER_ENTERED")
    else:
        ws.append_rows([headers], value_input_option="USER_ENTERED")

def _sync_write_to_sheets(players, matches, events):
    print("🔄 Connecting to Google Sheets...")
    gc = get_gc()
    sh = gc.open_by_key(SHEET_ID)

    # Aba Players
    p_headers = ["id", "username", "name", "tier", "wins", "losses", "kills", "deaths",
                  "region", "platform", "affiliation", "available", "hidden",
                  "previous_tier", "updated_at"]
    p_rows = [flatten({h: p.get(h) for h in p_headers}) for p in players]
    ws_p = sh.worksheet("Players") if "Players" in [ws.title for ws in sh.worksheets()] else sh.add_worksheet("Players", 1000, 20)
    write_sheet(ws_p, p_headers, p_rows)
    print(f"   ✅ Players sheet updated ({len(p_rows)} rows)")

    # Aba Matches
    m_headers = ["id", "event", "played_at", "side1_playerids", "side2_playerids",
                  "side1_score", "side2_score", "winning_side", "status", "recording_url"]
    m_rows = [flatten({h: m.get(h) for h in m_headers}) for m in matches]
    ws_m = sh.worksheet("Matches") if "Matches" in [ws.title for ws in sh.worksheets()] else sh.add_worksheet("Matches", 1000, 15)
    write_sheet(ws_m, m_headers, m_rows)
    print(f"   ✅ Matches sheet updated ({len(m_rows)} rows)")

    # Aba Events
    e_headers = ["id", "name", "date", "completed", "completed_at", "is_tournament", "description"]
    e_rows = [flatten({h: e.get(h) for h in e_headers}) for e in events]
    ws_e = sh.worksheet("Events") if "Events" in [ws.title for ws in sh.worksheets()] else sh.add_worksheet("Events", 100, 10)
    write_sheet(ws_e, e_headers, e_rows)
    print(f"   ✅ Events sheet updated ({len(e_rows)} rows)")

    print(f"\n✅ Backup completo — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")

async def backup_all():
    if not all([SUPABASE_URL, SUPABASE_KEY, SHEET_ID]):
        print("❌ Missing SUPABASE_URL, SUPABASE_KEY or SHEET_ID")
        return

    print("🔄 Fetching data from Supabase...")
    players, matches, events = await asyncio.gather(
        fetch_table("players"),
        fetch_table("matches?order=played_at.desc"),
        fetch_table("events?order=date.desc"),
    )
    print(f"   {len(players)} players, {len(matches)} matches, {len(events)} events")

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _sync_write_to_sheets, players, matches, events)

if __name__ == "__main__":
    asyncio.run(backup_all())
