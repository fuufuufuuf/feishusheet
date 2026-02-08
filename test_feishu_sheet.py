#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试飞书多维表格操作
"""

import json
from feishu_sheet import FeishuSheet

# 从配置文件读取配置信息
try:
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    # 配置信息
    APP_ID = config.get('feishu', {}).get('app_id')
    APP_SECRET = config.get('feishu', {}).get('app_secret')
    APP_TOKEN = config.get('bitable', {}).get('app_token')
    TABLE_ID = config.get('bitable', {}).get('table_id')
    #VIEW_ID = config.get('bitable', {}).get('view_id', '')  # 视图的 view_id，可选
    
    print("成功读取配置文件")
except Exception as e:
    print(f"读取配置文件失败: {str(e)}")
    # 使用默认值
    APP_ID = "cli_a90e2a7c70381bd6"  # 在飞书开放平台创建应用后获取
    APP_SECRET = "o0lrqtV7XPaXo4FrlC5RJJtGZBXOhrw2"  # 在飞书开放平台创建应用后获取
    APP_TOKEN = "AMyYbIx6Ya7xSXsOvTbc65B8nqe"  # 多维表格的 app_token，在多维表格链接中获取
    TABLE_ID = "tblhEdUtW6C69Vmn"  # 表格的 table_id，在多维表格链接中获取
    #VIEW_ID = "vewSETp8gf"  # 视图的 view_id，可选
    print("使用默认配置")


def test_auth():
    """
    测试认证功能
    """
    print("=== 测试认证功能 ===")
    feishu = FeishuSheet(APP_ID, APP_SECRET)
    token = feishu.get_access_token()
    if token:
        print(f"获取 access_token 成功: {token[:20]}...")
    else:
        print("获取 access_token 失败")
    return token


def test_read_data():
    """
    测试读取数据功能
    """
    print("\n=== 测试读取数据功能 ===")
    feishu = FeishuSheet(APP_ID, APP_SECRET)
    # 测试获取表格数据
    result = feishu.get_sheet_data(APP_TOKEN, TABLE_ID)
    if result:
        print(f"获取表格数据成功，记录数: {len(result.get('data', {}).get('items', []))}")
        # 打印前两条记录
        items = result.get('data', {}).get('items', [])
        for i, item in enumerate(items[:2]):
            print(f"记录 {i+1}: {item.get('fields', {})}")
    else:
        print("获取表格数据失败")
    
    # 测试获取视图数据（如果提供了 view_id）
    if VIEW_ID:
        result = feishu.get_view_data(APP_TOKEN, TABLE_ID, VIEW_ID)
        if result:
            print(f"获取视图数据成功，记录数: {len(result.get('data', {}).get('items', []))}")
        else:
            print("获取视图数据失败")


def test_write_data():
    """
    测试写入数据功能
    """
    print("\n=== 测试写入数据功能 ===")
    feishu = FeishuSheet(APP_ID, APP_SECRET)
    
    # 测试创建记录
    test_fields = {
        "名称": "测试记录",
        "数值": 123,
        "状态": "正常"
    }
    result = feishu.create_record(APP_TOKEN, TABLE_ID, test_fields)
    if result:
        record_id = result.get('data', {}).get('record_id')
        print(f"创建记录成功，记录 ID: {record_id}")
        
        # 测试更新记录
        update_fields = {
            "数值": 456,
            "状态": "已更新"
        }
        update_result = feishu.update_record(APP_TOKEN, TABLE_ID, record_id, update_fields)
        if update_result:
            print("更新记录成功")
        else:
            print("更新记录失败")
        
        # 测试删除记录
        delete_result = feishu.delete_record(APP_TOKEN, TABLE_ID, record_id)
        if delete_result:
            print("删除记录成功")
        else:
            print("删除记录失败")
    else:
        print("创建记录失败")


if __name__ == "__main__":
    # 测试认证功能
    test_auth()
    
    # 测试读取数据功能
    test_read_data()
    
    # 测试写入数据功能
    test_write_data()
    
    print("\n=== 测试完成 ===")
