#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
使用 Playwright 监听网络请求
"""

import asyncio
from playwright.async_api import async_playwright
import json
import os
import time
import random
import platform
import sys
from feishu_sheet import FeishuSheet
from cloudinary_helper import upload_url_to_cloudinary


async def intercept_requests(page, url, feishu_sheet=None, app_token=None, table_id=None, existing_video_ids=None):
        """
        拦截并分析网络请求
        """
        # 存储所有请求
        requests_data = []
        responses_data = []
        # 标记是否已处理过第一个 itemList
        first_item_list_done = [False]

        def log_request(request):
            """
            记录请求信息
            """
            # 过滤只包含 item_list 的请求
            if "item_list" not in request.url:
                return
            
            request_info = {
                "url": request.url,
                "method": request.method,
                "headers": dict(request.headers),
                "timestamp": time.time()
            }
            # 尝试获取请求体
            try:
                if request.post_data:
                    request_info["post_data"] = request.post_data
            except Exception as e:
                request_info["post_data_error"] = str(e)
            
            requests_data.append(request_info)
            #print(f"\n[请求] {request.method} {request.url}")
            #print(f"[请求头] {dict(request.headers)}")
            if request.post_data:
                #print(f"[请求体] {request.post_data}")
                pass

        async def log_response(response):
            """
            记录响应信息
            """
            # 过滤只包含 item_list 的请求的响应
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
            # 尝试获取响应体（仅针对特定内容类型）
            content_type = response.headers.get("content-type", "")
            if any(ct in content_type for ct in ["application/json", "text/plain", "text/html"]):
                try:
                    body = await response.body()
                    if body:
                        try:
                            # 尝试解析为 JSON
                            json_body = json.loads(body.decode('utf-8', errors='ignore'))

                            # 提取和处理 itemList 中的 anchors.extra 字段
                            if "itemList" in json_body:
                                if first_item_list_done[0]:
                                    print("\n[跳过 itemList] 已处理过第一个 itemList，跳过后续数据")
                                    return
                                first_item_list_done[0] = True
                                item_list = json_body["itemList"]
                                print("\n[解析 itemList] 找到 itemList 数组，包含 {} 项".format(len(item_list)))

                                for i, item in enumerate(item_list):
                                    if "anchors" in item and isinstance(item["anchors"], list) and item["anchors"]:
                                        first_anchor = item["anchors"][0]
                                        if "extra" in first_anchor and isinstance(first_anchor["extra"], str):
                                            extra_str = first_anchor["extra"]

                                            # 尝试将 extra 字符串解析为 JSON
                                            try:
                                                extra_json = json.loads(extra_str)[0]
                                                if 'extra' not in extra_json:
                                                    continue
                                                # 移除不需要的字段
                                                unwanted_fields = ['icon', 'actions', 'component_key', 'anchor_strong']
                                                for field in unwanted_fields:
                                                    if field in extra_json:
                                                        del extra_json[field]
                                                try:
                                                    inner_extra = json.loads(extra_json['extra'])
                                                    if 'product_id' in inner_extra:
                                                        extra_json['product_id'] = inner_extra['product_id']
                                                    if 'title' in inner_extra:
                                                        extra_json['title'] = inner_extra['title']
                                                except json.JSONDecodeError as inner_e:
                                                    print(f"解析 inner extra 失败: {str(inner_e)}")

                                                # 写入飞书表格
                                                if feishu_sheet and app_token and table_id:
                                                    # 检查 video_id 是否已存在
                                                    video_id = str(item.get('id', ''))
                                                    if existing_video_ids and video_id in existing_video_ids:
                                                        continue
                                                    # 提取视频封面并转存到 Cloudinary tiktok/pics
                                                    video_info = item.get('video') or {}
                                                    raw_cover = video_info.get('cover') or video_info.get('originCover') or ''
                                                    cover_public_id = video_id or None
                                                    cloud_cover_url = upload_url_to_cloudinary(
                                                        raw_cover,
                                                        folder="tiktok/pics",
                                                        public_id=cover_public_id,
                                                    ) if raw_cover else ''
                                                    # 构建字段数据
                                                    fields = {
                                                        "handle": item.get('author', '').get('uniqueId', ''),
                                                        "video_id": item.get('id', ''),
                                                        "video_create_time": str(item.get('createTime', '')),
                                                        "video_title": item.get('desc', ''),
                                                        "video_cover": cloud_cover_url or raw_cover,
                                                        "product_id": extra_json.get('id', ''),
                                                        "product_title": extra_json.get('title', ''),
                                                    }
                                                    # 写入记录
                                                    result = feishu_sheet.create_record(app_token, table_id, fields, f"第 {i+1} 项")
                                                    if not result:
                                                        print("写入飞书表格失败")
                                            except json.JSONDecodeError as e:
                                                print(f"解析失败: {str(e)}")
                        except:
                            # 非 JSON 格式
                            print(f"[响应体] {body.decode('utf-8', errors='ignore')[:500]}...")
                except Exception as e:
                    print(f"[获取响应体失败] {str(e)}")

        # 设置请求和响应监听器
        page.on("request", log_request)
        page.on("response", log_response)

        # 导航到目标 URL
        print(f"\n=== 导航到: {url} ===")
        try:
            # 使用 domcontentloaded 等待策略，减少超时风险
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"页面加载超时: {str(e)}")
            print("继续执行，捕获已产生的网络请求...")

        # 等待 5 秒，捕获更多网络请求
        print("\n=== 等待 5 秒捕获更多请求 ===")
        await asyncio.sleep(5)
        # 统计请求数量
        print(f"\n=== 统计信息 ===")
        print(f"总请求数: {len(requests_data)}")
        print(f"总响应数: {len(responses_data)}")

        # 分析请求类型
        request_methods = {}
        for req in requests_data:
            method = req["method"]
            request_methods[method] = request_methods.get(method, 0) + 1
        print(f"请求方法分布: {request_methods}")

        # 分析响应状态码
        status_codes = {}
        for resp in responses_data:
            status = resp["status"]
            status_codes[status] = status_codes.get(status, 0) + 1
        print(f"响应状态码分布: {status_codes}")

        return requests_data, responses_data


async def update_titkok_video():
    """
    主函数
    urls: 目标网址列表或单个网址
    """
    print("=== Playwright 网络请求监听器 ====")
    
    # 从配置文件读取飞书表格信息
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        # 初始化飞书表格实例（用于写入数据）
        app_id = config.get('feishu', {}).get('app_id')
        app_secret = config.get('feishu', {}).get('app_secret')
        feishu_sheet = FeishuSheet(app_id, app_secret)
        
        # 飞书表格配置（用于写入数据）
        app_token = config.get('bitable', {}).get('app_token')
        table_id = config.get('bitable', {}).get('table_id')
        
        # 初始化飞书表格实例（用于读取handle数据）
        app_id_r = config.get('feishu_r', {}).get('app_id')
        app_secret_r = config.get('feishu_r', {}).get('app_secret')
        feishu_sheet_r = FeishuSheet(app_id_r, app_secret_r)
        
        # 飞书表格配置（用于读取handle数据）
        app_token_r = config.get('bitable_r', {}).get('app_token')
        table_id_r = config.get('bitable_r', {}).get('table_id')
        
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
        print("使用默认配置")
    
    # 从飞书表格读取handle数据
    handles = []
    try:
        print("\n=== 从飞书表格读取handle数据 ===")
        # 读取表格数据
        sheet_data = feishu_sheet_r.get_sheet_data(app_token_r, table_id_r)
        if sheet_data:
            # 提取handle数据
            records = sheet_data.get('data', {}).get('items', [])
            print(f"从表格中读取到 {len(records)} 条记录")
            
            for record in records:
                # 尝试从fields中获取handle字段
                fields = record.get('fields', {})
                # 查找可能的handle字段名
                handle = None
                for key, value in fields.items():
                    if 'handle' in key.lower() or 'uniqueid' in key.lower():
                        handle = value
                        break
                # 直接检查handle字段
                if not handle:
                    handle = fields.get('handle')
                if handle:
                    handles.append(handle)
                    print(f"获取到handle: {handle}")
            
            print(f"成功提取 {len(handles)} 个handle")
        else:
            print("读取表格数据失败")
    except Exception as e:
        print(f"读取handle数据异常: {str(e)}")
    
    # 生成URL列表
    url_list = []
    if handles:
        # 如果提供了handles，生成对应的URL
        base_url = "https://www.tiktok.com/@"
        for handle in handles:
            url = f"{base_url}{handle}"
            url_list.append(url)
        print(f"\n生成了 {len(url_list)} 个URL")
        for url in url_list:
            print(f"- {url}")
    else:
        print("未获取到任何 handle，无 URL 可处理")
        return
    
    # 根据操作系统获取 Chrome profile 路径和可执行文件路径
    system = platform.system()
    
    if system == "Windows":
        # Windows 系统
        profile_path = os.path.expanduser("~\\AppData\\Local\\Google\\Chrome\\User Data\\Profile 4")
        chrome_paths = [
            "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
            "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
            os.path.expanduser("~\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe")
        ]
    elif system == "Darwin":
        # macOS 系统
        profile_path = os.path.expanduser("~/Library/Application Support/Google/Chrome/Profile 4")
        chrome_paths = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        ]
    elif system == "Linux":
        # Linux 系统
        profile_path = os.path.expanduser("~/.config/google-chrome/Profile 4")
        chrome_paths = [
            "/usr/bin/google-chrome",
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium"
        ]
    else:
        # 其他系统
        profile_path = os.path.expanduser("~/.config/google-chrome/Profile 4")
        chrome_paths = []
    
    print(f"当前操作系统: {system}")
    print(f"使用指定的 Chrome profile: {profile_path}")
    
    async with async_playwright() as p:
        # 启动浏览器（尝试使用系统已安装的 Chrome）
        print("\n=== 启动浏览器 ===")
        try:
            # 尝试使用系统已安装的 Chrome
            chrome_exe = None
            for path in chrome_paths:
                if os.path.exists(path):
                    chrome_exe = path
                    break
            
            if chrome_exe:
                print(f"使用系统 Chrome: {chrome_exe}")
                # 使用系统 Chrome 创建持久上下文
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
            # 顺序处理每个URL
            print(f"\n=== 开始处理 {len(url_list)} 个URL ===")
            for i, url in enumerate(url_list, 1):
                print(f"\n=== 处理第 {i} 个URL: {url} ===")
                try:
                    # 从URL中提取handle
                    handle = url.split("@")[-1] if "@" in url else ""

                    # 从飞书表格读取该handle已有的video_id
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
                        result = feishu_sheet.get_records_by_filter(app_token, table_id, filter_formula, get_all=True, field_names=["video_id"])
                        if result:
                            items = result.get("data", {}).get("items", []) or []
                            for item in items:
                                vid = item.get("fields", {}).get("video_id", "")
                                # 处理飞书富文本格式
                                if isinstance(vid, list):
                                    vid = vid[0].get("text", "") if vid else ""
                                if vid:
                                    existing_video_ids.add(str(vid))
                            print(f"已有 {len(existing_video_ids)} 个 video_id")

                    # 拦截请求
                    await intercept_requests(page, url, feishu_sheet, app_token, table_id, existing_video_ids)
                    print(f"URL {url} 处理成功")
                except Exception as e:
                    print(f"URL {url} 处理失败: {str(e)}")
                    # 记录错误信息
                    print(f"错误详情: {str(e)}")
                    # 继续处理下一个URL
                    continue
        finally:
            # 关闭浏览器
            print("\n=== 关闭浏览器 ===")
            if 'context' in locals():
                await context.close()


if __name__ == "__main__":

    asyncio.run(update_titkok_video())
