#!/usr/bin/env python3
import subprocess, sys, os

os.chdir(os.path.dirname(os.path.abspath(__file__)))
subprocess.Popen([sys.executable, "pl_api.py"])
sys.exit(subprocess.run([sys.executable, "bot_pl.py"]).returncode)