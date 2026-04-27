#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
根据 video_id 从飞书表格查找 product_id，再通过爬虫获取产品信息
"""

import json
from feishu_sheet import FeishuSheet
from tiktok_pid_to_product import TikTokProductScraperPlaywright


def main(video_id):
    # 1. 读取配置
    with open("config.json", "r", encoding="utf-8") as f:
        config = json.load(f)

    app_id = config["feishu"]["app_id"]
    app_secret = config["feishu"]["app_secret"]
    app_token = config["bitable"]["app_token"]
    table_id = config["bitable"]["table_id"]

    # 2. 初始化飞书
    feishu = FeishuSheet(app_id, app_secret)

    # 3. 根据 video_id 查找记录
    print(f"正在查找 video_id: {video_id}")
    filter_formula = {
        "conjunction": "and",
        "conditions": [
            {
                "field_name": "video_id",
                "operator": "is",
                "value": [video_id]
            }
        ]
    }

    result = feishu.get_records_by_filter(app_token, table_id, filter_formula)
    if not result:
        print("未找到匹配的记录")
        return

    items = result.get("data", {}).get("items", [])
    if not items:
        print("未找到匹配的记录")
        return

    # 4. 提取 product_id
    record = items[0]
    fields = record.get("fields", {})
    record_id = record.get("record_id", "")
    product_id = fields.get("product_id")

    print(f"找到记录: record_id={record_id}")
    print(f"字段信息:")
    for k, v in fields.items():
        print(f"  {k}: {v}")

    if not product_id:
        print("该记录没有 product_id")
        return

    # 处理 product_id 可能是列表的情况
    if isinstance(product_id, list):
        product_id = product_id[0].get("text", "") if product_id else ""
    elif isinstance(product_id, dict):
        product_id = product_id.get("text", "") or str(product_id)

    print(f"\nproduct_id: {product_id}")

    # 5. 通过爬虫获取产品信息
    print(f"\n正在获取产品信息...")
    scraper = TikTokProductScraperPlaywright(headless=False)
    try:
        product_data = scraper.get_product_images_sync(product_id)

        print("\n========== 产品信息 ==========")
        if isinstance(product_data, dict):
            print(f"标题: {product_data.get('product_title', '')}")
            print(f"描述: {product_data.get('product_description', '')}")
            image_urls = product_data.get("image_urls", [])
            print(f"图片数量: {len(image_urls)}")
            for i, img in enumerate(image_urls):
                if isinstance(img, dict):
                    print(f"  [{i+1}] ({img.get('type','')}) {img.get('url','')}")
                else:
                    print(f"  [{i+1}] {img}")
        else:
            print(f"返回数据: {product_data}")
        print("==============================")
    finally:
        scraper.close()


def get_pending_upload_records(handle):
    """
    根据 handle 查询：是否生成视频=是，且 video_upload_device 为空的记录
    """
    with open("config.json", "r", encoding="utf-8") as f:
        config = json.load(f)

    feishu = FeishuSheet(config["feishu"]["app_id"], config["feishu"]["app_secret"])
    app_token = config["bitable"]["app_token"]
    table_id = config["bitable"]["table_id"]

    filter_formula = {
        "conjunction": "and",
        "conditions": [
            {
                "field_name": "handle",
                "operator": "is",
                "value": [handle]
            },
            {
                "field_name": "是否生成视频",
                "operator": "is",
                "value": ["是"]
            },
            {
                "field_name": "video_upload_device",
                "operator": "isEmpty",
                "value": []
            }
        ]
    }

    result = feishu.get_records_by_filter(app_token, table_id, filter_formula, get_all=True)
    if not result:
        print("查询失败")
        return []

    items = result.get("data", {}).get("items", []) or []
    print(f"handle={handle} 下，已生成视频但未上传的记录共 {len(items)} 条")
    for i, item in enumerate(items):
        fields = item.get("fields", {})
        print(f"  [{i+1}] record_id={item.get('record_id', '')}  video_id={fields.get('video_id', '')}  product_id={fields.get('product_id', '')}")

    return items


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法:")
        print("  python test_video_to_product.py video <video_id>")
        print("  python test_video_to_product.py pending <handle>")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "video" and len(sys.argv) >= 3:
        main(sys.argv[2])
    elif cmd == "pending" and len(sys.argv) >= 3:
        get_pending_upload_records(sys.argv[2])
    else:
        print("未知命令或参数不足")
        sys.exit(1)
