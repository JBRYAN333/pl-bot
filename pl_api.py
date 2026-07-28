#!/usr/bin/env python3
"""PL API — REST server that reads pl_records.json and serves data for PL Dashboard.
   Zero modifications to bot_pl.py."""

import os, sys, json, asyncio, re, hashlib
from aiohttp import web

JSON_PATH = "pl_records.json"

def _load():
    with open(JSON_PATH, encoding="utf-8") as f:
        return json.load(f)

def _build_players(raw):
    seen, players = set(), {}
    for region, reg in raw.items():
        for p in reg.get("ranking", []):
            k = p["player"].lower()
            if k not in seen:
                seen.add(k)
                players[k] = {"name": p["player"], "primaryRegion": region, "regions": {}, "affiliation": p.get("affiliation", ""), "totalWins": 0, "totalLosses": 0, "totalMP": 0}
            players[k]["regions"][region] = {"pos": p["pos"], "wins": p["wins"], "losses": p["losses"], "mp": p["mp"], "aff": p.get("affiliation", "")}
            players[k]["totalWins"] += p["wins"]
            players[k]["totalLosses"] += p["losses"]
            players[k]["totalMP"] += p["mp"]
        for pname in reg.get("records", {}):
            k = pname.lower()
            if k not in seen:
                seen.add(k)
                players[k] = {"name": pname, "primaryRegion": region, "regions": {}, "affiliation": "", "totalWins": 0, "totalLosses": 0, "totalMP": 0}
    for p in players.values():
        p["regions"] = json.dumps(p["regions"])
    return list(players.values())

def _build_matches(raw):
    matches, seen = [], set()
    re_fotn = re.compile(r'fight\s+of\s+the\s+\w+|fight\s+of\s+night|FOTN|\bFON\b', re.I)
    re_title = re.compile(r'\b(championship|title|defended|won\s+the)\b', re.I)
    for region, reg in raw.items():
        for pname, fights in reg.get("records", {}).items():
            for f in fights:
                key = (pname, f.get("opponent",""), f.get("score",""), f.get("event",""))
                if key in seen: continue
                seen.add(key)
                p1, p2 = sorted([pname, f.get("opponent","")])
                notes = f.get("notes", "") or ""
                matches.append({
                    "player1": p1, "player2": p2, "score": f.get("score",""),
                    "rounds": "", "region": region, "event": f.get("event",""),
                    "vod": f.get("vod",""), "notes": notes,
                    "fotn": bool(re_fotn.search(notes)),
                    "titleFight": bool(re_title.search(notes)),
                    "forfeit": "ff" in notes.lower() or "forfeit" in notes.lower(),
                    "createdAt": 0
                })
    return matches

def _build_events(raw):
    events, seen = [], set()
    for region, reg in raw.items():
        for pname, fights in reg.get("records", {}).items():
            for f in fights:
                ev = f.get("event","").strip()
                if not ev or ev.lower() in seen: continue
                seen.add(ev.lower())
                events.append({"name": ev, "region": region, "isTournament": "tournament" in ev.lower(), "completed": True, "createdAt": 0})
    return events

async def _get_data():
    raw = await asyncio.get_event_loop().run_in_executor(None, _load)
    return raw.get("data", {})

async def handle_players(request):
    data = await _get_data()
    players = await asyncio.get_event_loop().run_in_executor(None, _build_players, data)
    return web.json_response(players)

async def handle_matches(request):
    data = await _get_data()
    matches = await asyncio.get_event_loop().run_in_executor(None, _build_matches, data)
    return web.json_response(matches)

async def handle_events(request):
    data = await _get_data()
    events = await asyncio.get_event_loop().run_in_executor(None, _build_events, data)
    return web.json_response(events)

async def handle_regions(request):
    data = await _get_data()
    return web.json_response(list(data.keys()))

async def handle_player(request):
    name = request.match_info.get("name","").lower()
    data = await _get_data()
    players = await asyncio.get_event_loop().run_in_executor(None, _build_players, data)
    for p in players:
        if p["name"].lower() == name:
            return web.json_response(p)
    return web.json_response({"error":"not found"}, status=404)

async def handle_health(request):
    exists = os.path.exists(JSON_PATH)
    return web.json_response({"status":"ok","json_exists":exists,"json_size":os.path.getsize(JSON_PATH) if exists else 0})

app = web.Application()

async def cors_middleware(request, handler):
    if request.method == "OPTIONS":
        return web.Response(headers={"Access-Control-Allow-Origin":"*","Access-Control-Allow-Methods":"GET,POST,OPTIONS","Access-Control-Allow-Headers":"Content-Type,Authorization"})
    resp = await handler(request)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp

app.middlewares.append(cors_middleware)
app.router.add_get("/api/players", handle_players)
app.router.add_get("/api/players/{name}", handle_player)
app.router.add_get("/api/matches", handle_matches)
app.router.add_get("/api/events", handle_events)
app.router.add_get("/api/regions", handle_regions)
app.router.add_get("/api/health", handle_health)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"[PL API] Starting on port {port}...", flush=True)
    web.run_app(app, host="0.0.0.0", port=port, print=lambda *a: None)