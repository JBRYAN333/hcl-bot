import discord
from discord.ext import commands
from discord import ui
import aiohttp
import asyncio
import sys
import base64
import io
import os
import time
from datetime import datetime, timezone
from PIL import Image
import legacy_data as legacy

import supabase_client as sb

# === FIX WINDOWS ===
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # Required for on_member_join
bot = commands.Bot(command_prefix="!", intents=intents)
bot.remove_command("help")

# ========================= HCL REFERENCES =========================
HCL_SITE    = "https://hclmanager.replit.app"          # Main site / Record Book
API_BASE    = f"{HCL_SITE}/api"                        # API base (all data comes from here)
WELCOME_GIF = "gif.gif"                                # Place gif.gif in the same folder as this script

# ========================= SUPABASE (persistence / fallback) =========================
SUPABASE_URL   = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY   = os.environ.get("SUPABASE_KEY", "")
last_sync_time = None  # datetime of last successful full sync

# ========================= CACHE =========================
players_cache = None
players_cache_ts = None  # time.monotonic() when players_cache was filled
matches_cache = None
events_cache = None

# Roster (incl. availability) changes during matchmaking; avoid stale "Available" for the bot process lifetime.
PLAYERS_CACHE_TTL_SEC = 120

async def get_players():
    global players_cache, players_cache_ts
    now = time.monotonic()
    if (
        players_cache is not None
        and players_cache_ts is not None
        and (now - players_cache_ts) < PLAYERS_CACHE_TTL_SEC
    ):
        return players_cache
    # Tenta com timeout maior; a API pode estar lenta com muitos dados
    for attempt in range(2):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{API_BASE}/players", timeout=aiohttp.ClientTimeout(total=90)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        players_cache = data if isinstance(data, list) else data.get("players", data)
                        players_cache_ts = now
                        return players_cache
                    print(f"⚠️ get_players HTTP {resp.status}")
        except asyncio.TimeoutError:
            print(f"⚠️ get_players timeout (attempt {attempt+1}/2)")
        except Exception as e:
            print(f"⚠️ get_players error: {e}")
    # Fallback: tenta ler do Supabase
    sb_data = await fetch_from_supabase("players", "updated_at.desc")
    if sb_data:
        players_cache = sb_data
        players_cache_ts = now
        return players_cache
    return []

async def get_matches():
    global matches_cache
    if matches_cache:
        return matches_cache
    for attempt in range(2):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{API_BASE}/matches", timeout=aiohttp.ClientTimeout(total=90)) as resp:
                    if resp.status == 200:
                        matches_cache = await resp.json()
                        return matches_cache
                    print(f"⚠️ get_matches HTTP {resp.status}")
        except asyncio.TimeoutError:
            print(f"⚠️ get_matches timeout (attempt {attempt+1}/2)")
        except Exception as e:
            print(f"⚠️ get_matches error: {e}")
    # Fallback: Supabase
    sb_data = await fetch_from_supabase("matches", "played_at.desc")
    if sb_data:
        matches_cache = sb_data
        return matches_cache
    return []

async def get_events():
    global events_cache
    if events_cache:
        return events_cache
    for attempt in range(2):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{API_BASE}/events", timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    if resp.status == 200:
                        events_cache = await resp.json()
                        return events_cache
                    print(f"⚠️ get_events HTTP {resp.status}")
        except asyncio.TimeoutError:
            print(f"⚠️ get_events timeout (attempt {attempt+1}/2)")
        except Exception as e:
            print(f"⚠️ get_events error: {e}")
    # Fallback: Supabase
    sb_data = await fetch_from_supabase("events", "date.desc")
    if sb_data:
        events_cache = sb_data
        return events_cache
    return []

# ========================= SUPABASE SYNC =========================
async def sync_table_from_api(endpoint: str, table: str, transform_fn=None):
    global last_sync_time
    async with aiohttp.ClientSession() as s:
        try:
            async with s.get(f"{API_BASE}/{endpoint}", timeout=aiohttp.ClientTimeout(total=120)) as r:
                if r.status != 200:
                    print(f"⚠️ {endpoint} API returned {r.status}")
                    return 0
                data = await r.json()
        except asyncio.TimeoutError:
            print(f"⚠️ {endpoint} API timeout")
            return 0
        except Exception as ex:
            print(f"⚠️ {endpoint} API request failed: {ex}")
            return 0
    raw = data if isinstance(data, list) else data.get(endpoint, data)
    if not isinstance(raw, list):
        print(f"⚠️ {endpoint} unexpected format: {type(data).__name__}")
        return 0
    print(f"📦 {endpoint}: {len(raw)} items from API")
    if transform_fn:
        rows = [transform_fn(r) for r in raw]
    else:
        rows = raw
    count = await sb.supabase_upsert(table, rows)
    print(f"💾 {endpoint}: upserted {count} rows to Supabase")
    try:
        await sb.record_sync(endpoint, count)
    except Exception:
        pass
    last_sync_time = datetime.now(timezone.utc)
    return count

def transform_player(p):
    return {
        "id": p.get("id"),
        "username": p.get("username") or p.get("name") or "Unknown",
        "name": p.get("name") or p.get("username") or "Unknown",
        "tier": p.get("tier") or "F",
        "wins": p.get("wins", 0),
        "losses": p.get("losses", 0),
        "kills": p.get("kills", 0),
        "deaths": p.get("deaths", 0),
        "region": p.get("region") or "",
        "platform": p.get("platform") or "PC",
        "affiliation": p.get("affiliation") or "",
        "available": p.get("available", False),
        "hidden": p.get("hiddenFromLeaderboard", False),
        "previous_tier": p.get("previousTier") or "",
        # avatar_data omitido — grandes demais, causa timeout no upsert
    }

def transform_match(m):
    return {
        "id": m.get("id"),
        "event": m.get("event") or "",
        "played_at": m.get("playedAt") or None,
        "side1_playerids": m.get("side1PlayerIds") or [],
        "side2_playerids": m.get("side2PlayerIds") or [],
        "side1_score": m.get("side1Score", 0),
        "side2_score": m.get("side2Score", 0),
        "winning_side": m.get("winningSide", 0),
        "status": m.get("status") or "completed",
        "recording_url": m.get("recordingUrl") or "",
    }

def transform_event(e):
    return {
        "id": e.get("id"),
        "name": e.get("name") or "Event",
        "date": e.get("date") or e.get("scheduledTime") or None,
        "completed": e.get("completed", False),
        "completed_at": e.get("completedAt") or None,
        "is_tournament": e.get("isTournament", False),
        "description": e.get("description") or "",
        "location": e.get("location") or "",
    }

async def sync_all_to_supabase():
    p = m = e = 0
    try:
        p = await sync_table_from_api("players", "players", transform_player)
    except Exception as ex:
        print(f"⚠️ Players sync failed: {ex}")
    try:
        m = await sync_table_from_api("matches", "matches", transform_match)
    except Exception as ex:
        print(f"⚠️ Matches sync failed: {ex}")
    try:
        e = await sync_table_from_api("events", "events", transform_event)
    except Exception as ex:
        print(f"⚠️ Events sync failed: {ex}")
    return p, m, e

async def supabase_sync_loop():
    await bot.wait_until_ready()
    consecutive_failures = 0
    while not bot.is_closed():
        if not SUPABASE_URL or not SUPABASE_KEY:
            consecutive_failures += 1
            if consecutive_failures == 1:
                print("⚠️ Supabase not configured — sync disabled")
            await asyncio.sleep(3600)
            continue
        try:
            p, m, e = await sync_all_to_supabase()
            total = p + m + e
            if total == 0 and consecutive_failures > 0:
                consecutive_failures += 1
            else:
                consecutive_failures = 0
            if consecutive_failures > 10:
                backoff = min(3600, 300 * (2 ** min(consecutive_failures - 10, 5)))
                print(f"⏳ Supabase sync backing off — {backoff}s until next attempt")
                await asyncio.sleep(backoff)
                continue
            if os.environ.get("SHEET_ID"):
                try:
                    import sheets_backup
                    await sheets_backup.backup_all()
                except Exception as sheets_ex:
                    print(f"⚠️ Sheets backup error: {sheets_ex}")
        except Exception as e:
            print(f"⚠️ Supabase sync error: {e}")
            consecutive_failures += 1
        await asyncio.sleep(300)

async def initial_supabase_sync():
    await bot.wait_until_ready()
    await asyncio.sleep(5)
    print("🔄 Running initial sync (Supabase + Sheets)...")
    try:
        p, m, e = await sync_all_to_supabase()
        print(f"✅ Supabase initial sync: {p} players, {m} matches, {e} events")
    except Exception as ex:
        print(f"⚠️ Initial Supabase sync failed (will retry in background): {ex}")

async def fetch_from_supabase(table: str, order: str = None):
    params = f"?order={order}" if order else ""
    return await sb.supabase_select(table, params) or []

# ========================= HELPERS =========================
def get_name(p):
    return p.get("username") or p.get("name") or "Unknown"

def find_player(players: list[dict], query: str) -> dict | None:
    q = query.strip().lower()
    if not q:
        return None
    # 1. match exato (case insensitive)
    for p in players:
        if q == get_name(p).lower():
            return p
    # 2. começo do nome
    for p in players:
        if get_name(p).lower().startswith(q):
            return p
    # 3. substring (fallback)
    for p in players:
        if q in get_name(p).lower():
            return p
    return None

def player_is_available(p):
    """True if the API marks the fighter as available (hclmanager /api/players `available`, toggled in Admin)."""
    v = p.get("available")
    if v is True:
        return True
    if v is False or v is None:
        return False
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "on")
    if isinstance(v, (int, float)):
        return v != 0
    return bool(v)

def get_record(p):
    return f"{p.get('wins', 0)}-{p.get('losses', 0)}"

def get_streak(p):
    history = p.get("matchHistory")
    if not isinstance(history, list):
        return 0
    count = 0
    for m in history:
        if isinstance(m, dict) and m.get("result") == "win":
            count += 1
        else:
            break
    return count

