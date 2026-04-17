"""
preprocess.py — Gera pl_records.json a partir do PDF do recordbook.

Rode este script localmente sempre que atualizar o PDF:
    python preprocess.py

O bot lê pl_records.json (236 KB) em vez do PDF (2.8 MB + pdfplumber pesando 600 MB de RAM).
"""
import pdfplumber, re, json, os, sys

PDF_PATH  = "DW2PL_Records.pdf"
JSON_PATH = "pl_records.json"

REGIONS = ["EU","NA","SA","Global"]
_RM     = {"eu":"EU","na":"NA","sa":"SA","global":"Global"}
_RE_SR  = re.compile(r'^(EU|NA|SA|Global) Rankings?$', re.I)
_RE_SC  = re.compile(r'^(EU|NA|SA|Global) Records?$', re.I)
_RE_RR  = re.compile(r'^(Win|Loss|Draw|NC|WIn)\b', re.I)
_RE_RF  = re.compile(r'^\d+-\d+$')
_RE_TH  = re.compile(r'^Tier \d', re.I)
_RE_NR  = re.compile(r'^(.+?)\s*\((\d+)-(\d+)\)$')
_RE_CT  = re.compile(r'[\u200b\u00a0\u200c\u200d\u2060\ufeff\u202f\xa0]')
_RE_EV  = re.compile(
    r'(DW2PL\s+(?:Fight\s+Night\s+|EU\s+Tournament\s+|NA\s+Tournament\s+'
    r'|SA\s+Tournament\s+|Global\s+Tournament\s+|Global\s+Part\s+\d+\s*)?#?\d+)', re.I)
_RE_HDR = re.compile(r'\bRes\.?\s+Record\b|\bOpponent\b.*\bScore\b', re.I)
_SKIP   = re.compile(
    r'Non-Tournament|Fight of the|Qualifiers?|Prelims?|VOD Link|'
    r'Round \d+|Losers|Winners|Bracket|Inaugural|won the|replaced |forfeited|'
    r'Finals?|Exhibitions?|Inactive|'
    r'^\s*(Top \d+|DW2PL|Rules?|Lag Rule|Inter-Regional|Same.region)\b', re.I
)

def read_pdf(path):
    print(f"Reading {path}...")
    text_pages, vod_map = [], {}
    re_ev = re.compile(
        r'DW2PL\s+(?:Fight\s+Night\s+|EU\s+Tournament\s+|NA\s+Tournament\s+'
        r'|SA\s+Tournament\s+|Global\s+Tournament\s+|Global\s+Part\s+\d+\s*)?#?\d+', re.I)
    with pdfplumber.open(path) as pdf:
        total = len(pdf.pages)
        for i, page in enumerate(pdf.pages):
            if i % 20 == 0:
                print(f"  Page {i+1}/{total}...")
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
    return "\n".join(text_pages), vod_map

def parse(text, vod_map):
    print("Parsing...")
    res = {r: {"ranking": [], "unranked": {}, "records": {}} for r in REGIONS}
    lines = [re.sub(r'\s+', ' ', _RE_CT.sub(' ', l).strip())
             for l in text.splitlines() if l.strip()]
    cr = cs = cp = None
    th = []
    pending_name = None

    for line in lines:
        m = _RE_SR.match(line)
        if m:
            cr = _RM[m.group(1).lower()]; cs = "ranking"
            th = []; cp = None; pending_name = None; continue
        m = _RE_SC.match(line)
        if m:
            cr = _RM[m.group(1).lower()]; cs = "records"
            cp = None; pending_name = None; continue
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
                reg["ranking"].append({
                    "pos": pos, "player": player,
                    "mp": mp, "wins": w, "losses": l, "affiliation": aff
                })

        elif cs == "unranked":
            if _RE_TH.match(line):
                th = re.findall(r'Tier \d+', line, re.I)
                for t in th:
                    reg["unranked"].setdefault(t, [])
                continue
            if not th: continue
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
                cs = None; continue
            if _RE_HDR.search(line):
                if pending_name is not None:
                    cp = pending_name
                    if cp not in reg["records"]:
                        reg["records"][cp] = []
                pending_name = None
                continue
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
                for tok in toks[idx + 2:]:
                    if ep and not re.match(
                        r'^(DW2PL|EU|NA|SA|Fight|Night|Tournament|Global|Part|#\d+|\d+)$',
                        tok, re.I
                    ):
                        in_n = True
                    (np_ if in_n else ep).append(tok)
                ev = " ".join(ep)
                nt = " ".join(np_)
                vm = _RE_EV.search(ev)
                vod = vod_map.get(re.sub(r"\s+", " ", vm.group(1).strip()), "") if vm else ""
                reg["records"][cp].append({
                    "result": rv, "record": rec, "opponent": opp,
                    "score": sc, "event": ev, "notes": nt, "vod": vod
                })
                continue

            has_g = bool(re.search(r'\(G\)\s*$', line))
            cand  = re.sub(r'\s*\(G\)\s*$', '', line).strip()
            is_c  = False
            if has_g and 1 <= len(cand) <= 35 and not _SKIP.search(cand):
                is_c = True
            elif (2 <= len(cand) <= 35
                    and not re.search(r'\d{4,}', cand)
                    and not re.match(r'^[#\d\-]', cand)
                    and len(cand.split()) <= 4
                    and cand.upper() not in (
                        "EU", "NA", "SA", "GLOBAL", "UNRANKED",
                        "TOP 10:", "TOP 15:", "TIER 1", "TIER 2", "TIER 3"
                    )
                    and not _SKIP.search(cand)):
                is_c = True
            pending_name = cand if is_c else None

    return res

if __name__ == "__main__":
    if not os.path.exists(PDF_PATH):
        print(f"ERROR: {PDF_PATH} not found in current directory.")
        sys.exit(1)

    text, vods = read_pdf(PDF_PATH)
    data = parse(text, vods)

    output = {"data": data, "vods": vods}
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, separators=(',', ':'))

    size = os.path.getsize(JSON_PATH)
    print(f"\n✅ {JSON_PATH} generated: {size / 1024:.1f} KB")
    print("\nSummary:")
    for r in REGIONS:
        reg  = data[r]
        recs = reg.get("records", {})
        print(f"  {r}: {len(reg.get('ranking',[]))} ranked | "
              f"{len(recs)} players | "
              f"{sum(len(v) for v in recs.values())} fights")
    print(f"\n  VODs mapped: {len(vods)}")
    print("\nNow commit pl_records.json to your repo and remove DW2PL_Records.pdf.")
