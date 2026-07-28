#!/usr/bin/env python3
"""PL API — lightweight HTTP server that reads pl_records.json and serves REST API.
   Runs alongside the Discord bot. Zero modifications to bot_pl.py."""

import os, sys, json, asyncio, re, hashlib
from aiohttp import web

JSON_PATH = "pl_records.json"
_lock = asyncio.Lock()

def _load():
    with open(JSON_PATH, encoding="utf-8") as f:
        return json.load(f)

def _build_players(raw):
    seen, players = set(), []
    regions_map = {}
    for region, reg in raw.items():
        for p in reg.get("ranking", []):
            key = p["player"].lower()
            if key not in seen:
                seen.add(key)
                regions_map[key] = {}
            regions_map[key][region] = {
                "pos": p["pos"], "wins": p["wins"], "losses": p["losses"],
                "mp": p["mp"], "aff": p.get("affiliation", "")
            }
        for pname in reg.get("records", {}):
            key = pname.lower()
            if key not in seen:
                seen.add(key)
                regions_map[key] = {}
    # Build player objects
    for region, reg in raw.items():
        for p in reg.get("ranking", []):
            key = p["player"].lower()
            if key not in seen: continue
            seen.discard(key)
            rm = regions_map.get(key, {})
            rj = json.dumps(rm)
            players.append({
                "name": p["player"],
                "primaryRegion": region,
                "regions": rj,
                "affiliation": p.get("affiliation", ""),
                "totalWins": sum(r["wins"] for r in rm.values()),
                "totalLosses": sum(r["losses"] for r in rm.values()),
                "totalMP": sum(r["mp"] for r in rm.values()),
                "createdAt": 0
            })
        for pname in reg.get("records", {}):
            key = pname.lower()
            if key not in seen: continue
            seen.discard(key)
            rm = regions_map.get(key, {})
            rj = json.dumps(rm)
            players.append({
                "name": pname,
                "primaryRegion": region,
                "regions": rj,
                "affiliation": "",
                "totalWins": 0,
                "totalLosses": 0,
                "totalMP": 0,
                "createdAt": 0
            })
    return players

def _build_matches(raw):
    matches = []
    re_fotn = re.compile(r'fight\s+of\s+the\s+\w+|fight\s+of\s+night|FOTN|\bFON\b', re.I)
    re_title = re.compile(r'\b(championship|title|defended|won\s+the)\b', re.I)
    seen = set()
    for region, reg in raw.items():
        for pname, fights in reg.get("records", {}).items():
            for f in fights:
                key = (pname, f.get("opponent",""), f.get("score",""), f.get("event",""))
                if key in seen: continue
                seen.add(key)
                p1, p2 = sorted([pname, f.get("opponent","")])
                notes = f.get("notes", "") or ""
                matches.append({
                    "player1": p1,
                    "player2": p2,
                    "score": f.get("score", ""),
                    "rounds": "",
                    "region": region,
                    "event": f.get("event", ""),
                    "vod": f.get("vod", ""),
                    "notes": notes,
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
                ev = f.get("event", "").strip()
                if not ev or ev.lower() in seen: continue
                seen.add(ev.lower())
                is_tour = "tournament" in ev.lower()
                events.append({
                    "name": ev,
                    "region": region,
                    "isTournament": is_tour,
                    "completed": True,
                    "createdAt": 0
                })
    return events

async def _get_data():
    async with _lock:
        raw = await asyncio.get_event_loop().run_in_executor(None, _load)
    data = raw.get("data", {})
    return data

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
    regions = list(data.keys())
    return web.json_response(regions)

async def handle_player(request):
    name = request.match_info.get("name", "").lower()
    data = await _get_data()
    players = await asyncio.get_event_loop().run_in_executor(None, _build_players, data)
    for p in players:
        if p["name"].lower() == name:
            return web.json_response(p)
    return web.json_response({"error": "not found"}, status=404)

async def handle_health(request):
    exists = os.path.exists(JSON_PATH)
    return web.json_response({"status": "ok", "json_exists": exists, "json_size": os.path.getsize(JSON_PATH) if exists else 0})

async def handle_cors(request):
    return web.Response(text="OK")

def build_app():
    app = web.Application()
    cors_headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization"
    }
    
    async def cors_middleware(request, handler):
        if request.method == "OPTIONS":
            return web.Response(headers=cors_headers)
        try:
            resp = await handler(request)
            for k, v in cors_headers.items():
                resp.headers[k] = v
            return resp
        except web.HTTPException as exc:
            for k, v in cors_headers.items():
                exc.headers[k] = v
            raise
    
    app.middlewares.append(cors_middleware)
    
    app.router.add_get("/api/players", handle_players)
    app.router.add_get("/api/players/{name}", handle_player)
    app.router.add_get("/api/matches", handle_matches)
    app.router.add_get("/api/events", handle_events)
    app.router.add_get("/api/regions", handle_regions)
    app.router.add_get("/api/health", handle_health)
    app.router.add_route("*", "/api/{tail:.*}", handle_cors)
    
    return app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app = build_app()
    print(f"[PL API] Starting on port {port}...", flush=True)
    web.run_app(app, host="0.0.0.0", port=port, print=lambda *a: None)