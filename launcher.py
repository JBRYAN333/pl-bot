import threading, os, sys, traceback
sys.dont_write_bytecode = True
os.chdir(os.path.dirname(os.path.abspath(__file__)))

def start_api():
    try:
        import pl_api
        print("[PL API] Iniciando servidor...", flush=True)
        pl_api.web.run_app(pl_api.app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
    except:
        traceback.print_exc()
        print("[PL API] FALHA AO INICIAR", flush=True)

print("[Launcher] Iniciando thread da API...", flush=True)
t = threading.Thread(target=start_api, daemon=True)
t.start()

print("[Launcher] Importando bot_pl...", flush=True)
import bot_pl