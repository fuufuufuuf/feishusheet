import asyncio
import json
import httpx
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse

from tiktok_account_monitor import update_titkok_video
from tiktok_pid_to_product import main_process_empty_product_source_imgs
from feishu_sheet import FeishuSheet

app = FastAPI()

with open("config.json") as f:
    _config = json.load(f)

CALLBACK_URLS = _config.get("n8n_callback_urls", {})

_monitor_lock = asyncio.Lock()
_product_lock = asyncio.Lock()


async def _run_and_callback(job: str, lock: asyncio.Lock, coro_or_func):
    async with lock:
        payload = {"job": job, "status": "success"}
        try:
            if asyncio.iscoroutinefunction(coro_or_func):
                await coro_or_func()
            else:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, coro_or_func)
        except Exception as e:
            payload = {"job": job, "status": "error", "error": str(e)}
        url = CALLBACK_URLS.get(job, "")
        if url:
            async with httpx.AsyncClient() as client:
                await client.post(url, json=payload, timeout=10)


@app.post("/run/monitor", status_code=202)
async def run_monitor(background_tasks: BackgroundTasks):
    if _monitor_lock.locked():
        raise HTTPException(status_code=409, detail="monitor already running")
    background_tasks.add_task(_run_and_callback, "monitor", _monitor_lock, update_titkok_video)
    return {"status": "started", "job": "monitor"}


@app.post("/run/product", status_code=202)
async def run_product(background_tasks: BackgroundTasks):
    if _product_lock.locked():
        raise HTTPException(status_code=409, detail="product already running")
    background_tasks.add_task(_run_and_callback, "product", _product_lock, main_process_empty_product_source_imgs)
    return {"status": "started", "job": "product"}



@app.get("/run/delete-duplicates")
def run_delete_duplicates(duplicate_field: str = "重复", duplicate_value: str = "重复"):
    feishu_cfg = _config["feishu"]
    bitable_cfg = _config["bitable"]
    sheet = FeishuSheet(feishu_cfg["app_id"], feishu_cfg["app_secret"])
    deleted = sheet.delete_duplicate_records(
        bitable_cfg["app_token"],
        bitable_cfg["table_id"],
        duplicate_field,
        duplicate_value,
    )
    return {"status": "success", "deleted": deleted}
