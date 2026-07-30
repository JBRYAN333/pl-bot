"""Script para gerar pl_records.json sem rodar o bot do Discord."""
import os, sys, json, re, io, urllib.request, urllib.parse
from collections import Counter

# Copia as funcoes necessarias do bot_pl.py
GOOGLE_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
GOOGLE_REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN")
DOC_ID               = "1fYokf-Tbj1NgZa1fukSFH7snGgP1xqYOyUVPd2EkRHQ"
JSON_PATH            = "pl_records.json"

# Copia as regex e funcoes de parsing do bot_pl.py
import pdfplumber

_RE_SR = re.compile(r'^([A-Za-z]{2,8})\s+Rankings?$')
_RE_SC = re.compile(r'^([A-Za-z]{2,8})\s+Records?$')
_RE_RR = re.compile(r'^(Win|Loss|Draw|NC|WIn)\b')
_RE_RF = re.compile(r'^\d+-\d+$')
_RE_TH = re.compile(r'^Tier \d')
_RE_NR = re.compile(r'^(.+?)\s*\((\d+)-(\d+)\)$')
_RE_CT = re.compile(r'[\u200b\u00a0\u200c\u200d\u2060\ufeff\u202f\xa0]')
_RE_EV = re.compile(r'(DW2PL\s+(?:Fight\s+Night\s+\|[A-Za-z]{2,8}\s+Tournament\s+\|Global\s+Part\s+\d+\s*)?#?\d+)')
_RE_HDR = re.compile(r'\bRes\.?\s+Record\b|\bOpponent\b.*\bScore\b')
_SKIP = re.compile(
    r'Non-Tournament|Qualifiers?|Prelims?|VOD Link'
    r'|Round \d+|Losers|Winners|Bracket|Finals?|Exhibitions?'
    r'|Inactive|^\s*(Top \d+|DW2PL|Rules?|Lag Rule|Inter-Regional|Same.region)\b',
    re.I
)
REGIONS = ['EU', 'NA', 'SA', 'AS', 'Global']
skip_upper = {r.upper() for r in REGIONS} | {'UNRANKED', 'TOP 10:', 'TOP 15:', 'TIER 1', 'TIER 2', 'TIER 3'}

def _get_access_token():
    data = urllib.parse.urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": GOOGLE_REFRESH_TOKEN,
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())["access_token"]

def _download_pdf_bytes():
    token = _get_access_token()
    url = f"https://docs.google.com/document/d/{DOC_ID}/export?format=pdf"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()

