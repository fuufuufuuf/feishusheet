#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auto_upload_video.py
====================
独立的"自动上传视频"后处理脚本。

触发场景：
    视频的产品信息（product_desc / product_source_imgs）更新完成之后，
    若 config.json 中 "auto_upload_video" 为 true，则对主表中已经具备
    产品信息但尚未排上传计划的记录批量执行：
        1. 是否生成视频 = "是"   （单选）
        2. post_account = bitable_r 中按 handle 匹配的 post_account 字段
        3. video_upload_device = bitable_device 中按 handle 匹配的 device 字段

bitable_r / bitable_device 使用 config.feishu_r 凭据访问。

使用方式：
    # 命令行直接运行（处理所有待处理记录）
    python auto_upload_video.py

    # 也可作为模块在产品信息更新完成后调用
    from auto_upload_video import run
    run()
"""

import json

from feishu_sheet import FeishuSheet


CONFIG_PATH = "config.json"


def _unwrap_text(val):
    """飞书富文本字段统一转字符串"""
    if isinstance(val, list) and val and isinstance(val[0], dict):
        return val[0].get("text", "") or ""
    if val is None:
        return ""
    return str(val)


def _load_config(config_path=CONFIG_PATH):
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_handle_maps(config):
    """
    读 bitable_r 和 bitable_device，构建：
      handle -> post_account     （来自 bitable_r）
      post_account -> device     （来自 bitable_device，其 handle 列实际存放的是 post_account）
    使用 feishu_r 凭据。
    """
    post_account_map = {}
    device_map = {}

    feishu_r_cfg = config.get("feishu_r", {}) or {}
    bitable_r_cfg = config.get("bitable_r", {}) or {}
    bitable_device_cfg = config.get("bitable_device", {}) or {}

    app_id = feishu_r_cfg.get("app_id")
    app_secret = feishu_r_cfg.get("app_secret")
    if not app_id or not app_secret:
        print("[auto_upload] 缺少 feishu_r 凭据，无法读取 bitable_r / bitable_device")
        return post_account_map, device_map

    feishu_r = FeishuSheet(app_id, app_secret)

    # bitable_r -> post_account
    r_token = bitable_r_cfg.get("app_token")
    r_table = bitable_r_cfg.get("table_id")
    if r_token and r_table:
        try:
            result = feishu_r.get_sheet_data(r_token, r_table, get_all=True)
            for rec in (result or {}).get("data", {}).get("items", []) or []:
                fields = rec.get("fields", {}) or {}
                handle = _unwrap_text(fields.get("handle")).strip()
                post_account = _unwrap_text(fields.get("post_account")).strip()
                if handle and post_account:
                    post_account_map[handle] = post_account
            print(f"[auto_upload] bitable_r 加载 {len(post_account_map)} 条 handle->post_account")
        except Exception as e:
            print(f"[auto_upload] 读取 bitable_r 失败: {e}")
    else:
        print("[auto_upload] bitable_r 配置缺失，跳过 post_account 映射")

    # bitable_device -> device（注意：该表的 handle 列实际存放 post_account 值）
    d_token = bitable_device_cfg.get("app_token")
    d_table = bitable_device_cfg.get("table_id")
    if d_token and d_table:
        try:
            result = feishu_r.get_sheet_data(d_token, d_table, get_all=True)
            for rec in (result or {}).get("data", {}).get("items", []) or []:
                fields = rec.get("fields", {}) or {}
                post_account = _unwrap_text(fields.get("handle")).strip()
                device = _unwrap_text(fields.get("device")).strip()
                if post_account and device:
                    device_map[post_account] = device
            print(f"[auto_upload] bitable_device 加载 {len(device_map)} 条 post_account->device")
        except Exception as e:
            print(f"[auto_upload] 读取 bitable_device 失败: {e}")
    else:
        print("[auto_upload] bitable_device 配置缺失，跳过 device 映射")

    return post_account_map, device_map


def _fetch_pending_records(feishu_sheet, app_token, table_id):
    """
    主表中需要做 auto_upload 处理的记录：
      product_source_imgs 不为空（说明产品信息已更新）
      且 是否生成视频 为空（还没排过上传计划）
    """
    filter_formula = {
        "conjunction": "and",
        "conditions": [
            {"field_name": "product_source_imgs", "operator": "isNotEmpty", "value": []},
            {"field_name": "是否生成视频", "operator": "isEmpty", "value": []},
        ],
    }
    result = feishu_sheet.get_records_by_filter(
        app_token, table_id, filter_formula, get_all=True
    )
    if not result:
        return []
    return result.get("data", {}).get("items", []) or []


def _build_update_fields(handle, post_account_map, device_map):
    """
    根据 source handle 构造单条更新 payload。
    查找链：source_handle -> bitable_r -> post_account -> bitable_device -> device
    handle 不存在时仍会写入 是否生成视频
    """
    fields = {"是否生成视频": "是"}
    if not handle:
        return fields, "记录缺少 handle"

    miss = []
    post_account = post_account_map.get(handle)
    if post_account:
        fields["post_account"] = post_account
        device = device_map.get(post_account)
        if device:
            fields["video_upload_device"] = device
        else:
            miss.append(f"video_upload_device(post_account={post_account})")
    else:
        miss.append("post_account")
        miss.append("video_upload_device(无 post_account 无法链式查找)")

    note = f"未匹配字段: {','.join(miss)}" if miss else "全部匹配"
    return fields, note


def run(config_path=CONFIG_PATH):
    """
    主入口：扫描主表，对待处理记录批量执行 auto_upload 字段写回。
    返回 (success_count, fail_count, skipped_count)
    """
    config = _load_config(config_path)

    if not config.get("auto_upload_video"):
        print("[auto_upload] config.auto_upload_video=false，跳过")
        return 0, 0, 0

    feishu_cfg = config.get("feishu", {}) or {}
    bitable_cfg = config.get("bitable", {}) or {}
    app_id = feishu_cfg.get("app_id")
    app_secret = feishu_cfg.get("app_secret")
    app_token = bitable_cfg.get("app_token")
    table_id = bitable_cfg.get("table_id")

    if not all([app_id, app_secret, app_token, table_id]):
        print("[auto_upload] 主表 feishu / bitable 配置不完整，终止")
        return 0, 0, 0

    feishu_sheet = FeishuSheet(app_id, app_secret)

    # 1. 预加载 handle 映射
    post_account_map, device_map = _build_handle_maps(config)

    # 2. 扫描待处理记录
    pending = _fetch_pending_records(feishu_sheet, app_token, table_id)
    print(f"[auto_upload] 待处理记录共 {len(pending)} 条")
    if not pending:
        return 0, 0, 0

    success = 0
    fail = 0
    skipped = 0

    for i, rec in enumerate(pending, 1):
        record_id = rec.get("record_id") or rec.get("id")
        fields = rec.get("fields", {}) or {}
        handle = _unwrap_text(fields.get("handle")).strip()
        video_id = _unwrap_text(fields.get("video_id")).strip()

        update_fields, note = _build_update_fields(handle, post_account_map, device_map)

        print(
            f"  [{i}/{len(pending)}] record_id={record_id} "
            f"handle={handle or '<空>'} video_id={video_id or '<空>'} -> {note}"
        )

        result = feishu_sheet.update_record(app_token, table_id, record_id, update_fields)
        if result:
            success += 1
        else:
            fail += 1
            print(f"     更新失败")

    print(f"[auto_upload] 完成: 成功 {success}, 失败 {fail}, 总计 {len(pending)}")
    return success, fail, skipped


if __name__ == "__main__":
    run()
