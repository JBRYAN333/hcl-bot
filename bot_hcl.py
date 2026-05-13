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
from PIL import Image

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
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{API_BASE}/players") as resp:
            if resp.status == 200:
                data = await resp.json()
                players_cache = data if isinstance(data, list) else data.get("players", data)
                players_cache_ts = now
                return players_cache
    return []

async def get_matches():
    global matches_cache
    if matches_cache:
        return matches_cache
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{API_BASE}/matches") as resp:
            if resp.status == 200:
                matches_cache = await resp.json()
                return matches_cache
    return []

async def get_events():
    global events_cache
    if events_cache:
        return events_cache
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{API_BASE}/events") as resp:
            if resp.status == 200:
                events_cache = await resp.json()
                return events_cache
    return []

# ========================= HELPERS =========================
def get_name(p):
    return p.get("username") or p.get("name") or "Unknown"

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

def get_affiliation(p):
    aff = p.get("affiliation")
    return aff if aff else None

def get_tier_color(tier):
    return {
        "Champion": 0xFFD700,
        "S": 0x00FFFF,
        "A": 0xFFFF00,
        "B": 0xAAAAAA,
        "C": 0xFF8800,
        "D": 0x00FF00,
        "F": 0xFF0000,
    }.get(tier.upper() if tier else "F", 0xFFFFFF)

def get_tier_emoji(tier):
    return {
        "Champion": "👑",
        "S": "🔵",
        "A": "🟡",
        "B": "⚪",
        "C": "🟠",
        "D": "🟢",
        "F": "🔴",
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
        aff = get_affiliation(p)
        aff_str = f" *{aff}*" if aff else ""
        lines.append(f"**{get_name(p)}**{aff_str}  `{get_record(p)}`")
    embed = discord.Embed(
        title=f"{emoji} {tier} TIER  ({len(players_in_tier)} fighters)",
        description="\n".join(lines),
        color=color
    )
    return embed

def build_player_embed(found):
    pname = get_name(found)
    aff = get_affiliation(found)
    tier = found.get("tier") or "F"
    color = get_tier_color(tier)
    emoji = get_tier_emoji(tier)
    title = pname + (f"  *{aff}*" if aff else "")
    embed = discord.Embed(title=title, color=color)
    embed.add_field(name="Tier", value=f"{emoji} **{tier}**", inline=True)
    embed.add_field(name="Record", value=get_record(found), inline=True)
    embed.add_field(name="Streak", value=found.get("consecutiveWinsInCurrentTier", 0), inline=True)
    embed.add_field(name="Kills", value=found.get("kills", 0), inline=True)
    embed.add_field(name="Deaths", value=found.get("deaths", 0), inline=True)
    embed.add_field(name="K/D", value=round(found.get("kills", 0) / max(found.get("deaths", 1), 1), 2), inline=True)
    embed.add_field(name="Region", value=found.get("region") or "?", inline=True)
    embed.add_field(name="Platform", value=found.get("platform") or "PC", inline=True)
    embed.add_field(name="Affiliation", value=aff or "None", inline=True)
    if found.get("previousTier"):
        embed.add_field(name="Prev. Tier", value=found["previousTier"], inline=True)
    embed.add_field(name="Available", value="✅" if player_is_available(found) else "❌", inline=True)
    history = found.get("matchHistory") or []
    if history:
        lines = []
        for m in history[:5]:
            res = "✅" if m.get("result") == "win" else "❌"
            lines.append(f"{res} vs **{m.get('opponent','?')}**  {m.get('playerScore','?')}-{m.get('opponentScore','?')}  _{m.get('event','')}_")
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
    embed.add_field(name="🏅 Rankings", value="Top fighters by wins/kills/K/D", inline=True)
    embed.add_field(name="🔄 Refresh", value="Refresh data from the API", inline=True)
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
        embed = discord.Embed(
            title="🥊 Fighters",
            description="Select a filter below to browse the roster:",
            color=0x0055FF
        )
        await interaction.response.edit_message(embed=embed, view=FightersFilterView())

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
            key=lambda m: m.get("playedAt") or "", reverse=True
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
        embed.add_field(name="Fighters by Affiliation", value="  ".join(f"**{a}**: {c}" for a, c in sorted(aff_counts.items())), inline=False)
        embed.add_field(name="🔪 Top 3 Kills", value="\n".join(f"{i+1}. **{get_name(p)}** — {p.get('kills',0)}" for i, p in enumerate(top_kills)), inline=True)
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


# ---------- Back to Main ----------
class BackToMainView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="🔙 Back to Panel", style=discord.ButtonStyle.secondary, custom_id="hcl_back_main")
    async def back(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(embed=build_main_panel_embed(), view=HCLMainPanel())


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
        # Build one combined embed with all tiers
        embed = discord.Embed(title="📋 HCL Full Tier List", color=0xFFD700)
        for tier in TIER_ORDER:
            if tier not in tiers:
                continue
            emoji = get_tier_emoji(tier)
            lines = []
            for p in tiers[tier]:
                aff = get_affiliation(p)
                aff_str = f" *{aff}*" if aff else ""
                lines.append(f"**{get_name(p)}**{aff_str}  `{get_record(p)}`")
            embed.add_field(
                name=f"{emoji} {tier} ({len(tiers[tier])})",
                value="\n".join(lines) or "—",
                inline=False
            )
        await interaction.edit_original_response(embed=embed, view=BackToTierView())

    @ui.button(label="🔙 Back", style=discord.ButtonStyle.secondary, custom_id="tier_back", row=2)
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
            aff = get_affiliation(champ)
            embed = discord.Embed(
                title="🥇 HCL WORLD CHAMPION",
                description=f"👑 **{get_name(champ)}**" + (f"  *{aff}*" if aff else ""),
                color=0xFFD700
            )
            embed.add_field(name="Record", value=get_record(champ), inline=True)
            embed.add_field(name="Kills", value=champ.get("kills", 0), inline=True)
            embed.add_field(name="Region", value=champ.get("region", "?"), inline=True)
            embed.set_footer(text=f"Affiliation: {aff or 'None'}")
            return await interaction.edit_original_response(embed=embed, view=BackToTierView())
        filtered = [p for p in visible if (p.get("tier") or "").upper() == tier.upper()]
        if not filtered:
            embed = discord.Embed(title=f"{get_tier_emoji(tier)} {tier} Tier", description="❌ No players in this tier.", color=get_tier_color(tier))
            return await interaction.edit_original_response(embed=embed, view=BackToTierView())
        await interaction.edit_original_response(embed=build_tier_embed(tier, filtered), view=BackToTierView())


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
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(FightersRegionSelect())
        self.add_item(FightersTierSelect())
        self.add_item(FightersAffiliationSelect())
        self.add_item(FightersBackButton())

class FightersBackButton(ui.Button):
    def __init__(self):
        super().__init__(label="🔙 Back", style=discord.ButtonStyle.secondary, custom_id="fighters_back", row=3)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=build_main_panel_embed(), view=HCLMainPanel())

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
        await send_fighters(interaction, region=self.values[0])

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
        await send_fighters(interaction, tier=self.values[0])

