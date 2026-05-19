# -*- coding: utf-8 -*-
"""
使用 Playwright 监听网络请求,抓取 TikTok 账号下的视频信息:
  1) 拦截 /api/post/item_list/ 响应,解析每条视频的元数据
  2) 下载视频 mp4 到本地 (.part -> 原子 rename;优先 playAddr, 自动 fallback)
  3) 将视频封面和本地 mp4 上传到 Cloudinary,拿到 secure_url
  4) 将一整行数据 (含「视频文件」列) 写入飞书多维表格
"""

import asyncio
import json
import os
import re
import time
import platform
from playwright.async_api import async_playwright

from feishu_sheet import FeishuSheet
from cloudinary_helper import upload_url_to_cloudinary, upload_file_to_cloudinary


# ============================================================
# 视频下载相关工具函数
# ============================================================

def _safe_filename(s: str) -> str:
    """去掉 Windows 文件名非法字符"""
    return re.sub(r'[\\/:*?"<>|]+', '_', str(s)).strip() or "untitled"


def _build_filename(item: dict, include_create_time: bool = True) -> str:
    video_id = str(item.get("id") or "")
    handle = (item.get("author") or {}).get("uniqueId") or "unknown"
    if include_create_time and item.get("createTime"):
        name = f"{handle}_{video_id}_{item['createTime']}.mp4"
    else:
        name = f"{handle}_{video_id}.mp4"
    return _safe_filename(name)


async def download_video_via_page(
    page,
    item: dict,
    out_dir: str,
    *,
    include_create_time: bool = True,
    max_retries_per_url: int = 2,
    min_valid_bytes: int = 10_000,
    timeout_ms: int = 60_000,
):
    """
    复用当前浏览器 context 的 Cookie/UA,直接从签名 URL 下载 mp4。
    优先级: playAddr -> downloadAddr -> bitrateInfo[].PlayAddr.UrlList[]
    特性:
      - 写入 .part 临时文件,完成后 os.replace 原子改名
      - 每个候选 URL 最多重试 max_retries_per_url 次,指数退避
      - 断点续存: 目标文件已存在且体积合法则跳过
      - 残留的小体积 .part 自动清理
    返回: 成功 -> 保存路径字符串; 失败 -> None
    """
    video = item.get("video") or {}
    video_id = str(item.get("id") or "")
    if not video_id:
        print("[下载跳过] item 缺少 id")
        return None

    # 按优先级收集候选 URL
    candidates = []
    if video.get("playAddr"):
        candidates.append(("playAddr", video["playAddr"]))
    if video.get("downloadAddr"):
        candidates.append(("downloadAddr", video["downloadAddr"]))
    for bi, b in enumerate(video.get("bitrateInfo") or []):
        for ui, u in enumerate((b.get("PlayAddr") or {}).get("UrlList") or []):
            candidates.append((f"bitrate{bi}.url{ui}", u))

    if not candidates:
        print(f"[下载跳过] {video_id} 没有可用的视频地址")
        return None

    os.makedirs(out_dir, exist_ok=True)
    filename = _build_filename(item, include_create_time=include_create_time)
    save_path = os.path.join(out_dir, filename)
    part_path = save_path + ".part"

    # 断点续存: 已有合法成品 -> 跳过
    if os.path.exists(save_path) and os.path.getsize(save_path) >= min_valid_bytes:
        print(f"[下载跳过] 已存在 {save_path}")
        return save_path

    # 清理残留的小体积 .part
    if os.path.exists(part_path) and os.path.getsize(part_path) < min_valid_bytes:
        try:
            os.remove(part_path)
        except OSError:
            pass

    for label, url in candidates:
        for attempt in range(1, max_retries_per_url + 1):
            try:
                resp = await page.request.get(
                    url,
                    headers={
                        "Referer": "https://www.tiktok.com/",
                        "Accept": "*/*",
                    },
                    timeout=timeout_ms,
                )
                if not resp.ok:
                    print(f"[下载失败] {video_id} {label} 第{attempt}次 HTTP {resp.status}")
                    if 500 <= resp.status < 600 and attempt < max_retries_per_url:
                        await asyncio.sleep(1.5 * attempt)
                        continue
                    break

                body = await resp.body()
                ctype = (resp.headers.get("content-type") or "").lower()
                if "video" not in ctype or len(body) < min_valid_bytes:
                    print(f"[下载失败] {video_id} {label} 第{attempt}次 非法响应 "
                          f"ct={ctype} size={len(body)}")
                    break

                with open(part_path, "wb") as f:
                    f.write(body)
                os.replace(part_path, save_path)

                size_mb = round(len(body) / 1024 / 1024, 2)
                print(f"[下载成功] {video_id} <- {label} ({size_mb} MB) -> {save_path}")
                return save_path

            except Exception as e:
                print(f"[下载异常] {video_id} {label} 第{attempt}次: {e}")
                if attempt < max_retries_per_url:
                    await asyncio.sleep(1.5 * attempt)
                    continue
                break

    if os.path.exists(part_path):
        try:
            os.remove(part_path)
        except OSError:
            pass
    print(f"[下载失败] {video_id} 所有候选均不可用")
    return None


