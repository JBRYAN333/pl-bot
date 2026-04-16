import discord
from discord.ext import commands
from discord import ui
import asyncio
import sys
import re
import os
import pdfplumber
from collections import Counter

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)
bot.remove_command("help")

PDF_PATH           = "DW2PL_Records.pdf"
WELCOME_CHANNEL_ID = 0
PANEL_COLOR        = 0x1E90FF
REGIONS            = ["EU", "NA", "SA", "Global"]
FIGHTS_PER_PAGE    = 10

_raw_text:  str | None  = None
_data:      dict | None = None
_vods:      dict | None = None
_pdf_mtime: float       = 0

def _read_pdf():
    if not os.path.exists(PDF_PATH):
        print(f"[PL Bot] ERROR: '{PDF_PATH}' not found.")
        return "", {}
    try:
        text_pages, vod_map = [], {}
        re_ev = re.compile(
            r'DW2PL\s+(?:Fight\s+Night\s+|EU\s+Tournament\s+|NA\s+Tournament\s+'
            r'|SA\s+Tournament\s+|Global\s+Tournament\s+|Global\s+Part\s+\d+\s*)?#?\d+', re.I)
        with pdfplumber.open(PDF_PATH) as pdf:
            for page in pdf.pages:
                text_pages.append(page.extract_text() or "")
                yt = {}
                for h in (page.hyperlinks or []):
                    u = h.get("uri","")
                    if u and ("youtube" in u or "youtu.be" in u or "twitch" in u):
                        yt[round(h["top"])] = u
                if not yt:
                    continue
                ly = {}
                for w in page.extract_words():
                    ly.setdefault(round(w["top"]), []).append(w["text"])
                for link_y, url in yt.items():
                    above = sorted([(y," ".join(ws)) for y,ws in ly.items()
                                    if y < link_y and link_y-y < 200], key=lambda x:-x[0])
                    for _, ln in above:
                        m = re_ev.search(ln)
                        if m:
                            vod_map[re.sub(r"\s+"," ",m.group(0).strip())] = url
                            break
        txt = "\n".join(text_pages)
        print(f"[PL Bot] PDF loaded: {len(txt)} chars, {len(vod_map)} VODs")
        return txt, vod_map
    except Exception as e:
        print(f"[PL Bot] Error reading PDF: {e}")
        return "", {}

async def fetch_doc():
    global _raw_text, _vods, _pdf_mtime
    try: mtime = os.path.getmtime(PDF_PATH)
    except FileNotFoundError: mtime = 0
    if _raw_text and mtime == _pdf_mtime:
        return _raw_text, _vods
    # Roda em thread separada para não bloquear o event loop do Discord
    loop = asyncio.get_event_loop()
    _raw_text, _vods = await loop.run_in_executor(None, _read_pdf)
    _pdf_mtime = mtime
    return _raw_text or "", _vods or {}

def clear_cache():
    global _raw_text, _data, _vods, _pdf_mtime
    _raw_text = _data = _vods = None
    _pdf_mtime = 0

_RE_SR  = re.compile(r'^(EU|NA|SA|Global) Rankings?$', re.I)
_RE_SC  = re.compile(r'^(EU|NA|SA|Global) Records?$', re.I)
_RE_RR  = re.compile(r'^(Win|Loss|Draw|NC)\b', re.I)
_RE_RF  = re.compile(r'^\d+-\d+$')
_RE_TH  = re.compile(r'^Tier \d', re.I)
_RE_NR  = re.compile(r'^(.+?)\s*\((\d+)-(\d+)\)$')
_RE_CT  = re.compile(r'[\u200b\u00a0\u200c\u200d\u2060\ufeff\u202f\xa0]')
_RM     = {"eu":"EU","na":"NA","sa":"SA","global":"Global"}
_SKIP   = re.compile(
    r'Non-Tournament|Fight of the|Championship|Qualifiers?|Prelims?|VOD Link|'
    r'Round \d+|Losers|Winners|Bracket|Inaugural|won the|replaced |forfeited|'
    r'Finals?|Exhibitions?|^AS$|^(EU|NA|SA|Global)\s*$|Inactive|'
    r'^\s*(Top \d+|DW2PL|Rules?|Lag Rule|Inter-Regional|Same.region)\b', re.I
)
_RE_EV  = re.compile(r'(DW2PL\s+(?:Fight\s+Night\s+|EU\s+Tournament\s+|NA\s+Tournament\s+'
                     r'|SA\s+Tournament\s+|Global\s+Tournament\s+|Global\s+Part\s+\d+\s*)?#?\d+)', re.I)

