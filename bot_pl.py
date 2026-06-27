import discord
from discord.ext import commands
from discord import ui
import asyncio
import sys
import re
import os
import io
import json
from collections import Counter

# ── Google OAuth ──────────────────────────────────────────────────────────────
import urllib.request
import urllib.parse

GOOGLE_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID",     "511113456386-lm9tc5gspiuhevfl62u5t5b0clqtklbc.apps.googleusercontent.com")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "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")
GOOGLE_REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN", "1//0hVI-rgLdkxFhCgYIARAAGBESNwF-L9IrzRLHFPewWNYSZmD3MalLC-RZ0L59Y9lefeTRc_3QhPMTjlgDvYzwnIeavoYE1hpfeR8")
DOC_ID               = "1fYokf-Tbj1NgZa1fukSFH7snGgP1xqYOyUVPd2EkRHQ"
JSON_PATH            = "pl_records.json"

def _get_access_token() -> str:
    data = urllib.parse.urlencode({
        "client_id":     GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": GOOGLE_REFRESH_TOKEN,
        "grant_type":    "refresh_token",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())["access_token"]

def _download_pdf_bytes() -> bytes:
    token = _get_access_token()
    url   = f"https://docs.google.com/document/d/{DOC_ID}/export?format=pdf"
    req   = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()

# ── Parser ────────────────────────────────────────────────────────────────────
import pdfplumber

# Populado dinamicamente pelo parser. Fallback garante que nunca fica vazio.
REGIONS: list[str] = ["EU", "NA", "SA", "AS", "Global"]

_REGION_FLAG  = {"EU":"🇪🇺","NA":"🇺🇸","SA":"🇧🇷","AS":"🇰🇷","Global":"🌍"}
_REGION_COLOR = {"EU":0x003BB5,"NA":0xBF0000,"SA":0x009C3B,"AS":0xFF6600,"Global":0x00BFFF}

_RE_SR  = re.compile(r'^([A-Za-z]{2,8})\s+Rankings?$', re.I)
_RE_SC  = re.compile(r'^([A-Za-z]{2,8})\s+Records?$',  re.I)
_RE_RR  = re.compile(r'^(Win|Loss|Draw|NC|WIn)\b', re.I)
_RE_RF  = re.compile(r'^\d+-\d+$')
_RE_TH  = re.compile(r'^Tier \d', re.I)
_RE_NR  = re.compile(r'^(.+?)\s*\((\d+)-(\d+)\)$')
_RE_CT  = re.compile(r'[\u200b\u00a0\u200c\u200d\u2060\ufeff\u202f\xa0]')
_RE_EV  = re.compile(
    r'(DW2PL\s+(?:Fight\s+Night\s+|[A-Za-z]{2,8}\s+Tournament\s+'
    r'|Global\s+Part\s+\d+\s*)?#?\d+)', re.I)
_RE_HDR = re.compile(r'\bRes\.?\s+Record\b|\bOpponent\b.*\bScore\b', re.I)
_SKIP   = re.compile(
    r'Non-Tournament|Qualifiers?|Prelims?|VOD Link|'
    r'Round \d+|Losers|Winners|Bracket|'
    r'Finals?|Exhibitions?|Inactive|'
    r'^\s*(Top \d+|DW2PL|Rules?|Lag Rule|Inter-Regional|Same.region)\b', re.I
)

def _canonical(raw: str) -> str:
    """eu→EU, na→NA, as→AS, global→Global"""
    return raw.upper() if len(raw) <= 3 else raw.capitalize()

def _parse_pdf_bytes(pdf_bytes: bytes) -> tuple[dict, dict]:
    global REGIONS
    re_ev = re.compile(
        r'DW2PL\s+(?:Fight\s+Night\s+|[A-Za-z]{2,8}\s+Tournament\s+'
        r'|Global\s+Part\s+\d+\s*)?#?\d+', re.I)
    text_pages, vod_map = [], {}
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text_pages.append(page.extract_text() or "")
            yt = {}
            for h in (page.hyperlinks or []):
                u = h.get("uri", "")
                if u and ("youtube" in u or "youtu.be" in u or "twitch" in u):
                    yt[round(h["top"])] = u
            if not yt:
                continue
            ly = {}
            for w in page.extract_words():
                ly.setdefault(round(w["top"]), []).append(w["text"])
            for link_y, url in yt.items():
                above = sorted(
                    [(y, " ".join(ws)) for y, ws in ly.items()
                     if y < link_y and link_y - y < 200],
                    key=lambda x: -x[0]
                )
                for _, ln in above:
                    m = re_ev.search(ln)
                    if m:
                        vod_map[re.sub(r"\s+", " ", m.group(0).strip())] = url
                        break
    text = "\n".join(text_pages)
    lines = [re.sub(r'\s+', ' ', _RE_CT.sub(' ', l).strip())
             for l in text.splitlines() if l.strip()]

    # Descoberta dinâmica — varre linhas em ordem, Global sempre vai pro final
    seen: dict[str, str] = {}
    for line in lines:
        m = _RE_SR.match(line) or _RE_SC.match(line)
        if m:
            key = m.group(1).lower()
            if key not in seen:
                seen[key] = _canonical(m.group(1))
    ordered = [v for k, v in seen.items() if k != "global"]
    if "global" in seen:
        ordered.append(seen["global"])
    if ordered:
        REGIONS[:] = ordered
    print(f"[PL Bot] Regions detected: {REGIONS}")

    res = {r: {"ranking": [], "unranked": {}, "records": {}} for r in REGIONS}
    cr = cs = cp = None
    th = []
    pending_name = None
    for line in lines:
        m = _RE_SR.match(line)
        if m:
            c = _canonical(m.group(1))
            if c not in res: continue
            cr = c; cs = "ranking"; th = []; cp = None; pending_name = None; continue
        m = _RE_SC.match(line)
        if m:
            c = _canonical(m.group(1))
            if c not in res: continue
            cr = c; cs = "records"; cp = None; pending_name = None; continue
        if not cr:
            continue
        reg = res[cr]
        if cs == "ranking":
            if re.match(r'^Unranked$', line, re.I):
                cs = "unranked"; th = []; continue
            if re.search(r'Top \d+|^Position\b|^Nation\b|^Player\b|\bMP\b.*Wins|Affiliation', line, re.I):
                continue
            toks = line.split()
            if not toks: continue
            f = toks[0]
            if not re.match(r'^(Champion|#\d+)$', f, re.I): continue
            ni = [i for i, t in enumerate(toks) if re.match(r'^\d+$', t)]
            if len(ni) < 3: continue
            im, iw, il = ni[-3], ni[-2], ni[-1]
            mp, w, l = int(toks[im]), int(toks[iw]), int(toks[il])
            aff = toks[il + 1] if il + 1 < len(toks) else ""
            player = " ".join(toks[1:im]).strip()
            pos = "Champion" if f.lower() == "champion" else f.lstrip("#")
            if player and player.upper() not in ("VACANT", "N/A", ""):
                reg["ranking"].append({"pos": pos, "player": player, "mp": mp, "wins": w, "losses": l, "affiliation": aff})
        elif cs == "unranked":
            if _RE_TH.match(line):
                th = re.findall(r'Tier \d+', line, re.I)
                for t in th: reg["unranked"].setdefault(t, [])
                continue
            if not th: continue
            ms = _RE_NR.findall(line)
            if not ms:
                for part in re.findall(r'\S+\s*\(\d+-\d+\)', line):
                    mm = _RE_NR.match(part.strip())
                    if mm: ms.append((mm.group(1).strip(), mm.group(2), mm.group(3)))
            for idx, (nm, ww, ll) in enumerate(ms):
                key = th[idx] if idx < len(th) else th[-1]
                reg["unranked"][key].append(f"{nm.strip()} ({ww}-{ll})")
        elif cs == "records":
            if re.match(r'^Inactive:?\s*$', line, re.I):
                cs = None; continue
            if _RE_HDR.search(line):
                if pending_name is not None:
                    cp = pending_name
                    if cp not in reg["records"]: reg["records"][cp] = []
                pending_name = None; continue
            if _RE_RR.match(line):
                pending_name = None
                if not cp: continue
                toks = line.split()
                if len(toks) < 3: continue
                rv = toks[0]; idx = 1
                rec = toks[idx] if _RE_RF.match(toks[idx]) else ""
                if rec: idx += 1
                opp = toks[idx]     if idx     < len(toks) else ""
                sc  = toks[idx + 1] if idx + 1 < len(toks) else ""
                ep, np_ = [], []
                in_n = False
                region_toks = "|".join(REGIONS)
                for tok in toks[idx + 2:]:
                    if ep and not re.match(rf'^(DW2PL|{region_toks}|Fight|Night|Tournament|Global|Part|#\d+|\d+)$', tok, re.I):
                        in_n = True
                    (np_ if in_n else ep).append(tok)
                ev = " ".join(ep); nt = " ".join(np_)
                vm = _RE_EV.search(ev)
                vod = vod_map.get(re.sub(r"\s+", " ", vm.group(1).strip()), "") if vm else ""
                reg["records"][cp].append({"result": rv, "record": rec, "opponent": opp, "score": sc, "event": ev, "notes": nt, "vod": vod})
                continue
            has_g = bool(re.search(r'\(G\)\s*$', line))
            cand  = re.sub(r'\s*\(G\)\s*$', '', line).strip()
            is_c  = False
            skip_upper = {r.upper() for r in REGIONS} | {"UNRANKED","TOP 10:","TOP 15:","TIER 1","TIER 2","TIER 3"}
            if has_g and 1 <= len(cand) <= 35 and not _SKIP.search(cand):
                is_c = True
            elif (2 <= len(cand) <= 35 and not re.search(r'\d{4,}', cand)
                    and not re.match(r'^[#\d\-]', cand) and len(cand.split()) <= 4
                    and cand.upper() not in skip_upper
                    and not _SKIP.search(cand)):
                is_c = True
            pending_name = cand if is_c else None
    return res, vod_map

def _rebuild_json() -> tuple[dict, dict]:
    print("[PL Bot] Downloading PDF from Google Docs...")
    pdf_bytes = _download_pdf_bytes()
    print(f"[PL Bot] PDF downloaded ({len(pdf_bytes)//1024} KB). Parsing...")
    data, vods = _parse_pdf_bytes(pdf_bytes)
    del pdf_bytes
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump({"data": data, "vods": vods, "regions": REGIONS}, f, ensure_ascii=False, separators=(',', ':'))
    size = os.path.getsize(JSON_PATH)
    print(f"[PL Bot] JSON saved: {size//1024} KB. VODs: {len(vods)}")
    for r in REGIONS:
        reg = data.get(r, {})
        print(f"[PL Bot] {r}: {len(reg.get('ranking',[]))} ranked, {len(reg.get('records',{}))} with history")
    return data, vods

def _load_json() -> tuple[dict, dict]:
    global REGIONS
    with open(JSON_PATH, encoding="utf-8") as f:
        obj = json.load(f)
    data = obj.get("data", {}); vods = obj.get("vods", {})
    saved = obj.get("regions", [])
    if saved:
        REGIONS[:] = saved
    print(f"[PL Bot] Loaded from disk. Regions: {REGIONS}. VODs: {len(vods)}")
    for r in REGIONS:
        reg = data.get(r, {})
        print(f"[PL Bot] {r}: {len(reg.get('ranking',[]))} ranked, {len(reg.get('records',{}))} with history")
    return data, vods

# ── Bot setup ─────────────────────────────────────────────────────────────────
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)
bot.remove_command("help")