async def download_audio_via_page(
    page,
    item: dict,
    out_dir: str,
    *,
    max_retries: int = 2,
    min_valid_bytes: int = 1_000,
    timeout_ms: int = 60_000,
):
    """
    下载 item['music']['playUrl'] 指向的音频文件 (一般为 mp3)。
    复用浏览器 context 的 cookie/UA;.part -> os.replace 原子改名。
    成功返回保存路径,失败返回 None。
    """
    music = item.get("music") or {}
    play_url = music.get("playUrl") or ""
    music_id = str(music.get("id") or "")
    if not play_url or not music_id:
        return None

    os.makedirs(out_dir, exist_ok=True)
    save_path = os.path.join(out_dir, _safe_filename(f"{music_id}.mp3"))
    part_path = save_path + ".part"

    if os.path.exists(save_path) and os.path.getsize(save_path) >= min_valid_bytes:
        print(f"[音频跳过] 已存在 {save_path}")
        return save_path

    if os.path.exists(part_path) and os.path.getsize(part_path) < min_valid_bytes:
        try:
            os.remove(part_path)
        except OSError:
            pass

    for attempt in range(1, max_retries + 1):
        try:
            resp = await page.request.get(
                play_url,
                headers={"Referer": "https://www.tiktok.com/", "Accept": "*/*"},
                timeout=timeout_ms,
            )
            if not resp.ok:
                print(f"[音频失败] music_id={music_id} 第{attempt}次 HTTP {resp.status}")
                if 500 <= resp.status < 600 and attempt < max_retries:
                    await asyncio.sleep(1.5 * attempt)
                    continue
                break

            body = await resp.body()
            ctype = (resp.headers.get("content-type") or "").lower()
            if not body or len(body) < min_valid_bytes:
                print(f"[音频失败] music_id={music_id} 第{attempt}次 size={len(body)} ct={ctype}")
                break

            with open(part_path, "wb") as f:
                f.write(body)
            os.replace(part_path, save_path)

            size_kb = round(len(body) / 1024, 2)
            print(f"[音频成功] music_id={music_id} ({size_kb} KB) -> {save_path}")
            return save_path

        except Exception as e:
            print(f"[音频异常] music_id={music_id} 第{attempt}次: {e}")
            if attempt < max_retries:
                await asyncio.sleep(1.5 * attempt)
                continue
            break

    if os.path.exists(part_path):
        try:
            os.remove(part_path)
        except OSError:
            pass
    return None


# ============================================================
# 网络请求拦截 + 飞书写入
# ============================================================

