"""
yanghao.py
==========
封装"按 TikTok URL 生成新视频"全流程：
  1. 登录 hangbeiai 账号
  2. 本地生成 session_id（前端 JS 同款 16 字节 hex）
  3. set_link_mode 绑定对标视频
  4. submit 提交任务
  5. 轮询 /api/task/{task_id} 直到结束
  6. 若 status=completed，返回下载链接；否则返回 "N/A"
"""

import json
import os
import re
import secrets
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from cloudinary_helper import upload_file_to_cloudinary
from feishu_sheet import FeishuSheet

CONFIG_PATH = "config.json"
STYLE_FILE = "yanghao_style.json"

POLL_INTERVAL = 10                    # 秒
POLL_TIMEOUT = 60 * 30                # 30 分钟硬超时
NA = "N/A"
DEFAULT_MAX_CONCURRENT = 3
DEFAULT_SUBMIT_INTERVAL = 2.0         # config 缺失时的兜底值


def _load_preset_style(preset_name: str, style_file: str = STYLE_FILE):
    """从 yanghao_sytle.json 读取指定 preset；找不到/出错返回空 dict。"""
    if not preset_name:
        return {}
    try:
        with open(style_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        presets = (data or {}).get("presets", {}) or {}
        preset = presets.get(preset_name)
        if not isinstance(preset, dict):
            print(f"[yanghao] 在 {style_file} 中未找到 preset '{preset_name}'")
            return {}
        return preset
    except FileNotFoundError:
        print(f"[yanghao] 缺少 {style_file}")
        return {}
    except Exception as e:
        print(f"[yanghao] 读取 {style_file} 失败: {e}")
        return {}

_submit_gate = threading.Lock()
_last_submit_ts = 0.0


def _wait_for_submit_slot(interval: float):
    """进程级节流：保证两次进入 submit 之间至少间隔 interval 秒。"""
    global _last_submit_ts
    with _submit_gate:
        now = time.time()
        wait = interval - (now - _last_submit_ts)
        if wait > 0:
            time.sleep(wait)
        _last_submit_ts = time.time()


def _load_yanghao_config(config_path=CONFIG_PATH):
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    y = cfg.get("yanghao", {}) or {}
    base_url = y.get("base_url")
    username = y.get("username")
    password = y.get("password")
    submit_interval = float(y.get("submit_interval", DEFAULT_SUBMIT_INTERVAL))
    preset_style = (y.get("preset_style") or "").strip()
    if not all([base_url, username, password]):
        raise RuntimeError(
            "config.json 中 yanghao.base_url / username / password 不完整"
        )
    return base_url, username, password, submit_interval, preset_style


def generate_video_from_tiktok(tiktok_url: str, verbose: bool = True,
                               config_path: str = CONFIG_PATH,
                               slot_num: str = "1") -> str:
    """
    传入 TikTok 视频 URL，返回新生成视频的下载链接；失败/未完成返回 "N/A"。

    slot_num: 服务端用 (slot_num, 秒级时间戳) 拼接 task_id。并发场景下必须给每个
              线程不同的 slot_num，否则同一秒提交的多个请求会共用一个 task_id，
              结果回写时所有记录都指向同一份输出。
    """
    def _log(msg):
        if verbose:
            print(msg)

    try:
        base_url, username, password, submit_interval, preset_style = _load_yanghao_config(config_path)
    except Exception as e:
        _log(f"[配置加载失败] {e}")
        return NA

    # 加载动态 preset（找不到时返回 {}，下面 merge 时不影响原默认值）
    preset_overrides = _load_preset_style(preset_style) if preset_style else {}
    if preset_overrides:
        _log(f"使用 preset '{preset_style}'，覆盖字段: {list(preset_overrides.keys())}")

    session = requests.Session()

    # 登录
    login_resp = session.post(
        f"{base_url}/login",
        json={"username": username, "password": password},
        allow_redirects=True,
    )
    if login_resp.status_code != 200:
        _log(f"[登录失败] status={login_resp.status_code} body={login_resp.text[:200]}")
        return NA
    _log(f"登录成功: {login_resp.text[:120]}")

    # 生成 session_id（前端 crypto.getRandomValues(Uint8Array(16)) 同款）
    session_id = secrets.token_hex(16)
    _log(f"Session ID: {session_id}")

    # 绑定对标链接
    set_link_resp = session.post(
        f"{base_url}/api/set_link_mode",
        json={"session_id": session_id, "slot_num": slot_num, "url": tiktok_url},
    )
    try:
        set_link_data = set_link_resp.json()
    except Exception as e:
        _log(f"[set_link_mode 解析失败] {e}: {set_link_resp.text[:200]}")
        return NA
    if not set_link_data.get("ok"):
        _log(f"[set_link_mode 失败] {set_link_data}")
        return NA
    _log(f"set_link_mode: {set_link_data}")

    # 提交任务（节流：保证全局两次 submit 至少间隔 submit_interval 秒）
    _wait_for_submit_slot(submit_interval)

    # 默认 body（preset_overrides 中存在的字段会覆盖这些）
    submit_body = {
        "session_id": session_id,
        "character_style": "All human characters MUST have Western/European/Caucasian appearance...",
        "prefix": "American graphic novel style, motion comic aesthetic...",
        "motion": ", (slow cinematic pan, slight character movement...)",
        "suffix": ", muted color palette, dramatic lighting...",
        "negative": "--no Japanese anime, moe, cute...",
        "api_preset": "fun",
        "aspect_ratio": "9:16",
        "video_engine": "default",
        "font_color": "&H00FFFFFF&|&H000000&",
        "protagonist_description": "",
        "prompt_style": "default",
        "preset_name": "Comic(美式黑色漫画)",
        "subtitle_max_chars": "default",
    }
    # 用 preset 覆盖；preset 没有的字段保留默认
    submit_body.update(preset_overrides)
    # preset_name 永远跟随 config.yanghao.preset_style（如果有的话）
    if preset_style:
        submit_body["preset_name"] = preset_style

    submit_resp = session.post(
        f"{base_url}/api/submit",
        json=submit_body,
    )
    try:
        submit_data = submit_resp.json()
    except Exception as e:
        _log(f"[submit 解析失败] {e}: {submit_resp.text[:200]}")
        return NA
    _log(f"submit: {submit_data}")

    queued = submit_data.get("queued") or []
    task_id = queued[0] if queued else None
    if not task_id:
        _log(f"[submit] 没有 queued task: {submit_data}")
        return NA

    # 轮询任务
    _log(f"开始轮询任务 {task_id}...")
    start_ts = time.time()
    final_status = ""
    while True:
        if time.time() - start_ts > POLL_TIMEOUT:
            _log("[超时] 等待时间过长，停止轮询")
            return NA

        task_resp = session.get(f"{base_url}/api/task/{task_id}")
        try:
            task_info = task_resp.json()
        except Exception as e:
            _log(f"[轮询解析异常] {e}: {task_resp.text[:200]}")
            time.sleep(POLL_INTERVAL)
            continue

        status = task_info.get("status", "")
        phase = task_info.get("phase", "")
        progress = task_info.get("progress", "")
        _log(f"  status={status}  phase={phase}  progress={progress}")

        if status not in ("running", "pending", "processing", "queued"):
            final_status = status
            _log(f"任务结束，最终状态: {final_status}")
            break

        time.sleep(POLL_INTERVAL)

    if final_status != "completed":
        return NA

    # 任务完成：用同一个登录态 session 把 mp4 拉下来，再上传 Cloudinary，
    # 返回公开 URL 以供下游（n8n / 上传设备）免登录访问
    download_url = f"{base_url}/api/task/{task_id}/download"
    _log(f"开始下载 {download_url}")

    local_path = None
    try:
        with session.get(download_url, stream=True, timeout=300) as r:
            if r.status_code != 200:
                _log(f"[下载失败] status={r.status_code}")
                return NA
            # 从 Content-Disposition 或 task_id 推断扩展名
            ext = ".mp4"
            cd = r.headers.get("content-disposition") or ""
            m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd)
            if m:
                fname = m.group(1)
                if "." in fname:
                    ext = "." + fname.rsplit(".", 1)[-1]
            fd, local_path = tempfile.mkstemp(prefix=f"{task_id}_", suffix=ext)
            try:
                with os.fdopen(fd, "wb") as out:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            out.write(chunk)
            except Exception:
                # mkstemp 已经把 fd 给我们了，下面 finally 会清掉 local_path
                raise
            size_mb = round(os.path.getsize(local_path) / 1024 / 1024, 2)
            _log(f"下载完成 {size_mb} MB -> {local_path}")
    except Exception as e:
        _log(f"[下载异常] {e}")
        if local_path and os.path.exists(local_path):
            try:
                os.remove(local_path)
            except OSError:
                pass
        return NA

    try:
        cloud_url = upload_file_to_cloudinary(
            local_path,
            folder="tiktok/yanghao",
            public_id=task_id,
            resource_type="video",
        )
    except Exception as e:
        _log(f"[Cloudinary 异常] {e}")
        cloud_url = ""
    finally:
        try:
            os.remove(local_path)
        except OSError:
            pass

    if not cloud_url:
        _log("[Cloudinary] 上传未返回 URL")
        return NA
    _log(f"Cloudinary URL = {cloud_url}")
    return cloud_url