class FightersAffiliationSelect(ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="All Affiliations", value="ALL", emoji="🤝"),
            discord.SelectOption(label="No Affiliation", value="NONE", emoji="🚫"),
            discord.SelectOption(label="BBK", value="BBK", emoji="🔥"),
            discord.SelectOption(label="CHAOS", value="CHAOS", emoji="💥"),
            discord.SelectOption(label="DWO", value="DWO", emoji="⚔️"),
            discord.SelectOption(label="SAL", value="SAL", emoji="🛡️"),
        ]
        super().__init__(placeholder="🤝 Filter by Affiliation", options=options, custom_id="fighters_affiliation", row=2)

    async def callback(self, interaction: discord.Interaction):
        await send_fighters(interaction, affiliation=self.values[0])

async def send_fighters(interaction: discord.Interaction, region="ALL", tier="ALL", affiliation="ALL"):
    await interaction.response.defer()
    players = await get_players()
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
    labels = " | ".join(filter(None, [
        f"Region: {region}" if region != "ALL" else "",
        f"Tier: {tier}" if tier != "ALL" else "",
        f"Affiliation: {affiliation}" if affiliation != "ALL" else "",
    ])) or "All fighters"
    embed = discord.Embed(title=f"🥊 FIGHTERS  ({len(result)} found)", description=f"Filter: `{labels}`", color=0xFF0000)
    for p in result[:25]:
        aff = get_affiliation(p)
        t = p.get("tier") or "?"
        emoji = get_tier_emoji(t)
        value = (
            f"{emoji} **{t}**  |  {get_record(p)}  |  K: {p.get('kills',0)} / D: {p.get('deaths',0)}\n"
            f"Region: {p.get('region','?')}  |  Platform: {p.get('platform') or 'PC'}"
            + (f"\nAffiliation: *{aff}*" if aff else "")
        )
        embed.add_field(name=get_name(p), value=value, inline=False)
    if len(result) > 25:
        embed.set_footer(text=f"Showing 25 of {len(result)}. Use more specific filters.")
    await interaction.edit_original_response(embed=embed, view=BackToFightersView())