async def _process_item_list(page, item_list, feishu_sheet, app_token, table_id,
                             existing_video_ids, out_dir, download_include_create_time,
                             is_nurturing: bool = False):
    """
    顺序处理 itemList:上传封面 -> 下载视频 -> 上传视频 -> 写飞书。
    独立出来是为了让处理过程在 browser context 仍然存活时同步完成,
    避免在 Playwright response 回调里以 fire-and-forget 方式做重活。
    """
    for i, item in enumerate(item_list):
        # 解析商品锚点。养号账号的视频通常没有商品锚点，此处允许 extra_json 为空
        extra_json = {}
        anchors = item.get("anchors")
        if isinstance(anchors, list) and anchors:
            first_anchor = anchors[0]
            extra_str = first_anchor.get("extra")
            if isinstance(extra_str, str):
                try:
                    arr = json.loads(extra_str)
                    candidate = arr[0] if isinstance(arr, list) and arr else {}
                except (json.JSONDecodeError, IndexError, KeyError) as e:
                    print(f"解析失败: {str(e)}")
                    candidate = {}
                if isinstance(candidate, dict) and 'extra' in candidate:
                    for field in ('icon', 'actions', 'component_key', 'anchor_strong'):
                        candidate.pop(field, None)
                    try:
                        inner_extra = json.loads(candidate['extra'])
                        if 'product_id' in inner_extra:
                            candidate['product_id'] = inner_extra['product_id']
                        if 'title' in inner_extra:
                            candidate['title'] = inner_extra['title']
                    except json.JSONDecodeError as inner_e:
                        print(f"解析 inner extra 失败: {str(inner_e)}")
                    extra_json = candidate

        # 非养号模式下，必须存在商品锚点才写入飞书
        if not is_nurturing and not extra_json:
            continue

        if not (feishu_sheet and app_token and table_id):
            continue

        video_id = str(item.get('id', ''))
        if existing_video_ids and video_id in existing_video_ids:
            continue

        video_info = item.get('video') or {}
        raw_cover = video_info.get('cover') or video_info.get('originCover') or ''
        cloud_cover_url = upload_url_to_cloudinary(
            raw_cover, folder="tiktok/pics", public_id=video_id or None,
        ) if raw_cover else ''

        music = item.get('music') or {}
        music_id = music.get('id', '')
        music_link = f"https://www.tiktok.com/music/-{music_id}" if music_id else ''
        music_title = music.get('title', '') or ''

        cloud_audio_url = ''
        local_audio_path = None
        if is_nurturing:
            # 养号模式：跳过音频下载和 Cloudinary 上传，audio 直接留空
            print(f"[养号] music_id={music_id} 跳过音频下载/上传")
        else:
            try:
                local_audio_path = await download_audio_via_page(page, item, out_dir)
            except Exception as au_e:
                print(f"[音频异常] music_id={music_id}: {au_e}")
            if local_audio_path:
                try:
                    cloud_audio_url = upload_file_to_cloudinary(
                        local_audio_path,
                        folder="tiktok/audio",
                        public_id=str(music_id) or None,
                        resource_type="video",
                    )
                    if not cloud_audio_url:
                        print(f"[Cloudinary] music_id={music_id} 音频上传未返回 URL")
                except Exception as up_e:
                    print(f"[Cloudinary 异常] music_id={music_id}: {up_e}")

        music_info = json.dumps(
            {"url": music_link, "title": music_title, "audio": cloud_audio_url},
            ensure_ascii=False,
        ) if (music_link or music_title or cloud_audio_url) else ''

        cloud_video_url = ''
        local_video_path = None
        if is_nurturing:
            # 养号模式：视频文件直接用 TikTok 视频页 URL，跳过下载与 Cloudinary 上传
            handle_for_url = (item.get('author') or {}).get('uniqueId') or ''
            if handle_for_url and video_id:
                cloud_video_url = f"https://www.tiktok.com/@{handle_for_url}/video/{video_id}"
                print(f"[养号] video_id={video_id} 使用原始 URL {cloud_video_url}")
            else:
                print(f"[养号] video_id={video_id} 缺少 handle/video_id，无法生成原始 URL")
        else:
            try:
                local_video_path = await download_video_via_page(
                    page, item, out_dir,
                    include_create_time=download_include_create_time,
                )
            except Exception as dl_e:
                print(f"[下载异常] video_id={video_id}: {dl_e}")

            if local_video_path:
                try:
                    cloud_video_url = upload_file_to_cloudinary(
                        local_video_path,
                        folder="tiktok/videos",
                        public_id=video_id or None,
                        resource_type="video",
                    )
                    if not cloud_video_url:
                        print(f"[Cloudinary] video_id={video_id} 上传未返回 URL")
                except Exception as up_e:
                    print(f"[Cloudinary 异常] video_id={video_id}: {up_e}")
            else:
                print(f"[跳过上传] video_id={video_id} 本地文件不存在")

        fields = {
            "handle": item.get('author', '').get('uniqueId', ''),
            "video_id": item.get('id', ''),
            "video_create_time": str(item.get('createTime', '')),
            "video_title": item.get('desc', ''),
            "video_cover": cloud_cover_url or raw_cover,
            "视频文件": cloud_video_url,
            "product_id": extra_json.get('id', ''),
            "product_title": extra_json.get('title', ''),
            "music info": music_info,
        }

        result = feishu_sheet.create_record(app_token, table_id, fields, f"第 {i+1} 项")
        if not result:
            print("写入飞书表格失败")

        if local_video_path and cloud_video_url and os.path.exists(local_video_path):
            try:
                os.remove(local_video_path)
                print(f"[本地清理] 已删除 {local_video_path}")
            except OSError as rm_e:
                print(f"[本地清理失败] {local_video_path}: {rm_e}")

        if local_audio_path and cloud_audio_url and os.path.exists(local_audio_path):
            try:
                os.remove(local_audio_path)
                print(f"[本地清理] 已删除 {local_audio_path}")
            except OSError as rm_e:
                print(f"[本地清理失败] {local_audio_path}: {rm_e}")


