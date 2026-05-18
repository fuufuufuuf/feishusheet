"""
一次性回填脚本：把主表 ai_video_urls 还指向 yanghao 原始服务（需要登录态才能下载）
的旧记录，统一迁到 Cloudinary 公开 URL。

执行步骤：
  1. 主表查 ai_video_urls 含有 yanghao base_url 的记录
  2. 每条记录用一个独立 requests.Session 登录 hangbeiai
  3. 带 cookie 流式下载原始 mp4 到临时文件
  4. 上传 Cloudinary，public_id=task_id（避免重复占空间）
  5. 把新的 secure_url 写回 ai_video_urls，本地临时文件清掉
  6. 任何一步失败都不动飞书，下次重跑会自动重试

用法：
    python backfill_yanghao_to_cloudinary.py
"""

import json
import os
import re
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from cloudinary_helper import upload_file_to_cloudinary
from feishu_sheet import FeishuSheet

CONFIG_PATH = "config.json"


def _unwrap_text(val):
    if isinstance(val, list) and val and isinstance(val[0], dict):
        return val[0].get("text", "") or val[0].get("link", "") or ""
    if val is None:
        return ""
    return str(val)


def _login(base_url, username, password):
    """单次登录，返回带 cookie 的 requests.Session；失败返回 None。"""
    session = requests.Session()
    resp = session.post(
        f"{base_url}/login",
        json={"username": username, "password": password},
    )
    if resp.status_code != 200:
        print(f"[登录失败] status={resp.status_code} body={resp.text[:200]}")
        return None
    return session


def _process_one(rec, feishu, app_token, table_id, yanghao_cfg, idx, total):
    record_id = rec.get("record_id") or rec.get("id")
    fields = rec.get("fields", {}) or {}
    ai_url = _unwrap_text(fields.get("ai_video_urls")).strip()
    video_id = _unwrap_text(fields.get("video_id")).strip()

    tag = f"[{idx}/{total}] record={record_id} video_id={video_id}"

    # 提取 task_id
    m = re.search(r"/api/task/([^/]+)/download", ai_url)
    if not m:
        print(f"  {tag} 跳过：ai_video_urls 不是 yanghao 原始格式 ({ai_url[:80]})")
        return "skipped"
    task_id = m.group(1)

    base_url = yanghao_cfg["base_url"]
    download_url = f"{base_url}/api/task/{task_id}/download"
    print(f"  {tag} task_id={task_id}")

    # 登录拿 session
    session = _login(base_url, yanghao_cfg["username"], yanghao_cfg["password"])
    if session is None:
        return "failed"

    # 流式下载
    local_path = None
    try:
        with session.get(download_url, stream=True, timeout=300) as r:
            if r.status_code != 200:
                print(f"  {tag} 下载失败 status={r.status_code} body={r.text[:200]}")
                return "failed"
            ext = ".mp4"
            cd = r.headers.get("content-disposition") or ""
            fm = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd)
            if fm and "." in fm.group(1):
                ext = "." + fm.group(1).rsplit(".", 1)[-1]
            fd, local_path = tempfile.mkstemp(prefix=f"{task_id}_", suffix=ext)
            with os.fdopen(fd, "wb") as out:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        out.write(chunk)
        size_mb = round(os.path.getsize(local_path) / 1024 / 1024, 2)
        print(f"  {tag} 下载完成 {size_mb} MB")
    except Exception as e:
        print(f"  {tag} 下载异常: {e}")
        if local_path and os.path.exists(local_path):
            try: os.remove(local_path)
            except OSError: pass
        return "failed"

    # 上传 Cloudinary
    try:
        cloud_url = upload_file_to_cloudinary(
            local_path,
            folder="tiktok/yanghao",
            public_id=task_id,
            resource_type="video",
        )
    except Exception as e:
        print(f"  {tag} Cloudinary 异常: {e}")
        cloud_url = ""
    finally:
        try: os.remove(local_path)
        except OSError: pass

    if not cloud_url:
        print(f"  {tag} Cloudinary 上传未返回 URL")
        return "failed"

    # 回写飞书
    upd = feishu.update_record(app_token, table_id, record_id, {"ai_video_urls": cloud_url})
    if upd:
        print(f"  {tag} -> {cloud_url}")
        return "ok"
    print(f"  {tag} 回写飞书失败")
    return "failed"


def main():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    yanghao_cfg = cfg.get("yanghao", {}) or {}
    if not all(k in yanghao_cfg for k in ("base_url", "username", "password")):
        print("config.yanghao 不完整")
        return

    feishu_cfg = cfg["feishu"]
    bitable_cfg = cfg["bitable"]
    feishu = FeishuSheet(feishu_cfg["app_id"], feishu_cfg["app_secret"])
    app_token = bitable_cfg["app_token"]
    table_id = bitable_cfg["table_id"]

    # 查询：ai_video_urls 含有 yanghao 服务域名的记录（即旧格式）
    # 飞书没有 "contains" 操作符是公开的，使用 ai_video_urls isNotEmpty 然后 Python 过滤
    filter_formula = {
        "conjunction": "and",
        "conditions": [
            {"field_name": "ai_video_urls", "operator": "isNotEmpty", "value": []},
        ],
    }
    result = feishu.get_records_by_filter(app_token, table_id, filter_formula, get_all=True)
    if not result:
        print("查询主表失败")
        return

    items = result.get("data", {}).get("items", []) or []
    base_url = yanghao_cfg["base_url"]
    host_marker = base_url.split("//", 1)[-1].rstrip("/")  # 154.40.59.124:2026

    pending = []
    for rec in items:
        ai = _unwrap_text((rec.get("fields") or {}).get("ai_video_urls")).strip()
        if host_marker in ai and "/api/task/" in ai:
            pending.append(rec)

    print(f"主表 ai_video_urls 非空 {len(items)} 条；其中指向旧 yanghao 服务的 {len(pending)} 条需要回填")
    if not pending:
        return

    max_concurrent = max(1, int(yanghao_cfg.get("max_concurrent", 3)))
    print(f"并发数 = {max_concurrent}")

    counts = {"ok": 0, "failed": 0, "skipped": 0}
    total = len(pending)

    with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
        futures = {
            executor.submit(_process_one, rec, feishu, app_token, table_id, yanghao_cfg, i, total): i
            for i, rec in enumerate(pending, 1)
        }
        for fut in as_completed(futures):
            try:
                outcome = fut.result()
            except Exception as e:
                print(f"  任务异常 idx={futures[fut]}: {e}")
                outcome = "failed"
            counts[outcome] = counts.get(outcome, 0) + 1

    print(f"\n完成: 成功 {counts['ok']}, 失败 {counts['failed']}, 跳过 {counts['skipped']}, 总计 {total}")


if __name__ == "__main__":
    main()