class BackToFightersView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="🔙 Back to Fighters", style=discord.ButtonStyle.secondary, custom_id="back_to_fighters")
    async def back(self, interaction: discord.Interaction, button: ui.Button):
        embed = discord.Embed(title="🥊 Fighters", description="Select a filter below to browse the roster:", color=0x0055FF)
        await interaction.response.edit_message(embed=embed, view=FightersFilterView())


# ---------- Player Lookup Modal ----------
class PlayerLookupModal(ui.Modal, title="🔍 Fighter Lookup"):
    name = ui.TextInput(label="Fighter name", placeholder="e.g. NLG, JAB, KYMORA...", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        players = await get_players()
        found = next((p for p in players if self.name.value.lower() in get_name(p).lower()), None)
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
        history = found.get("matchHistory") or []
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
            res = "✅ Win" if m.get("result") == "win" else "❌ Loss"
            score = f"{m.get('playerScore','?')}-{m.get('opponentScore','?')}"
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
        found = next((p for p in players if self.name.value.lower() in get_name(p).lower()), None)
        if not found:
            return await interaction.followup.send(f"❌ Fighter **{self.name.value}** not found.")
        history = found.get("matchHistory") or []
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
            res = "✅ Win" if m.get("result") == "win" else "❌ Loss"
            score = f"{m.get('playerScore','?')}-{m.get('opponentScore','?')}"
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
    def __init__(self, matches: list, players: list, page: int):
        super().__init__(timeout=None)
        self.matches = matches
        self.players = players
        self.page = page
        self.per_page = 8
        self.max_page = max(0, (len(matches) - 1) // self.per_page)
        self._update_buttons()

    def _update_buttons(self):
        self.btn_prev.disabled = self.page == 0
        self.btn_next.disabled = self.page >= self.max_page

    def build_embed(self):
        start = self.page * self.per_page
        embed = discord.Embed(
            title=f"🥊 HCL Matches  (Page {self.page + 1}/{self.max_page + 1})",
            color=0xFF0000
        )
        for m in self.matches[start:start + self.per_page]:
            p1 = player_id_to_name((m.get("side1PlayerIds") or ["?"])[0], self.players)
            p2 = player_id_to_name((m.get("side2PlayerIds") or ["?"])[0], self.players)
            score = f"{m.get('side1Score','?')}-{m.get('side2Score','?')}"
            winner = p1 if m.get("winningSide") == 1 else p2
            vod = m.get("recordingUrl")
            vod_str = f"[▶ Watch match]({vod})" if vod else "No link"
            date = (m.get("playedAt") or "")[:10]
            embed.add_field(
                name=f"{p1}  vs  {p2}  |  {score}",
                value=f"🏆 **{winner}** won  |  📅 {date}  |  {m.get('event','')}  |  {vod_str}",
                inline=False
            )
        return embed

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

    @ui.button(label="🔪 Top Kills", style=discord.ButtonStyle.danger, custom_id="top_kills")
    async def top_kills(self, interaction: discord.Interaction, button: ui.Button):
        await send_top(interaction, "kills")

    @ui.button(label="⚡ Top K/D", style=discord.ButtonStyle.primary, custom_id="top_kd")
    async def top_kd(self, interaction: discord.Interaction, button: ui.Button):
        await send_top(interaction, "kd")

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
        val_fn = lambda p: p.get("kills", 0)
    elif category == "kd":
        ranked = sorted(visible, key=lambda p: p.get("kills", 0) / max(p.get("deaths", 1), 1), reverse=True)
        label = "K/D"
        val_fn = lambda p: round(p.get("kills", 0) / max(p.get("deaths", 1), 1), 2)
    else:
        ranked = sorted(visible, key=lambda p: p.get("wins", 0), reverse=True)
        label = "Wins"
        val_fn = lambda p: p.get("wins", 0)
    medals = ["🥇", "🥈", "🥉"]
    embed = discord.Embed(title=f"🏆 Top {label} — HCL", color=0xFFAA00)
    for i, p in enumerate(ranked[:15]):
        medal = medals[i] if i < 3 else f"`#{i+1}`"
        aff = get_affiliation(p)
        aff_str = f" *{aff}*" if aff else ""
        embed.add_field(
            name=f"{medal} {get_name(p)}{aff_str}",
            value=f"{label}: **{val_fn(p)}**  |  Tier: {p.get('tier','?')}  |  Record: {get_record(p)}",
            inline=False
        )
    await interaction.edit_original_response(embed=embed, view=TopSelectView())


# ========================= BOT READY =========================
@bot.event
async def on_ready():
    print(f"✅ HCL Bot ONLINE as {bot.user}!")
    print("Use !panel to post the interactive control panel.")
    print("Use legacy commands: !tierlist !fighters !player !history !matches !events !stats !top !help")

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
            f"🌐 **Record Book & Standings:** [hclmanager.replit.app]({HCL_SITE})\n"
            f"📋 Register to compete: <#{REGISTRATION_CH_ID}>\n"
            f"📜 Read the rules: <#{RULEBOOK_CH_ID}>\n"
            f"🏆 Tier List & Records: <#{TIERLIST_CH_ID}>"
        ),
        color=0xFF0000
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"HCL Bot • {HCL_SITE}")
    await channel.send(embed=embed)

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
    await ctx.send("Filter fighters by:", view=FightersFilterView())