def parse_doc(text, vod_map):
    res = {r: {"ranking":[],"unranked":{},"records":{}} for r in REGIONS}
    lines = [re.sub(r'\s+',' ',_RE_CT.sub(' ',l).strip())
             for l in text.splitlines() if l.strip()]
    cr = cs = cp = None
    th = []
    pending_name = None  # candidate name waiting for header confirmation

    for line in lines:
        m = _RE_SR.match(line)
        if m:
            cr=_RM[m.group(1).lower()]; cs="ranking"; th=[]; cp=None
            pending_name=None; continue
        m = _RE_SC.match(line)
        if m:
            cr=_RM[m.group(1).lower()]; cs="records"; cp=None
            pending_name=None; continue
        if not cr: continue
        reg = res[cr]

        # ── RANKING ──────────────────────────────────────────────────────
        if cs == "ranking":
            if re.match(r'^Unranked$',line,re.I): cs="unranked"; th=[]; continue
            if re.search(r'Top \d+|^Position\b|^Nation\b|^Player\b|\bMP\b.*Wins|Affiliation',line,re.I): continue
            toks = line.split()
            if not toks: continue
            f = toks[0]
            if not re.match(r'^(Champion|#\d+)$',f,re.I): continue
            ni = [i for i,t in enumerate(toks) if re.match(r'^\d+$',t)]
            if len(ni)<3: continue
            im,iw,il = ni[-3],ni[-2],ni[-1]
            mp,w,l = int(toks[im]),int(toks[iw]),int(toks[il])
            aff = toks[il+1] if il+1<len(toks) else ""
            player = " ".join(toks[1:im]).strip()
            pos = "Champion" if f.lower()=="champion" else f.lstrip("#")
            if player and player.upper() not in ("VACANT","N/A",""):
                reg["ranking"].append({"pos":pos,"player":player,"mp":mp,"wins":w,"losses":l,"affiliation":aff})

        # ── UNRANKED ──────────────────────────────────────────────────────
        elif cs == "unranked":
            if _RE_TH.match(line):
                th = re.findall(r'Tier \d+',line,re.I)
                for t in th: reg["unranked"].setdefault(t,[])
                continue
            if not th: continue
            ms = _RE_NR.findall(line)
            if not ms:
                for part in re.findall(r'\S+\s*\(\d+-\d+\)',line):
                    mm = _RE_NR.match(part.strip())
                    if mm: ms.append((mm.group(1).strip(),mm.group(2),mm.group(3)))
            for idx,(nm,ww,ll) in enumerate(ms):
                key = th[idx] if idx<len(th) else th[-1]
                reg["unranked"][key].append(f"{nm.strip()} ({ww}-{ll})")

        # ── RECORDS ───────────────────────────────────────────────────────
        elif cs == "records":
            # Stop at inactive section
            if re.match(r'^Inactive:?\s*$', line, re.I): cs=None; continue

            # Header "Res. Record Opponent Score Event Notes"
            # → confirms the pending_name as a real player
            if re.search(r'\bRes\.?\s+Record\b|\bOpponent\b.*\bScore\b', line, re.I):
                if pending_name is not None:
                    cp = pending_name
                    if cp not in reg["records"]: reg["records"][cp]=[]
                pending_name = None
                continue

            # Fight result line
            if _RE_RR.match(line):
                pending_name = None
                if not cp: continue
                toks = line.split()
                if len(toks)<3: continue
                rv=toks[0]; idx=1
                rec=toks[idx] if _RE_RF.match(toks[idx]) else ""
                if rec: idx+=1
                opp=toks[idx]   if idx   < len(toks) else ""
                sc =toks[idx+1] if idx+1 < len(toks) else ""
                ep,np=[],[]
                in_n=False
                for tok in toks[idx+2:]:
                    if ep and not re.match(r'^(DW2PL|EU|NA|SA|Fight|Night|Tournament|Global|Part|#\d+|\d+)$',tok,re.I): in_n=True
                    (np if in_n else ep).append(tok)
                ev=" ".join(ep); nt=" ".join(np)
                vm = _RE_EV.search(ev)
                vod = vod_map.get(re.sub(r"\s+"," ",vm.group(1).strip()),"") if vm else ""
                reg["records"][cp].append({"result":rv,"record":rec,"opponent":opp,"score":sc,"event":ev,"notes":nt,"vod":vod})
                continue

            # Candidate player name — set as pending, confirmed by next header
            has_g = bool(re.search(r'\(G\)\s*$', line))
            cand  = re.sub(r'\s*\(G\)\s*$','',line).strip()

            is_candidate = False
            if has_g and 1<=len(cand)<=35 and not _SKIP.search(cand):
                is_candidate = True
            elif (2<=len(cand)<=35
                    and not re.search(r'\d{4,}',cand)
                    and not re.match(r'^[#\d\-]',cand)
                    and len(cand.split()) <= 4
                    and cand.upper() not in ("EU","NA","SA","GLOBAL","UNRANKED","TOP 10:","TOP 15:","TIER 1","TIER 2","TIER 3")
                    and not _SKIP.search(cand)):
                is_candidate = True

            pending_name = cand if is_candidate else None

    return res