def _unwrap_feishu_text(val):
    """飞书富文本字段 -> 字符串"""
    if isinstance(val, list) and val and isinstance(val[0], dict):
        return val[0].get("text", "") or ""
    if val is None:
        return ""
    return str(val)


def _load_nurturing_handles(config):
    """从 bitable_r 读取 养号=是 的 handle 集合，使用 feishu_r 凭据"""
    feishu_r_cfg = config.get("feishu_r", {}) or {}
    bitable_r_cfg = config.get("bitable_r", {}) or {}
    app_id = feishu_r_cfg.get("app_id")
    app_secret = feishu_r_cfg.get("app_secret")
    app_token = bitable_r_cfg.get("app_token")
    table_id = bitable_r_cfg.get("table_id")

    if not all([app_id, app_secret, app_token, table_id]):
        print("[yanghao_batch] feishu_r / bitable_r 配置缺失")
        return set()

    feishu_r = FeishuSheet(app_id, app_secret)
    result = feishu_r.get_sheet_data(app_token, table_id, get_all=True)
    nurturing = set()
    for record in (result or {}).get("data", {}).get("items", []) or []:
        fields = record.get("fields", {}) or {}
        handle = _unwrap_feishu_text(fields.get("handle")).strip()
        nurturing_raw = fields.get("养号")
        if isinstance(nurturing_raw, list) and nurturing_raw:
            first = nurturing_raw[0]
            nurturing_raw = first.get("text", "") if isinstance(first, dict) else str(first)
        if handle and str(nurturing_raw or "").strip() == "是":
            nurturing.add(handle)
    print(f"[yanghao_batch] bitable_r 中养号 handle 共 {len(nurturing)} 个")
    return nurturing