async def intercept_requests(page, url, feishu_sheet=None, app_token=None, table_id=None,
                             existing_video_ids=None,
                             out_dir: str = "downloads",
                             download_include_create_time: bool = True,
                             is_nurturing: bool = False):
    """
    拦截并分析网络请求
    """
    # 存储所有请求
    requests_data = []
    responses_data = []
    # 只收集第一个 itemList 的原始数据,真正的处理放到监听器之外做
    collected_items = []
    first_item_list_done = [False]

    def log_request(request):
        """记录请求信息"""
        if "item_list" not in request.url:
            return
        request_info = {
            "url": request.url,
            "method": request.method,
            "headers": dict(request.headers),
            "timestamp": time.time()
        }
        try:
            if request.post_data:
                request_info["post_data"] = request.post_data
        except Exception as e:
            request_info["post_data_error"] = str(e)
        requests_data.append(request_info)

    async def log_response(response):
        """
        监听响应,只做轻量采集。
        注意:Playwright 会把 async 监听器当作后台 task 调度,如果这里做重活
        (上传/下载/写库),在 intercept_requests 返回、context.close() 之后
        这些 task 仍在跑,会抛 "Target page, context or browser has been closed"。
        """
        if "item_list" not in response.request.url:
            return

        response_info = {
            "url": response.url,
            "status": response.status,
            "status_text": response.status_text,
            "headers": dict(response.headers),
            "timestamp": time.time()
        }
        responses_data.append(response_info)

        if first_item_list_done[0]:
            return

        content_type = response.headers.get("content-type", "")
        if not any(ct in content_type for ct in ["application/json", "text/plain", "text/html"]):
            return

        try:
            body = await response.body()
        except Exception as e:
            print(f"[获取响应体失败] {str(e)}")
            return
        if not body:
            return

        try:
            json_body = json.loads(body.decode('utf-8', errors='ignore'))
        except Exception:
            print(f"[响应体] {body.decode('utf-8', errors='ignore')[:500]}...")
            return

        if "itemList" not in json_body:
            return
        if first_item_list_done[0]:
            print("\n[跳过 itemList] 已处理过第一个 itemList,跳过后续数据")
            return
        first_item_list_done[0] = True
        item_list = json_body["itemList"]
        print("\n[解析 itemList] 找到 itemList 数组,包含 {} 项".format(len(item_list)))
        collected_items.extend(item_list)

    # 设置请求和响应监听器
    page.on("request", log_request)
    page.on("response", log_response)

    try:
        # 导航到目标 URL
        print(f"\n=== 导航到: {url} ===")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"页面加载超时: {str(e)}")
            print("继续执行,捕获已产生的网络请求...")

        print("\n=== 等待 5 秒捕获更多请求 ===")
        await asyncio.sleep(5)

        # 在浏览器 context 仍然存活时,顺序处理收集到的 items
        if collected_items:
            await _process_item_list(
                page, collected_items,
                feishu_sheet, app_token, table_id,
                existing_video_ids, out_dir,
                download_include_create_time,
                is_nurturing=is_nurturing,
            )
    finally:
        # 防止同一个 page 多次注册监听器导致累积
        try:
            page.remove_listener("request", log_request)
            page.remove_listener("response", log_response)
        except Exception:
            pass

    print(f"\n=== 统计信息 ===")
    print(f"总请求数: {len(requests_data)}")
    print(f"总响应数: {len(responses_data)}")

    request_methods = {}
    for req in requests_data:
        method = req["method"]
        request_methods[method] = request_methods.get(method, 0) + 1
    print(f"请求方法分布: {request_methods}")

    status_codes = {}
    for resp in responses_data:
        status = resp["status"]
        status_codes[status] = status_codes.get(status, 0) + 1
    print(f"响应状态码分布: {status_codes}")

    return requests_data, responses_data


