import os
import time
from datetime import datetime, timezone

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

async def supabase_select(table: str, params: str = ""):
    import aiohttp
    url = f"{SUPABASE_URL}/{table}{params}"
    async with aiohttp.ClientSession() as s:
        async with s.get(url, headers=HEADERS) as r:
            if r.status == 200:
                return await r.json()
            return None

async def supabase_upsert(table: str, rows: list[dict]):
    import aiohttp
    if not rows:
        return 0
    url = f"{SUPABASE_URL}/{table}?on_conflict=id"
    async with aiohttp.ClientSession() as s:
        async with s.post(url, headers=HEADERS, json=rows) as r:
            if r.status in (200, 201):
                data = await r.json()
                return len(data) if isinstance(data, list) else 1
            return 0

async def supabase_delete_where_not_in(table: str, ids: list[str]):
    import aiohttp
    if not ids:
        return 0
    placeholders = ",".join(f"'{i}'" for i in ids)
    url = f"{SUPABASE_URL}/{table}?id=not.in.({placeholders})"
    async with aiohttp.ClientSession() as s:
        async with s.delete(url, headers=HEADERS) as r:
            return r.status in (200, 204)

async def get_last_sync(endpoint: str):
    data = await supabase_select("sync_log", f"?endpoint=eq.{endpoint}&order=synced_at.desc&limit=1")
    if data and len(data) > 0:
        return data[0]
    return None

async def record_sync(endpoint: str, rows_synced: int):
    import aiohttp
    url = f"{SUPABASE_URL}/sync_log"
    body = [{"endpoint": endpoint, "rows_synced": rows_synced, "synced_at": datetime.now(timezone.utc).isoformat()}]
    async with aiohttp.ClientSession() as s:
        async with s.post(url, headers=HEADERS, json=body) as r:
            pass
