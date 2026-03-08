import os
import subprocess
import sys

os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUNBUFFERED"] = "1"

log = open("./logs/n8n.log", "a", encoding="utf-8")

# 同时输出到控制台和日志文件
def log_print(msg):
    print(msg)
    log.write(msg + "\n")
    log.flush()

# ngrok = subprocess.Popen(
#     ["ngrok", "start", "--config", "ngrok.yml", "webhook_py"],
#     stdout=log, stderr=log
# )

uvicorn = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "webhook_server:app", "--host", "0.0.0.0", "--port", "8000"],
    stdout=log, stderr=log
)

log_print("uvicorn started. Logs -> n8n.log")

try:
    uvicorn.wait()
finally:
    # ngrok.terminate()
    log.close()