# ============================================================
# 主流程
# ============================================================

async def update_titkok_video():
    """
    主函数
    """
    print("=== Playwright 网络请求监听器 ====")

    # 从配置文件读取飞书表格信息
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)

        # 初始化飞书表格实例(用于写入数据)
        app_id = config.get('feishu', {}).get('app_id')
        app_secret = config.get('feishu', {}).get('app_secret')
        feishu_sheet = FeishuSheet(app_id, app_secret)

        # 飞书表格配置(用于写入数据)
        app_token = config.get('bitable', {}).get('app_token')
        table_id = config.get('bitable', {}).get('table_id')

        # 初始化飞书表格实例(用于读取handle数据)
        app_id_r = config.get('feishu_r', {}).get('app_id')
        app_secret_r = config.get('feishu_r', {}).get('app_secret')
        feishu_sheet_r = FeishuSheet(app_id_r, app_secret_r)

        # 飞书表格配置(用于读取handle数据)
        app_token_r = config.get('bitable_r', {}).get('app_token')
        table_id_r = config.get('bitable_r', {}).get('table_id')

        # 下载配置
        out_dir = config.get('download', {}).get('dir', 'downloads')
        download_include_create_time = config.get('download', {}).get('include_create_time', True)

        print("成功读取配置文件")
    except Exception as e:
        print(f"读取配置文件失败: {str(e)}")
        # 使用默认值
        app_id = "your_app_id"
        app_secret = "your_app_secret"
        feishu_sheet = FeishuSheet(app_id, app_secret)
        app_token = "your_app_token"
        table_id = "your_table_id"

        app_id_r = "your_app_id"
        app_secret_r = "your_app_secret"
        feishu_sheet_r = FeishuSheet(app_id_r, app_secret_r)
        app_token_r = "your_app_token"
        table_id_r = "your_table_id"

        out_dir = "downloads"
        download_include_create_time = True
        print("使用默认配置")

    # 从飞书表格读取handle数据
    handles = []
    handle_nurturing = {}  # handle -> bool，True 表示养号模式
    try:
        print("\n=== 从飞书表格读取handle数据 ===")
        sheet_data = feishu_sheet_r.get_sheet_data(app_token_r, table_id_r, get_all=True)
        if sheet_data:
            records = sheet_data.get('data', {}).get('items', [])
            print(f"从表格中读取到 {len(records)} 条记录")

            for record in records:
                fields = record.get('fields', {})
                handle = None
                for key, value in fields.items():
                    if 'handle' in key.lower() or 'uniqueid' in key.lower():
                        handle = value
                        break
                if not handle:
                    handle = fields.get('handle')

                # 解析养号字段（单选）
                nurturing_raw = fields.get('养号')
                if isinstance(nurturing_raw, list) and nurturing_raw:
                    first = nurturing_raw[0]
                    nurturing_raw = first.get('text', '') if isinstance(first, dict) else str(first)
                is_nurturing = str(nurturing_raw or '').strip() == '是'

                if handle:
                    handles.append(handle)
                    handle_nurturing[handle] = is_nurturing
                    print(f"获取到handle: {handle}  养号={'是' if is_nurturing else '否'}")

            print(f"成功提取 {len(handles)} 个handle")
        else:
            print("读取表格数据失败")
    except Exception as e:
        print(f"读取handle数据异常: {str(e)}")

    # 生成URL列表
    url_list = []
    if handles:
        base_url = "https://www.tiktok.com/@"
        for handle in handles:
            url = f"{base_url}{handle}"
            url_list.append(url)
        print(f"\n生成了 {len(url_list)} 个URL")
        for url in url_list:
            print(f"- {url}")
    else:
        print("未获取到任何 handle,无 URL 可处理")
        return

    # 根据操作系统获取 Chrome profile 路径和可执行文件路径
    system = platform.system()

    if system == "Windows":
        profile_path = os.path.expanduser("~\\AppData\\Local\\Google\\Chrome\\User Data\\Profile 4")
        chrome_paths = [
            "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
            "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
            os.path.expanduser("~\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe")
        ]
    elif system == "Darwin":
        profile_path = os.path.expanduser("~/Library/Application Support/Google/Chrome/Profile 4")
        chrome_paths = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        ]
    elif system == "Linux":
        profile_path = os.path.expanduser("~/.config/google-chrome/Profile 4")
        chrome_paths = [
            "/usr/bin/google-chrome",
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium"
        ]
    else:
        profile_path = os.path.expanduser("~/.config/google-chrome/Profile 4")
        chrome_paths = []

    print(f"当前操作系统: {system}")
    print(f"使用指定的 Chrome profile: {profile_path}")
    print(f"视频下载目录: {out_dir}")

    async with async_playwright() as p:
        print("\n=== 启动浏览器 ===")
        try:
            chrome_exe = None
            for path in chrome_paths:
                if os.path.exists(path):
                    chrome_exe = path
                    break

            if chrome_exe:
                print(f"使用系统 Chrome: {chrome_exe}")
                context = await p.chromium.launch_persistent_context(
                    profile_path,
                    headless=False,
                    slow_mo=100,
                    executable_path=chrome_exe,
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
                    viewport={"width": 1920, "height": 1080},
                    accept_downloads=True,
                    locale="en-US",
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--disable-features=IsolateOrigins,site-per-process'
                    ],
                )
                page = context.pages[0] if context.pages else await context.new_page()
                print("浏览器启动成功")
            else:
                print("未找到系统 Chrome 浏览器")
                raise Exception("未找到可用的 Chrome 浏览器")
        except Exception as e:
            print(f"浏览器启动失败: {str(e)}")
            raise

        try:
            print(f"\n=== 开始处理 {len(url_list)} 个URL ===")
            for i, url in enumerate(url_list, 1):
                print(f"\n=== 处理第 {i} 个URL: {url} ===")
                try:
                    handle = url.split("@")[-1] if "@" in url else ""

                    existing_video_ids = set()
                    if handle and feishu_sheet and app_token and table_id:
                        print(f"正在读取 handle={handle} 的已有 video_id...")
                        filter_formula = {
                            "conjunction": "and",
                            "conditions": [
                                {
                                    "field_name": "handle",
                                    "operator": "is",
                                    "value": [handle]
                                }
                            ]
                        }
                        result = feishu_sheet.get_records_by_filter(
                            app_token, table_id, filter_formula,
                            get_all=True, field_names=["video_id"]
                        )
                        if result:
                            items = result.get("data", {}).get("items", []) or []
                            for item in items:
                                vid = item.get("fields", {}).get("video_id", "")
                                if isinstance(vid, list):
                                    vid = vid[0].get("text", "") if vid else ""
                                if vid:
                                    existing_video_ids.add(str(vid))
                            print(f"已有 {len(existing_video_ids)} 个 video_id")

                    is_nurturing = handle_nurturing.get(handle, False)
                    if is_nurturing:
                        print(f"  handle={handle} 处于养号模式，视频文件将使用 TikTok 原始页面 URL，跳过下载/Cloudinary 上传")

                    await intercept_requests(
                        page, url,
                        feishu_sheet, app_token, table_id,
                        existing_video_ids,
                        out_dir=out_dir,
                        download_include_create_time=download_include_create_time,
                        is_nurturing=is_nurturing,
                    )
                    print(f"URL {url} 处理成功")
                except Exception as e:
                    print(f"URL {url} 处理失败: {str(e)}")
                    print(f"错误详情: {str(e)}")
                    continue
        finally:
            print("\n=== 关闭浏览器 ===")
            if 'context' in locals():
                await context.close()

if __name__ == "__main__":
    asyncio.run(update_titkok_video())