async def get_data():
    global _data
    # Se já tem dados e o PDF não mudou, retorna cache
    if _data and _raw_text: return _data, _vods or {}
    text,vods = await fetch_doc()
    if not text: return {r:{"ranking":[],"unranked":{},"records":{}} for r in REGIONS},{}
    # Só reparseia se não tinha dados ou PDF mudou
    if not _data:
        _data = parse_doc(text,vods)
        for r in REGIONS:
            print(f"[PL Bot] {r}: {len(_data[r]['ranking'])} ranked, {len(_data[r]['records'])} with history")
    return _data, vods or {}

def region_color(r): return {"EU":0x003BB5,"NA":0xCC0000,"SA":0x009933,"Global":0xFFAA00}.get(r,PANEL_COLOR)
def region_flag(r):  return {"EU":"🇪🇺","NA":"🇺🇸","SA":"🌎","Global":"🌍"}.get(r,"🏴")
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
    """Busca lutas onde o jogador é DONO do record (não oponente)."""
    dn=query; fights=[]; entry=None; ereg=None
    for region,reg in data.items():
        for p in reg.get("ranking",[]):
            if query in p["player"].lower() and not entry:
                entry=p; ereg=region
        for pname,fs in reg.get("records",{}).items():
            # Só pega se o nome buscado é o DONO das lutas (não aparece só como oponente)
            if query in pname.lower() and fs:
                dn=pname
                for f in fs: fights.append((region,f))
    # Remove lutas duplicadas (mesmo record+oponente)
    seen=set(); unique=[]
    for region,f in fights:
        key=(f.get("record",""),f.get("opponent",""),f.get("score",""))
        if key not in seen:
            seen.add(key); unique.append((region,f))
    return dn, unique, entry, ereg

def build_main_embed():
    e=discord.Embed(title="🏁 DW2 PRO LEAGUE BOT",
        description="**Drunken Wrestlers 2 — Pro League** | Interactive Panel\n\nRankings, player cards and match history across all regions.",
        color=PANEL_COLOR)
    e.add_field(name="🌍 Rankings",      value="Top 10 by region (EU/NA/SA/Global)",inline=True)
    e.add_field(name="👤 Player Lookup", value="Full card with match history",       inline=True)
    e.add_field(name="📜 History",       value="Complete fight log for any player",  inline=True)
    e.add_field(name="📊 Stats",         value="Region overview & leaderboards",     inline=True)
    e.add_field(name="🏅 Top Rankings",  value="Top Wins, Win Rate, MP",            inline=True)
    e.add_field(name="🔄 Refresh",       value="Reload PDF data",                   inline=True)
    e.set_footer(text="PL Bot • Source: DW2PL_Records.pdf")
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
    e.set_footer(text="Source: DW2PL_Records.pdf")
    return e