@bot.command(name="player")
async def cmd_player(ctx, *, name: str = None):
    if name:
        players = await get_players()
        found = next((p for p in players if name.lower() in get_name(p).lower()), None)
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
        found = next((p for p in players if name.lower() in get_name(p).lower()), None)
        if not found:
            return await ctx.send("❌ Fighter not found.")
        history = found.get("matchHistory") or []
        if not history:
            return await ctx.send(f"✅ **{get_name(found)}** has no recorded matches yet.")
        tier = found.get("tier") or "F"
        embed = discord.Embed(
            title=f"📜 Match History — {get_name(found)}",
            description=f"**{len(history)} match(es)** | Record: {get_record(found)}",
            color=get_tier_color(tier)
        )
        for m in history:
            res = "✅ Win" if m.get("result") == "win" else "❌ Loss"
            score = f"{m.get('playerScore','?')}-{m.get('opponentScore','?')}"
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
        key=lambda m: m.get("playedAt") or "", reverse=True
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

async def send_top_ctx(ctx, category: str):
    players = await get_players()
    visible = [p for p in players if not p.get("hiddenFromLeaderboard")]
    if category == "kills":
        ranked = sorted(visible, key=lambda p: p.get("kills", 0), reverse=True)
        label, val_fn = "Kills", lambda p: p.get("kills", 0)
    elif category == "kd":
        ranked = sorted(visible, key=lambda p: p.get("kills", 0) / max(p.get("deaths", 1), 1), reverse=True)
        label, val_fn = "K/D", lambda p: round(p.get("kills", 0) / max(p.get("deaths", 1), 1), 2)
    else:
        ranked = sorted(visible, key=lambda p: p.get("wins", 0), reverse=True)
        label, val_fn = "Wins", lambda p: p.get("wins", 0)
    medals = ["🥇", "🥈", "🥉"]
    embed = discord.Embed(title=f"🏆 Top {label} — HCL", color=0xFFAA00)
    for i, p in enumerate(ranked[:15]):
        medal = medals[i] if i < 3 else f"`#{i+1}`"
        aff = get_affiliation(p)
        embed.add_field(
            name=f"{medal} {get_name(p)}" + (f" *{aff}*" if aff else ""),
            value=f"{label}: **{val_fn(p)}**  |  Tier: {p.get('tier','?')}  |  Record: {get_record(p)}",
            inline=False
        )
    await ctx.send(embed=embed, view=TopSelectView())

@bot.command(name="refresh")
async def cmd_refresh(ctx):
    global players_cache, players_cache_ts, matches_cache, events_cache
    players_cache = None
    players_cache_ts = None
    matches_cache = None
    events_cache = None
    await ctx.send("🔄 Cache cleared! Next request will pull fresh data from the API.")

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
    await ctx.send(embed=embed)

# ========================= TOKEN =========================
bot.run(os.environ["DISCORD_TOKEN"])