PANEL_COLOR        = 0x00BFFF
PANEL_DARK         = 0x0A0E1A
FIGHTS_PER_PAGE    = 10

# ── Cache ─────────────────────────────────────────────────────────────────────
_data:       dict | None = None
_vods:       dict | None = None
_refreshing: bool        = False

def clear_cache():
    global _data, _vods
    _data = _vods = None

async def get_data() -> tuple[dict, dict]:
    global _data, _vods
    if _data:
        return _data, _vods or {}
    loop = asyncio.get_event_loop()
    if os.path.exists(JSON_PATH):
        _data, _vods = await loop.run_in_executor(None, _load_json)
    else:
        _data, _vods = await loop.run_in_executor(None, _rebuild_json)
    return _data, _vods or {}

async def do_refresh() -> str:
    global _data, _vods, _refreshing
    if _refreshing:
        return "⏳ A refresh is already in progress. Please wait."
    _refreshing = True
    try:
        clear_cache()
        loop = asyncio.get_event_loop()
        _data, _vods = await loop.run_in_executor(None, _rebuild_json)
        ranked = sum(len(_data.get(r,{}).get("ranking",[])) for r in REGIONS)
        fights = sum(len(v) for r in REGIONS for v in _data.get(r,{}).get("records",{}).values())
        return f"✅ Data updated! {ranked} ranked | {fights} fights | {len(_vods)} VODs"
    except Exception as e:
        return f"❌ Error: `{e}`"
    finally:
        _refreshing = False

# ── Helpers ───────────────────────────────────────────────────────────────────
def region_color(r): return _REGION_COLOR.get(r, 0x7289DA)
def region_flag(r):  return _REGION_FLAG.get(r, "🏴")
def pos_medal(p):
    s=str(p).lower()
    if s=="champion": return "👑"
    n=s.lstrip("#")
    return {"1":"🥇","2":"🥈","3":"🥉"}.get(n,f"`#{n}`")
def res_emoji(r): return {"win":"✅","loss":"❌","draw":"🤝","nc":"🚫"}.get(r.lower(),"❓")
def wr(w,l): t=w+l; return f"{round(w/t*100,1)}%" if t>0 else "N/A"

def fight_line(m):
    em=res_emoji(m["result"])
    vod=f" [▶]({m['vod']})" if m.get("vod") else ""
    nt=f"\n   ↳ _{m['notes']}_" if m.get("notes") else ""
    return f"{em} vs **{m['opponent']}** `{m['score']}` — {m['event']}{vod}{nt}"

def collect_fights(query, data):
    dn=query; fights=[]; entry=None; ereg=None
    for region,reg in data.items():
        for p in reg.get("ranking",[]):
            if query in p["player"].lower() and not entry:
                entry=p; ereg=region
        for pname,fs in reg.get("records",{}).items():
            if query in pname.lower() and fs:
                dn=pname
                for f in fs: fights.append((region,f))
    seen=set(); unique=[]
    for region,f in fights:
        # Deduplicate: same fight can appear if player listed in multiple regions
        # Key: result + record + opponent + score (all fields together)
        key=(f.get("result",""),f.get("record",""),f.get("opponent",""),f.get("score",""),f.get("event",""))
        if key not in seen:
            seen.add(key); unique.append((region,f))
    return dn, unique, entry, ereg

# ── Embeds ────────────────────────────────────────────────────────────────────
def build_main_embed():
    e=discord.Embed(
        title="PRO LEAGUE — RECORD BOOK",
        description="**Drunken Wrestlers 2 — Pro League** | Interactive Panel\n\nRankings, player cards and match history across all regions.",
        color=PANEL_DARK)
    e.add_field(name="🌍 Rankings",        value="Top 10 by region",                inline=True)
    e.add_field(name="👤 Player Lookup",   value="Full card with match history",     inline=True)
    e.add_field(name="📊 Stats",           value="Region overview & leaderboards",   inline=True)
    e.add_field(name="🏅 Top Rankings",    value="Top Wins, Win Rate, MP",          inline=True)
    e.add_field(name="👥 All Players",     value="Full all-time roster",            inline=True)
    e.add_field(name="🌟 Fight of the Night", value="Award-winning fights",         inline=True)
    e.add_field(name="📅 Events",          value="Browse by region & event",        inline=True)
    e.add_field(name="🏆 Championship",    value="Title fight history by region",   inline=True)
    e.add_field(name="🐐 GOAT",            value="Greatest of All Time per region", inline=True)
    e.add_field(name="🔄 Refresh",         value="Reload data from Google Docs",    inline=True)
    e.set_footer(text="PL Bot • Source: DW2PL Records (Google Docs) • EST. 2021")
    return e

def build_player_embed(entry, region, all_fights, dn):
    e=discord.Embed(title=f"👤 {dn}",color=region_color(region))
    e.add_field(name="Region",      value=f"{region_flag(region)} {region}",              inline=True)
    e.add_field(name="Position",    value=f"{pos_medal(str(entry['pos']))} {entry['pos']}",inline=True)
    e.add_field(name="Affiliation", value=entry.get("affiliation") or "—",                inline=True)
    e.add_field(name="Record",      value=f"{entry['wins']}-{entry['losses']}",            inline=True)
    e.add_field(name="MP",          value=entry.get("mp",len(all_fights)),                 inline=True)
    e.add_field(name="Win Rate",    value=wr(entry["wins"],entry["losses"]),               inline=True)
    if all_fights:
        rc=Counter(r for r,_ in all_fights)
        if len(rc)>1:
            tags=" | ".join(f"{region_flag(r)} {r} ({c})" for r,c in rc.most_common())
            e.add_field(name="📍 Regions with history",value=tags,inline=False)
        lines=[fight_line(f) for _,f in all_fights[:5]]
        e.add_field(name=f"📜 Recent fights ({len(all_fights)} total)",value="\n".join(lines),inline=False)
    return e

