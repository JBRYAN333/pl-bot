#!/usr/bin/env python3
"""Subprocess worker: download PDF, parse with pdfplumber, write JSON to stdout.
   Called by PL Bot during !refresh. Dies after output, freeing all memory."""

import sys, io, re, json, urllib.request
from collections import Counter

DOC_ID = "1fYokf-Tbj1NgZa1fukSFH7snGgP1xqYOyUVPd2EkRHQ"

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
    return raw.upper() if len(raw) <= 3 else raw.capitalize()

def parse(pdf_bytes: bytes) -> tuple[dict, dict, list[str]]:
    import pdfplumber
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
    regions = ordered if ordered else ["EU", "NA", "SA", "AS", "Global"]
    print(f"Regions detected: {regions}", flush=True)

    res = {r: {"ranking": [], "unranked": {}, "records": {}} for r in regions}
    cr = cs = cp = None
    th = []
    pending_name = None
    last_entry = None
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
                region_toks = "|".join(regions)
                for tok in toks[idx + 2:]:
                    if ep and not re.match(rf'^(DW2PL|{region_toks}|Fight|Night|Tournament|Global|Part|#\d+|\d+)$', tok, re.I):
                        in_n = True
                    (np_ if in_n else ep).append(tok)
                ev = " ".join(ep); nt = " ".join(np_)
                vm = re_ev.search(ev)
                vod = vod_map.get(re.sub(r"\s+", " ", vm.group(1).strip()), "") if vm else ""
                reg["records"][cp].append({"result": rv, "record": rec, "opponent": opp, "score": sc, "event": ev, "notes": nt, "vod": vod})
                last_entry = reg["records"][cp][-1]
                continue
            if last_entry is not None and not line.startswith('#'):
                cand_note = line.strip()
                if (4 <= len(cand_note) <= 80 and not re.search(r'\d{4,}', cand_note)
                        and not _SKIP.search(cand_note)
                        and not re.match(r'^(Res\.?|Record|Opponent|Score|Event|Notes)', cand_note, re.I)):
                    words = cand_note.split()
                    if len(words) >= 3 or any(kw in cand_note.lower() for kw in ['won', 'championship', 'title', 'eliminator', 'fotn', 'fight of the']):
                        existing = last_entry.get("notes", "") or ""
                        if existing and cand_note not in existing:
                            last_entry["notes"] = existing + " " + cand_note
                        elif not existing:
                            last_entry["notes"] = cand_note
                        last_entry = None
                        continue
            has_g = bool(re.search(r'\(G\)\s*$', line))
            cand  = re.sub(r'\s*\(G\)\s*$', '', line).strip()
            is_c  = False
            skip_upper = {r.upper() for r in regions} | {"UNRANKED","TOP 10:","TOP 15:","TIER 1","TIER 2","TIER 3"}
            if has_g and 1 <= len(cand) <= 35 and not _SKIP.search(cand):
                is_c = True
            elif (2 <= len(cand) <= 35 and not re.search(r'\d{4,}', cand)
                    and not re.match(r'^[#\d\-]', cand) and len(cand.split()) <= 4
                    and cand.upper() not in skip_upper
                    and not _SKIP.search(cand)):
                is_c = True
            pending_name = cand if is_c else None
            if pending_name is not None:
                last_entry = None
    return res, vod_map, regions

def main():
    print("parse_pdf: downloading...", flush=True)
    url = f"https://docs.google.com/document/d/{DOC_ID}/export?format=pdf"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=60) as r:
        pdf_bytes = r.read()
    print(f"parse_pdf: downloaded {len(pdf_bytes)//1024} KB. parsing...", flush=True)
    data, vods, regions = parse(pdf_bytes)
    print(f"parse_pdf: done. VODs: {len(vods)}", flush=True)
    for r in regions:
        reg = data.get(r, {})
        print(f"parse_pdf: {r}: {len(reg.get('ranking',[]))} ranked, {len(reg.get('records',{}))} with history", flush=True)
    json.dump({"data": data, "vods": vods, "regions": regions}, sys.stdout,
              ensure_ascii=False, separators=(',', ':'))
    sys.stdout.flush()

if __name__ == "__main__":
    main()