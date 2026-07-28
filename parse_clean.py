"""
Parseia o PDF local usando a logica EXATA do bot_pl.py
"""
import re, json, io, pdfplumber, os

DOC_ID   = "1fYokf-Tbj1NgZa1fukSFH7snGgP1xqYOyUVPd2EkRHQ"
JSON_PATH = "pl_records.json"

_RE_SR  = re.compile(r'^([A-Za-z]{2,8})\s+Rankings?$', re.I)
_RE_SC  = re.compile(r'^([A-Za-z]{2,8})\s+Records?$',  re.I)
_RE_RR  = re.compile(r'^(Win|Loss|Draw|NC|WIn)\b', re.I)
_RE_RF  = re.compile(r'^\d+-\d+$')
_RE_TH  = re.compile(r'^Tier \d', re.I)
_RE_NR  = re.compile(r'^(.+?)\s*\((\d+)-(\d+)\)$')
_RE_CT  = re.compile(r'[\u200b\u00a0\u200c\u200d\u2060\ufeff\u202f\xa0]')
_RE_EV  = re.compile(r'(DW2PL\s+(?:Fight\s+Night\s+|[A-Za-z]{2,8}\s+Tournament\s+|Global\s+Part\s+\d+\s*)?#?\d+(?:\.\d+)?)', re.I)
_RE_HDR = re.compile(r'\bRes\.?\s+Record\b|\bOpponent\b.*\bScore\b', re.I)
_SKIP   = re.compile(
    r'Non-Tournament|Qualifiers?|Prelims?|VOD Link|'
    r'Round \d+|Losers|Winners|Bracket|'
    r'Finals?|Exhibitions?|Inactive|'
    r'^\s*(Top \d+|DW2PL|Rules?|Lag Rule|Inter-Regional|Same.region)\b', re.I
)

def _canonical(raw: str) -> str:
    return raw.upper() if len(raw) <= 3 else raw.capitalize()