AFFILIATION_EMOJIS = {
    "BBK": "🍌", "CHAOS": "💀", "DWO": "⚫", "SAL": "🦅", "IMPERIO": "👑",
}

def get_affiliation(p):
    aff = p.get("affiliation")
    return aff if aff else None

def get_affiliation_display(p):
    """Retorna afiliação com emoji, ex: 🍌 BBK"""
    aff = get_affiliation(p)
    if not aff:
        return ""
    emoji = AFFILIATION_EMOJIS.get(aff.upper(), "🏷️")
    return f"{emoji} {aff}"

def get_tier_color(tier):
    key = (tier or "").upper() if isinstance(tier, str) else "F"
    return {
        "CHAMPION": 0xFFD700,
        "S": 0x00FFFF,
        "A": 0xFFFF00,
        "B": 0xAAAAAA,
        "C": 0xFF8800,
        "D": 0x00FF00,
        "F": 0xFF0000,
    }.get(key, 0xFFFFFF)

def get_match_result(m):
    """Detecta Win/Draw/Loss. Draw quando scores iguais (winningSide null)."""
    if m.get("result") == "win":
        return "✅ Win"
    ps = m.get("playerScore")
    os_ = m.get("opponentScore")
    if ps is not None and os_ is not None and str(ps) == str(os_):
        return "➖ Draw"
    return "❌ Loss"

def get_match_date(m):
    """Retorna a data REAL do evento (scheduledTime) ou playedAt como fallback."""
    return m.get("scheduledTime") or m.get("playedAt") or ""

def get_tier_emoji(tier):
    if not isinstance(tier, str):
        return "❓"
    return {
        "Champion": "👑", "champion": "👑",
        "S": "🔵", "s": "🔵",
        "A": "🟡", "a": "🟡",
        "B": "⚪", "b": "⚪",
        "C": "🟠", "c": "🟠",
        "D": "🟢", "d": "🟢",
        "F": "🔴", "f": "🔴",
    }.get(tier, "❓")

