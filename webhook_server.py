import asyncio
import json
import httpx
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse

from tiktok_account_monitor import update_titkok_video
from tiktok_pid_to_product import main_process_empty_product_source_imgs
from auto_upload_video import run as run_auto_upload_video
from feishu_sheet import FeishuSheet

# https://lauren-moodier-adjunctly.ngrok-free.dev
app = FastAPI()

with open("config.json") as f:
    _config = json.load(f)

CALLBACK_URLS = _config.get("n8n_callback_urls", {})

_monitor_lock = asyncio.Lock()
_product_lock = asyncio.Lock()
_auto_upload_lock = asyncio.Lock()


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


@app.post("/run/auto-upload", status_code=202)
async def run_auto_upload(background_tasks: BackgroundTasks):
    if _auto_upload_lock.locked():
        raise HTTPException(status_code=409, detail="auto-upload already running")
    background_tasks.add_task(_run_and_callback, "auto_upload", _auto_upload_lock, run_auto_upload_video)
    return {"status": "started", "job": "auto_upload"}



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


@app.post("/run/update-record")
def run_update_record(record_id: str, fields: dict):
    feishu_cfg = _config["feishu"]
    bitable_cfg = _config["bitable"]
    sheet = FeishuSheet(feishu_cfg["app_id"], feishu_cfg["app_secret"])
    result = sheet.update_record(
        bitable_cfg["app_token"],
        bitable_cfg["table_id"],
        record_id,
        fields,
    )
    if result is None:
        raise HTTPException(status_code=500, detail="update record failed")
    return {"status": "success", "data": result}


@app.get("/run/pending-upload")
def run_pending_upload(handle: str):
    feishu_cfg = _config["feishu"]
    bitable_cfg = _config["bitable"]
    sheet = FeishuSheet(feishu_cfg["app_id"], feishu_cfg["app_secret"])
    items = sheet.get_pending_upload_records(
        bitable_cfg["app_token"],
        bitable_cfg["table_id"],
        handle,
    )
    return {"status": "success", "count": len(items), "items": items}