def build_stats_embed(region, reg):
    ranking=reg.get("ranking",[]); records=reg.get("records",{})
    e=discord.Embed(title=f"{region_flag(region)} {region} — Statistics",color=region_color(region))
    e.add_field(name="Ranked Players",   value=len(ranking),                          inline=True)
    e.add_field(name="Players w/ History",value=len(records),                         inline=True)
    e.add_field(name="Total Fights",     value=sum(len(v) for v in records.values()), inline=True)
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
    @ui.button(label="📜 History",      style=discord.ButtonStyle.secondary,custom_id="pl_history", row=0)
    async def history(self,i,b): await i.response.send_modal(HistoryModal())
    @ui.button(label="📊 Stats",        style=discord.ButtonStyle.secondary,custom_id="pl_stats",   row=1)
    async def stats(self,i,b): await safe_edit(i,embed=discord.Embed(title="📊 Stats — Select Region",color=PANEL_COLOR),view=RegionView("stats"))
    @ui.button(label="🏅 Top Rankings", style=discord.ButtonStyle.danger,   custom_id="pl_top",     row=1)
    async def top(self,i,b): await safe_edit(i,embed=discord.Embed(title="🏅 Top Rankings",color=PANEL_COLOR),view=TopView())
    @ui.button(label="🔄 Refresh",      style=discord.ButtonStyle.secondary,custom_id="pl_refresh", row=1)
    async def refresh(self,i,b):
        clear_cache()
        await safe_edit(i,embed=discord.Embed(title="🔄 Cache Cleared",description="✅ Next request reloads the PDF.",color=0x00FF88),view=BackView())

class RegionView(ui.View):
    def __init__(self,mode): super().__init__(timeout=None); self.mode=mode
    @ui.button(label="🇪🇺 EU",   style=discord.ButtonStyle.primary,  custom_id="rv_eu",    row=0)
    async def eu(self,i,b): await self._go(i,"EU")
    @ui.button(label="🇺🇸 NA",   style=discord.ButtonStyle.danger,   custom_id="rv_na",    row=0)
    async def na(self,i,b): await self._go(i,"NA")
    @ui.button(label="🌎 SA",    style=discord.ButtonStyle.success,  custom_id="rv_sa",    row=0)
    async def sa(self,i,b): await self._go(i,"SA")
    @ui.button(label="🌍 Global",style=discord.ButtonStyle.secondary,custom_id="rv_global",row=0)
    async def glb(self,i,b): await self._go(i,"Global")
    @ui.button(label="🔙 Back",  style=discord.ButtonStyle.secondary,custom_id="rv_back",  row=1)
    async def back(self,i,b): await safe_edit(i,embed=build_main_embed(),view=MainPanel())
    async def _go(self,i,region):
        if not await safe_defer(i): return
        data,_=await get_data(); reg=data.get(region,{})
        if self.mode=="ranking":
            view=RankView(region,data); embed=build_ranking_embed(region,reg.get("ranking",[]),reg.get("unranked",{}))
        else:
            view=StatsView(region,data); embed=build_stats_embed(region,reg)
        try: await i.edit_original_response(embed=embed,view=view)
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
    @ui.button(label="🔙 Back",style=discord.ButtonStyle.secondary,custom_id="rv_back",row=1)
    async def back(self,i,b): await safe_edit(i,embed=discord.Embed(title="🌍 Rankings — Select Region",color=PANEL_COLOR),view=RegionView("ranking"))
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
    def __init__(self,dn,fights,page,back_region="EU"):
        super().__init__(timeout=300)
        self.dn=dn; self.fights=fights; self.page=page; self.back_region=back_region
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
    @ui.button(label="📜 History",style=discord.ButtonStyle.primary,custom_id="hn_label",row=0,disabled=True)
    async def btn_label(self,i,b): pass
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

class PlayerModal(ui.Modal,title="🔍 Player Lookup"):
    name=ui.TextInput(label="Player name",placeholder="e.g. NLG, Jab, Larry, Weewarrior...",required=True)
    async def on_submit(self,i):
        await i.response.defer(ephemeral=False)
        data,_=await get_data(); q=self.name.value.lower().strip()
        dn,fights,entry,ereg=collect_fights(q,data)
        if not entry and not fights:
            return await i.followup.send(f"❌ Player **{self.name.value}** not found.")
        if not entry:
            ereg=Counter(r for r,_ in fights).most_common(1)[0][0] if fights else "EU"
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

@bot.event
async def on_ready():
    print(f"✅ PL Bot ONLINE as {bot.user}!")
    # Registra views persistentes para sobreviver a reinicializações
    bot.add_view(MainPanel())
    bot.add_view(RegionView("ranking"))
    bot.add_view(RegionView("stats"))
    bot.add_view(TopView())
    asyncio.create_task(_preload())

