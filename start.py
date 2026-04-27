import os
import subprocess
import sys
import threading

os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUNBUFFERED"] = "1"

log = open("./logs/n8n.log", "a", encoding="utf-8")

def log_print(msg):
    print(msg)
    log.write(msg + "\n")
    log.flush()

def tee(stream):
    for line in iter(stream.readline, ""):
        sys.stdout.write(line)
        sys.stdout.flush()
        log.write(line)
        log.flush()

# ngrok = subprocess.Popen(
#     ["ngrok", "start", "--config", "ngrok.yml", "webhook_py"],
#     stdout=log, stderr=log
# )

uvicorn = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "webhook_server:app", "--host", "0.0.0.0", "--port", "8000"],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    encoding="utf-8",
    bufsize=1,
)

log_print("uvicorn started. Logs -> n8n.log")

threading.Thread(target=tee, args=(uvicorn.stdout,), daemon=True).start()

try:
    uvicorn.wait()
finally:
    # ngrok.terminate()
    log.close()