def build_history_page(dn, all_fights, page):
    tp=max(1,(len(all_fights)+FIGHTS_PER_PAGE-1)//FIGHTS_PER_PAGE)
    chunk=all_fights[page*FIGHTS_PER_PAGE:(page+1)*FIGHTS_PER_PAGE]
    mr=Counter(r for r,_ in all_fights).most_common(1)[0][0] if all_fights else "EU"
    e=discord.Embed(title=f"📜 {dn} — Match History",
        description=f"**{len(all_fights)} fights** | Page {page+1}/{tp}",
        color=region_color(mr))
    cr=None
    for region,m in chunk:
        if region!=cr:
            e.add_field(name=f"{region_flag(region)} {region}",value="─────────────",inline=False)
            cr=region
        e.add_field(
            name=f"{res_emoji(m['result'])} vs **{m['opponent']}** `{m['score']}`",
            value=f"📅 {m['event']}"+(f" [▶]({m['vod']})" if m.get("vod") else "")+(f"\n_{m['notes']}_" if m.get("notes") else ""),
            inline=False)
    return e

def build_ranking_embed(region, ranking, unranked):
    e=discord.Embed(title=f"{region_flag(region)} {region} Rankings — Pro League",color=region_color(region))
    if not ranking: e.description="❌ No data found. Try **🔄 Refresh**."; return e
    lines=[f"{pos_medal(p['pos'])} **{p['player']}**{(' *'+p['affiliation']+'*') if p.get('affiliation') else ''} `{p['wins']}-{p['losses']}` | MP: {p['mp']}" for p in ranking]
    e.add_field(name=f"🏆 Top {len(ranking)}",value="\n".join(lines),inline=False)
    for tier,names in unranked.items():
        if names: e.add_field(name=f"📋 Unranked — {tier}",value="  ".join(names),inline=False)
    e.set_footer(text="Source: DW2PL Records (Google Docs)")
    return e

def build_stats_embed(region, reg):
    ranking=reg.get("ranking",[]); records=reg.get("records",{})
    e=discord.Embed(title=f"{region_flag(region)} {region} — Statistics",color=region_color(region))
    e.add_field(name="Ranked Players",    value=len(ranking),                          inline=True)
    e.add_field(name="Players w/ History",value=len(records),                          inline=True)
    e.add_field(name="Total Fights",      value=sum(len(v) for v in records.values()), inline=True)
    champ=next((p for p in ranking if str(p["pos"]).lower()=="champion"),None)
    if champ:
        e.add_field(name="👑 Current Champion",
            value=f"**{champ['player']}** `{champ['wins']}-{champ['losses']}`"+(f" *{champ['affiliation']}*" if champ.get("affiliation") else ""),
            inline=False)
    if ranking:
        tw=sorted(ranking,key=lambda p:p["wins"],reverse=True)[:3]
        e.add_field(name="🏆 Top 3 Wins",value="\n".join(f"{pos_medal(p['pos'])} **{p['player']}** — {p['wins']} wins" for p in tw),inline=True)
        tm=sorted(ranking,key=lambda p:p["mp"],reverse=True)[:3]
        e.add_field(name="🥊 Top 3 MP",value="\n".join(f"{pos_medal(p['pos'])} **{p['player']}** — {p['mp']} MP" for p in tm),inline=True)
    ac={}
    for p in ranking: a=p.get("affiliation") or "—"; ac[a]=ac.get(a,0)+1
    if ac: e.add_field(name="🤝 By Affiliation",value=" | ".join(f"**{a}**: {c}" for a,c in sorted(ac.items(),key=lambda x:-x[1])),inline=False)
    return e

def make_select(region, reg):
    opts=[]
    for p in reg.get("ranking",[]):
        medal="👑" if str(p["pos"]).lower()=="champion" else f"#{p['pos']}"
        aff=f" [{p['affiliation']}]" if p.get("affiliation") else ""
        opts.append(discord.SelectOption(label=f"{medal} {p['player']}{aff}"[:100],value=p["player"],description=f"{p['wins']}-{p['losses']} | MP: {p['mp']}"))
    for tier,names in reg.get("unranked",{}).items():
        for entry in names:
            m=re.match(r'^(.+?)\s*\((\d+-\d+)\)$',entry)
            nm=m.group(1).strip() if m else entry; rec=m.group(2) if m else "?"
            if not any(o.value==nm for o in opts):
                opts.append(discord.SelectOption(label=f"[{tier}] {nm}"[:100],value=nm,description=f"Unranked — {rec}"))
            if len(opts)>=25: break
        if len(opts)>=25: break
    if not opts: return None
    s=discord.ui.Select(placeholder=f"🔎 Select a {region} player...",options=opts[:25],custom_id=f"sel_{region}")
    return s

async def safe_edit(interaction, **kw):
    try: await interaction.response.edit_message(**kw)
    except Exception:
        try: await interaction.edit_original_response(**kw)
        except Exception: pass

async def safe_defer(interaction):
    try: await interaction.response.defer(); return True
    except Exception: return False

# ── Views ─────────────────────────────────────────────────────────────────────
class BackView(ui.View):
    def __init__(self): super().__init__(timeout=None)
    @ui.button(label="🔙 Back to Panel",style=discord.ButtonStyle.secondary,custom_id="back_main")
    async def back(self,i,b): await safe_edit(i,embed=build_main_embed(),view=MainPanel())

class MainPanel(ui.View):
    def __init__(self): super().__init__(timeout=None)
    @ui.button(label="🌍 Rankings",     style=discord.ButtonStyle.primary,  custom_id="pl_rankings",row=0)
    async def rankings(self,i,b): await safe_edit(i,embed=discord.Embed(title="🌍 Rankings — Select Region",color=PANEL_COLOR),view=RegionView("ranking"))
    @ui.button(label="👤 Player Lookup",style=discord.ButtonStyle.success,  custom_id="pl_player",  row=0)
    async def player(self,i,b): await i.response.send_modal(PlayerModal())
    @ui.button(label="📊 Stats",        style=discord.ButtonStyle.secondary,custom_id="pl_stats",   row=0)
    async def stats(self,i,b): await safe_edit(i,embed=discord.Embed(title="📊 Stats — Select Region",color=PANEL_COLOR),view=RegionView("stats"))
    @ui.button(label="🏅 Top Rankings", style=discord.ButtonStyle.danger,   custom_id="pl_top",     row=1)
    async def top(self,i,b): await safe_edit(i,embed=discord.Embed(title="🏅 Top Rankings",color=PANEL_COLOR),view=TopView())
    @ui.button(label="👥 All Players",  style=discord.ButtonStyle.primary,  custom_id="pl_allp",    row=1)
    async def all_players(self,i,b):
        if not await safe_defer(i): return
        data,_=await get_data()
        all_p=build_all_players_options(data)
        try: await i.edit_original_response(embed=build_all_players_embed(all_p),view=AllPlayersView(all_p))
        except Exception: pass
    @ui.button(label="🌟 Fight of the Night",style=discord.ButtonStyle.secondary,custom_id="pl_fotn",row=2)
    async def fotn(self,i,b):
        if not await safe_defer(i): return
        data,_=await get_data()
        fotn=collect_fotn(data)
        try: await i.edit_original_response(embed=build_fotn_embed(fotn),view=FOTNView(fotn))
        except Exception: pass
    @ui.button(label="📅 Events",       style=discord.ButtonStyle.success,  custom_id="pl_events",  row=2)
    async def events(self,i,b): await safe_edit(i,embed=discord.Embed(title="📅 Events — Select Region",color=PANEL_COLOR),view=EventRegionView())
    @ui.button(label="🏆 Championship", style=discord.ButtonStyle.danger,   custom_id="pl_champ",   row=3)
    async def champ(self,i,b): await safe_edit(i,embed=discord.Embed(title="🏆 Championship History — Select Region",color=PANEL_COLOR),view=ChampHistoryRegionView())
    @ui.button(label="🐐 GOAT",         style=discord.ButtonStyle.primary,  custom_id="pl_goat",    row=3)
    async def goat(self,i,b):
        if not await safe_defer(i): return
        data,_=await get_data()
        goat_data=compute_goat(data)
        try: await i.edit_original_response(embed=build_goat_embed(goat_data),view=GoatView())
        except Exception: pass
    @ui.button(label="👑 Ex-Champions", style=discord.ButtonStyle.secondary,custom_id="pl_exchamp", row=3)
    async def exchamp(self,i,b):
        if not await safe_defer(i): return
        data,_=await get_data()
        ex_data=collect_ex_champions(data)
        try: await i.edit_original_response(embed=build_ex_champ_embed(ex_data),view=ExChampView())
        except Exception: pass
    @ui.button(label="🔄 Refresh",      style=discord.ButtonStyle.secondary,custom_id="pl_refresh", row=4)
    async def refresh(self,i,b):
        await safe_defer(i)
        try:
            await i.edit_original_response(
                embed=discord.Embed(title="🔄 Updating...",description="⏳ Downloading data from Google Docs...",color=0xFFAA00),
                view=None)
        except Exception: pass
        status = await do_refresh()
        color  = 0x00FF88 if status.startswith("✅") else 0xFF0000
        try:
            await i.edit_original_response(
                embed=discord.Embed(title="🔄 Refresh",description=status,color=color),
                view=BackView())
        except Exception: pass

class RegionView(ui.View):
    """Dinâmica — botões gerados a partir de REGIONS em tempo de execução."""
    def __init__(self, mode):
        super().__init__(timeout=None)
        self.mode = mode
        _styles = [
            discord.ButtonStyle.primary,
            discord.ButtonStyle.danger,
            discord.ButtonStyle.success,
            discord.ButtonStyle.secondary,
            discord.ButtonStyle.primary,
        ]
        for idx, region in enumerate(REGIONS):
            btn = discord.ui.Button(
                label=region,
                emoji=region_flag(region),
                style=_styles[idx % len(_styles)],
                custom_id=f"rv_{region.lower()}_{mode}",
                row=min(idx // 4, 3)
            )
            btn.callback = self._make_cb(region)
            self.add_item(btn)
        back_row = min(len(REGIONS) // 4 + 1, 4)
        back_btn = discord.ui.Button(
            label="🔙 Back",
            style=discord.ButtonStyle.secondary,
            custom_id=f"rv_back_{mode}",
            row=back_row
        )
        back_btn.callback = self._back
        self.add_item(back_btn)

    def _make_cb(self, region):
        async def cb(i):
            await self._go(i, region)
        return cb

    async def _back(self, i):
        await safe_edit(i, embed=build_main_embed(), view=MainPanel())

    async def _go(self, i, region):
        if not await safe_defer(i): return
        data, _ = await get_data()
        reg = data.get(region, {})
        if self.mode == "ranking":
            embed = build_ranking_embed(region, reg.get("ranking", []), reg.get("unranked", {}))
            view  = RankView(region, data)
        else:
            embed = build_stats_embed(region, reg)
            view  = StatsView(region, data)
        try: await i.edit_original_response(embed=embed, view=view)
        except Exception: pass

class RankView(ui.View):
    def __init__(self,region,data):
        super().__init__(timeout=None); self.region=region
        s=make_select(region,data.get(region,{}))
        if s: s.callback=self._on_sel; self.add_item(s)
    async def _on_sel(self,i):
        if not await safe_defer(i): return
        q=i.data["values"][0].lower(); data,_=await get_data()
        dn,fights,entry,ereg=collect_fights(q,data)
        if not entry and not fights:
            try: await i.followup.send("❌ Player not found.",ephemeral=True)
            except Exception: pass; return
        if not entry:
            ereg=Counter(r for r,_ in fights).most_common(1)[0][0] if fights else self.region
            entry={"pos":"—","wins":sum(1 for _,f in fights if f["result"].lower()=="win"),
                   "losses":sum(1 for _,f in fights if f["result"].lower()=="loss"),"mp":len(fights),"affiliation":""}
        embed=build_player_embed(entry,ereg,fights,dn)
        view=HistNavView(dn,fights,0,back_region=self.region)
        try: await i.edit_original_response(embed=embed,view=view)
        except Exception: pass
    @ui.button(label="◀ Prev",style=discord.ButtonStyle.secondary,custom_id="rv_prev",row=1)
    async def prev(self,i,b): await self._shift(i,-1)
    @ui.button(label="Next ▶",style=discord.ButtonStyle.secondary,custom_id="rv_next",row=1)
    async def next(self,i,b): await self._shift(i,+1)
    @ui.button(label="🔙 Back",style=discord.ButtonStyle.secondary,custom_id="rv_back2",row=1)
    async def back(self,i,b): await safe_edit(i,embed=discord.Embed(title="🌍 Rankings — Select Region",color=PANEL_COLOR),view=RegionView("ranking"))
    @ui.button(label="🏠 Main Menu",style=discord.ButtonStyle.primary,custom_id="rv_home",row=1)
    async def home(self,i,b): await safe_edit(i,embed=build_main_embed(),view=MainPanel())
    async def _shift(self,i,delta):
        if not await safe_defer(i): return
        nr=REGIONS[(REGIONS.index(self.region)+delta)%len(REGIONS)]
        data,_=await get_data(); reg=data.get(nr,{})
        self.region=nr
        for item in list(self.children):
            if isinstance(item,discord.ui.Select): self.remove_item(item)
        s=make_select(nr,reg)
        if s: s.callback=self._on_sel; self.add_item(s)
        try: await i.edit_original_response(embed=build_ranking_embed(nr,reg.get("ranking",[]),reg.get("unranked",{})),view=self)
        except Exception: pass

class StatsView(ui.View):
    def __init__(self,region,data):
        super().__init__(timeout=None); self.region=region
        s=make_select(region,data.get(region,{}))
        if s: s.callback=self._on_sel; self.add_item(s)
    async def _on_sel(self,i):
        if not await safe_defer(i): return
        q=i.data["values"][0].lower(); data,_=await get_data()
        dn,fights,entry,ereg=collect_fights(q,data)
        if not entry:
            ereg=Counter(r for r,_ in fights).most_common(1)[0][0] if fights else self.region
            entry={"pos":"—","wins":sum(1 for _,f in fights if f["result"].lower()=="win"),
                   "losses":sum(1 for _,f in fights if f["result"].lower()=="loss"),"mp":len(fights),"affiliation":""}
        embed=build_player_embed(entry,ereg,fights,dn)
        view=HistNavView(dn,fights,0,back_region=self.region)
        try: await i.edit_original_response(embed=embed,view=view)
        except Exception: pass
    @ui.button(label="◀ Prev",style=discord.ButtonStyle.secondary,custom_id="sv_prev",row=1)
    async def prev(self,i,b): await self._shift(i,-1)
    @ui.button(label="Next ▶",style=discord.ButtonStyle.secondary,custom_id="sv_next",row=1)
    async def next(self,i,b): await self._shift(i,+1)
    @ui.button(label="🔙 Back",style=discord.ButtonStyle.secondary,custom_id="sv_back",row=1)
    async def back(self,i,b): await safe_edit(i,embed=discord.Embed(title="📊 Stats — Select Region",color=PANEL_COLOR),view=RegionView("stats"))
    @ui.button(label="🏠 Main Menu",style=discord.ButtonStyle.primary,custom_id="sv_home",row=1)
    async def home(self,i,b): await safe_edit(i,embed=build_main_embed(),view=MainPanel())
    async def _shift(self,i,delta):
        if not await safe_defer(i): return
        nr=REGIONS[(REGIONS.index(self.region)+delta)%len(REGIONS)]
        data,_=await get_data(); reg=data.get(nr,{})
        self.region=nr
        for item in list(self.children):
            if isinstance(item,discord.ui.Select): self.remove_item(item)
        s=make_select(nr,reg)
        if s: s.callback=self._on_sel; self.add_item(s)
        try: await i.edit_original_response(embed=build_stats_embed(nr,reg),view=self)
        except Exception: pass

class HistNavView(ui.View):
    def __init__(self,dn,fights,page,back_region=None):
        super().__init__(timeout=300)
        self.dn=dn; self.fights=fights; self.page=page
        self.back_region=back_region or (REGIONS[0] if REGIONS else "EU")
        self.tp=max(1,(len(fights)+FIGHTS_PER_PAGE-1)//FIGHTS_PER_PAGE)
        self._upd()
    def _upd(self):
        self.btn_prev.disabled = self.page == 0
        self.btn_next.disabled = self.page >= self.tp - 1
        self.btn_prev.label = f"◀ {self.page}/{self.tp}" if self.page > 0 else "◀"
        self.btn_next.label = f"{self.page+2}/{self.tp} ▶" if self.page < self.tp-1 else "▶"
    @ui.button(label="◀",style=discord.ButtonStyle.secondary,custom_id="hn_prev",row=0,disabled=True)
    async def btn_prev(self,i,b):
        if not await safe_defer(i): return
        self.page-=1; self._upd()
        try: await i.edit_original_response(embed=build_history_page(self.dn,self.fights,self.page),view=self)
        except Exception: pass
    @ui.button(label="▶",style=discord.ButtonStyle.secondary,custom_id="hn_next",row=0)
    async def btn_next(self,i,b):
        if not await safe_defer(i): return
        self.page+=1; self._upd()
        try: await i.edit_original_response(embed=build_history_page(self.dn,self.fights,self.page),view=self)
        except Exception: pass
    @ui.button(label="🔙 Back to Rankings",style=discord.ButtonStyle.secondary,custom_id="hn_back",row=1)
    async def btn_back(self,i,b):
        if not await safe_defer(i): return
        data,_=await get_data(); reg=data.get(self.back_region,{})
        try: await i.edit_original_response(embed=build_ranking_embed(self.back_region,reg.get("ranking",[]),reg.get("unranked",{})),view=RankView(self.back_region,data))
        except Exception: pass
    @ui.button(label="🏠 Main Menu",style=discord.ButtonStyle.primary,custom_id="hn_home",row=1)
    async def btn_home(self,i,b): await safe_edit(i,embed=build_main_embed(),view=MainPanel())

class PlayerModal(ui.Modal,title="🔍 Player Lookup"):
    name=ui.TextInput(label="Player name",placeholder="e.g. NLG, Jab, Larry, Weewarrior...",required=True)
    async def on_submit(self,i):
        await i.response.defer(ephemeral=False)
        data,_=await get_data(); q=self.name.value.lower().strip()
        dn,fights,entry,ereg=collect_fights(q,data)
        if not entry and not fights:
            return await i.followup.send(f"❌ Player **{self.name.value}** not found.")
        if not entry:
            ereg=Counter(r for r,_ in fights).most_common(1)[0][0] if fights else REGIONS[0]
            entry={"pos":"—","wins":sum(1 for _,f in fights if f["result"].lower()=="win"),
                   "losses":sum(1 for _,f in fights if f["result"].lower()=="loss"),"mp":len(fights),"affiliation":""}
        embed=build_player_embed(entry,ereg,fights,dn)
        view=HistNavView(dn,fights,0,back_region=ereg) if fights else BackView()
        await i.followup.send(embed=embed,view=view)

class HistoryModal(ui.Modal,title="📜 Match History"):
    name=ui.TextInput(label="Player name",placeholder="e.g. Jab, Larry, Weewarrior...",required=True)
    async def on_submit(self,i):
        await i.response.defer(ephemeral=False)
        data,_=await get_data(); q=self.name.value.lower().strip()
        dn,fights,_,_=collect_fights(q,data)
        if not fights:
            return await i.followup.send(f"❌ Player **{self.name.value}** not found.")
        await i.followup.send(embed=build_history_page(dn,fights,0),view=HistNavView(dn,fights,0))

class TopView(ui.View):
    def __init__(self): super().__init__(timeout=None)
    @ui.button(label="🏆 Top Wins",    style=discord.ButtonStyle.danger,   custom_id="top_w",  row=0)
    async def tw(self,i,b): await _top(i,"wins")
    @ui.button(label="⚡ Top Win Rate",style=discord.ButtonStyle.primary,  custom_id="top_wr", row=0)
    async def twr(self,i,b): await _top(i,"winrate")
    @ui.button(label="🥊 Top MP",      style=discord.ButtonStyle.secondary,custom_id="top_mp", row=0)
    async def tmp(self,i,b): await _top(i,"mp")
    @ui.button(label="🔙 Back",        style=discord.ButtonStyle.secondary,custom_id="top_bk", row=1)
    async def back(self,i,b): await safe_edit(i,embed=build_main_embed(),view=MainPanel())
    @ui.button(label="🏠 Main Menu",   style=discord.ButtonStyle.primary,  custom_id="top_hm", row=1)
    async def home(self,i,b): await safe_edit(i,embed=build_main_embed(),view=MainPanel())

async def _top(i,cat):
    if not await safe_defer(i): return
    data,_=await get_data()
    all_p=[{**p,"region":r} for r,reg in data.items() for p in reg.get("ranking",[])]
    if cat=="wins":
        ranked=sorted(all_p,key=lambda p:p["wins"],reverse=True); label="Wins"
        vf=lambda p:f"{p['wins']} wins (`{p['wins']}-{p['losses']}`)"
    elif cat=="winrate":
        ranked=sorted([p for p in all_p if p["wins"]+p["losses"]>=3],
                      key=lambda p:p["wins"]/max(p["wins"]+p["losses"],1),reverse=True)
        label="Win Rate (min. 3 fights)"; vf=lambda p:f"{round(p['wins']/(p['wins']+p['losses'])*100,1)}% (`{p['wins']}-{p['losses']}`)"
    else:
        ranked=sorted(all_p,key=lambda p:p["mp"],reverse=True); label="Matches Played"
        vf=lambda p:f"{p['mp']} MP"
    medals=["🥇","🥈","🥉"]
    e=discord.Embed(title=f"🏅 Top {label} — Pro League (All Regions)",color=PANEL_COLOR)
    for idx,p in enumerate(ranked[:15]):
        medal=medals[idx] if idx<3 else f"`#{idx+1}`"
        aff=f" *{p['affiliation']}*" if p.get("affiliation") else ""
        e.add_field(name=f"{medal} {p['player']}{aff}",
                    value=f"{vf(p)} | {region_flag(p['region'])} {p['region']} | Pos: {p['pos']}",inline=False)
    try: await i.edit_original_response(embed=e,view=TopView())
    except Exception: await i.followup.send(embed=e,view=TopView())

# ── All Players View ──────────────────────────────────────────────────────────
def build_all_players_options(data):
    """Build a deduplicated list of all players across all regions for a Select."""
    seen = {}  # player_name_lower -> (display_name, record, region)
    for region, reg in data.items():
        for p in reg.get("ranking", []):
            key = p["player"].lower()
            if key not in seen:
                seen[key] = (p["player"], f"{p['wins']}-{p['losses']}", region)
        for pname in reg.get("records", {}):
            key = pname.lower()
            if key not in seen:
                seen[key] = (pname, "?-?", region)
    return sorted(seen.values(), key=lambda x: x[0].lower())

class AllPlayersView(ui.View):
    def __init__(self, all_players, page=0):
        super().__init__(timeout=None)
        self.all_players = all_players
        self.page = page
        self.per_page = 25
        self.total_pages = max(1, (len(all_players) + self.per_page - 1) // self.per_page)
        self._build()

    def _build(self):
        # Remove old select/nav items except persistent buttons
        for item in list(self.children):
            self.remove_item(item)
        chunk = self.all_players[self.page * self.per_page:(self.page + 1) * self.per_page]
        opts = []
        for name, rec, region in chunk:
            opts.append(discord.SelectOption(
                label=name[:100],
                value=name,
                description=f"{region_flag(region)} {region} | {rec}",
                emoji="👤"
            ))
        if opts:
            sel = discord.ui.Select(
                placeholder=f"👥 All Players — Page {self.page+1}/{self.total_pages} ({len(self.all_players)} total)...",
                options=opts,
                custom_id="ap_sel",
                row=0
            )
            sel.callback = self._on_sel
            self.add_item(sel)
        prev_btn = discord.ui.Button(
            label=f"◀ {self.page}/{self.total_pages}" if self.page > 0 else "◀",
            style=discord.ButtonStyle.secondary,
            custom_id="ap_prev", row=1,
            disabled=(self.page == 0)
        )
        prev_btn.callback = self._prev
        self.add_item(prev_btn)
        next_btn = discord.ui.Button(
            label=f"{self.page+2}/{self.total_pages} ▶" if self.page < self.total_pages - 1 else "▶",
            style=discord.ButtonStyle.secondary,
            custom_id="ap_next", row=1,
            disabled=(self.page >= self.total_pages - 1)
        )
        next_btn.callback = self._next
        self.add_item(next_btn)
        home_btn = discord.ui.Button(label="🏠 Main Menu", style=discord.ButtonStyle.primary, custom_id="ap_home", row=1)
        home_btn.callback = self._home
        self.add_item(home_btn)

    async def _on_sel(self, i):
        if not await safe_defer(i): return
        q = i.data["values"][0].lower()
        data, _ = await get_data()
        dn, fights, entry, ereg = collect_fights(q, data)
        if not entry and not fights:
            try: await i.followup.send("❌ Player not found.", ephemeral=True)
            except Exception: pass
            return
        if not entry:
            ereg = Counter(r for r, _ in fights).most_common(1)[0][0] if fights else REGIONS[0]
            entry = {"pos": "—", "wins": sum(1 for _, f in fights if f["result"].lower() == "win"),
                     "losses": sum(1 for _, f in fights if f["result"].lower() == "loss"),
                     "mp": len(fights), "affiliation": ""}
        embed = build_player_embed(entry, ereg, fights, dn)
        view = HistNavView(dn, fights, 0, back_region=ereg)
        try: await i.edit_original_response(embed=embed, view=view)
        except Exception: pass

    async def _prev(self, i):
        if not await safe_defer(i): return
        self.page -= 1; self._build()
        data, _ = await get_data()
        all_p = build_all_players_options(data)
        try: await i.edit_original_response(embed=build_all_players_embed(all_p, self.page), view=self)
        except Exception: pass

    async def _next(self, i):
        if not await safe_defer(i): return
        self.page += 1; self._build()
        data, _ = await get_data()
        all_p = build_all_players_options(data)
        try: await i.edit_original_response(embed=build_all_players_embed(all_p, self.page), view=self)
        except Exception: pass

    async def _home(self, i):
        await safe_edit(i, embed=build_main_embed(), view=MainPanel())

def build_all_players_embed(all_players, page=0, per_page=25):
    total_pages = max(1, (len(all_players) + per_page - 1) // per_page)
    e = discord.Embed(
        title="👥 All Players — Pro League",
        description=f"**{len(all_players)} players** across all regions | Page {page+1}/{total_pages}\nSelect a player from the dropdown to view their card.",
        color=PANEL_COLOR
    )
    e.set_footer(text="PL Bot • All-time roster across EU, NA, SA, Global")
    return e

# ── Fight of the Night ────────────────────────────────────────────────────────
def collect_fotn(data):
    """Collect all fights flagged as Fight of the Night (any variant)."""
    _re_fotn = re.compile(r'fight\s+of\s+the\s+\w+|fight\s+of\s+night|FOTN|\bFON\b', re.I)
    results = []
    for region, reg in data.items():
        for pname, fights in reg.get("records", {}).items():
            for f in fights:
                notes = f.get("notes", "") or ""
                event = f.get("event", "") or ""
                if _re_fotn.search(notes) or _re_fotn.search(event):
                    results.append((region, pname, f))
    return results

def build_fotn_embed(fotn_list, page=0, per_page=8):
    total_pages = max(1, (len(fotn_list) + per_page - 1) // per_page)
    chunk = fotn_list[page * per_page:(page + 1) * per_page]
    e = discord.Embed(
        title="🌟 Fight of the Night — All Time",
        description=f"**{len(fotn_list)} award-winning fights** | Page {page+1}/{total_pages}",
        color=0xFFAA00
    )
    for region, pname, f in chunk:
        vod = f" [\u25b6]({f['vod']})" if f.get("vod") else ""
        notes = f.get("notes", "") or ""
        e.add_field(
            name=f"🌟 {pname} vs {f.get('opponent','?')}" + " `" + f.get("score","") + "`",
            value=(f"{region_flag(region)} {region} | {res_emoji(f['result'])} {f['result'].upper()} | 📅 {f.get('event','')}{vod}\n_{notes}_" if notes else
                   f"{region_flag(region)} {region} | {res_emoji(f['result'])} {f['result'].upper()} | 📅 {f.get('event','')}{vod}"),
            inline=False
        )
    if not chunk:
        e.description = "No Fight of the Night records found. Try **🔄 Refresh** to reload data."
    e.set_footer(text="PL Bot • Fight of the Night Awards")
    return e

class FOTNView(ui.View):
    def __init__(self, fotn_list, page=0):
        super().__init__(timeout=None)
        self.fotn_list = fotn_list
        self.page = page
        self.per_page = 8
        self.total_pages = max(1, (len(fotn_list) + self.per_page - 1) // self.per_page)
        self._upd()

    def _upd(self):
        self.btn_prev.disabled = self.page == 0
        self.btn_next.disabled = self.page >= self.total_pages - 1
        self.btn_prev.label = f"◀ {self.page}/{self.total_pages}" if self.page > 0 else "◀"
        self.btn_next.label = f"{self.page+2}/{self.total_pages} ▶" if self.page < self.total_pages - 1 else "▶"

    @ui.button(label="◀", style=discord.ButtonStyle.secondary, custom_id="fotn_prev", row=0, disabled=True)
    async def btn_prev(self, i, b):
        if not await safe_defer(i): return
        self.page -= 1; self._upd()
        try: await i.edit_original_response(embed=build_fotn_embed(self.fotn_list, self.page), view=self)
        except Exception: pass

    @ui.button(label="▶", style=discord.ButtonStyle.secondary, custom_id="fotn_next", row=0)
    async def btn_next(self, i, b):
        if not await safe_defer(i): return
        self.page += 1; self._upd()
        try: await i.edit_original_response(embed=build_fotn_embed(self.fotn_list, self.page), view=self)
        except Exception: pass

    @ui.button(label="🏠 Main Menu", style=discord.ButtonStyle.primary, custom_id="fotn_home", row=1)
    async def home(self, i, b): await safe_edit(i, embed=build_main_embed(), view=MainPanel())

# ── Champion History ───────────────────────────────────────────────────────────
def collect_champion_history(data):
    _re_won      = re.compile(r'\bwon\b.+championship', re.I)
    _re_defended = re.compile(r'\bdefended\b.+championship', re.I)
    _re_lost     = re.compile(r'\blost\b.+championship', re.I)
    _re_for      = re.compile(r'\bfor\b.+championship', re.I)
    _re_elim     = re.compile(r'title\s+eliminator', re.I)
    _re_mandatory= re.compile(r'mandatory\s+rematch', re.I)
    history = {}
    for region, reg in data.items():
        entries = []
        for pname, fights in reg.get("records", {}).items():
            for f in fights:
                notes = f.get("notes", "") or ""
                if not notes: continue
                tag = None
                if _re_won.search(notes):        tag = "👑 Won"
                elif _re_defended.search(notes): tag = "🛡️ Defended"
                elif _re_lost.search(notes):     tag = "💀 Lost"
                elif _re_for.search(notes):      tag = "🥊 Title Fight"
                elif _re_elim.search(notes):     tag = "⚡ Eliminator"
                elif _re_mandatory.search(notes):tag = "🔁 Mandatory Rematch"
                if tag:
                    entries.append((pname, f, tag, notes))
        history[region] = entries
    return history

def build_champ_history_embed(region, entries, page=0, per_page=8):
    total_pages = max(1, (len(entries) + per_page - 1) // per_page)
    chunk = entries[page * per_page:(page + 1) * per_page]
    e = discord.Embed(
        title=f"🏆 {region_flag(region)} {region} — Championship History",
        description=f"**{len(entries)} title fights recorded** | Page {page+1}/{total_pages}",
        color=region_color(region)
    )
    for pname, f, tag, notes in chunk:
        vod = f" [\u25b6]({f['vod']})" if f.get("vod") else ""
        e.add_field(
            name=f"{tag} — **{pname}** vs {f.get('opponent','?')}" + " `" + f.get("score","") + "`",
            value=f"📅 {f.get('event','')}{vod}\n_{notes}_",
            inline=False
        )
    if not chunk:
        e.description = "No championship history found. Try **🔄 Refresh**."
    e.set_footer(text=f"PL Bot • {region} Championship History")
    return e

class ChampHistoryRegionView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        _styles = [discord.ButtonStyle.primary, discord.ButtonStyle.danger,
                   discord.ButtonStyle.success, discord.ButtonStyle.secondary,
                   discord.ButtonStyle.primary]
        for idx, region in enumerate(REGIONS):
            btn = discord.ui.Button(
                label=region, emoji=region_flag(region),
                style=_styles[idx % len(_styles)],
                custom_id=f"ch_reg_{region.lower()}",
                row=min(idx // 4, 2)
            )
            btn.callback = self._make_cb(region)
            self.add_item(btn)
        home_btn = discord.ui.Button(label="🏠 Main Menu", style=discord.ButtonStyle.primary,
                                     custom_id="ch_home", row=min(len(REGIONS)//4+1,3))
        home_btn.callback = self._home
        self.add_item(home_btn)

    def _make_cb(self, region):
        async def cb(i):
            if not await safe_defer(i): return
            data, _ = await get_data()
            history = collect_champion_history(data)
            entries = history.get(region, [])
            embed = build_champ_history_embed(region, entries)
            view = ChampHistoryNavView(region, entries)
            try: await i.edit_original_response(embed=embed, view=view)
            except Exception: pass
        return cb

    async def _home(self, i):
        await safe_edit(i, embed=build_main_embed(), view=MainPanel())

class ChampHistoryNavView(ui.View):
    def __init__(self, region, entries, page=0):
        super().__init__(timeout=None)
        self.region = region; self.entries = entries; self.page = page
        self.per_page = 8
        self.total_pages = max(1, (len(entries) + self.per_page - 1) // self.per_page)
        self._upd()

    def _upd(self):
        self.btn_prev.disabled = self.page == 0
        self.btn_next.disabled = self.page >= self.total_pages - 1
        self.btn_prev.label = f"◀ {self.page}/{self.total_pages}" if self.page > 0 else "◀"
        self.btn_next.label = f"{self.page+2}/{self.total_pages} ▶" if self.page < self.total_pages-1 else "▶"

    @ui.button(label="◀", style=discord.ButtonStyle.secondary, custom_id="ch_prev", row=0, disabled=True)
    async def btn_prev(self, i, b):
        if not await safe_defer(i): return
        self.page -= 1; self._upd()
        try: await i.edit_original_response(embed=build_champ_history_embed(self.region, self.entries, self.page), view=self)
        except Exception: pass

    @ui.button(label="▶", style=discord.ButtonStyle.secondary, custom_id="ch_next", row=0)
    async def btn_next(self, i, b):
        if not await safe_defer(i): return
        self.page += 1; self._upd()
        try: await i.edit_original_response(embed=build_champ_history_embed(self.region, self.entries, self.page), view=self)
        except Exception: pass

    @ui.button(label="🔙 Back", style=discord.ButtonStyle.secondary, custom_id="ch_back", row=1)
    async def back(self, i, b): await safe_edit(i, embed=discord.Embed(title="🏆 Championship History — Select Region", color=PANEL_COLOR), view=ChampHistoryRegionView())

    @ui.button(label="🏠 Main Menu", style=discord.ButtonStyle.primary, custom_id="ch_home2", row=1)
    async def home(self, i, b): await safe_edit(i, embed=build_main_embed(), view=MainPanel())

# ── GOAT Card ─────────────────────────────────────────────────────────────────
def compute_goat(data):
    _re_title = re.compile(r'(?:won|for|claim|captur|became).{0,40}(?:championship|title|belt)', re.I)
    _re_def   = re.compile(r'\bdefend(?:ed|ing|s)?\b|\bretain(?:ed|ing|s)?\b', re.I)
    goat_by_region = {}
    for region, reg in data.items():
        records = reg.get("records", {})
        ranking = reg.get("ranking", [])
        player_stats = {}
        for p in ranking:
            titles = 1 if str(p.get("pos","")).lower() == "champion" else 0
            player_stats[p["player"].lower()] = {
                "name": p["player"], "wins": p["wins"], "losses": p["losses"],
                "mp": p["mp"], "affiliation": p.get("affiliation",""), "pos": p["pos"],
                "titles_won": titles, "titles_defended": 0
            }
        for pname, fights in records.items():
            key = pname.lower()
            if key not in player_stats:
                wins = sum(1 for f in fights if f.get("result","").lower() == "win")
                losses = sum(1 for f in fights if f.get("result","").lower() == "loss")
                player_stats[key] = {
                    "name": pname, "wins": wins, "losses": losses,
                    "mp": len(fights), "affiliation": "", "pos": "—",
                    "titles_won": 0, "titles_defended": 0
                }
            for f in fights:
                notes = f.get("notes","") or ""
                result = f.get("result","").lower()
                if result != "win":
                    continue
                if _re_def.search(notes):
                    player_stats[key]["titles_defended"] += 1
                elif _re_title.search(notes):
                    player_stats[key]["titles_won"] += 1
        eligible = [v for v in player_stats.values()
                    if v["wins"] + v["losses"] >= 10 or v["titles_won"] > 0]
        if not eligible:
            goat_by_region[region] = []; continue
        max_wins = max(p["wins"] for p in eligible) or 1
        max_mp   = max(p["mp"] for p in eligible) or 1
        scored = []
        for p in eligible:
            total = p["wins"] + p["losses"]
            wr    = p["wins"] / total if total > 0 else 0
            score = (wr * 0.40
                     + (p["wins"] / max_wins) * 0.25
                     + (p["mp"] / max_mp) * 0.15
                     + p["titles_won"] * 0.15
                     + p["titles_defended"] * 0.10)
            scored.append((round(score, 4), p))
        champs = [s for s in scored if s[1]["titles_won"] > 0]
        non_champs = [s for s in scored if s[1]["titles_won"] == 0]
        champs.sort(key=lambda x: -x[0])
        non_champs.sort(key=lambda x: -x[0])
        goat_by_region[region] = champs + non_champs
    return goat_by_region

def build_goat_embed(goat_by_region):
    e = discord.Embed(
        title="🐐 GOAT Card — Pro League All Time",
        description="Greatest of All Time per region, scored by win rate, championship history and matches played. Champions are ranked above non-champions.",
        color=0xFFD700
    )
    for region in REGIONS:
        scores = goat_by_region.get(region, [])
        if not scores:
            e.add_field(name=f"{region_flag(region)} {region}", value="Not enough data", inline=False); continue
        top_score, goat = scores[0]
        total = goat["wins"] + goat["losses"]
        wr_pct = round(goat["wins"] / total * 100, 1) if total > 0 else 0
        title_str = ""
        if goat["titles_won"]: title_str += f" | 👑 {goat['titles_won']}x Champ"
        if goat["titles_defended"]: title_str += f" | 🛡️ {goat['titles_defended']}x Def."
        aff = f" *{goat['affiliation']}*" if goat.get("affiliation") else ""
        runners = []
        for sc, p in scores[1:4]:
            t2 = p["wins"] + p["losses"]
            wr2 = round(p["wins"]/t2*100,1) if t2>0 else 0
            runners.append(f"`#{scores.index((sc,p))+1}` **{p['name']}** Score `{round(sc*100,1)}` | {wr2}% WR ({p['wins']}-{p['losses']})")
        runner_str = "\n".join(runners) if runners else ""
        value = (f"**{goat['name']}**{aff} — Score `{round(top_score*100,1)}`\n"
                 f"`{goat['wins']}-{goat['losses']}` | {wr_pct}% WR | {goat['mp']} MP{title_str}")
        if runner_str:
            value += f"\n\n**Runners-up:**\n{runner_str}"
        e.add_field(name=f"{region_flag(region)} {region} GOAT", value=value, inline=False)
    e.set_footer(text="PL Bot • GOAT formula: WR(40%) + Wins(25%) + MP(15%) + Titles(0.15 each) + Defenses(0.10 each) | Min. 10 fights (waived for champs) | Champs ranked first")
    return e


# ── Ex-Champions ──────────────────────────────────────────────────────────────
def collect_ex_champions(data):
    """Return dict of region -> {current_champ, ex_champs: [{name, titles, defenses, record}]}"""
    _re_won = re.compile(r'\bwon\b.+championship', re.I)
    _re_def = re.compile(r'\bdefended\b.+championship', re.I)
    result = {}
    for region in REGIONS:
        reg = data.get(region, {})
        ranking = reg.get("ranking", [])
        records = reg.get("records", {})
        # Current champion from ranking
        current = next((p["player"] for p in ranking if str(p.get("pos","")).lower() in ["champion","c"]), None)
        # Collect everyone who ever won a title
        champ_stats = {}
        for pname, fights in records.items():
            titles = sum(1 for f in fights if _re_won.search(f.get("notes","") or ""))
            defenses = sum(1 for f in fights if _re_def.search(f.get("notes","") or ""))
            if titles > 0:
                # Get their record from ranking if available
                entry = next((p for p in ranking if p["player"].lower() == pname.lower()), None)
                rec = f"{entry['wins']}-{entry['losses']}" if entry else "?"
                champ_stats[pname] = {"name": pname, "titles": titles, "defenses": defenses, "record": rec}
        # Remove current champion from ex list
        ex = [v for k, v in champ_stats.items() if not (current and k.lower() == current.lower())]
        ex.sort(key=lambda x: (-x["titles"], -x["defenses"]))
        result[region] = {"current": current, "ex": ex}
    return result

def build_ex_champ_embed(ex_data):
    e = discord.Embed(
        title="👑 Champions History — All Regions",
        description="Current and former champions of every region.",
        color=0xFFD700
    )
    for region in REGIONS:
        rd = ex_data.get(region, {})
        current = rd.get("current") or "Vacant"
        ex_list = rd.get("ex", [])
        lines = [f"👑 **{current}** *(Current)*"]
        if ex_list:
            for p in ex_list:
                def_str = f" | 🛡️ {p['defenses']}x def." if p["defenses"] > 0 else ""
                title_str = f"🏆 {p['titles']}x" if p["titles"] > 1 else "🏆"
                lines.append(f"{title_str} **{p['name']}** `{p['record']}`{def_str}")
        else:
            lines.append("*No former champions recorded*")
        e.add_field(
            name=f"{region_flag(region)} {region}",
            value="\n".join(lines),
            inline=True
        )
    e.set_footer(text="PL Bot • Based on championship notes in DW2PL Records")
    return e

class ExChampView(ui.View):
    def __init__(self): super().__init__(timeout=None)
    @ui.button(label="🏠 Main Menu", style=discord.ButtonStyle.primary, custom_id="exchamp_home", row=0)
    async def home(self, i, b): await safe_edit(i, embed=build_main_embed(), view=MainPanel())

class GoatView(ui.View):
    def __init__(self): super().__init__(timeout=None)
    @ui.button(label="🏠 Main Menu", style=discord.ButtonStyle.primary, custom_id="goat_home", row=0)
    async def home(self, i, b): await safe_edit(i, embed=build_main_embed(), view=MainPanel())

# ── Events Browser ────────────────────────────────────────────────────────────
def collect_events(data, region_filter=None):
    """Return sorted unique events, optionally filtered by region."""
    events = {}  # event_name -> list of (region, pname, fight)
    for region, reg in data.items():
        if region_filter and region != region_filter:
            continue
        for pname, fights in reg.get("records", {}).items():
            for f in fights:
                ev = (f.get("event") or "").strip()
                if ev:
                    events.setdefault(ev, []).append((region, pname, f))
    return dict(sorted(events.items()))

def build_event_embed(event_name, fights):
    regions_in = list(dict.fromkeys(r for r, _, _ in fights))
    color = region_color(regions_in[0]) if regions_in else PANEL_COLOR
    e = discord.Embed(
        title=f"📅 {event_name}",
        description=f"**{len(fights)} fights** | Regions: {' '.join(region_flag(r)+' '+r for r in regions_in)}",
        color=color
    )
    for region, pname, f in fights[:20]:
        vod = f" [▶]({f['vod']})" if f.get("vod") else ""
        nt = f"\n_{f['notes']}_" if f.get("notes") else ""
        e.add_field(
            name=f"{res_emoji(f['result'])} {pname} vs {f.get('opponent','?')} `{f.get('score','')}`",
            value=f"{region_flag(region)} {region}{vod}{nt}",
            inline=False
        )
    if len(fights) > 20:
        e.set_footer(text=f"Showing first 20 of {len(fights)} fights")
    return e

class EventRegionView(ui.View):
    """Step 1: Choose region (or All) to browse events."""
    def __init__(self):
        super().__init__(timeout=None)
        # Build dynamic region buttons
        _styles = [discord.ButtonStyle.primary, discord.ButtonStyle.danger,
                   discord.ButtonStyle.success, discord.ButtonStyle.secondary,
                   discord.ButtonStyle.primary]
        for idx, region in enumerate(REGIONS):
            btn = discord.ui.Button(
                label=region, emoji=region_flag(region),
                style=_styles[idx % len(_styles)],
                custom_id=f"ev_reg_{region.lower()}",
                row=min(idx // 4, 2)
            )
            btn.callback = self._make_cb(region)
            self.add_item(btn)
        all_btn = discord.ui.Button(
            label="🌐 All Regions", style=discord.ButtonStyle.secondary,
            custom_id="ev_reg_all", row=min(len(REGIONS) // 4 + 1, 3)
        )
        all_btn.callback = self._all
        self.add_item(all_btn)
        back_btn = discord.ui.Button(
            label="🏠 Main Menu", style=discord.ButtonStyle.primary,
            custom_id="ev_home", row=min(len(REGIONS) // 4 + 1, 3)
        )
        back_btn.callback = self._home
        self.add_item(back_btn)

    def _make_cb(self, region):
        async def cb(i):
            if not await safe_defer(i): return
            data, _ = await get_data()
            events = collect_events(data, region_filter=region)
            view = EventSelectView(events, region_filter=region)
            embed = discord.Embed(
                title=f"📅 Events — {region_flag(region)} {region}",
                description=f"**{len(events)} events** found. Select one to view its fights.",
                color=region_color(region)
            )
            try: await i.edit_original_response(embed=embed, view=view)
            except Exception: pass
        return cb

    async def _all(self, i):
        if not await safe_defer(i): return
        data, _ = await get_data()
        events = collect_events(data)
        view = EventSelectView(events, region_filter=None)
        embed = discord.Embed(
            title="📅 Events — All Regions",
            description=f"**{len(events)} events** found. Select one to view its fights.",
            color=PANEL_COLOR
        )
        try: await i.edit_original_response(embed=embed, view=view)
        except Exception: pass

    async def _home(self, i):
        await safe_edit(i, embed=build_main_embed(), view=MainPanel())

class EventSelectView(ui.View):
    """Step 2: Select event from dropdown (paginated 25 at a time)."""
    def __init__(self, events, region_filter=None, page=0):
        super().__init__(timeout=None)
        self.events = list(events.items())  # [(event_name, fights_list), ...]
        self.region_filter = region_filter
        self.page = page
        self.per_page = 25
        self.total_pages = max(1, (len(self.events) + self.per_page - 1) // self.per_page)
        self._build()

    def _build(self):
        for item in list(self.children):
            self.remove_item(item)
        chunk = self.events[self.page * self.per_page:(self.page + 1) * self.per_page]
        if chunk:
            opts = []
            for ev_name, fights in chunk:
                regions_in = list(dict.fromkeys(r for r, _, _ in fights))
                flags = " ".join(region_flag(r) for r in regions_in[:3])
                opts.append(discord.SelectOption(
                    label=ev_name[:100],
                    value=ev_name[:100],
                    description=f"{flags} | {len(fights)} fights",
                    emoji="📅"
                ))
            sel = discord.ui.Select(
                placeholder=f"📅 Select event — Page {self.page+1}/{self.total_pages}...",
                options=opts,
                custom_id="ev_sel",
                row=0
            )
            sel.callback = self._on_sel
            self.add_item(sel)
        prev_btn = discord.ui.Button(
            label=f"◀ {self.page}/{self.total_pages}" if self.page > 0 else "◀",
            style=discord.ButtonStyle.secondary, custom_id="ev_prev", row=1,
            disabled=(self.page == 0)
        )
        prev_btn.callback = self._prev
        self.add_item(prev_btn)
        next_btn = discord.ui.Button(
            label=f"{self.page+2}/{self.total_pages} ▶" if self.page < self.total_pages - 1 else "▶",
            style=discord.ButtonStyle.secondary, custom_id="ev_next", row=1,
            disabled=(self.page >= self.total_pages - 1)
        )
        next_btn.callback = self._next
        self.add_item(next_btn)
        back_btn = discord.ui.Button(
            label="🔙 Back", style=discord.ButtonStyle.secondary, custom_id="ev_back", row=1
        )
        back_btn.callback = self._back
        self.add_item(back_btn)
        home_btn = discord.ui.Button(
            label="🏠 Main Menu", style=discord.ButtonStyle.primary, custom_id="ev_shome", row=1
        )
        home_btn.callback = self._home
        self.add_item(home_btn)

    async def _on_sel(self, i):
        if not await safe_defer(i): return
        ev_name = i.data["values"][0]
        # Find fights for this event
        fights = next((f for n, f in self.events if n == ev_name), [])
        embed = build_event_embed(ev_name, fights)
        view = EventDetailView(ev_name, fights, self)
        try: await i.edit_original_response(embed=embed, view=view)
        except Exception: pass

    async def _prev(self, i):
        if not await safe_defer(i): return
        self.page -= 1; self._build()
        rf = self.region_filter
        title = f"📅 Events — {region_flag(rf)} {rf}" if rf else "📅 Events — All Regions"
        color = region_color(rf) if rf else PANEL_COLOR
        embed = discord.Embed(title=title, description=f"**{len(self.events)} events** found. Select one.", color=color)
        try: await i.edit_original_response(embed=embed, view=self)
        except Exception: pass

    async def _next(self, i):
        if not await safe_defer(i): return
        self.page += 1; self._build()
        rf = self.region_filter
        title = f"📅 Events — {region_flag(rf)} {rf}" if rf else "📅 Events — All Regions"
        color = region_color(rf) if rf else PANEL_COLOR
        embed = discord.Embed(title=title, description=f"**{len(self.events)} events** found. Select one.", color=color)
        try: await i.edit_original_response(embed=embed, view=self)
        except Exception: pass

    async def _back(self, i):
        await safe_edit(i, embed=discord.Embed(title="📅 Events — Select Region", color=PANEL_COLOR), view=EventRegionView())

    async def _home(self, i):
        await safe_edit(i, embed=build_main_embed(), view=MainPanel())

class EventDetailView(ui.View):
    """Step 3: Viewing a specific event."""
    def __init__(self, ev_name, fights, parent_select_view):
        super().__init__(timeout=None)
        self.ev_name = ev_name
        self.fights = fights
        self.parent = parent_select_view

    @ui.button(label="🔙 Back to Events", style=discord.ButtonStyle.secondary, custom_id="evd_back", row=0)
    async def back(self, i, b):
        if not await safe_defer(i): return
        rf = self.parent.region_filter
        title = f"📅 Events — {region_flag(rf)} {rf}" if rf else "📅 Events — All Regions"
        color = region_color(rf) if rf else PANEL_COLOR
        embed = discord.Embed(title=title, description=f"**{len(self.parent.events)} events** found. Select one.", color=color)
        try: await i.edit_original_response(embed=embed, view=self.parent)
        except Exception: pass

    @ui.button(label="🏠 Main Menu", style=discord.ButtonStyle.primary, custom_id="evd_home", row=0)
    async def home(self, i, b): await safe_edit(i, embed=build_main_embed(), view=MainPanel())


@bot.event
async def on_ready():
    print(f"✅ PL Bot ONLINE as {bot.user}!")
    bot.add_view(MainPanel())
    bot.add_view(TopView())
    asyncio.create_task(_preload())

async def _preload():
    await asyncio.sleep(2)
    if os.path.exists(JSON_PATH):
        print("[PL Bot] JSON on disk found — loading light copy.")
    else:
        print("[PL Bot] No JSON on disk — downloading and parsing PDF.")
    await get_data()
    # Registra RegionView DEPOIS de carregar dados, quando REGIONS já está populado
    bot.add_view(RegionView("ranking"))
    bot.add_view(RegionView("stats"))
    print(f"[PL Bot] Ready! Regions: {REGIONS}")

# ── Commands ──────────────────────────────────────────────────────────────────
@bot.command(name="panel",aliases=["painel"])
async def cmd_panel(ctx): await ctx.send(embed=build_main_embed(),view=MainPanel())

@bot.command(name="ranking")
async def cmd_ranking(ctx,region:str=None):
    if not region or region.upper() not in [r.upper() for r in REGIONS]:
        await ctx.send("Usage: `!ranking <" + "|".join(REGIONS) + ">`",view=RegionView("ranking")); return
    rk=next(r for r in REGIONS if r.upper()==region.upper())
    data,_=await get_data(); reg=data.get(rk,{})
    await ctx.send(embed=build_ranking_embed(rk,reg.get("ranking",[]),reg.get("unranked",{})),view=RankView(rk,data))

@bot.command(name="player",aliases=["jogador"])
async def cmd_player(ctx,*,name:str=None):
    if not name: return await ctx.send("Usage: `!player <name>`")
    data,_=await get_data(); dn,fights,entry,ereg=collect_fights(name.lower(),data)
    if not entry and not fights: return await ctx.send(f"❌ Player **{name}** not found.")
    if not entry:
        ereg=Counter(r for r,_ in fights).most_common(1)[0][0] if fights else REGIONS[0]
        entry={"pos":"—","wins":sum(1 for _,f in fights if f["result"].lower()=="win"),
               "losses":sum(1 for _,f in fights if f["result"].lower()=="loss"),"mp":len(fights),"affiliation":""}
    await ctx.send(embed=build_player_embed(entry,ereg,fights,dn),view=HistNavView(dn,fights,0,back_region=ereg))

@bot.command(name="history",aliases=["historico","hist"])
async def cmd_history(ctx,*,name:str=None):
    if not name: return await ctx.send("Usage: `!history <name>`")
    data,_=await get_data(); dn,fights,_,_=collect_fights(name.lower(),data)
    if not fights: return await ctx.send(f"❌ Player **{name}** not found.")
    await ctx.send(embed=build_history_page(dn,fights,0),view=HistNavView(dn,fights,0))

@bot.command(name="stats",aliases=["estatisticas"])
async def cmd_stats(ctx,region:str=None):
    if not region or region.upper() not in [r.upper() for r in REGIONS]:
        return await ctx.send("Usage: `!stats <" + "|".join(REGIONS) + ">`")
    rk=next(r for r in REGIONS if r.upper()==region.upper())
    data,_=await get_data()
    await ctx.send(embed=build_stats_embed(rk,data.get(rk,{})),view=StatsView(rk,data))

@bot.command(name="top")
async def cmd_top(ctx,cat:str="wins"):
    cm={"wins":"wins","win":"wins","winrate":"winrate","wr":"winrate","mp":"mp"}
    c=cm.get(cat.lower(),"wins")
    data,_=await get_data()
    all_p=[{**p,"region":r} for r,reg in data.items() for p in reg.get("ranking",[])]
    if c=="wins": ranked=sorted(all_p,key=lambda p:p["wins"],reverse=True); label="Wins"; vf=lambda p:f"{p['wins']} wins"
    elif c=="winrate": ranked=sorted([p for p in all_p if p["wins"]+p["losses"]>=3],key=lambda p:p["wins"]/max(p["wins"]+p["losses"],1),reverse=True); label="Win Rate"; vf=lambda p:f"{round(p['wins']/(p['wins']+p['losses'])*100,1)}%"
    else: ranked=sorted(all_p,key=lambda p:p["mp"],reverse=True); label="MP"; vf=lambda p:f"{p['mp']} fights"
    medals=["🥇","🥈","🥉"]
    e=discord.Embed(title=f"🏅 Top {label} — Pro League",color=PANEL_COLOR)
    for idx,p in enumerate(ranked[:10]):
        e.add_field(name=f"{medals[idx] if idx<3 else f'`#{idx+1}`'} {p['player']}",value=f"{vf(p)} | {region_flag(p['region'])} {p['region']}",inline=False)
    await ctx.send(embed=e)

@bot.command(name="refresh",aliases=["atualizar"])
async def cmd_refresh(ctx):
    msg = await ctx.send("🔄 Downloading data from Google Docs...")
    status = await do_refresh()
    await msg.edit(content=status)

@bot.command(name="fotn",aliases=["fightofthenight"])
async def cmd_fotn(ctx):
    data,_=await get_data()
    fotn=collect_fotn(data)
    await ctx.send(embed=build_fotn_embed(fotn),view=FOTNView(fotn))

@bot.command(name="goat")
async def cmd_goat(ctx):
    data,_=await get_data()
    goat_data=compute_goat(data)
    await ctx.send(embed=build_goat_embed(goat_data),view=GoatView())

@bot.command(name="championship",aliases=["champ","champions"])
async def cmd_championship(ctx,region:str=None):
    data,_=await get_data()
    history=collect_champion_history(data)
    if region:
        rf=next((r for r in REGIONS if r.upper()==region.upper()),None)
        if rf:
            entries=history.get(rf,[])
            await ctx.send(embed=build_champ_history_embed(rf,entries),view=ChampHistoryNavView(rf,entries))
            return
    await ctx.send(embed=discord.Embed(title="🏆 Championship History — Select Region",color=PANEL_COLOR),view=ChampHistoryRegionView())

@bot.command(name="events",aliases=["eventos"])
async def cmd_events(ctx,region:str=None):
    data,_=await get_data()
    rf=None
    if region:
        rf=next((r for r in REGIONS if r.upper()==region.upper()),None)
    events=collect_events(data,region_filter=rf)
    title=f"📅 Events — {region_flag(rf)} {rf}" if rf else "📅 Events — All Regions"
    color=region_color(rf) if rf else PANEL_COLOR
    embed=discord.Embed(title=title,description=f"**{len(events)} events** found. Select one.",color=color)
    await ctx.send(embed=embed,view=EventSelectView(events,region_filter=rf))

@bot.command(name="help",aliases=["ajuda"])
async def cmd_help(ctx):
    e=discord.Embed(title="📜 PL Bot — Commands",description="Use **`!panel`** for the full interactive panel.",color=PANEL_COLOR)
    e.add_field(name="!panel",value="Interactive panel with buttons",inline=False)
    e.add_field(name="!ranking <region>",value=f"Region top 10 — `{'|'.join(REGIONS)}`",inline=False)
    e.add_field(name="!player <n>",value="Player card. e.g. `!player NLG`",inline=False)
    e.add_field(name="!history <n>",value="Fight log. e.g. `!history Jab`",inline=False)
    e.add_field(name="!stats <region>",value="Region statistics",inline=False)
    e.add_field(name="!top [wins|wr|mp]",value="`wins` | `wr` | `mp`",inline=False)
    e.add_field(name="!fotn",value="Fight of the Night awards",inline=False)
    e.add_field(name="!goat",value="GOAT card — greatest per region",inline=False)
    e.add_field(name="!championship [region]",value="Title fight history by region",inline=False)
    e.add_field(name="!events [region]",value="Browse events by region",inline=False)
    e.add_field(name="!refresh",value="Reload data from Google Docs",inline=False)
    await ctx.send(embed=e)

bot.run(os.environ["DISCORD_TOKEN"])
