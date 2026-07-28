#!/usr/bin/env python3
import threading, os, sys
sys.dont_write_bytecode = True
os.chdir(os.path.dirname(os.path.abspath(__file__)))

def start_api():
    import pl_api
    pl_api.web.run_app(pl_api.app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

t = threading.Thread(target=start_api, daemon=True)
t.start()

import bot_pl