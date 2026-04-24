# -*- coding: utf-8 -*-
"""
一次性脚本:回填飞书表格里 "视频文件" 为空的历史记录。
对每一条空记录,访问其 TikTok 视频详情页,解析 __UNIVERSAL_DATA_FOR_REHYDRATION__
中的 itemStruct -> 下载 mp4 -> 上传 Cloudinary -> update_record 回写 "视频文件"。

用法:
    python backfill_videos.py
"""

import asyncio
import json
import os
import platform
import re

from playwright.async_api import async_playwright

from feishu_sheet import FeishuSheet
from cloudinary_helper import upload_file_to_cloudinary
from tiktok_account_monitor import download_video_via_page


_UNIVERSAL_DATA_RE = re.compile(
    r'<script[^>]+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
    re.DOTALL,
)


def _unwrap_feishu_text(val):
    """飞书文本字段有时返回 [{text, type}] 富文本数组,统一转成字符串。"""
    if isinstance(val, list):
        return val[0].get("text", "") if val else ""
    return str(val or "")


async def _fetch_item_from_video_page(page, handle, video_id):
    """
    打开视频详情页,从 __UNIVERSAL_DATA_FOR_REHYDRATION__ 中提取 itemStruct。
    失败返回 None。
    """
    url = f"https://www.tiktok.com/@{handle}/video/{video_id}"
    print(f"  导航 {url}")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        print(f"  [导航失败] {e}")
        return None

    # 给 SSR 脚本一点渲染时间
    await asyncio.sleep(2)

    try:
        content = await page.content()
    except Exception as e:
        print(f"  [获取页面内容失败] {e}")
        return None

    match = _UNIVERSAL_DATA_RE.search(content)
    if not match:
        print("  [未找到 __UNIVERSAL_DATA_FOR_REHYDRATION__]")
        return None

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as e:
        print(f"  [解析嵌入 JSON 失败] {e}")
        return None

    try:
        item_struct = (
            data.get("__DEFAULT_SCOPE__", {})
                .get("webapp.video-detail", {})
                .get("itemInfo", {})
                .get("itemStruct")
        )
    except AttributeError:
        item_struct = None

    if not item_struct:
        print("  [未找到 itemStruct] 可能视频已被删除或需要登录")
        return None
    if str(item_struct.get("id", "")) != str(video_id):
        print(f"  [video_id 不匹配] 期望 {video_id} 实际 {item_struct.get('id')}")
        return None

    return item_struct


def _load_config():
    with open("config.json", "r", encoding="utf-8") as f:
        config = json.load(f)

    app_id = config.get("feishu", {}).get("app_id")
    app_secret = config.get("feishu", {}).get("app_secret")
    feishu_sheet = FeishuSheet(app_id, app_secret)

    app_token = config.get("bitable", {}).get("app_token")
    table_id = config.get("bitable", {}).get("table_id")

    out_dir = config.get("download", {}).get("dir", "downloads")
    include_create_time = config.get("download", {}).get("include_create_time", True)

    return feishu_sheet, app_token, table_id, out_dir, include_create_time


def _resolve_chrome():
    system = platform.system()
    if system == "Windows":
        profile_path = os.path.expanduser("~\\AppData\\Local\\Google\\Chrome\\User Data\\Profile 4")
        candidates = [
            "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
            "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
            os.path.expanduser("~\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe"),
        ]
    elif system == "Darwin":
        profile_path = os.path.expanduser("~/Library/Application Support/Google/Chrome/Profile 4")
        candidates = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    else:
        profile_path = os.path.expanduser("~/.config/google-chrome/Profile 4")
        candidates = ["/usr/bin/google-chrome", "/usr/bin/chromium-browser", "/usr/bin/chromium"]

    exe = next((p for p in candidates if os.path.exists(p)), None)
    if not exe:
        raise RuntimeError("未找到可用的 Chrome 可执行文件")
    return profile_path, exe


async def backfill_empty_videos():
    feishu_sheet, app_token, table_id, out_dir, include_create_time = _load_config()

    # 查询 "视频文件" 为空,且 handle / video_id 都存在的记录
    filter_formula = {
        "conjunction": "and",
        "conditions": [
            {"field_name": "视频文件", "operator": "isEmpty", "value": []},
            {"field_name": "handle", "operator": "isNotEmpty", "value": []},
            {"field_name": "video_id", "operator": "isNotEmpty", "value": []},
        ],
    }
    result = feishu_sheet.get_records_by_filter(
        app_token, table_id, filter_formula,
        get_all=True, field_names=["handle", "video_id"],
    )
    if not result:
        print("查询待回填记录失败")
        return

    records = result.get("data", {}).get("items", []) or []
    print(f"\n=== 共找到 {len(records)} 条 '视频文件' 为空的记录 ===")
    if not records:
        return

    profile_path, chrome_exe = _resolve_chrome()
    print(f"使用 Chrome: {chrome_exe}")
    print(f"使用 Profile: {profile_path}")
    print(f"视频下载目录: {out_dir}")

    ok, fail = 0, 0

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            profile_path,
            headless=False,
            slow_mo=100,
            executable_path=chrome_exe,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            accept_downloads=True,
            locale="en-US",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        page = context.pages[0] if context.pages else await context.new_page()

        try:
            for i, rec in enumerate(records, 1):
                record_id = rec.get("record_id") or rec.get("id")
                fields = rec.get("fields", {}) or {}
                handle = _unwrap_feishu_text(fields.get("handle")).strip()
                video_id = _unwrap_feishu_text(fields.get("video_id")).strip()

                print(f"\n[{i}/{len(records)}] record_id={record_id} handle={handle} video_id={video_id}")

                if not handle or not video_id:
                    print("  跳过: handle/video_id 缺失")
                    fail += 1
                    continue

                item = await _fetch_item_from_video_page(page, handle, video_id)
                if not item:
                    fail += 1
                    continue

                local_path = None
                try:
                    local_path = await download_video_via_page(
                        page, item, out_dir,
                        include_create_time=include_create_time,
                    )
                except Exception as e:
                    print(f"  [下载异常] {e}")
                if not local_path:
                    fail += 1
                    continue

                try:
                    cloud_video_url = upload_file_to_cloudinary(
                        local_path,
                        folder="tiktok/videos",
                        public_id=video_id,
                        resource_type="video",
                    )
                except Exception as e:
                    print(f"  [Cloudinary 异常] {e}")
                    cloud_video_url = ""

                if not cloud_video_url:
                    print("  上传 Cloudinary 失败,保留本地文件用于重试")
                    fail += 1
                    continue

                update_result = feishu_sheet.update_record(
                    app_token, table_id, record_id, {"视频文件": cloud_video_url},
                )
                if not update_result:
                    print("  更新飞书记录失败")
                    fail += 1
                    continue

                print(f"  更新成功 -> {cloud_video_url}")
                ok += 1

                # 成功则清理本地文件
                if os.path.exists(local_path):
                    try:
                        os.remove(local_path)
                    except OSError as rm_e:
                        print(f"  [本地清理失败] {rm_e}")

                # 轻度节流,避免触发 TikTok 风控
                await asyncio.sleep(1.5)

        finally:
            print(f"\n=== 回填结束: 成功 {ok}, 失败 {fail}, 合计 {len(records)} ===")
            await context.close()


if __name__ == "__main__":
    asyncio.run(backfill_empty_videos())