def parse_pdf(pdf_bytes: bytes) -> tuple[dict, dict]:
    re_ev = re.compile(
        r'DW2PL\s+(?:Fight\s+Night\s+|[A-Za-z]{2,8}\s+Tournament\s+|Global\s+Part\s+\d+\s*)?#?\d+(?:\.\d+)?', re.I)
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

    seen = {}
    for line in lines:
        m = _RE_SR.match(line) or _RE_SC.match(line)
        if m:
            key = m.group(1).lower()
            if key not in seen:
                seen[key] = _canonical(m.group(1))
    ordered = [v for k, v in seen.items() if k != "global"]
    if "global" in seen:
        ordered.append(seen["global"])
    REGIONS = ordered if ordered else ["EU", "NA", "SA", "AS", "Global"]
    print(f"Regions detected: {REGIONS}")

    res = {r: {"ranking": [], "unranked": {}, "records": {}} for r in REGIONS}
    cr = cs = cp = None
    th = []
    pending_name = None
    last_match = None  # tracks (region_key, player_name, match_index) for standalone FOTN

    for line in lines:
        m = _RE_SR.match(line)
        if m:
            c = _canonical(m.group(1))
            if c not in res:
                continue
            cr = c
            cs = "ranking"
            th = []
            cp = None
            pending_name = None
            last_match = None
            continue
        m = _RE_SC.match(line)
        if m:
            c = _canonical(m.group(1))
            if c not in res:
                continue
            cr = c
            cs = "records"
            cp = None
            pending_name = None
            last_match = None
            continue
        if not cr:
            continue
        reg = res[cr]
        if cs == "ranking":
            if re.match(r'^Unranked$', line, re.I):
                cs = "unranked"
                th = []
                continue
            if re.search(r'Top \d+|^Position\b|^Nation\b|^Player\b|\bMP\b.*Wins|Affiliation', line, re.I):
                continue
            toks = line.split()
            if not toks:
                continue
            f = toks[0]
            if not re.match(r'^(Champion|#\d+)$', f, re.I):
                continue
            ni = [i for i, t in enumerate(toks) if re.match(r'^\d+$', t)]
            if len(ni) < 3:
                continue
            im, iw, il = ni[-3], ni[-2], ni[-1]
            mp, w, l = int(toks[im]), int(toks[iw]), int(toks[il])
            aff = toks[il + 1] if il + 1 < len(toks) else ""
            player = " ".join(toks[1:im]).strip()
            pos = "Champion" if f.lower() == "champion" else f.lstrip("#")
            if player and player.upper() not in ("VACANT", "N/A", ""):
                reg["ranking"].append({
                    "pos": pos, "player": player, "mp": mp,
                    "wins": w, "losses": l, "affiliation": aff
                })
        elif cs == "unranked":
            if _RE_TH.match(line):
                th = re.findall(r'Tier \d+', line, re.I)
                for t in th:
                    reg["unranked"].setdefault(t, [])
                continue
            if not th:
                continue
            ms = _RE_NR.findall(line)
            if not ms:
                for part in re.findall(r'\S+\s*\(\d+-\d+\)', line):
                    mm = _RE_NR.match(part.strip())
                    if mm:
                        ms.append((mm.group(1).strip(), mm.group(2), mm.group(3)))
            for idx, (nm, ww, ll) in enumerate(ms):
                key = th[idx] if idx < len(th) else th[-1]
                reg["unranked"][key].append(f"{nm.strip()} ({ww}-{ll})")
        elif cs == "records":
            if re.match(r'^Inactive:?\s*$', line, re.I):
                cs = None
                last_match = None
                continue
            if _RE_HDR.search(line):
                if pending_name is not None:
                    cp = pending_name
                    if cp not in reg["records"]:
                        reg["records"][cp] = []
                pending_name = None
                continue
            # ── Detect standalone "Fight of the Night" → mark last match ──
            fotn_standalone = re.match(r'^Fight\s+of\s+the\s+Night\s*$', line, re.I)
            if fotn_standalone and last_match:
                rk, rp, mi = last_match
                res[rk]["records"][rp][mi]["notes"] = (
                    (res[rk]["records"][rp][mi]["notes"] + " ") if res[rk]["records"][rp][mi]["notes"] else ""
                ) + "Fight of the Night"
                continue
            if _RE_RR.match(line):
                pending_name = None
                if not cp:
                    continue
                toks = line.split()
                if len(toks) < 3:
                    continue
                rv = toks[0]
                idx = 1
                rec = toks[idx] if _RE_RF.match(toks[idx]) else ""
                if rec:
                    idx += 1
                opp = toks[idx] if idx < len(toks) else ""
                sc = toks[idx + 1] if idx + 1 < len(toks) else ""
                ep, np_ = [], []
                in_n = False
                region_toks = "|".join(REGIONS)
                for tok in toks[idx + 2:]:
                    if ep and not re.match(
                        rf'^(DW2PL|{region_toks}|Fight|Night|Tournament|Global|Part|#\d+(?:\.\d+)?|\d+)$', tok, re.I
                    ):
                        in_n = True
                    (np_ if in_n else ep).append(tok)
                ev = " ".join(ep)
                nt = " ".join(np_)
                # ── Fix "Fight of the Night" split across event/notes ──
                ntc = nt.strip().lower()
                if ev.endswith(" Fight") and ntc.startswith("of the night"):
                    ev = ev[:-6].rstrip()
                    nt = "Fight " + nt.strip()
                vm = _RE_EV.search(ev)
                vod = vod_map.get(re.sub(r"\s+", " ", vm.group(1).strip()), "") if vm else ""
                reg["records"][cp].append({
                    "result": rv, "record": rec, "opponent": opp,
                    "score": sc, "event": ev, "notes": nt, "vod": vod
                })
                last_match = (cr, cp, len(reg["records"][cp]) - 1)
                continue
            has_g = bool(re.search(r'\(G\)\s*$', line))
            cand = re.sub(r'\s*\(G\)\s*$', '', line).strip()
            is_c = False
            skip_upper = {r.upper() for r in REGIONS} | {"UNRANKED", "TOP 10:", "TOP 15:", "TIER 1", "TIER 2", "TIER 3"}
            if has_g and 1 <= len(cand) <= 35 and not _SKIP.search(cand):
                is_c = True
            elif (2 <= len(cand) <= 35 and not re.search(r'\d{4,}', cand)
                  and not re.match(r'^[#\d\-]', cand) and len(cand.split()) <= 4
                  and cand.upper() not in skip_upper
                  and not _SKIP.search(cand)):
                is_c = True
            pending_name = cand if is_c else None

    return res, vod_map


print("Lendo PDF...")
with open("pl_records.pdf", "rb") as f:
    pdf_bytes = f.read()

print(f"Parseando {len(pdf_bytes)} bytes...")
data, vods = parse_pdf(pdf_bytes)

REGIONS = list(data.keys())

out = {"data": data, "vods": vods, "regions": REGIONS}
with open(JSON_PATH, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

size = os.path.getsize(JSON_PATH)
print(f"\nSalvo: {JSON_PATH} ({size//1024} KB)")
print(f"VODs: {len(vods)}")
for r in REGIONS:
    reg = data.get(r, {})
    ranked = len(reg.get("ranking", []))
    records = len(reg.get("records", {}))
    matches = sum(len(v) for v in reg.get("records", {}).values())
    print(f"  {r}: {ranked} ranked, {records} players, {matches} matches")

total_matches = sum(sum(len(v) for v in data[r].get("records", {}).values()) for r in data)
print(f"\nTotal matches: {total_matches}")