def _process_single_yanghao_record(rec, feishu_sheet, app_token, table_id, config_path, total, idx, slot_num):
    """处理单条养号记录：调用 yanghao 生成 + 写回 ai_video_urls。返回 ('ok'|'failed'|'skipped', ...)"""
    record_id = rec.get("record_id") or rec.get("id")
    fields = rec.get("fields", {}) or {}
    handle = _unwrap_feishu_text(fields.get("handle")).strip()
    video_id = _unwrap_feishu_text(fields.get("video_id")).strip()
    source_url = _unwrap_feishu_text(fields.get("视频文件")).strip()

    tag = f"[{idx}/{total}] slot={slot_num} record={record_id} handle={handle} video_id={video_id}"

    if not source_url:
        print(f"  {tag} 视频文件为空，跳过")
        return "skipped"

    print(f"  {tag} 开始生成 (源 URL: {source_url})")
    download_url = generate_video_from_tiktok(
        source_url, verbose=False, config_path=config_path, slot_num=slot_num,
    )
    if download_url == NA:
        print(f"  {tag} 生成失败，保留 ai_video_urls 为空以便下次重试")
        return "failed"

    upd = feishu_sheet.update_record(app_token, table_id, record_id, {"ai_video_urls": download_url})
    if upd:
        print(f"  {tag} ai_video_urls = {download_url}")
        return "ok"
    print(f"  {tag} 回写 ai_video_urls 失败")
    return "failed"