async def _preload():
    await asyncio.sleep(2)
    print("[PL Bot] Pre-loading PDF data...")
    await get_data()
    print("[PL Bot] Ready!")

@bot.event
async def on_member_join(member):
    if not WELCOME_CHANNEL_ID: return
    ch=bot.get_channel(WELCOME_CHANNEL_ID)
    if not ch: return
    e=discord.Embed(title="🏁 NEW COMPETITOR IN THE PRO LEAGUE!",
        description=f"**Welcome to the DW2 Pro League, {member.mention}!**\n\nThe Pro League is the official DW2 tournament with rankings across EU, NA, SA and Global.\n\n🏆 Check rankings with `!panel`\n📋 Register in the sign-up channel to compete.",
        color=PANEL_COLOR)
    e.set_thumbnail(url=member.display_avatar.url)
    await ch.send(embed=e)

@bot.command(name="panel",aliases=["painel"])
async def cmd_panel(ctx): await ctx.send(embed=build_main_embed(),view=MainPanel())

@bot.command(name="ranking")
async def cmd_ranking(ctx,region:str=None):
    if not region or region.upper() not in [r.upper() for r in REGIONS]:
        await ctx.send("Usage: `!ranking <EU|NA|SA|Global>`",view=RegionView("ranking")); return
    rk=next(r for r in REGIONS if r.upper()==region.upper())
    data,_=await get_data(); reg=data.get(rk,{})
    await ctx.send(embed=build_ranking_embed(rk,reg.get("ranking",[]),reg.get("unranked",{})),view=RankView(rk,data))

@bot.command(name="player",aliases=["jogador"])
async def cmd_player(ctx,*,name:str=None):
    if not name: return await ctx.send("Usage: `!player <n>`")
    data,_=await get_data(); dn,fights,entry,ereg=collect_fights(name.lower(),data)
    if not entry and not fights: return await ctx.send(f"❌ Player **{name}** not found.")
    if not entry:
        ereg=Counter(r for r,_ in fights).most_common(1)[0][0] if fights else "EU"
        entry={"pos":"—","wins":sum(1 for _,f in fights if f["result"].lower()=="win"),
               "losses":sum(1 for _,f in fights if f["result"].lower()=="loss"),"mp":len(fights),"affiliation":""}
    await ctx.send(embed=build_player_embed(entry,ereg,fights,dn),view=HistNavView(dn,fights,0,back_region=ereg))

@bot.command(name="history",aliases=["historico","hist"])
async def cmd_history(ctx,*,name:str=None):
    if not name: return await ctx.send("Usage: `!history <n>`")
    data,_=await get_data(); dn,fights,_,_=collect_fights(name.lower(),data)
    if not fights: return await ctx.send(f"❌ Player **{name}** not found.")
    await ctx.send(embed=build_history_page(dn,fights,0),view=HistNavView(dn,fights,0))

@bot.command(name="stats",aliases=["estatisticas"])
async def cmd_stats(ctx,region:str=None):
    if not region or region.upper() not in [r.upper() for r in REGIONS]:
        return await ctx.send("Usage: `!stats <EU|NA|SA|Global>`")
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
async def cmd_refresh(ctx): clear_cache(); await ctx.send("🔄 Cache cleared! Next request reloads the PDF.")

@bot.command(name="help",aliases=["ajuda"])
async def cmd_help(ctx):
    e=discord.Embed(title="📜 PL Bot — Commands",description="Use **`!panel`** for the full interactive panel.",color=PANEL_COLOR)
    e.add_field(name="!panel",value="Interactive panel with buttons",inline=False)
    e.add_field(name="!ranking <EU|NA|SA|Global>",value="Region top 10",inline=False)
    e.add_field(name="!player <n>",value="Player card. e.g. `!player NLG`",inline=False)
    e.add_field(name="!history <n>",value="Fight log. e.g. `!history Jab`",inline=False)
    e.add_field(name="!stats <EU|NA|SA|Global>",value="Region statistics",inline=False)
    e.add_field(name="!top [wins|wr|mp]",value="`wins` | `wr` | `mp`",inline=False)
    e.add_field(name="!refresh",value="Reload PDF",inline=False)
    await ctx.send(embed=e)

# Abre o bot_pl.py e muda a linha 672 para:
TOKEN = os.environ.get("DISCORD_TOKEN", "")