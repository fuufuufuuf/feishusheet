import json
import logging

import cloudinary
import cloudinary.uploader

_configured = False


def configure_cloudinary(config_path="config.json"):
    """
    从 config.json 读取 cloudinary 配置并初始化 SDK，保证只初始化一次。
    """
    global _configured
    if _configured:
        return
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    c = config.get("cloudinary", {})
    cloudinary.config(
        cloud_name=c.get("cloud_name"),
        api_key=c.get("api_key"),
        api_secret=c.get("api_secret"),
        secure=True,
    )
    _configured = True


def upload_url_to_cloudinary(url, folder="tiktok/pics", public_id=None):
    """
    直接把一个远程 URL 交给 Cloudinary 去抓取并存到指定文件夹。
    返回上传成功后的 secure_url，失败返回 None。
    """
    if not url:
        return None
    try:
        configure_cloudinary()
        kwargs = {
            "folder": folder,
            "resource_type": "image",
            "overwrite": False,
            "unique_filename": True,
        }
        if public_id:
            kwargs["public_id"] = public_id
        result = cloudinary.uploader.upload(url, **kwargs)
        secure_url = result.get("secure_url") or result.get("url")
        logging.info(f"Cloudinary 上传成功: {secure_url}")
        return secure_url
    except Exception as e:
        logging.error(f"Cloudinary 上传失败 url={url}: {str(e)}")
        return None