def main_process_yanghao_records(config_path: str = CONFIG_PATH, max_concurrent: int = None):
    """
    批量处理养号待生成视频的记录：
      1. 主表过滤 视频文件 isNotEmpty 且 ai_video_urls isEmpty
      2. 仅保留 handle ∈ 养号集合 的记录
      3. 通过线程池并发调用 generate_video_from_tiktok(视频文件) 拿下载链接
      4. 写回 ai_video_urls（仅在拿到非 N/A 的链接时才写，方便下次重试）

    并发数优先级：函数参数 > config.yanghao.max_concurrent > DEFAULT_MAX_CONCURRENT
    """
    print("=== 开始处理养号待生成视频记录 ===")
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    feishu_cfg = config.get("feishu", {}) or {}
    bitable_cfg = config.get("bitable", {}) or {}
    app_id = feishu_cfg.get("app_id")
    app_secret = feishu_cfg.get("app_secret")
    app_token = bitable_cfg.get("app_token")
    table_id = bitable_cfg.get("table_id")
    if not all([app_id, app_secret, app_token, table_id]):
        print("[yanghao_batch] 主表 feishu / bitable 配置不完整，终止")
        return 0, 0, 0

    # 决定并发数
    if max_concurrent is None:
        max_concurrent = (config.get("yanghao", {}) or {}).get("max_concurrent", DEFAULT_MAX_CONCURRENT)
    max_concurrent = max(1, int(max_concurrent))
    print(f"[yanghao_batch] 并发数 = {max_concurrent}")

    nurturing_handles = _load_nurturing_handles(config)
    if not nurturing_handles:
        print("[yanghao_batch] 没有养号 handle，跳过")
        return 0, 0, 0

    feishu_sheet = FeishuSheet(app_id, app_secret)

    # 主表筛选待处理记录
    filter_formula = {
        "conjunction": "and",
        "conditions": [
            {"field_name": "视频文件", "operator": "isNotEmpty", "value": []},
            {"field_name": "ai_video_urls", "operator": "isEmpty", "value": []},
        ],
    }
    result = feishu_sheet.get_records_by_filter(
        app_token, table_id, filter_formula, get_all=True
    )
    if not result:
        print("[yanghao_batch] 查询主表失败")
        return 0, 0, 0

    items = result.get("data", {}).get("items", []) or []

    # 仅保留属于养号集合的
    pending = []
    for rec in items:
        fields = rec.get("fields", {}) or {}
        handle = _unwrap_feishu_text(fields.get("handle")).strip()
        if handle and handle in nurturing_handles:
            pending.append(rec)

    print(f"[yanghao_batch] 找到 {len(pending)} 条养号待处理记录（主表命中 {len(items)} 条）")
    if not pending:
        return 0, 0, 0

    counts = {"ok": 0, "failed": 0, "skipped": 0}
    total = len(pending)

    with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
        # 每条记录分配 1..max_concurrent 之间循环的 slot_num，保证同时在跑的
        # 任务 slot_num 互不相同 —— 服务端用 (slot_num, 秒级时间戳) 生成 task_id，
        # 不区分就会让同一秒提交的几个任务共用一个 task_id。
        futures = {
            executor.submit(
                _process_single_yanghao_record,
                rec, feishu_sheet, app_token, table_id, config_path,
                total, i, str(((i - 1) % max_concurrent) + 1),
            ): i
            for i, rec in enumerate(pending, 1)
        }
        for fut in as_completed(futures):
            try:
                outcome = fut.result()
            except Exception as e:
                print(f"  [{futures[fut]}/{total}] 任务异常: {e}")
                outcome = "failed"
            counts[outcome] = counts.get(outcome, 0) + 1

    print(
        f"[yanghao_batch] 完成: 成功 {counts['ok']}, 失败 {counts['failed']}, "
        f"跳过 {counts['skipped']}, 总计 {total}"
    )
    return counts["ok"], counts["failed"], counts["skipped"]


if __name__ == "__main__":
    # 单条测试
    # url = "https://www.tiktok.com/@mute.soul7/video/7635022944615304450"
    # print(generate_video_from_tiktok(url))

    # 批量处理（实际生产入口）
    main_process_yanghao_records()
