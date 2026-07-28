import json, os, re, asyncio
from http.server import HTTPServer, BaseHTTPRequestHandler

JSON_PATH = "pl_records.json"

class Handler(BaseHTTPRequestHandler):
    def _json(self, data):
        b = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b)

    def _load(self):
        with open(JSON_PATH, encoding="utf-8") as f:
            return json.load(f).get("data", {})

    def _players(self, data):
        seen, players = set(), {}
        for region, reg in data.items():
            for p in reg.get("ranking", []):
                k = p["player"].lower()
                if k in seen: continue
                seen.add(k)
                players[k] = {"name":p["player"],"primaryRegion":region,"regions":{},"affiliation":p.get("affiliation",""),"totalWins":0,"totalLosses":0,"totalMP":0}
                players[k]["regions"][region] = {"pos":p["pos"],"wins":p["wins"],"losses":p["losses"],"mp":p["mp"],"aff":p.get("affiliation","")}
                players[k]["totalWins"] += p["wins"]
                players[k]["totalLosses"] += p["losses"]
                players[k]["totalMP"] += p["mp"]
        for p in players.values():
            p["regions"] = json.dumps(p["regions"])
        return list(players.values())

    def _matches(self, data):
        matches, seen = [], set()
        re_fotn = re.compile(r'fight\s+of\s+the\s+\w+|fight\s+of\s+night|FOTN|\bFON\b', re.I)
        re_title = re.compile(r'\b(championship|title|defended|won\s+the)\b', re.I)
        for region, reg in data.items():
            for pname, fights in reg.get("records", {}).items():
                for f in fights:
                    key = (pname, f.get("opponent",""), f.get("score",""), f.get("event",""))
                    if key in seen: continue
                    seen.add(key)
                    p1, p2 = sorted([pname, f.get("opponent","")])
                    notes = f.get("notes", "") or ""
                    matches.append({"player1":p1,"player2":p2,"score":f.get("score",""),"rounds":"","region":region,"event":f.get("event",""),"vod":f.get("vod",""),"notes":notes,"fotn":bool(re_fotn.search(notes)),"titleFight":bool(re_title.search(notes)),"forfeit":"ff" in notes.lower() or "forfeit" in notes.lower(),"createdAt":0})
        return matches

    def _events(self, data):
        events, seen = [], set()
        for region, reg in data.items():
            for pname, fights in reg.get("records", {}).items():
                for f in fights:
                    ev = f.get("event","").strip()
                    if not ev or ev.lower() in seen: continue
                    seen.add(ev.lower())
                    events.append({"name":ev,"region":region,"isTournament":"tournament" in ev.lower(),"completed":True,"createdAt":0})
        return events

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,OPTIONS")
        self.end_headers()

    def do_GET(self):
        try:
            data = self._load()
            if self.path == "/api/players":
                self._json(self._players(data))
            elif self.path.startswith("/api/players/"):
                name = self.path.split("/")[-1].lower()
                for p in self._players(data):
                    if p["name"].lower() == name: return self._json(p)
                self.send_response(404); self._json({"error":"not found"})
            elif self.path == "/api/matches":
                self._json(self._matches(data))
            elif self.path == "/api/events":
                self._json(self._events(data))
            elif self.path == "/api/regions":
                self._json(list(data.keys()))
            elif self.path == "/api/health":
                self._json({"status":"ok","json_size":os.path.getsize(JSON_PATH)})
            else:
                self.send_response(404); self._json({"error":"not found"})
        except Exception as e:
            self.send_response(500); self._json({"error":str(e)})

    def log_message(self, *a): pass

def run():
    port = int(os.environ.get("PORT", 8080))
    print(f"[PL API] Iniciando na porta {port}...", flush=True)
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()

if __name__ == "__main__":
    run()