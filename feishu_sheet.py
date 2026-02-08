import requests
import json
import logging
import time

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class FeishuSheet:
    def __init__(self, app_id, app_secret):
        self.app_id = app_id
        self.app_secret = app_secret
        self.access_token = None
        self.token_expire = 0
        self.token_time = 0
    
    def get_access_token(self):
        """
        获取飞书 API 访问令牌
        """
        try:
            url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal/"
            headers = {"Content-Type": "application/json"}
            payload = {
                "app_id": self.app_id,
                "app_secret": self.app_secret
            }
            response = requests.post(url, headers=headers, json=payload)
            result = response.json()
            
            if result.get("code") == 0:
                self.access_token = result.get("tenant_access_token")
                self.token_expire = result.get("expire")
                self.token_time = time.time()
                logging.info("获取 access_token 成功")
                return self.access_token
            else:
                logging.error(f"获取 access_token 失败: {result.get('msg')}")
                return None
        except Exception as e:
            logging.error(f"获取 access_token 异常: {str(e)}")
            return None
    
    def ensure_token(self):
        """
        确保 access_token 有效
        """
        # 检查 token 是否存在且未过期
        if not self.access_token or time.time() - self.token_time > self.token_expire - 60:
            # 提前 60 秒刷新 token，避免过期
            return self.get_access_token()
        return self.access_token
    
    def get_sheet_data(self, app_token, table_id, page_size=100, page_token=""):
        """
        获取表格数据
        app_token: 应用 token
        table_id: 表格 ID
        page_size: 每页数据量
        page_token: 分页标记
        """
        try:
            token = self.ensure_token()
            if not token:
                return None
            
            url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
            params = {
                "page_size": page_size,
                "page_token": page_token
            }
            
            response = requests.get(url, headers=headers, params=params)
            result = response.json()
            
            if result.get("code") == 0:
                logging.info("获取表格数据成功")
                return result
            else:
                logging.error(f"获取表格数据失败: {result.get('msg')}")
                return None
        except Exception as e:
            logging.error(f"获取表格数据异常: {str(e)}")
            return None
    
    def get_view_data(self, app_token, table_id, view_id, page_size=100, page_token=""):
        """
        获取视图数据
        app_token: 应用 token
        table_id: 表格 ID
        view_id: 视图 ID
        page_size: 每页数据量
        page_token: 分页标记
        """
        try:
            token = self.ensure_token()
            if not token:
                return None
            
            url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/views/{view_id}/records"
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
            params = {
                "page_size": page_size,
                "page_token": page_token
            }
            
            response = requests.get(url, headers=headers, params=params)
            result = response.json()
            
            if result.get("code") == 0:
                logging.info("获取视图数据成功")
                return result
            else:
                logging.error(f"获取视图数据失败: {result.get('msg')}")
                return None
        except Exception as e:
            logging.error(f"获取视图数据异常: {str(e)}")
            return None
    
    def create_record(self, app_token, table_id, fields):
        """
        创建记录
        app_token: 应用 token
        table_id: 表格 ID
        fields: 字段数据，格式为 {"字段名": "值"}
        """
        try:
            token = self.ensure_token()
            if not token:
                return None
            
            url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
            payload = {
                "fields": fields
            }
            
            response = requests.post(url, headers=headers, json=payload)
            result = response.json()
            
            if result.get("code") == 0:
                logging.info("创建记录成功")
                return result
            else:
                logging.error(f"创建记录失败: {result.get('msg')}")
                return None
        except Exception as e:
            logging.error(f"创建记录异常: {str(e)}")
            return None
    
    def update_record(self, app_token, table_id, record_id, fields):
        """
        更新记录
        app_token: 应用 token
        table_id: 表格 ID
        record_id: 记录 ID
        fields: 字段数据，格式为 {"字段名": "值"}
        """
        try:
            token = self.ensure_token()
            if not token:
                return None
            
            url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}"
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
            payload = {
                "fields": fields
            }
            
            response = requests.put(url, headers=headers, json=payload)
            result = response.json()
            
            if result.get("code") == 0:
                logging.info("更新记录成功")
                return result
            else:
                logging.error(f"更新记录失败: {result.get('msg')}")
                return None
        except Exception as e:
            logging.error(f"更新记录异常: {str(e)}")
            return None
    
    def delete_record(self, app_token, table_id, record_id):
        """
        删除记录
        app_token: 应用 token
        table_id: 表格 ID
        record_id: 记录 ID
        """
        try:
            token = self.ensure_token()
            if not token:
                return None
            
            url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}"
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
            
            response = requests.delete(url, headers=headers)
            result = response.json()
            
            if result.get("code") == 0:
                logging.info("删除记录成功")
                return result
            else:
                logging.error(f"删除记录失败: {result.get('msg')}")
                return None
        except Exception as e:
            logging.error(f"删除记录异常: {str(e)}")
            return None
