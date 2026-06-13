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
from datetime import datetime, timezone

import aiohttp
from google.oauth2.service_account import Credentials
import gspread

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
GOOGLE_CREDS_PATH = os.environ.get("GOOGLE_CREDENTIALS_PATH", "google_credentials.json")
SHEET_ID = os.environ.get("SHEET_ID", "")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
}

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

async def fetch_table(table: str) -> list[dict]:
    url = f"{SUPABASE_URL}/{table}?limit=5000"
    async with aiohttp.ClientSession() as s:
        async with s.get(url, headers=HEADERS) as r:
            if r.status == 200:
                return await r.json()
    return []

def flatten(obj: dict) -> list:
    """Converte uma linha com campos aninhados em valores planos para a planilha."""
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

async def backup_all():
    if not all([SUPABASE_URL, SUPABASE_KEY, SHEET_ID]):
        print("❌ Missing SUPABASE_URL, SUPABASE_KEY or SHEET_ID")
        return

    print("🔄 Fetching data from Supabase...")
    players, matches, events = await asyncio.gather(
        fetch_table("players?order=username.asc"),
        fetch_table("matches?order=played_at.desc"),
        fetch_table("events?order=date.desc"),
    )
    print(f"   {len(players)} players, {len(matches)} matches, {len(events)} events")

    print("🔄 Connecting to Google Sheets...")
    creds = Credentials.from_service_account_file(GOOGLE_CREDS_PATH, scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)

    # Aba Players
    p_headers = ["id", "username", "name", "tier", "wins", "losses", "kills", "deaths",
                  "region", "platform", "affiliation", "available", "hidden",
                  "previous_tier", "match_history", "updated_at"]
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

if __name__ == "__main__":
    asyncio.run(backup_all())