def get_avatar_file(p):
    avatar = p.get("avatar") or ""
    if not avatar.startswith("data:image/png;base64,"):
        return None
    try:
        img_bytes = base64.b64decode(avatar.split(",", 1)[1])
        img = Image.open(io.BytesIO(img_bytes)).resize((100, 100), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return discord.File(buf, filename="avatar.png")
    except:
        return None

def player_id_to_name(pid, players):
    found = next((p for p in players if p.get("id") == pid), None)
    return get_name(found) if found else pid[:8]

def build_tier_embed(tier, players_in_tier):
    color = get_tier_color(tier)
    emoji = get_tier_emoji(tier)
    lines = []
    for p in players_in_tier:
        aff_str = f" {get_affiliation_display(p)}" if get_affiliation(p) else ""
        lines.append(f"**{get_name(p)}**{aff_str}  `{get_record(p)}`")
    embed = discord.Embed(
        title=f"{emoji} {tier} TIER  ({len(players_in_tier)} fighters)",
        description="\n".join(lines),
        color=color
    )
    return embed

def build_player_embed(found):
    pname = get_name(found)
    aff_display = get_affiliation_display(found)
    tier = found.get("tier") or "F"
    color = get_tier_color(tier)
    emoji = get_tier_emoji(tier)
    title = pname + (f"  {aff_display}" if aff_display else "")
    embed = discord.Embed(title=title, color=color)
    embed.add_field(name="Tier", value=f"{emoji} **{tier}**", inline=True)
    embed.add_field(name="Record", value=get_record(found), inline=True)
    embed.add_field(name="Streak", value=get_streak(found), inline=True)
    embed.add_field(name="Kills", value=found.get("kills", 0), inline=True)
    embed.add_field(name="Deaths", value=found.get("deaths", 0), inline=True)
    embed.add_field(name="K/D", value=round(found.get("kills", 0) / max(found.get("deaths", 1), 1), 2), inline=True)
    embed.add_field(name="Region", value=found.get("region") or "?", inline=True)
    embed.add_field(name="Platform", value=found.get("platform") or "PC", inline=True)
    embed.add_field(name="Affiliation", value=aff_display or "None", inline=True)
    if found.get("previousTier"):
        embed.add_field(name="Prev. Tier", value=found["previousTier"], inline=True)
    embed.add_field(name="Available", value="✅" if player_is_available(found) else "❌", inline=True)
    history = found.get("matchHistory")
    if isinstance(history, list) and history:
        lines = []
        for m in history[:5]:
            if not isinstance(m, dict):
                continue
            res = get_match_result(m)
            ps = m.get("playerScore", "?")
            os_ = m.get("opponentScore", "?")
            lines.append(f"{res} vs **{m.get('opponent','?')}**  {ps}-{os_}  _{m.get('event','')}_")
        if lines:
            embed.add_field(name=f"📜 Recent Matches ({len(history)} total)", value="\n".join(lines), inline=False)
    return embed

# ========================= VIEWS =========================

# Helper: embed e view do painel principal (usado pelo Back)
def build_main_panel_embed():
    embed = discord.Embed(
        title="🏆 HCL MANAGER BOT",
        description=(
            "**Hardcore Combat League** — Drunken Wrestlers 2\n\n"
            "Use the buttons below to explore the HCL roster, tier list, match history and more."
        ),
        color=0xFF0000
    )
    embed.add_field(name="🏆 Tier List", value="View the full tier list or filter by tier", inline=True)
    embed.add_field(name="🥊 Fighters", value="Browse fighters with filters", inline=True)
    embed.add_field(name="👤 Player Lookup", value="Get a fighter's full card", inline=True)
    embed.add_field(name="📜 Match History", value="See any fighter's fight record", inline=True)
    embed.add_field(name="🥊 Latest Matches", value="Recent completed matches", inline=True)
    embed.add_field(name="📅 Events", value="Upcoming and past events", inline=True)
    embed.add_field(name="📊 Stats", value="General HCL statistics", inline=True)
    embed.add_field(name="🏅 Rankings", value="Top fighters by wins/kills/K/D/GOAT", inline=True)
    embed.add_field(name="🔄 Refresh", value="Refresh data from the API", inline=True)
    embed.add_field(name="📜 Past Seasons", value="Season 1, Season 2, All-Time stats", inline=True)
    embed.set_footer(text=f"HCL Bot • {HCL_SITE}")
    return embed

# ---------- Main HCL Panel ----------
class HCLMainPanel(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="🏆 Tier List", style=discord.ButtonStyle.danger, custom_id="hcl_tierlist", row=0)
    async def btn_tierlist(self, interaction: discord.Interaction, button: ui.Button):
        embed = discord.Embed(
            title="🏆 Tier List",
            description="Choose a tier to view:",
            color=0xFF0000
        )
        await interaction.response.edit_message(embed=embed, view=TierSelectView())

    @ui.button(label="🥊 Fighters", style=discord.ButtonStyle.primary, custom_id="hcl_fighters", row=0)
    async def btn_fighters(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()
        players = await get_players()
        opts = build_affiliation_options(players)
        embed = discord.Embed(
            title="🥊 Fighters",
            description="Select a filter below to browse the roster:",
            color=0x0055FF
        )
        await interaction.edit_original_response(embed=embed, view=FightersFilterView(opts))

    @ui.button(label="👤 Player Lookup", style=discord.ButtonStyle.success, custom_id="hcl_player", row=0)
    async def btn_player(self, interaction: discord.Interaction, button: ui.Button):
        # Modal: Discord não permite edit_message depois de modal, resultado vai ephemeral
        await interaction.response.send_modal(PlayerLookupModal())

    @ui.button(label="📜 Match History", style=discord.ButtonStyle.secondary, custom_id="hcl_history", row=1)
    async def btn_history(self, interaction: discord.Interaction, button: ui.Button):
        # Modal: mesmo caso acima
        await interaction.response.send_modal(HistoryLookupModal())

    @ui.button(label="🥊 Latest Matches", style=discord.ButtonStyle.primary, custom_id="hcl_matches", row=1)
    async def btn_matches(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()
        matches = await get_matches()
        players = await get_players()
        if not matches:
            return await interaction.followup.send("❌ Failed to fetch matches.", ephemeral=True)
        completed = sorted(
            [m for m in matches if m.get("status") == "completed"],
            key=lambda m: get_match_date(m), reverse=True
        )
        nav = MatchesNavView(completed, players, 0)
        await interaction.edit_original_response(embed=nav.build_embed(), view=nav)

    @ui.button(label="📅 Events", style=discord.ButtonStyle.secondary, custom_id="hcl_events", row=1)
    async def btn_events(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()
        events = await get_events()
        if not events:
            return await interaction.followup.send("❌ Failed to fetch events.", ephemeral=True)
        embed = discord.Embed(title="📅 HCL Events", color=0x00FF88)
        for e in events:
            status = "✅ Completed" if e.get("completed") else "⏳ Upcoming"
            date = (e.get("date") or e.get("scheduledTime") or "")[:10]
            embed.add_field(name=e.get("name", "Event"), value=f"Date: {date}  |  {status}", inline=False)
        await interaction.edit_original_response(embed=embed, view=BackToMainView())

    @ui.button(label="📊 Stats", style=discord.ButtonStyle.secondary, custom_id="hcl_stats", row=2)
    async def btn_stats(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()
        players = await get_players()
        matches = await get_matches()
        events = await get_events()
        visible = [p for p in players if not p.get("hiddenFromLeaderboard")]
        completed = [m for m in matches if m.get("status") == "completed"]
        top_kills = sorted(visible, key=lambda p: p.get("kills", 0), reverse=True)[:3]
        top_wins = sorted(visible, key=lambda p: p.get("wins", 0), reverse=True)[:3]
        embed = discord.Embed(title="📊 HCL — General Stats", color=0xFF0000)
        embed.add_field(name="Fighters", value=len(visible), inline=True)
        embed.add_field(name="Matches Played", value=len(completed), inline=True)
        embed.add_field(name="Events", value=len(events), inline=True)
        tier_counts = {}
        for p in visible:
            t = p.get("tier") or "?"
            tier_counts[t] = tier_counts.get(t, 0) + 1
        embed.add_field(name="Fighters by Tier", value="  ".join(f"**{t}**: {c}" for t, c in sorted(tier_counts.items())), inline=False)
        aff_counts = {}
        for p in visible:
            a = p.get("affiliation") or "None"
            aff_counts[a] = aff_counts.get(a, 0) + 1
        AFF_EMOJIS = {"BBK": "🍌", "CHAOS": "💀", "DWO": "⚫", "SAL": "🦅", "IMPERIO": "👑"}
        aff_lines = []
        for a, c in sorted(aff_counts.items()):
            emoji = AFF_EMOJIS.get(a.upper(), "🏷️") if a != "None" else ""
            aff_lines.append(f"{emoji} **{a}**: {c}".strip())
        embed.add_field(name="Fighters by Affiliation", value="  ".join(aff_lines), inline=False)
        embed.add_field(name="☠️ Top 3 Kills", value="\n".join(f"{i+1}. **{get_name(p)}** — {p.get('kills',0)} kills" for i, p in enumerate(top_kills)), inline=True)
        embed.add_field(name="🏆 Top 3 Wins", value="\n".join(f"{i+1}. **{get_name(p)}** — {p.get('wins',0)}" for i, p in enumerate(top_wins)), inline=True)
        await interaction.edit_original_response(embed=embed, view=BackToMainView())

    @ui.button(label="🏅 Rankings", style=discord.ButtonStyle.danger, custom_id="hcl_top", row=2)
    async def btn_top(self, interaction: discord.Interaction, button: ui.Button):
        embed = discord.Embed(
            title="🏅 Rankings",
            description="Choose a ranking category:",
            color=0xFFAA00
        )
        await interaction.response.edit_message(embed=embed, view=TopSelectView())

    @ui.button(label="🔄 Refresh Data", style=discord.ButtonStyle.secondary, custom_id="hcl_refresh", row=2)
    async def btn_refresh(self, interaction: discord.Interaction, button: ui.Button):
        global players_cache, players_cache_ts, matches_cache, events_cache
        players_cache = None
        players_cache_ts = None
        matches_cache = None
        events_cache = None
        embed = discord.Embed(
            title="🔄 Cache Cleared",
            description="✅ Next request will pull fresh data from the API.",
            color=0x00FF88
        )
        await interaction.response.edit_message(embed=embed, view=BackToMainView())

    @ui.button(label="📜 Past Seasons", style=discord.ButtonStyle.secondary, custom_id="hcl_seasons", row=3)
    async def btn_seasons(self, interaction: discord.Interaction, button: ui.Button):
        embed = discord.Embed(
            title="📜 Past Seasons",
            description="Select a season to view historical rankings.",
            color=0xAA88FF
        )
        await interaction.response.edit_message(embed=embed, view=LegacySeasonsView())


# ---------- Back to Main ----------
class BackToMainView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="🔙 Back to Panel", style=discord.ButtonStyle.secondary, custom_id="hcl_back_main")
    async def back(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(embed=build_main_panel_embed(), view=HCLMainPanel())


# ========================= LEGACY SEASONS =========================


class LegacySeasonsView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="🏆 Season 1 Regional", style=discord.ButtonStyle.primary, custom_id="legacy_s1", row=0)
    async def btn_s1(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()
        data = await legacy.fetch_all_season1()
        if not data:
            return await interaction.followup.send("❌ Could not load Season 1.", ephemeral=True)
        regions = {}
        for f in data:
            r = f.get("region", "?")
            regions.setdefault(r, []).append(f)
        embed = discord.Embed(title="🏆 Season 1 Regional", color=0xAA88FF)
        for r in legacy.S1_REGION_ORDER:
            fighters = regions.get(r, [])
            top = fighters[:5]
            lines = "\n".join(f"{f['name']} — W:{f['w']} L:{f['l']}" for f in top)
            embed.add_field(name=f"🌎 {r} — {len(fighters)} fighters", value=lines or "—", inline=False)
        await interaction.edit_original_response(embed=embed, view=LegacyS1View(regions, 0))

    @ui.button(label="🌍 Season 2 Global", style=discord.ButtonStyle.success, custom_id="legacy_s2", row=0)
    async def btn_s2(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()
        tabs = legacy.S2_REGION_ORDER
        all_data = []
        for tab in tabs:
            fighters = await legacy.fetch_season2_tab(tab)
            for f in fighters:
                f["region"] = tab
            all_data.append(fighters)
        if not any(all_data):
            return await interaction.followup.send("❌ Could not load Season 2.", ephemeral=True)
        embed = discord.Embed(title="🌍 Season 2 Global", color=0xAA88FF)
        for i, tab in enumerate(tabs):
            fighters = all_data[i]
            top = fighters[:5] if fighters else []
            lines = "\n".join(f"{f['name']} — W:{f['w']} L:{f['l']}" for f in top) if top else "—"
            embed.add_field(name=f"🌎 {tab} — {len(fighters)} fighters", value=lines, inline=False)
        await interaction.edit_original_response(embed=embed, view=LegacyS2View(all_data, tabs, 0))

    @ui.button(label="📊 All-Time", style=discord.ButtonStyle.danger, custom_id="legacy_at", row=1)
    async def btn_at(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()
        data = await legacy.fetch_alltime()
        if not data:
            return await interaction.followup.send("❌ Could not load All-Time.", ephemeral=True)
        embed, view = _build_alltime_page(data, 0)
        await interaction.edit_original_response(embed=embed, view=view)

    @ui.button(label="🔙 Back", style=discord.ButtonStyle.secondary, custom_id="legacy_back", row=2)
    async def back(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(embed=build_main_panel_embed(), view=HCLMainPanel())


class LegacyS1View(ui.View):
    def __init__(self, regions: dict, page: int):
        super().__init__(timeout=None)
        self.regions = regions
        self.page = page
        self.region_keys = [r for r in legacy.S1_REGION_ORDER if regions.get(r)]
        self.btn_prev.disabled = page <= 0
        self.btn_next.disabled = page >= len(self.region_keys) - 1

    def build(self):
        r = self.region_keys[self.page]
        fighters = self.regions[r]
        embed = discord.Embed(title=f"🏆 Season 1 — {r}", description=f"{len(fighters)} fighters", color=0xAA88FF)
        for f in fighters:
            embed.add_field(name="", value=f"**{f['name']}** — W:{f['w']} L:{f['l']} | K:{f['k']} D:{f['d']} | Rtg:{f['rating']}", inline=False)
        embed.set_footer(text=f"Page {self.page + 1}/{len(self.region_keys)}")
        return embed

    @ui.button(label="◀ Previous", style=discord.ButtonStyle.secondary, custom_id="s1_prev")
    async def btn_prev(self, interaction: discord.Interaction, button: ui.Button):
        self.page -= 1
        self.btn_prev.disabled = self.page <= 0
        self.btn_next.disabled = self.page >= len(self.region_keys) - 1
        await interaction.response.edit_message(embed=self.build(), view=self)

    @ui.button(label="Next ▶", style=discord.ButtonStyle.secondary, custom_id="s1_next")
    async def btn_next(self, interaction: discord.Interaction, button: ui.Button):
        self.page += 1
        self.btn_prev.disabled = self.page <= 0
        self.btn_next.disabled = self.page >= len(self.region_keys) - 1
        await interaction.response.edit_message(embed=self.build(), view=self)

    @ui.button(label="🔙 Back", style=discord.ButtonStyle.secondary, custom_id="s1_back")
    async def back(self, interaction: discord.Interaction, button: ui.Button):
        embed = discord.Embed(
            title="📜 Past Seasons",
            description="Select a season to view historical rankings.",
            color=0xAA88FF
        )
        await interaction.response.edit_message(embed=embed, view=LegacySeasonsView())


class LegacyS2View(ui.View):
    def __init__(self, all_data: list, tabs: list[str], page: int):
        super().__init__(timeout=None)
        self.all_data = all_data
        self.tabs = tabs
        self.page = page
        self.btn_prev.disabled = page <= 0
        self.btn_next.disabled = page >= len(tabs) - 1

    def build(self):
        tab = self.tabs[self.page]
        fighters = self.all_data[self.page]
        embed = discord.Embed(title=f"🌍 Season 2 — {tab}", description=f"{len(fighters)} fighters", color=0xAA88FF)
        for f in fighters:
            embed.add_field(name="", value=f"**{f['name']}** — W:{f['w']} L:{f['l']} | K:{f['k']} D:{f['d']} | Rtg:{f['rating']}", inline=False)
        embed.set_footer(text=f"Page {self.page + 1}/{len(self.tabs)}")
        return embed

    @ui.button(label="◀ Previous", style=discord.ButtonStyle.secondary, custom_id="s2_prev")
    async def btn_prev(self, interaction: discord.Interaction, button: ui.Button):
        self.page -= 1
        self.btn_prev.disabled = self.page <= 0
        self.btn_next.disabled = self.page >= len(self.tabs) - 1
        await interaction.response.edit_message(embed=self.build(), view=self)

    @ui.button(label="Next ▶", style=discord.ButtonStyle.secondary, custom_id="s2_next")
    async def btn_next(self, interaction: discord.Interaction, button: ui.Button):
        self.page += 1
        self.btn_prev.disabled = self.page <= 0
        self.btn_next.disabled = self.page >= len(self.tabs) - 1
        await interaction.response.edit_message(embed=self.build(), view=self)

    @ui.button(label="🔙 Back", style=discord.ButtonStyle.secondary, custom_id="s2_back")
    async def back(self, interaction: discord.Interaction, button: ui.Button):
        embed = discord.Embed(
            title="📜 Past Seasons",
            description="Select a season to view historical rankings.",
            color=0xAA88FF
        )
        await interaction.response.edit_message(embed=embed, view=LegacySeasonsView())


class AllTimeView(ui.View):
    def __init__(self, data: list[dict], page: int):
        super().__init__(timeout=None)
        self.data = data
        self.page = page
        per_page = 25
        self.total_pages = max(1, (len(data) + per_page - 1) // per_page)
        self.btn_prev.disabled = page <= 0
        self.btn_next.disabled = page >= self.total_pages - 1

    def build(self):
        per_page = 25
        start = self.page * per_page
        end = start + per_page
        page_data = self.data[start:end]
        embed = discord.Embed(
            title="📊 All-Time Ranking",
            description=f"{len(self.data)} fighters across all seasons",
            color=0xFFAA00
        )
        for f in page_data:
            seasons = ", ".join(sorted(f["seasons"]))
            embed.add_field(
                name="",
                value=f"**{f['name']}** — W:{f['w']} L:{f['l']} | K:{f['k']} D:{f['d']} | ({seasons})",
                inline=False
            )
        embed.set_footer(text=f"Page {self.page + 1}/{self.total_pages} • {len(self.data)} total")
        return embed

    @ui.button(label="◀ Previous", style=discord.ButtonStyle.secondary, custom_id="at_prev")
    async def btn_prev(self, interaction: discord.Interaction, button: ui.Button):
        self.page -= 1
        self.btn_prev.disabled = self.page <= 0
        self.btn_next.disabled = self.page >= self.total_pages - 1
        await interaction.response.edit_message(embed=self.build(), view=self)

    @ui.button(label="Next ▶", style=discord.ButtonStyle.secondary, custom_id="at_next")
    async def btn_next(self, interaction: discord.Interaction, button: ui.Button):
        self.page += 1
        self.btn_prev.disabled = self.page <= 0
        self.btn_next.disabled = self.page >= self.total_pages - 1
        await interaction.response.edit_message(embed=self.build(), view=self)

    @ui.button(label="🔙 Back", style=discord.ButtonStyle.secondary, custom_id="at_back")
    async def back(self, interaction: discord.Interaction, button: ui.Button):
        embed = discord.Embed(
            title="📜 Past Seasons",
            description="Select a season to view historical rankings.",
            color=0xAA88FF
        )
        await interaction.response.edit_message(embed=embed, view=LegacySeasonsView())


def _build_alltime_page(data: list[dict], page: int):
    """Helper: returns (embed, AllTimeView) for a given page."""
    view = AllTimeView(data, page)
    return view.build(), view


# ---------- Tier Select View ----------
class TierSelectView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="👑 Champion", style=discord.ButtonStyle.secondary, custom_id="tier_champ", row=0)
    async def tier_champ(self, interaction: discord.Interaction, button: ui.Button):
        await self._show_tier(interaction, "Champion")

    @ui.button(label="🔵 S Tier", style=discord.ButtonStyle.primary, custom_id="tier_s", row=0)
    async def tier_s(self, interaction: discord.Interaction, button: ui.Button):
        await self._show_tier(interaction, "S")

    @ui.button(label="🟡 A Tier", style=discord.ButtonStyle.primary, custom_id="tier_a", row=0)
    async def tier_a(self, interaction: discord.Interaction, button: ui.Button):
        await self._show_tier(interaction, "A")

    @ui.button(label="⚪ B Tier", style=discord.ButtonStyle.secondary, custom_id="tier_b", row=1)
    async def tier_b(self, interaction: discord.Interaction, button: ui.Button):
        await self._show_tier(interaction, "B")

    @ui.button(label="🟠 C Tier", style=discord.ButtonStyle.secondary, custom_id="tier_c", row=1)
    async def tier_c(self, interaction: discord.Interaction, button: ui.Button):
        await self._show_tier(interaction, "C")

    @ui.button(label="🟢 D Tier", style=discord.ButtonStyle.success, custom_id="tier_d", row=1)
    async def tier_d(self, interaction: discord.Interaction, button: ui.Button):
        await self._show_tier(interaction, "D")

    @ui.button(label="🔴 F Tier", style=discord.ButtonStyle.danger, custom_id="tier_f", row=2)
    async def tier_f(self, interaction: discord.Interaction, button: ui.Button):
        await self._show_tier(interaction, "F")

    @ui.button(label="📋 Full List", style=discord.ButtonStyle.secondary, custom_id="tier_all", row=2)
    async def tier_all(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()
        players = await get_players()
        visible = [p for p in players if not p.get("hiddenFromLeaderboard")]
        TIER_ORDER = ["Champion", "S", "A", "B", "C", "D", "F"]
        tiers = {}
        for p in visible:
            t = p.get("tier") or "F"
            tiers.setdefault(t, []).append(p)
        all_entries = []
        for tier in TIER_ORDER:
            if tier not in tiers:
                continue
            for p in tiers[tier]:
                all_entries.append((tier, p))
        total = len(all_entries)
        if total <= 25:
            embed = discord.Embed(title="📋 HCL Full Tier List", color=0xFFD700)
            for tier in TIER_ORDER:
                if tier not in tiers:
                    continue
                emoji = get_tier_emoji(tier)
                lines = []
                for p in tiers[tier]:
                    aff_str = f" {get_affiliation_display(p)}" if get_affiliation(p) else ""
                    lines.append(f"**{get_name(p)}**{aff_str}  `{get_record(p)}`")
                embed.add_field(name=f"{emoji} {tier} ({len(tiers[tier])})", value="\n".join(lines) or "—", inline=False)
            await interaction.edit_original_response(embed=embed, view=BackToTierView())
        else:
            view = RosterNavView(all_entries, 0, TIER_ORDER)
            await interaction.edit_original_response(embed=view.build_embed(), view=view)

    @ui.button(label="👥 All Fighters", style=discord.ButtonStyle.secondary, custom_id="tier_all_fighters", row=3)
    async def tier_all_fighters(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()
        players = await get_players()
        all_players = sorted(players, key=lambda p: (["Champion", "S", "A", "B", "C", "D", "F"].index(p.get("tier")) if p.get("tier") in ["Champion", "S", "A", "B", "C", "D", "F"] else 99, get_name(p)))
        TIER_ORDER = ["Champion", "S", "A", "B", "C", "D", "F"]
        entries = []
        for p in all_players:
            entries.append((p.get("tier") or "F", p))
        view = RosterNavView(entries, 0, TIER_ORDER, show_unavailable=True)
        await interaction.edit_original_response(embed=view.build_embed(), view=view)

    @ui.button(label="🔙 Back", style=discord.ButtonStyle.secondary, custom_id="tier_back", row=3)
    async def back(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(embed=build_main_panel_embed(), view=HCLMainPanel())

    async def _show_tier(self, interaction: discord.Interaction, tier: str):
        await interaction.response.defer()
        players = await get_players()
        visible = [p for p in players if not p.get("hiddenFromLeaderboard")]
        if tier == "Champion":
            champs = [p for p in visible if p.get("tier") == "Champion"]
            if not champs:
                embed = discord.Embed(title="👑 Champion", description="❌ No champion found.", color=0xFFD700)
                return await interaction.edit_original_response(embed=embed, view=BackToTierView())
            champ = champs[0]
            aff_display = get_affiliation_display(champ)
            embed = discord.Embed(
                title="🥇 HCL WORLD CHAMPION",
                description=f"👑 **{get_name(champ)}**" + (f"  {aff_display}" if aff_display else ""),
                color=0xFFD700
            )
            embed.add_field(name="Record", value=get_record(champ), inline=True)
            embed.add_field(name="Kills", value=champ.get("kills", 0), inline=True)
            embed.add_field(name="Region", value=champ.get("region", "?"), inline=True)
            embed.set_footer(text=f"Affiliation: {aff_display or 'None'}")
            return await interaction.edit_original_response(embed=embed, view=BackToTierView())
        filtered = [p for p in visible if (p.get("tier") or "").upper() == tier.upper()]
        if not filtered:
            embed = discord.Embed(title=f"{get_tier_emoji(tier)} {tier} Tier", description="❌ No players in this tier.", color=get_tier_color(tier))
            return await interaction.edit_original_response(embed=embed, view=BackToTierView())
        total = len(filtered)
        if total <= 25:
            await interaction.edit_original_response(embed=build_tier_embed(tier, filtered), view=BackToTierView())
        else:
            entries = [(tier, p) for p in filtered]
            view = RosterNavView(entries, 0, [tier])
            await interaction.edit_original_response(embed=view.build_embed(), view=view)


# ---------- Paginated Roster Navigation ----------
class RosterNavView(ui.View):
    def __init__(self, entries: list, page: int, tier_order: list[str], show_unavailable: bool = False):
        super().__init__(timeout=None)
        self.entries = entries
        self.page = page
        self.tier_order = tier_order
        self.show_unavailable = show_unavailable
        self.per_page = 25
        self.total_pages = max(1, (len(entries) + self.per_page - 1) // self.per_page)
        self.btn_prev.disabled = page <= 0
        self.btn_next.disabled = page >= self.total_pages - 1

    def build_embed(self):
        start = self.page * self.per_page
        end = start + self.per_page
        page_entries = self.entries[start:end]
        title = "👥 Full Roster (all players)" if self.show_unavailable else "📋 HCL Full Tier List"
        embed = discord.Embed(title=title, color=0xFFD700)
        embed.set_footer(text=f"Page {self.page + 1}/{self.total_pages} • {len(self.entries)} fighters total")
        current_tier = None
        current_lines = []
        tier_count = {}
        for tier, p in page_entries:
            t = tier or "F"
            tier_count[t] = tier_count.get(t, 0) + 1
            if t != current_tier and current_lines:
                emoji = get_tier_emoji(current_tier)
                embed.add_field(
                    name=f"{emoji} {current_tier} ({tier_count.get(current_tier, 0)})",
                    value="\n".join(current_lines) or "—",
                    inline=False
                )
                current_lines = []
            current_tier = t
            aff_str = f" {get_affiliation_display(p)}" if get_affiliation(p) else ""
            record = get_record(p)
            current_lines.append(f"**{get_name(p)}**{aff_str}  `{record}`")
        if current_lines and current_tier:
            emoji = get_tier_emoji(current_tier)
            embed.add_field(
                name=f"{emoji} {current_tier} ({tier_count.get(current_tier, 0)})",
                value="\n".join(current_lines) or "—",
                inline=False
            )
        return embed

    @ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary, custom_id="roster_prev")
    async def btn_prev(self, interaction: discord.Interaction, button: ui.Button):
        self.page -= 1
        self.btn_prev.disabled = self.page <= 0
        self.btn_next.disabled = self.page >= self.total_pages - 1
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @ui.button(label="Next ▶", style=discord.ButtonStyle.secondary, custom_id="roster_next")
    async def btn_next(self, interaction: discord.Interaction, button: ui.Button):
        self.page += 1
        self.btn_prev.disabled = self.page <= 0
        self.btn_next.disabled = self.page >= self.total_pages - 1
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @ui.button(label="🔙 Back", style=discord.ButtonStyle.secondary, custom_id="roster_back")
    async def back(self, interaction: discord.Interaction, button: ui.Button):
        embed = discord.Embed(title="🏆 Tier List", description="Choose a tier to view:", color=0xFF0000)
        await interaction.response.edit_message(embed=embed, view=TierSelectView())


# Back button that returns to tier selection screen
class BackToTierView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="🔙 Back to Tiers", style=discord.ButtonStyle.secondary, custom_id="back_to_tiers")
    async def back(self, interaction: discord.Interaction, button: ui.Button):
        embed = discord.Embed(title="🏆 Tier List", description="Choose a tier to view:", color=0xFF0000)
        await interaction.response.edit_message(embed=embed, view=TierSelectView())


# ---------- Fighters Filter View ----------
class FightersFilterView(ui.View):
    def __init__(self, affiliation_options: list[discord.SelectOption]):
        super().__init__(timeout=None)
        self.region = "ALL"
        self.tier = "ALL"
        self.affiliation = "ALL"
        self.available = "AVAILABLE"
        self.add_item(FightersRegionSelect())
        self.add_item(FightersTierSelect())
        self.add_item(FightersAffiliationSelect(affiliation_options))
        self.add_item(FightersAvailableSelect())
        self.add_item(FightersApplyButton())
        self.add_item(FightersBackButton())

class FightersBackButton(ui.Button):
    def __init__(self):
        super().__init__(label="🔙 Back", style=discord.ButtonStyle.secondary, custom_id="fighters_back", row=4)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=build_main_panel_embed(), view=HCLMainPanel())

class FightersApplyButton(ui.Button):
    def __init__(self):
        super().__init__(label="✅ Apply Filters", style=discord.ButtonStyle.success, custom_id="fighters_apply", row=4)

    async def callback(self, interaction: discord.Interaction):
        v = self.view
        await send_fighters(interaction, region=v.region, tier=v.tier, affiliation=v.affiliation, available=v.available)

class FightersRegionSelect(ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="All Regions", value="ALL", emoji="🌍"),
            discord.SelectOption(label="NA", value="NA", emoji="🇺🇸"),
            discord.SelectOption(label="EU", value="EU", emoji="🇪🇺"),
            discord.SelectOption(label="SA", value="SA", emoji="🌎"),
            discord.SelectOption(label="AS", value="AS", emoji="🌏"),
        ]
        super().__init__(placeholder="🌍 Filter by Region", options=options, custom_id="fighters_region", row=0)

    async def callback(self, interaction: discord.Interaction):
        self.view.region = self.values[0]
        await interaction.response.defer()

class FightersTierSelect(ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="All Tiers", value="ALL", emoji="📋"),
            discord.SelectOption(label="Champion", value="Champion", emoji="👑"),
            discord.SelectOption(label="S Tier", value="S", emoji="🔵"),
            discord.SelectOption(label="A Tier", value="A", emoji="🟡"),
            discord.SelectOption(label="B Tier", value="B", emoji="⚪"),
            discord.SelectOption(label="C Tier", value="C", emoji="🟠"),
            discord.SelectOption(label="D Tier", value="D", emoji="🟢"),
            discord.SelectOption(label="F Tier", value="F", emoji="🔴"),
        ]
        super().__init__(placeholder="🏆 Filter by Tier", options=options, custom_id="fighters_tier", row=1)

    async def callback(self, interaction: discord.Interaction):
        self.view.tier = self.values[0]
        await interaction.response.defer()

def build_affiliation_options(players: list[dict]) -> list[discord.SelectOption]:
    options = [
        discord.SelectOption(label="All Affiliations", value="ALL", emoji="🤝"),
        discord.SelectOption(label="No Affiliation", value="NONE", emoji="🚫"),
    ]
    seen = set()
    for p in players:
        aff = (p.get("affiliation") or "").strip().upper()
        if aff and aff not in seen:
            seen.add(aff)
            emoji = AFFILIATION_EMOJIS.get(aff, "🏷️")
            options.append(discord.SelectOption(label=aff, value=aff, emoji=emoji))
    return options

class FightersAffiliationSelect(ui.Select):
    def __init__(self, options: list[discord.SelectOption]):
        super().__init__(placeholder="🤝 Filter by Affiliation", options=options, custom_id="fighters_affiliation", row=2)

    async def callback(self, interaction: discord.Interaction):
        self.view.affiliation = self.values[0]
        await interaction.response.defer()

class FightersAvailableSelect(ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Available Only", value="AVAILABLE", emoji="🟢", default=True),
            discord.SelectOption(label="All Fighters", value="ALL", emoji="👥"),
        ]
        super().__init__(placeholder="🟢 Availability", options=options, custom_id="fighters_available", row=3)

    async def callback(self, interaction: discord.Interaction):
        self.view.available = self.values[0]
        await interaction.response.defer()

async def send_fighters(interaction: discord.Interaction, region="ALL", tier="ALL", affiliation="ALL", available="AVAILABLE"):
    await interaction.response.defer()
    players = await get_players()
    if available == "ALL":
        result = list(players)
    else:
        result = [p for p in players if not p.get("hiddenFromLeaderboard")]
    if region != "ALL":
        result = [p for p in result if (p.get("region") or "").upper() == region]
    if tier != "ALL":
        result = [p for p in result if (p.get("tier") or "").upper() == tier.upper()]
    if affiliation == "NONE":
        result = [p for p in result if not p.get("affiliation")]
    elif affiliation != "ALL":
        result = [p for p in result if (p.get("affiliation") or "").upper() == affiliation]
    if not result:
        embed = discord.Embed(title="🥊 Fighters", description="❌ No fighters found with those filters.", color=0xFF0000)
        return await interaction.edit_original_response(embed=embed, view=BackToFightersView())
    total = len(result)
    if total <= 25:
        labels = " | ".join(filter(None, [
            f"Region: {region}" if region != "ALL" else "",
            f"Tier: {tier}" if tier != "ALL" else "",
            f"Affiliation: {affiliation}" if affiliation != "ALL" else "",
        ])) or "All fighters"
        embed = discord.Embed(title=f"🥊 FIGHTERS  ({total} found)", description=f"Filter: `{labels}`", color=0xFF0000)
        for p in result:
            aff_display = get_affiliation_display(p)
            t = p.get("tier") or "?"
            emoji = get_tier_emoji(t)
            value = (
                f"{emoji} **{t}**  |  {get_record(p)}  |  K: {p.get('kills',0)} / D: {p.get('deaths',0)}\n"
                f"Region: {p.get('region','?')}  |  Platform: {p.get('platform') or 'PC'}"
                + (f"\nAffiliation: {aff_display}" if aff_display else "")
            )
            embed.add_field(name=get_name(p), value=value, inline=False)
        await interaction.edit_original_response(embed=embed, view=BackToFightersView())
    else:
        entries = [("", p) for p in result]
        view = FightersNavView(entries, 0, region, tier, affiliation, available)
        await interaction.edit_original_response(embed=view.build_embed(), view=view)

class FightersNavView(ui.View):
    def __init__(self, entries: list, page: int, region: str, tier: str, affiliation: str, available: str):
        super().__init__(timeout=None)
        self.entries = entries
        self.page = page
        self.region = region
        self.tier = tier
        self.affiliation = affiliation
        self.available = available
        self.per_page = 25
        self.total_pages = max(1, (len(entries) + self.per_page - 1) // self.per_page)
        self.btn_prev.disabled = page <= 0
        self.btn_next.disabled = page >= self.total_pages - 1

    def build_embed(self):
        start = self.page * self.per_page
        end = start + self.per_page
        page_entries = self.entries[start:end]
        labels = " | ".join(filter(None, [
            f"Region: {self.region}" if self.region != "ALL" else "",
            f"Tier: {self.tier}" if self.tier != "ALL" else "",
            f"Affiliation: {self.affiliation}" if self.affiliation != "ALL" else "",
        ])) or "All fighters"
        embed = discord.Embed(
            title=f"🥊 FIGHTERS  ({len(self.entries)} found)",
            description=f"Filter: `{labels}`",
            color=0xFF0000
        )
        for _, p in page_entries:
            aff_display = get_affiliation_display(p)
            t = p.get("tier") or "?"
            emoji = get_tier_emoji(t)
            value = (
                f"{emoji} **{t}**  |  {get_record(p)}  |  K: {p.get('kills',0)} / D: {p.get('deaths',0)}\n"
                f"Region: {p.get('region','?')}  |  Platform: {p.get('platform') or 'PC'}"
                + (f"\nAffiliation: {aff_display}" if aff_display else "")
            )
            embed.add_field(name=get_name(p), value=value, inline=False)
        embed.set_footer(text=f"Page {self.page + 1}/{self.total_pages} • {len(self.entries)} total")
        return embed

    @ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary, custom_id="fighters_nav_prev")
    async def btn_prev(self, interaction: discord.Interaction, button: ui.Button):
        self.page -= 1
        self.btn_prev.disabled = self.page <= 0
        self.btn_next.disabled = self.page >= self.total_pages - 1
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @ui.button(label="Next ▶", style=discord.ButtonStyle.secondary, custom_id="fighters_nav_next")
    async def btn_next(self, interaction: discord.Interaction, button: ui.Button):
        self.page += 1
        self.btn_prev.disabled = self.page <= 0
        self.btn_next.disabled = self.page >= self.total_pages - 1
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @ui.button(label="🔙 Back", style=discord.ButtonStyle.secondary, custom_id="fighters_nav_back")
    async def back(self, interaction: discord.Interaction, button: ui.Button):
        players = await get_players()
        opts = build_affiliation_options(players)
        embed = discord.Embed(title="🥊 Fighters", description="Select a filter below to browse the roster:", color=0x0055FF)
        await interaction.response.edit_message(embed=embed, view=FightersFilterView(opts))


class BackToFightersView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="🔙 Back to Fighters", style=discord.ButtonStyle.secondary, custom_id="back_to_fighters")
    async def back(self, interaction: discord.Interaction, button: ui.Button):
        players = await get_players()
        opts = build_affiliation_options(players)
        embed = discord.Embed(title="🥊 Fighters", description="Select a filter below to browse the roster:", color=0x0055FF)
        await interaction.response.edit_message(embed=embed, view=FightersFilterView(opts))


# ---------- Player Lookup Modal ----------
class PlayerLookupModal(ui.Modal, title="🔍 Fighter Lookup"):
    name = ui.TextInput(label="Fighter name", placeholder="e.g. NLG, JAB, KYMORA...", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        players = await get_players()
        found = find_player(players, self.name.value)
        if not found:
            return await interaction.followup.send(f"❌ No fighter found matching **{self.name.value}**.")
        embed = build_player_embed(found)
        file = get_avatar_file(found)
        if file:
            embed.set_thumbnail(url="attachment://avatar.png")
            await interaction.followup.send(embed=embed, file=file, view=PlayerActionsView(found))
        else:
            await interaction.followup.send(embed=embed, view=PlayerActionsView(found))


# ---------- Player Actions (buttons after player card) ----------
class PlayerActionsView(ui.View):
    def __init__(self, player: dict):
        super().__init__(timeout=120)
        self.player = player

    @ui.button(label="📜 Full Match History", style=discord.ButtonStyle.primary)
    async def btn_history(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=False)
        found = self.player
        raw_history = found.get("matchHistory")
        history = raw_history if isinstance(raw_history, list) else []
        if not history:
            return await interaction.followup.send(f"✅ **{get_name(found)}** has no recorded matches yet.")
        tier = found.get("tier") or "F"
        color = get_tier_color(tier)
        embed = discord.Embed(
            title=f"📜 Match History — {get_name(found)}",
            description=f"**{len(history)} match(es)** | Record: {get_record(found)}",
            color=color
        )
        for m in history:
            if not isinstance(m, dict):
                continue
            res = get_match_result(m)
            ps = m.get("playerScore", "?")
            os_ = m.get("opponentScore", "?")
            score = f"{ps}-{os_}"
            vod = m.get("recordingUrl")
            vod_str = f"[▶ Watch match]({vod})" if vod else "No link"
            t_before = m.get("tierBefore", "?")
            t_after = m.get("tierAfter", "?")
            tier_str = f"{t_before} → {t_after}" if t_before != t_after else t_before
            embed.add_field(
                name=f"{res}  vs **{m.get('opponent','?')}**  `{score}`",
                value=f"📅 {m.get('event','?')}  |  Tier: {tier_str}  |  {vod_str}",
                inline=False
            )
        await interaction.followup.send(embed=embed)


# ---------- History Lookup Modal ----------
class HistoryLookupModal(ui.Modal, title="📜 Match History Lookup"):
    name = ui.TextInput(label="Fighter name", placeholder="e.g. JAB, KYMORA, NLG...", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        players = await get_players()
        found = find_player(players, self.name.value)
        if not found:
            return await interaction.followup.send(f"❌ Fighter **{self.name.value}** not found.")
        raw_history = found.get("matchHistory")
        history = raw_history if isinstance(raw_history, list) else []
        if not history:
            return await interaction.followup.send(f"✅ **{get_name(found)}** has no recorded matches yet.")
        tier = found.get("tier") or "F"
        color = get_tier_color(tier)
        embed = discord.Embed(
            title=f"📜 Match History — {get_name(found)}",
            description=f"**{len(history)} match(es)** | Record: {get_record(found)}",
            color=color
        )
        for m in history:
            if not isinstance(m, dict):
                continue
            res = get_match_result(m)
            ps = m.get("playerScore", "?")
            os_ = m.get("opponentScore", "?")
            score = f"{ps}-{os_}"
            vod = m.get("recordingUrl")
            vod_str = f"[▶ Watch match]({vod})" if vod else "No link"
            t_before = m.get("tierBefore", "?")
            t_after = m.get("tierAfter", "?")
            tier_str = f"{t_before} → {t_after}" if t_before != t_after else t_before
            embed.add_field(
                name=f"{res}  vs **{m.get('opponent','?')}**  `{score}`",
                value=f"📅 {m.get('event','?')}  |  Tier: {tier_str}  |  {vod_str}",
                inline=False
            )
        await interaction.followup.send(embed=embed)


# ---------- Matches Navigation (pagination) ----------
class MatchesNavView(ui.View):
    def __init__(self, matches: list, players: list, page: int, sort_by: str = "event"):
        super().__init__(timeout=None)
        self.players = players
        self.page = page
        self.sort_by = sort_by
        self.per_page = 8
        self._apply_sort(matches)
        self.max_page = max(0, (len(self.matches) - 1) // self.per_page)
        self._update_buttons()

    def _apply_sort(self, matches):
        if self.sort_by == "date":
            self.matches = sorted(
                matches,
                key=lambda m: get_match_date(m), reverse=True
            )
        else:
            self.matches = sorted(
                matches,
                key=lambda m: (m.get("event") or "", get_match_date(m))
            )

    def _update_buttons(self):
        self.btn_prev.disabled = self.page == 0
        self.btn_next.disabled = self.page >= self.max_page
        self.btn_sort.label = f"📅 By Date" if self.sort_by == "event" else f"📋 By Event"
        self.btn_sort.style = discord.ButtonStyle.primary if self.sort_by == "event" else discord.ButtonStyle.secondary

    def build_embed(self):
        start = self.page * self.per_page
        order_label = "Most Recent" if self.sort_by == "date" else "By Event"
        embed = discord.Embed(
            title=f"🥊 HCL Matches  (Page {self.page + 1}/{self.max_page + 1})  —  {order_label}",
            color=0xFF0000
        )
        for m in self.matches[start:start + self.per_page]:
            side1 = [player_id_to_name(pid, self.players) for pid in (m.get("side1PlayerIds") or ["?"])]
            side2 = [player_id_to_name(pid, self.players) for pid in (m.get("side2PlayerIds") or ["?"])]
            p1 = " / ".join(side1)
            p2 = " / ".join(side2)
            score = f"{m.get('side1Score','?')}-{m.get('side2Score','?')}"
            winner = p1 if m.get("winningSide") == 1 else p2
            vod = m.get("recordingUrl")
            vod_str = f"[▶ Watch match]({vod})" if vod else "No link"
            date = get_match_date(m)[:10]
            embed.add_field(
                name=f"{p1}  vs  {p2}  |  {score}",
                value=f"🏆 **{winner}** won  |  📅 {date}  |  {m.get('event','')}  |  {vod_str}",
                inline=False
            )
        return embed

    @ui.button(label="📅 By Date", style=discord.ButtonStyle.secondary, custom_id="matches_sort")
    async def btn_sort(self, interaction: discord.Interaction, button: ui.Button):
        self.sort_by = "event" if self.sort_by == "date" else "date"
        self.page = 0
        self._apply_sort(self.matches)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary, custom_id="matches_prev")
    async def btn_prev(self, interaction: discord.Interaction, button: ui.Button):
        self.page -= 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @ui.button(label="Next ▶", style=discord.ButtonStyle.secondary, custom_id="matches_next")
    async def btn_next(self, interaction: discord.Interaction, button: ui.Button):
        self.page += 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @ui.button(label="🔙 Back", style=discord.ButtonStyle.secondary, custom_id="matches_back")
    async def btn_back(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(embed=build_main_panel_embed(), view=HCLMainPanel())


# ---------- Top/Rankings Select ----------
class TopSelectView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="🏆 Top Wins", style=discord.ButtonStyle.danger, custom_id="top_wins")
    async def top_wins(self, interaction: discord.Interaction, button: ui.Button):
        await send_top(interaction, "wins")

    @ui.button(label="☠️ Top Kills", style=discord.ButtonStyle.danger, custom_id="top_kills")
    async def top_kills(self, interaction: discord.Interaction, button: ui.Button):
        await send_top(interaction, "kills")

    @ui.button(label="⚡ Top K/D", style=discord.ButtonStyle.primary, custom_id="top_kd")
    async def top_kd(self, interaction: discord.Interaction, button: ui.Button):
        await send_top(interaction, "kd")

    @ui.button(label="🐐 GOAT", style=discord.ButtonStyle.success, custom_id="top_goat")
    async def goat(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()
        players = await get_players()
        goat_data = compute_hcl_goat(players)
        if not goat_data:
            embed = discord.Embed(title="🐐 GOAT", description="❌ Not enough data (min 5 fights needed).", color=0xFFD700)
            return await interaction.edit_original_response(embed=embed, view=TopSelectView())
        await interaction.edit_original_response(embed=build_goat_embed(goat_data), view=GoatView())

    @ui.button(label="🔙 Back", style=discord.ButtonStyle.secondary, custom_id="top_back")
    async def back(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(embed=build_main_panel_embed(), view=HCLMainPanel())

async def send_top(interaction: discord.Interaction, category: str):
    await interaction.response.defer()
    players = await get_players()
    visible = [p for p in players if not p.get("hiddenFromLeaderboard")]
    if category == "kills":
        ranked = sorted(visible, key=lambda p: p.get("kills", 0), reverse=True)
        label = "Kills"
        val_fn = lambda p: f"**{p.get('kills', 0)}** kills"
    elif category == "kd":
        ranked = sorted(visible, key=lambda p: p.get("kills", 0) / max(p.get("deaths", 1), 1), reverse=True)
        label = "K/D"
        val_fn = lambda p: f"**{round(p.get('kills', 0) / max(p.get('deaths', 1), 1), 2)}**  ({p.get('kills', 0)}K / {p.get('deaths', 0)}D)"
    else:
        ranked = sorted(visible, key=lambda p: (-p.get("wins", 0), p.get("losses", 0)))
        label = "Wins"
        val_fn = lambda p: f"**{p.get('wins', 0)}** wins"
    medals = ["🥇", "🥈", "🥉"]
    embed = discord.Embed(title=f"🏆 Top {label} — HCL", color=0xFFAA00)
    for i, p in enumerate(ranked[:15]):
        medal = medals[i] if i < 3 else f"`#{i+1}`"
        aff_str = f" {get_affiliation_display(p)}" if get_affiliation(p) else ""
        embed.add_field(
            name=f"{medal} {get_name(p)}{aff_str}",
            value=f"{val_fn(p)}  |  Tier: {p.get('tier','?')}  |  Record: {get_record(p)}",
            inline=False
        )
    await interaction.edit_original_response(embed=embed, view=TopSelectView())


# ========================= BOT READY =========================
@bot.event
async def on_ready():
    print(f"✅ HCL Bot ONLINE as {bot.user}!")
    print("Use !panel to post the interactive control panel.")
    print("Use legacy commands: !tierlist !fighters !player !history !matches !events !stats !top !help")
    # Inicia sync loop com Supabase (background — não bloqueia)
    bot.loop.create_task(supabase_sync_loop())
    # Sync inicial leve — se falhar, o background tenta de novo
    if SUPABASE_URL and SUPABASE_KEY:
        bot.loop.create_task(initial_supabase_sync())
    else:
        print("⚠️ Supabase not configured — skipping initial sync")

# ========================= WELCOME — NEW CHALLENGER =========================
WELCOME_CHANNEL_ID    = 1274034491073237086   # 💬-chat
REGISTRATION_CH_ID    = 1274035646238953548   # #registration
RULEBOOK_CH_ID        = 1274035466907291741   # #rule-book
TIERLIST_CH_ID        = 1274523008060493976   # #tierlist-recordbook

@bot.event
async def on_member_join(member: discord.Member):
    channel = bot.get_channel(WELCOME_CHANNEL_ID)
    if not channel:
        print(f"⚠️ Welcome channel {WELCOME_CHANNEL_ID} not found — check bot permissions.")
        return

    # GIF first — "Here Comes A New Challenger!" style
    try:
        with open(WELCOME_GIF, "rb") as f:
            gif_file = discord.File(f, filename="welcome.gif")
        await channel.send(file=gif_file)
    except FileNotFoundError:
        print(f"⚠️ {WELCOME_GIF} not found — skipping GIF.")

    # Welcome embed
    embed = discord.Embed(
        title="HERE COMES A NEW CHALLENGER!",
        description=(
            f"**Welcome to the Hardcore Combat League, {member.mention}!**\n\n"
            f"HCL is the premier league for Hardcore *\"Drunken Wrestlers\"* action.\n\n"
            f"📋 Register to compete: <#{REGISTRATION_CH_ID}>\n"
            f"📜 Read the rules: <#{RULEBOOK_CH_ID}>\n"
            f"🏆 Tier List & Records: <#{TIERLIST_CH_ID}>"
        ),
        color=0xFF0000
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"HCL Bot • {HCL_SITE}")
    await channel.send(embed=embed)

# ========================= GOAT =========================
TIER_WEIGHTS = {
    "Champion": 1.0, "S": 0.85, "A": 0.70, "B": 0.55,
    "C": 0.40, "D": 0.25, "F": 0.10,
}
MIN_GOAT_FIGHTS = 8
GOAT_WR_PRIOR_WINS = 3
GOAT_WR_PRIOR_MP = 6

def compute_hcl_goat(players: list[dict]) -> list[dict]:
    eligible = []
    for p in players:
        w = p.get("wins", 0)
        l = p.get("losses", 0)
        total = w + l
        if total < MIN_GOAT_FIGHTS or w <= l:
            continue
        tier = (p.get("tier") or "F").capitalize()
        tw = TIER_WEIGHTS.get(tier, 0.10)
        k = p.get("kills", 0)
        d = p.get("deaths", 0)
        kd = k / max(d, 1)
        eligible.append({
            "name": get_name(p),
            "tier": tier,
            "wins": w, "losses": l, "mp": total,
            "kills": k, "deaths": d, "kd": kd,
            "affiliation": get_affiliation(p) or "",
            "tier_weight": tw,
        })
    if not eligible:
        return []
    max_wins = max(e["wins"] for e in eligible) or 1
    max_mp = max(e["mp"] for e in eligible) or 1
    max_kd = max(e["kd"] for e in eligible) or 1
    scored = []
    for e in eligible:
        # Bayesian shrinkage: puxa WR baixo volume pra média (50%)
        adj_wr = (e["wins"] + GOAT_WR_PRIOR_WINS) / (e["mp"] + GOAT_WR_PRIOR_MP)
        score = (
            e["tier_weight"] * 0.10
            + adj_wr * 0.35
            + (e["kd"] / max_kd) * 0.10
            + (e["wins"] / max_wins) * 0.25
            + (e["mp"] / max_mp) * 0.20
        )
        scored.append((round(score, 4), e))
    scored.sort(key=lambda x: -x[0])
    champs = [s for s in scored if s[1]["tier"] == "Champion"]
    others = [s for s in scored if s[1]["tier"] != "Champion"]
    return champs + others

def build_goat_embed(goat_data: list) -> discord.Embed:
    e = discord.Embed(
        title="🐐 HCL GOAT Rankings",
        description="Greatest of All Time — scored by Adj.WR (Bayesian), wins, match volume, tier and K/D, plus draw detection in match history. Minimum 8 fights, positive record required.",
        color=0xFFD700
    )
    for rank, (score, p) in enumerate(goat_data[:15], 1):
        medal = ["🥇", "🥈", "🥉"][rank - 1] if rank <= 3 else f"`#{rank}`"
        wr_pct = round(p["wins"] / p["mp"] * 100, 1)
        aff_emoji = AFFILIATION_EMOJIS.get(p["affiliation"].upper(), "🏷️") if p["affiliation"] else ""
        aff = f" {aff_emoji}" if p["affiliation"] else ""
        e.add_field(
            name=f"{medal} {p['name']}{aff}",
            value=(
                f"🏆 **{p['tier']}**  |  `{p['wins']}-{p['losses']}`  |  "
                f"{wr_pct}% WR  |  K/D {round(p['kd'], 2)}  |  "
                f"{p['mp']} MP  |  Score `{score}`"
            ),
            inline=False
        )
    e.set_footer(text="GOAT: Tier(10%) + Adj.WR(35%) + K/D(10%) + Wins(25%) + MP(20%) | Min 8 fights | Need winning record (W > L) | Bayesian WR | Champs first")
    return e

class GoatView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="🏠 Main Menu", style=discord.ButtonStyle.primary, custom_id="goat_home")
    async def home(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(embed=build_main_panel_embed(), view=HCLMainPanel())

    @ui.button(label="🔙 Rankings", style=discord.ButtonStyle.secondary, custom_id="goat_back")
    async def back(self, interaction: discord.Interaction, button: ui.Button):
        embed = discord.Embed(title="🏅 Rankings", description="Choose a ranking category:", color=0xFFAA00)
        await interaction.response.edit_message(embed=embed, view=TopSelectView())


# ========================= !panel — Posts the interactive button panel =========================
@bot.command(name="panel")
async def cmd_panel(ctx):
    await ctx.send(embed=build_main_panel_embed(), view=HCLMainPanel())

# ========================= LEGACY TEXT COMMANDS (still work) =========================
@bot.command(name="tierlist")
async def cmd_tierlist(ctx, filter: str = None):
    await ctx.send("Choose a tier:", view=TierSelectView())

@bot.command(name="fighters")
async def cmd_fighters(ctx, *args):
    players = await get_players()
    opts = build_affiliation_options(players)
    await ctx.send("Filter fighters by:", view=FightersFilterView(opts))

@bot.command(name="player")
async def cmd_player(ctx, *, name: str = None):
    if name:
        players = await get_players()
        found = find_player(players, name)
        if not found:
            return await ctx.send(f"❌ No fighter found matching **{name}**.")
        embed = build_player_embed(found)
        file = get_avatar_file(found)
        if file:
            embed.set_thumbnail(url="attachment://avatar.png")
            await ctx.send(embed=embed, file=file, view=PlayerActionsView(found))
        else:
            await ctx.send(embed=embed, view=PlayerActionsView(found))
    else:
        await ctx.send("🔍 Type a fighter name:", view=ui.View())
        # Open modal via button fallback
        class ModalTrigger(ui.View):
            @ui.button(label="🔍 Search Fighter", style=discord.ButtonStyle.primary)
            async def open(self, interaction: discord.Interaction, button: ui.Button):
                await interaction.response.send_modal(PlayerLookupModal())
        await ctx.send("Click to search:", view=ModalTrigger())

@bot.command(name="history")
async def cmd_history(ctx, *, name: str = None):
    if name:
        players = await get_players()
        found = find_player(players, name)
        if not found:
            return await ctx.send("❌ Fighter not found.")
        raw_history = found.get("matchHistory")
        history = raw_history if isinstance(raw_history, list) else []
        if not history:
            return await ctx.send(f"✅ **{get_name(found)}** has no recorded matches yet.")
        tier = found.get("tier") or "F"
        embed = discord.Embed(
            title=f"📜 Match History — {get_name(found)}",
            description=f"**{len(history)} match(es)** | Record: {get_record(found)}",
            color=get_tier_color(tier)
        )
        for m in history:
            if not isinstance(m, dict):
                continue
            res = get_match_result(m)
            ps = m.get("playerScore", "?")
            os_ = m.get("opponentScore", "?")
            score = f"{ps}-{os_}"
            vod = m.get("recordingUrl")
            vod_str = f"[▶ Watch match]({vod})" if vod else "No link"
            t_before = m.get("tierBefore", "?")
            t_after = m.get("tierAfter", "?")
            tier_str = f"{t_before} → {t_after}" if t_before != t_after else t_before
            embed.add_field(
                name=f"{res}  vs **{m.get('opponent','?')}**  `{score}`",
                value=f"📅 {m.get('event','?')}  |  Tier: {tier_str}  |  {vod_str}",
                inline=False
            )
        await ctx.send(embed=embed)
    else:
        class ModalTrigger(ui.View):
            @ui.button(label="📜 Search History", style=discord.ButtonStyle.primary)
            async def open(self, interaction: discord.Interaction, button: ui.Button):
                await interaction.response.send_modal(HistoryLookupModal())
        await ctx.send("Click to search match history:", view=ModalTrigger())

@bot.command(name="matches")
async def cmd_matches(ctx, limit: int = 8):
    matches = await get_matches()
    players = await get_players()
    completed = sorted(
        [m for m in matches if m.get("status") == "completed"],
        key=lambda m: get_match_date(m), reverse=True
    )
    view = MatchesNavView(completed, players, 0)
    await ctx.send(embed=view.build_embed(), view=view)

@bot.command(name="events")
async def cmd_events(ctx):
    events = await get_events()
    embed = discord.Embed(title="📅 HCL Events", color=0x00FF88)
    for e in events:
        status = "✅ Completed" if e.get("completed") else "⏳ Upcoming"
        date = (e.get("date") or e.get("scheduledTime") or "")[:10]
        embed.add_field(name=e.get("name", "Event"), value=f"Date: {date}  |  {status}", inline=False)
    await ctx.send(embed=embed)

@bot.command(name="top")
async def cmd_top(ctx, category: str = "wins"):
    await send_top_ctx(ctx, category)

@bot.command(name="goat")
async def cmd_goat(ctx):
    players = await get_players()
    goat_data = compute_hcl_goat(players)
    if not goat_data:
        return await ctx.send("❌ Not enough data for GOAT rankings (min 5 fights).")
    await ctx.send(embed=build_goat_embed(goat_data), view=GoatView())

async def send_top_ctx(ctx, category: str):
    players = await get_players()
    visible = [p for p in players if not p.get("hiddenFromLeaderboard")]
    if category == "kills":
        ranked = sorted(visible, key=lambda p: p.get("kills", 0), reverse=True)
        label, val_fn = "Kills", lambda p: f"**{p.get('kills', 0)}** kills"
    elif category == "kd":
        ranked = sorted(visible, key=lambda p: p.get("kills", 0) / max(p.get("deaths", 1), 1), reverse=True)
        label, val_fn = "K/D", lambda p: f"**{round(p.get('kills', 0) / max(p.get('deaths', 1), 1), 2)}**  ({p.get('kills', 0)}K / {p.get('deaths', 0)}D)"
    else:
        ranked = sorted(visible, key=lambda p: (-p.get("wins", 0), p.get("losses", 0)))
        label, val_fn = "Wins", lambda p: f"**{p.get('wins', 0)}** wins"
    medals = ["🥇", "🥈", "🥉"]
    embed = discord.Embed(title=f"🏆 Top {label} — HCL", color=0xFFAA00)
    for i, p in enumerate(ranked[:15]):
        medal = medals[i] if i < 3 else f"`#{i+1}`"
        aff_str = f" {get_affiliation_display(p)}" if get_affiliation(p) else ""
        embed.add_field(
            name=f"{medal} {get_name(p)}{aff_str}",
            value=f"{val_fn(p)}  |  Tier: {p.get('tier','?')}  |  Record: {get_record(p)}",
            inline=False
        )
    await ctx.send(embed=embed, view=TopSelectView())

@bot.command(name="seasons")
async def cmd_seasons(ctx):
    embed = discord.Embed(
        title="📜 Past Seasons",
        description="Use `!season1`, `!season2` or `!alltime` for details.\nOr use the button in `!panel`.",
        color=0xAA88FF
    )
    embed.add_field(name="🏆 !season1", value="Season 1 Regional (SA, NA, EU)", inline=False)
    embed.add_field(name="🌍 !season2", value="Season 2 Global (GLBL, SA, NA, EU)", inline=False)
    embed.add_field(name="📊 !alltime", value="Aggregated totals across all seasons", inline=False)
    await ctx.send(embed=embed)

@bot.command(name="season1")
async def cmd_season1(ctx):
    await ctx.send("🔄 Loading Season 1...")
    data = await legacy.fetch_all_season1()
    if not data:
        return await ctx.send("❌ Could not load Season 1.")
    regions = {}
    for f in data:
        r = f.get("region", "?")
        regions.setdefault(r, []).append(f)
    embed = discord.Embed(title="🏆 Season 1 Regional", color=0xAA88FF)
    for r in legacy.S1_REGION_ORDER:
        fighters = regions.get(r, [])
        top = fighters[:5]
        lines = "\n".join(f"{f['name']} — W:{f['w']} L:{f['l']}" for f in top)
        embed.add_field(name=f"🌎 {r} — {len(fighters)} fighters", value=lines or "—", inline=False)
    await ctx.send(embed=embed, view=LegacyS1View(regions, 0))

@bot.command(name="season2")
async def cmd_season2(ctx):
    await ctx.send("🔄 Loading Season 2...")
    tabs = legacy.S2_REGION_ORDER
    all_data = []
    for tab in tabs:
        fighters = await legacy.fetch_season2_tab(tab)
        for f in fighters:
            f["region"] = tab
        all_data.append(fighters)
    if not any(all_data):
        return await ctx.send("❌ Could not load Season 2.")
    embed = discord.Embed(title="🌍 Season 2 Global", color=0xAA88FF)
    for i, tab in enumerate(tabs):
        fighters = all_data[i]
        top = fighters[:5] if fighters else []
        lines = "\n".join(f"{f['name']} — W:{f['w']} L:{f['l']}" for f in top) if top else "—"
        embed.add_field(name=f"🌎 {tab} — {len(fighters)} fighters", value=lines, inline=False)
    await ctx.send(embed=embed, view=LegacyS2View(all_data, tabs, 0))

@bot.command(name="alltime")
async def cmd_alltime(ctx):
    await ctx.send("🔄 Loading All-Time...")
    data = await legacy.fetch_alltime()
    if not data:
        return await ctx.send("❌ Could not load All-Time.")
    embed, view = _build_alltime_page(data, 0)
    await ctx.send(embed=embed, view=view)

@bot.command(name="refresh")
async def cmd_refresh(ctx):
    global players_cache, players_cache_ts, matches_cache, events_cache
    players_cache = None
    players_cache_ts = None
    matches_cache = None
    events_cache = None
    await ctx.send("🔄 Cache cleared! Next request will pull fresh data from the API.")

@bot.command(name="supastatus")
async def cmd_supastatus(ctx):
    """Shows Supabase sync status."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return await ctx.send("❌ Supabase not configured (missing env vars).")
    last = None
    if last_sync_time:
        last = last_sync_time.strftime("%Y-%m-%d %H:%M:%S UTC")
    p_count = await sb.supabase_count("players")
    m_count = await sb.supabase_count("matches")
    e_count = await sb.supabase_count("events")
    embed = discord.Embed(title="🗄️ Supabase Sync Status", color=0x00FF88)
    embed.add_field(name="Last Sync", value=last or "Never", inline=True)
    embed.add_field(name="Sync Interval", value="Every 5 min", inline=True)
    embed.add_field(name="Players (API → DB)", value=f"{p_count} rows", inline=True)
    embed.add_field(name="Matches (API → DB)", value=f"{m_count} rows", inline=True)
    embed.add_field(name="Events (API → DB)", value=f"{e_count} rows", inline=True)
    embed.add_field(name="Fallback Mode", value="✅ Active" if SUPABASE_URL else "❌ Disabled", inline=True)
    await ctx.send(embed=embed)

@bot.command(name="help")
async def cmd_help(ctx):
    embed = discord.Embed(
        title="📜 HCL Bot — Commands",
        description="Use **`!panel`** to open the full interactive button panel (recommended).\nLegacy text commands also work:",
        color=0xFF0000
    )
    embed.add_field(name="!panel", value="Opens the interactive control panel with buttons", inline=False)
    embed.add_field(name="!tierlist [tier]", value="Ex: `!tierlist` | `!tierlist S`", inline=False)
    embed.add_field(name="!fighters", value="Opens fighter filter menu", inline=False)
    embed.add_field(name="!player <name>", value="Ex: `!player NLG`", inline=False)
    embed.add_field(name="!history <name>", value="Ex: `!history JAB`", inline=False)
    embed.add_field(name="!matches [n]", value="Ex: `!matches` | `!matches 15`", inline=False)
    embed.add_field(name="!events", value="List of HCL events", inline=False)
    embed.add_field(name="!top [wins|kills|kd]", value="Ex: `!top` | `!top kills`", inline=False)
    embed.add_field(name="!refresh", value="Force a data refresh from the API", inline=False)
    embed.add_field(name="!goat", value="🐐 HCL GOAT rankings", inline=False)
    embed.add_field(name="!supastatus", value="Shows Supabase sync status", inline=False)
    embed.add_field(name="!seasons", value="List available past seasons", inline=False)
    embed.add_field(name="!season1", value="Season 1 Regional rankings", inline=False)
    embed.add_field(name="!season2", value="Season 2 Global rankings", inline=False)
    embed.add_field(name="!alltime", value="Aggregated totals across all seasons", inline=False)
    await ctx.send(embed=embed)

# ========================= TOKEN =========================
bot.run(os.environ["DISCORD_TOKEN"])
