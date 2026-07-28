import threading, os, sys, traceback
sys.dont_write_bytecode = True
os.chdir(os.path.dirname(os.path.abspath(__file__)))

def start_api():
    try:
        from pl_api import run
        print("[Launcher] API thread iniciada", flush=True)
        run()
    except:
        traceback.print_exc()
        print("[Launcher] API FALHOU", flush=True)

print("[Launcher] Iniciando...", flush=True)
t = threading.Thread(target=start_api, daemon=True)
t.start()

import bot_pl