import subprocess
import sys

log = open("./logs/n8n.log", "a")

# ngrok = subprocess.Popen(
#     ["ngrok", "start", "--config", "ngrok.yml", "webhook_py"],
#     stdout=log, stderr=log
# )

uvicorn = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "webhook_server:app", "--host", "0.0.0.0", "--port", "8000"],
    stdout=log, stderr=log
)

print("ngrok and uvicorn started. Logs -> n8n.log")

try:
    uvicorn.wait()
finally:
    #ngrok.terminate()
    log.close()
