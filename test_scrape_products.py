#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试重构后的scrape_products方法
"""

import os
from tiktok_product_scraper_playwright import TikTokProductScraperPlaywright
from feishu_sheet import FeishuSheet
import json


def test_scrape_products():
    """
    测试重构后的scrape_products方法
    """
    print("=== 测试重构后的scrape_products方法 ===")
    
    # 1. 创建爬虫实例
    scraper = TikTokProductScraperPlaywright(headless=False)
    
    # 2. 准备测试数据
    test_product_ids = [
        {
            "product_id": "1731572915654201371",
            "record_id": "rec123456"
        },
        {
            "product_id": "1731572915654201372",  # 假设这是一个无效的产品ID，用于测试错误处理
            "record_id": "rec789012"
        }
    ]
    
    print(f"准备测试 {len(test_product_ids)} 个产品")
    
    # 3. 读取飞书配置（可选）
    feishu_sheet = None
    app_token = None
    table_id = None
    
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        # 初始化飞书表格实例
        app_id = config.get('feishu', {}).get('app_id')
        app_secret = config.get('feishu', {}).get('app_secret')
        feishu_sheet = FeishuSheet(app_id, app_secret)
        
        # 飞书表格配置
        app_token = config.get('bitable', {}).get('app_token')
        table_id = config.get('bitable', {}).get('table_id')
        
        print("成功读取飞书配置")
    except Exception as e:
        print(f"读取飞书配置失败: {str(e)}")
        print("将在不更新多维表格的情况下进行测试")
    
    # 4. 调用scrape_products方法
    print("\n=== 开始测试scrape_products方法 ===")
    results = scraper.scrape_products(
        product_ids=test_product_ids,
        feishu_sheet=feishu_sheet,
        app_token=app_token,
        table_id=table_id,
        download_images=True,
        images_folder="test_images",
        batch_size=5
    )
    
    # 5. 打印测试结果
    print("\n=== 测试结果 ===")
    print(f"总处理产品数: {len(results)}")
    
    for i, result in enumerate(results):
        print(f"\n第 {i+1} 个产品结果:")
        print(f"  product_id: {result.get('product_id')}")
        print(f"  record_id: {result.get('record_id')}")
        print(f"  status: {result.get('status')}")
        if result.get('error'):
            print(f"  error: {result.get('error')}")
        else:
            print(f"  product_title: {result.get('product_title')}")
            print(f"  product_description: {result.get('product_description')[:50]}...")
            print(f"  image_urls_count: {len(result.get('image_urls', []))}")
    
    print("\n=== 测试完成 ===")


if __name__ == "__main__":
    test_scrape_products()