def _parse_pdf_bytes(pdf_bytes):
    text_lines = []
    vod_map = {}
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                for line in text.split('\n'):
                    clean = _RE_CT.sub('', line).strip()
                    clean = re.sub(r'\s+', ' ', clean)
                    if clean:
                        text_lines.append(clean)
            for link in page.hyperlinks or []:
                words = page.extract_words()
                for w in words:
                    if abs(w['top'] - link['top']) < 5:
                        txt = w['text'].strip()
                        if txt and not txt.startswith('DW2PL') and link.get('uri'):
                            vod_map[txt] = link['uri']
    res = {}
    current_region = None
    mode = None
    last_tier = None
    for line in text_lines:
        m = _RE_SR.match(line)
        if m:
            r = m.group(1)
            if r in REGIONS:
                current_region = r
                mode = 'ranking'
                if current_region not in res: res[current_region] = {'ranking': [], 'unranked': {}, 'records': {}}
            continue
        m = _RE_SC.match(line)
        if m:
            r = m.group(1)
            if r in REGIONS:
                current_region = r
                mode = 'records'
                if current_region not in res: res[current_region] = {'ranking': [], 'unranked': {}, 'records': {}}
            continue
        if not current_region or current_region not in res:
            continue
        if _SKIP.search(line):
            continue
        if mode == 'ranking':
            if _RE_TH.match(line):
                mode = 'unranked'
                last_tier = line
                continue
            if 'Top ' in line or line in ('Position', 'Nation', 'Player', 'MP', 'Wins', 'Affiliation'):
                continue
            m = _RE_NR.match(line)
            if m:
                name, w, l = m.groups()
                name = name.strip()
                pos = str(len(res[current_region]['ranking']) + 1)
                res[current_region]['ranking'].append({'pos': pos, 'player': name, 'mp': int(w)+int(l), 'wins': int(w), 'losses': int(l), 'affiliation': ''})
                continue
        elif mode == 'unranked':
            if _RE_TH.match(line):
                last_tier = line
                continue
            if _RE_SR.match(line) or _RE_SC.match(line):
                mode = 'ranking'
                continue
            m = _RE_NR.match(line)
            if m:
                name, w, l = m.groups()
                name = name.strip()
                tier_key = last_tier or 'Tier ?'
                if tier_key not in res[current_region]['unranked']: res[current_region]['unranked'][tier_key] = []
                res[current_region]['unranked'][tier_key].append(f"{name} ({w}-{l})")
                continue
            parts = line.split()
            if len(parts) >= 2:
                name = parts[0]
                rest = ' '.join(parts[1:])
                m2 = re.match(r'\((\d+)-(\d+)\)', rest)
                if m2:
                    tier_key = last_tier or 'Tier ?'
                    if tier_key not in res[current_region]['unranked']: res[current_region]['unranked'][tier_key] = []
                    res[current_region]['unranked'][tier_key].append(f"{name} ({m2.group(1)}-{m2.group(2)})")
                    continue
        elif mode == 'records':
            if _RE_SR.match(line) or _RE_SC.match(line):
                mode = 'ranking'
                continue
            if _RE_HDR.search(line):
                continue
            parts = line.split()
            if len(parts) < 4:
                continue
            result = parts[0]
            if not _RE_RR.match(result):
                result = ''
                rec = ''
                opp = parts[0]
                score = ''
                event = ''
                notes_parts = []
                j = 1
            else:
                rec = parts[1] if len(parts) > 1 else ''
                opp = parts[2] if len(parts) > 2 else ''
                score = parts[3] if len(parts) > 3 else ''
                event = parts[4] if len(parts) > 4 else ''
                notes_parts = list(parts[5:]) if len(parts) > 5 else []
                line_rest = ' '.join(parts[4:])
                evm = _RE_EV.search(line_rest)
                if evm:
                    event = evm.group(0)
                    notes_parts = line_rest[evm.end():].strip().split()
            if not opp or opp.upper() == 'VACANT' or len(opp) > 35:
                continue
            notes = ' '.join(notes_parts) if notes_parts else ''
            vod = ''
            if event in vod_map:
                vod = vod_map[event]
            rec_key = '_current_record_'
            if re.match(r'^\d+-\d+$', rec) and rec.count('-') == 1:
                rec_key = rec
            entry = {'result': result, 'record': rec_key, 'opponent': opp, 'score': score, 'event': event, 'notes': notes, 'vod': vod}
            # Find current player - use the last one being processed
            all_players = []
            for r in res:
                if 'records' in res[r]:
                    all_players.extend(res[r]['records'].keys())
            if all_players:
                last_player = all_players[-1]
                res[current_region]['records'].setdefault(last_player, []).append(entry)
    return res, vod_map

def _rebuild_json():
    print("Downloading PDF from Google Doc...")
    pdf = _download_pdf_bytes()
    print(f"Downloaded {len(pdf)} bytes. Parsing...")
    data, vods = _parse_pdf_bytes(pdf)
    out = {"data": data, "vods": vods, "regions": list(data.keys())}
    with open(JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"Saved {JSON_PATH}")
    total = sum(len(data[r].get('records', {})) for r in data)
    total_m = sum(len(v) for r in data for v in data[r].get('records', {}).values())
    print(f"Regions: {list(data.keys())}")
    for r in data:
        print(f"  {r}: {len(data[r].get('ranking', []))} ranked, {len(data[r].get('records', {}))} players with records")
    print(f"Total players with records: {total}")
    print(f"Total matches: {total_m}")
    return data, vods

if __name__ == '__main__':
    _rebuild_json()
