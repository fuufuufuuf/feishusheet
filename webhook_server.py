import asyncio
import json
import httpx
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse

from tiktok_account_monitor import update_titkok_video
from tiktok_pid_to_product import main_process_empty_product_source_imgs
from auto_upload_video import run as run_auto_upload_video
from yanghao import main_process_yanghao_records
from feishu_sheet import FeishuSheet

# https://lauren-moodier-adjunctly.ngrok-free.dev
app = FastAPI()

with open("config.json") as f:
    _config = json.load(f)

CALLBACK_URLS = _config.get("n8n_callback_urls", {})

_monitor_lock = asyncio.Lock()
_product_lock = asyncio.Lock()
_auto_upload_lock = asyncio.Lock()
_yanghao_lock = asyncio.Lock()


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


@app.post("/run/yanghao", status_code=202)
async def run_yanghao(background_tasks: BackgroundTasks):
    if _yanghao_lock.locked():
        raise HTTPException(status_code=409, detail="yanghao already running")
    background_tasks.add_task(_run_and_callback, "yanghao", _yanghao_lock, main_process_yanghao_records)
    return {"status": "started", "job": "yanghao"}



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
def run_pending_upload(handle: str = None):
    """
    查询已生成视频但未上传的记录。
      - 传入 handle：仅查询该 handle 的记录
      - 不传 handle：从 bitable_r（table_id 以 Vmn 结尾）读取所有监控账号 handle，
                     聚合返回每个 handle 对应的 pending 记录
    """
    feishu_cfg = _config["feishu"]
    bitable_cfg = _config["bitable"]
    sheet = FeishuSheet(feishu_cfg["app_id"], feishu_cfg["app_secret"])

    if handle:
        items = sheet.get_pending_upload_records(
            bitable_cfg["app_token"],
            bitable_cfg["table_id"],
            handle,
        )
        return {"status": "success", "count": len(items), "items": items}

    # 无 handle 参数：从 bitable_r 取全部账号 handle，再依次查询
    feishu_r_cfg = _config.get("feishu_r", {}) or {}
    bitable_r_cfg = _config.get("bitable_r", {}) or {}
    r_app_id = feishu_r_cfg.get("app_id")
    r_app_secret = feishu_r_cfg.get("app_secret")
    r_app_token = bitable_r_cfg.get("app_token")
    r_table_id = bitable_r_cfg.get("table_id")

    if not all([r_app_id, r_app_secret, r_app_token, r_table_id]):
        raise HTTPException(status_code=500, detail="feishu_r / bitable_r config missing")

    r_sheet = FeishuSheet(r_app_id, r_app_secret)
    r_result = r_sheet.get_sheet_data(r_app_token, r_table_id, get_all=True)
    if not r_result:
        raise HTTPException(status_code=500, detail="failed to read bitable_r")

    handles = []
    seen = set()
    for record in r_result.get("data", {}).get("items", []) or []:
        fields = record.get("fields", {}) or {}
        val = fields.get("handle")
        if isinstance(val, list) and val and isinstance(val[0], dict):
            val = val[0].get("text", "")
        if not isinstance(val, str):
            val = str(val) if val else ""
        val = val.strip()
        if val and val not in seen:
            seen.add(val)
            handles.append(val)

    results = []
    total = 0
    for h in handles:
        items = sheet.get_pending_upload_records(
            bitable_cfg["app_token"],
            bitable_cfg["table_id"],
            h,
        )
        results.append({"handle": h, "count": len(items), "items": items})
        total += len(items)

    return {
        "status": "success",
        "handle_count": len(handles),
        "total": total,
        "results": results,
    }
