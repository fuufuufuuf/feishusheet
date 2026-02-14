import os
import time
import asyncio
import requests
import re
from playwright.async_api import async_playwright
from playwright.sync_api import sync_playwright
import csv
import json
from urllib.parse import urljoin
from pathlib import Path
from feishu_sheet import FeishuSheet


class TikTokProductScraperPlaywright:
    def __init__(self, headless=True, user_data_dir=None, profile_name=None):
        """
        初始化TikTok产品爬虫 (Playwright版)
        :param headless: 是否以无头模式运行浏览器
        :param user_data_dir: Chrome用户数据目录路径
        :param profile_name: Chrome配置文件名称
        """
        self.headless = headless
        self.user_data_dir = user_data_dir
        self.profile_name = profile_name
        self.browser = None
        self.page = None
    
    def read_product_ids(self, file_path):
        """
        从文件中读取产品ID列表
        :param file_path: 包含产品ID的文件路径
        :return: 产品ID列表
        """
        product_ids = []
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:  # 忽略空行
                        product_ids.append(line)
        except FileNotFoundError:
            print(f"错误：找不到文件 {file_path}")
            return []
        except Exception as e:
            print(f"读取文件时出错: {e}")
            return []
        
        return product_ids
    
    def get_product_images_sync(self, product_id):
        """
        同步方式访问TikTok产品页面并抓取产品多个主图
        :param product_id: 产品ID
        :return: 产品主图URL列表，如果未找到则返回空列表
        """
        with sync_playwright() as p:
            # 如果指定了用户数据目录，则使用持久化上下文
            if self.user_data_dir:
                try:
                    context = p.chromium.launch_persistent_context(
                        user_data_dir=self.user_data_dir,
                        headless=self.headless,
                        args=[f"--profile-directory={self.profile_name or 'Default'}"],
                        channel="chrome"
                    )
                    page = context.new_page()
                    browser_ref = context  # 为了在finally中正确关闭
                except Exception as e:
                    print(f"无法使用指定的用户数据目录 (可能Chrome已在使用中): {e}")
                    print("切换到独立的浏览器实例")
                    browser = p.chromium.launch(headless=self.headless)
                    page = browser.new_page()
                    browser_ref = browser
            else:
                # 启动浏览器
                browser = p.chromium.launch(headless=self.headless)
                page = browser.new_page()
                browser_ref = browser  # 为了在finally中正确关闭
            
            # 设置用户代理以模拟真实用户
            page.set_extra_http_headers({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            })
            
            url = f"https://www.tiktok.com/shop/pdp/product/{product_id}"
            print(f"正在访问产品页面: {url}")
            
            try:
                # 访问页面
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                
                # 检查是否遇到安全验证页面
                security_check_detected = False
                try:
                    # 检查是否存在安全验证元素
                    # 检查页面title是否为"Security Check"
                    page_title = page.title()
                    if "Security Check" in page_title:
                        print(f"  检测到安全验证页面（Title: Security Check），等待30秒让用户完成验证...")
                        security_check_detected = True
                    
                    # 或者检查页面是否有"text=Verify to continue"
                    if not security_check_detected:
                        try:
                            element = page.query_selector("text=Verify to continue")
                            if element:
                                print(f"  检测到安全验证页面（Text: Verify to continue），等待30秒让用户完成验证...")
                                security_check_detected = True
                        except:
                            pass
                    
                    if security_check_detected:
                        # 等待30秒让用户完成验证
                        page.wait_for_timeout(30000)
                        
                        # 刷新页面以检查是否验证成功
                        page.reload(wait_until="domcontentloaded")
                        
                        # 再次检查是否仍然在安全验证页面
                        still_on_security_page = False
                        
                        # 检查页面title是否为"Security Check"
                        page_title = page.title()
                        if "Security Check" in page_title:
                            still_on_security_page = True
                        else:
                            # 检查页面是否有"text=Verify to continue"
                            try:
                                element = page.query_selector("text=Verify to continue")
                                if element:
                                    still_on_security_page = True
                            except:
                                pass
                        
                        if still_on_security_page:
                            print(f"  警告：安全验证似乎未完成，继续尝试获取图片...")
                        else:
                            print(f"  安全验证检测通过，继续处理页面...")
                except Exception as sec_e:
                    print(f"  检查安全验证时出错: {sec_e}")
                
                # 等待页面加载
                page.wait_for_timeout(5000)  # 等待5秒让页面完全加载
                
                # 获取产品标题
                product_title = ""
                try:
                    title_element = page.query_selector("div.overflow-y-auto h1 span.H2-Semibold")
                    if title_element:
                        product_title = title_element.inner_text().strip()
                        print(f"  产品标题: {product_title}")
                    else:
                        print("  未找到产品标题")
                except Exception as e:
                    print(f"  获取产品标题时出错: {e}")
                    product_title = ""
                
                # 获取产品描述
                product_description = ""
                try:
                    desc_element = page.query_selector("div.relative div.overflow-hidden.duration-300")
                    if desc_element:
                        product_description = desc_element.inner_text().strip()
                        print(f"  产品描述: {product_description[:100]}...")  # 只打印前100个字符
                    else:
                        print("  未找到产品描述")
                except Exception as e:
                    print(f"  获取产品描述时出错: {e}")
                    product_description = ""
                
                # 首先尝试使用指定的选择器获取多个主图
                image_urls = []
                
                # 新增：查找 div.items-center 下的 img.object-cover (主要主图)
                try:
                    img_elements = page.query_selector_all("div.items-center.overflow-x-scroll img.object-cover")
                    for img_element in img_elements:
                        src = img_element.get_attribute("src")
                        if src and src.startswith(("http", "https")):
                            # 确保URL是完整的
                            if not src.startswith(("http://", "https://")):
                                src = urljoin(url, src)
                            # 将普通图片也以字典形式存储，保持一致性
                            image_info = {
                                "url": src,
                                "title": "main_image",
                                "type": "main"
                            }
                            # 避免重复
                            if not any(isinstance(info, dict) and info["url"] == src for info in image_urls):
                                image_urls.append(image_info)
                    main_count = len([item for item in image_urls if isinstance(item, dict) and item["type"] == "main"])
                    if main_count > 0:
                        print(f"  找到 {main_count} 张主图使用选择器: div.items-center.overflow-x-scroll img.object-cover")
                except Exception as e:
                    print(f"  尝试使用指定选择器时出错: {e}")
                    pass
                
                # 新增：查找 div.overflow-x-auto 下的 div.items-center 下的 img (SKU图片)
                try:
                    sku_img_elements = page.query_selector_all("div.overflow-x-auto.flex-wrap div.items-center.border-solid.cursor-pointer img")
                    for img_element in sku_img_elements:
                        src = img_element.get_attribute("src")
                        title = img_element.get_attribute("title")
                        if src and src.startswith(("http", "https")):
                            # 将src中的200:200替换为800:800
                            if "200:200" in src:
                                src = src.replace("200:200", "800:800")
                            # 确保URL是完整的
                            if not src.startswith(("http://", "https://")):
                                src = urljoin(url, src)
                            # 使用标题作为标识符添加到URL列表中
                            image_info = {
                                "url": src,
                                "title": title,
                                "type": "sku"
                            }
                            # 避免重复
                            if not any(isinstance(info, dict) and info["url"] == src for info in image_urls):
                                image_urls.append(image_info)
                    sku_count = len([item for item in image_urls if isinstance(item, dict) and item["type"] == "sku"])
                    if sku_count > 0:
                        print(f"  找到 {sku_count} 张SKU图片使用选择器: div.overflow-x-auto div.items-center img")
                except Exception as e:
                    print(f"  尝试获取SKU图片时出错: {e}")
                    pass
                
                return {
                    "image_urls": image_urls,
                    "product_title": product_title,
                    "product_description": product_description
                }
                
            except Exception as e:
                print(f"获取产品 {product_id} 的图片时出错: {e}")
                return []
            finally:
                # 关闭浏览器或上下文
                if 'browser_ref' in locals() and browser_ref:
                    browser_ref.close()
    
    def scrape_products(self, product_ids, feishu_sheet=None, app_token=None, table_id=None, download_images=False, images_folder=None, batch_size=10):
        """
        批量抓取产品图片并更新多维表格
        :param product_ids: 产品信息字典数组，每个字典包含product_id和record_id
        :param feishu_sheet: FeishuSheet实例，用于更新多维表格
        :param app_token: 飞书应用token
        :param table_id: 多维表格ID
        :param download_images: 是否下载图片到本地
        :param images_folder: 图片保存文件夹
        :param batch_size: 批量处理大小
        :return: 结果字典列表
        """
        # 1. 参数验证
        if not isinstance(product_ids, list):
            print("错误：product_ids必须是字典数组")
            return []
        
        # 验证数组中每个字典的结构
        valid_product_ids = []
        for i, item in enumerate(product_ids):
            if not isinstance(item, dict):
                print(f"警告：第{i+1}个元素不是字典，跳过")
                continue
            if "product_id" not in item or "record_id" not in item:
                print(f"警告：第{i+1}个字典缺少必要的键，跳过")
                continue
            if not item["product_id"] or not item["record_id"]:
                print(f"警告：第{i+1}个字典的product_id或record_id为空，跳过")
                continue
            valid_product_ids.append(item)
        
        if not valid_product_ids:
            print("没有找到有效的产品信息")
            return []
        
        # 2. 初始化
        if download_images and not images_folder:
            images_folder = "downloaded_images"
        
        # 创建图片保存目录
        if download_images:
            os.makedirs(images_folder, exist_ok=True)
        
        results = []
        total_processed = 0
        total_success = 0
        total_failed = 0
        
        # 3. 批量处理
        for batch_start in range(0, len(valid_product_ids), batch_size):
            batch_end = min(batch_start + batch_size, len(valid_product_ids))
            batch_items = valid_product_ids[batch_start:batch_end]
            
            print(f"\n=== 处理第 {batch_start//batch_size + 1} 批，共 {len(valid_product_ids)} 个产品 ===")
            print(f"当前批次：{batch_start+1} - {batch_end}")
            
            for i, item in enumerate(batch_items):
                product_id = item["product_id"]
                record_id = item["record_id"]
                total_processed += 1
                
                print(f"\n正在处理第 {total_processed}/{len(valid_product_ids)} 个产品: {product_id}")
                print(f"对应的记录ID: {record_id}")
                
                try:
                    # 4. 处理产品数据
                    product_data = self.get_product_images_sync(product_id)
                    if not isinstance(product_data, dict):
                        print(f"  错误：获取产品数据失败，返回类型不正确")
                        total_failed += 1
                        results.append({
                            'product_id': product_id,
                            'record_id': record_id,
                            'status': 'failed',
                            'error': '获取产品数据失败'
                        })
                        continue
                    
                    image_urls = product_data.get("image_urls", [])
                    product_title = product_data.get("product_title", "")
                    product_description = product_data.get("product_description", "")
                    
                    # 5. 准备更新数据
                    # 处理image_urls，转换为字符串
                    image_urls_str = ""
                    if image_urls:
                        urls_list = []
                        for img_data in image_urls:
                            if isinstance(img_data, dict):
                                urls_list.append(img_data.get('url', ''))
                            else:
                                urls_list.append(str(img_data))
                        image_urls_str = ';'.join(urls_list)
                    
                    # 6. 更新多维表格
                    if feishu_sheet and app_token and table_id:
                        update_fields = {
                            "product_desc": product_description,
                            "product_source_imgs": image_urls_str
                        }
                        
                        update_result = feishu_sheet.update_record(app_token, table_id, record_id, update_fields)
                        if update_result:
                            print(f"  多维表格更新成功")
                        else:
                            print(f"  警告：多维表格更新失败")
                    
                    # 7. 下载图片（如果需要）
                    if download_images and image_urls:
                        self.download_images(image_urls, product_id, images_folder, product_title=product_title, product_description=product_description)
                    
                    # 8. 记录结果
                    status = 'success' if image_urls else 'failed'
                    result = {
                        'product_id': product_id,
                        'record_id': record_id,
                        'product_title': product_title,
                        'product_description': product_description,
                        'image_urls': image_urls,
                        'status': status,
                        'count': len(image_urls) if image_urls else 0
                    }
                    results.append(result)
                    
                    if status == 'success':
                        total_success += 1
                        print(f"  产品 {product_id} 处理成功，图片数量: {len(image_urls) if image_urls else 0}")
                    else:
                        total_failed += 1
                        print(f"  产品 {product_id} 处理失败，未找到图片")
                    
                except Exception as e:
                    # 9. 错误处理
                    total_failed += 1
                    print(f"  处理产品 {product_id} 时出错: {str(e)}")
                    results.append({
                        'product_id': product_id,
                        'record_id': record_id,
                        'status': 'error',
                        'error': str(e)
                    })
                finally:
                    # 10. 添加延迟避免请求过于频繁
                    time.sleep(3)
        
        # 11. 输出汇总信息
        print(f"\n=== 处理完成 ===")
        print(f"总处理产品数: {total_processed}")
        print(f"成功: {total_success}")
        print(f"失败: {total_failed}")
        
        if download_images:
            print(f"图片已保存到: {images_folder}")
        
        return results
    
    def download_image(self, image_url, product_id, folder):
        """
        下载图片到本地
        :param image_url: 图片URL
        :param product_id: 产品ID，用于命名文件
        :param folder: 保存文件夹路径
        """
        try:
            response = requests.get(image_url, timeout=30)
            if response.status_code == 200:
                # 从URL获取文件扩展名
                ext = '.jpg'  # 默认扩展名
                if '?' in image_url:
                    clean_url = image_url.split('?')[0]
                else:
                    clean_url = image_url
                if '.' in clean_url.split('/')[-1]:
                    ext = '.' + clean_url.split('.')[-1]
                    if ext not in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
                        ext = '.jpg'  # 默认使用jpg
                
                filename = f"{product_id}{ext}"
                filepath = os.path.join(folder, filename)
                
                with open(filepath, 'wb') as f:
                    f.write(response.content)
                
                print(f"    图片已下载: {filename}")
            else:
                print(f"    下载图片失败，状态码: {response.status_code}")
        except Exception as e:
            print(f"    下载图片时出错: {e}")

    def download_images(self, image_urls, product_id, base_folder, product_title="", product_description=""):
        """
        下载多个图片到本地，每个产品一个文件夹
        :param image_urls: 图片URL列表，包含字典格式(带标题和类型)
        :param product_id: 产品ID，用于命名文件夹
        :param base_folder: 基础保存文件夹路径
        :param product_title: 产品标题，用于保存到文本文件
        :param product_description: 产品描述，用于保存到文本文件
        """
        # 为每个产品创建单独的文件夹
        product_folder = os.path.join(base_folder, str(product_id))
        os.makedirs(product_folder, exist_ok=True)
        
        # 保存产品标题到文本文件
        if product_title:
            title_file_path = os.path.join(product_folder, "product_title.txt")
            try:
                with open(title_file_path, 'w', encoding='utf-8') as title_file:
                    title_file.write(product_title)
                print(f"    产品标题已保存: {title_file_path}")
            except Exception as e:
                print(f"    保存产品标题时出错: {e}")
        
        # 保存产品描述到文本文件
        if product_description:
            desc_file_path = os.path.join(product_folder, "product_description.txt")
            try:
                with open(desc_file_path, 'w', encoding='utf-8') as desc_file:
                    desc_file.write(product_description)
                print(f"    产品描述已保存: {desc_file_path}")
            except Exception as e:
                print(f"    保存产品描述时出错: {e}")
        
        # 保存图片URL到CSV文件
        image_csv_path = os.path.join(product_folder, "image_urls.csv")
        try:
            with open(image_csv_path, 'w', newline='', encoding='utf-8') as csv_file:
                writer = csv.writer(csv_file)
                writer.writerow(['Index', 'Image_URL', 'Title', 'Type'])  # 写入表头
                for idx, image_data in enumerate(image_urls):
                    if isinstance(image_data, dict):
                        writer.writerow([idx+1, image_data['url'], image_data['title'], image_data.get('type', 'main')])
                    else:
                        writer.writerow([idx+1, image_data, 'image', 'main'])
            print(f"    图片URL已保存到CSV: {image_csv_path}")
        except Exception as e:
            print(f"    保存图片URL到CSV时出错: {e}")
        
        for idx, image_data in enumerate(image_urls):
            try:
                # 处理图片URL，现在都是字典格式
                if isinstance(image_data, dict):
                    # 根据类型处理图片
                    image_url = image_data['url']
                    title = image_data['title']
                    img_type = image_data.get('type', 'main')
                    
                    # 清理标题作为文件名
                    # 移除文件名中不允许的字符
                    clean_title = re.sub(r'[<>:"/\\|?*]', '_', title)
                    clean_title = clean_title[:50]  # 限制长度
                    
                    if img_type == 'sku':
                        # SKU图片，使用标题作为文件名的一部分
                        filename = f"sku_{clean_title}_{idx+1:02d}"
                    else:
                        # 普通主图
                        filename = f"main_{idx+1:02d}"
                else:
                    # 处理旧格式（字符串）
                    image_url = image_data
                    filename = f"{product_id}_main_{idx+1:02d}"
                
                # 获取文件扩展名
                ext = '.jpg'  # 默认扩展名
                if '?' in image_url:
                    clean_url = image_url.split('?')[0]
                else:
                    clean_url = image_url
                if '.' in clean_url.split('/')[-1]:
                    ext = '.' + clean_url.split('.')[-1]
                    if ext not in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
                        ext = '.jpg'  # 默认使用jpg
                
                filename += ext
                filepath = os.path.join(product_folder, filename)
                
                response = requests.get(image_url, timeout=30)
                if response.status_code == 200:
                    with open(filepath, 'wb') as f:
                        f.write(response.content)
                    
                    print(f"    图片已下载: {filename}")
                else:
                    print(f"    下载图片失败，状态码: {response.status_code}")
            except Exception as e:
                print(f"    下载图片时出错: {e}")


def get_empty_product_source_imgs_records(config_path='config.json'):
    """
    根据配置文件读取表格，返回product_source_imgs为None的product_id和record_id
    :param config_path: 配置文件路径
    :return: dict数组，每个字典包含product_id和record_id
    """
    # 1. 读取配置文件
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        # 2. 提取配置信息
        feishu_config = config.get('feishu', {})
        bitable_config = config.get('bitable', {})
        
        app_id = feishu_config.get('app_id')
        app_secret = feishu_config.get('app_secret')
        app_token = bitable_config.get('app_token')
        table_id = bitable_config.get('table_id')
        
        if not all([app_id, app_secret, app_token, table_id]):
            print("错误：配置文件缺少必要的参数")
            return []
        
    except Exception as e:
        print(f"读取配置文件失败: {str(e)}")
        return []
    
    # 3. 初始化FeishuSheet实例
    try:
        feishu_sheet = FeishuSheet(app_id, app_secret)
    except Exception as e:
        print(f"初始化FeishuSheet失败: {str(e)}")
        return []
    
    # 4. 使用过滤条件查询product_source_imgs为空的记录
    try:
        # 使用指定的JSON格式过滤条件
        filter_formula = {
            "conjunction": "and",
            "conditions": [{
                "field_name": "product_source_imgs",
                "operator": "isEmpty",
                "value": []
            }]
        }
        
        result = feishu_sheet.get_records_by_filter(
            app_token=app_token,
            table_id=table_id,
            filter_formula=filter_formula,  # 过滤条件：product_source_imgs为空
            get_all=True
        )
        
        if not result:
            print("查询记录失败")
            return []
        
    except Exception as e:
        print(f"查询记录失败: {str(e)}")
        return []
    
    # 5. 处理查询结果
    # 从响应中提取记录
    records = result.get('data', {}).get('items', [])
    
    # 处理 items 为 None 的情况
    if records is None:
        records = []
    
    # 提取product_id和record_id
    empty_product_source_imgs_records = []
    
    for record in records:
        record_id = record.get('record_id') or record.get('id')
        fields = record.get('fields', {})
        
        # 提取product_id
        product_id = fields.get('product_id')
        
        # 处理product_id格式，确保获取纯数字字符串
        if isinstance(product_id, list) and len(product_id) > 0 and isinstance(product_id[0], dict):
            product_id = product_id[0].get('text', '')
        
        # 确保product_id和record_id存在
        if product_id and record_id:
            empty_product_source_imgs_records.append({
                'product_id': product_id,
                'record_id': record_id
            })
    
    print(f"找到 {len(empty_product_source_imgs_records)} 条product_source_imgs为None的记录")
    
    return empty_product_source_imgs_records


def main():
    """
    主函数 - 使用示例
    
    使用固定Chrome用户配置文件的方法：
    1. 先手动打开Chrome浏览器，创建或使用一个现有配置文件
    2. 找到Chrome的用户数据目录，默认位置通常为:
       Windows: C:/Users/<用户名>/AppData/Local/Google/Chrome/User Data
       macOS: ~/Library/Application Support/Google/Chrome
       Linux: ~/.config/google-chrome
    3. 确定使用的配置文件名称，如"Default"或"Profile 1"
    4. 在创建scraper实例时传入这些参数
    """
    # 创建示例输入文件
    sample_input = "product_ids.txt"
    if not os.path.exists(sample_input):
        with open(sample_input, 'w', encoding='utf-8') as f:
            f.write("1731572915654201371\n")
            # 可以添加更多示例ID
        print(f"已创建示例输入文件: {sample_input}")
    
    # 使用固定的Chrome用户配置文件示例
    scraper = TikTokProductScraperPlaywright(headless=False, user_data_dir=r"./User Data/Default", profile_name="Default")
    # scraper = TikTokProductScraperPlaywright(headless=False)  # 设置为True可无头模式运行
    
    try:
        # 抓取产品图片
        results = scraper.scrape_products(sample_input, download_images=True)
        
        # 打印汇总信息
        successful = sum(1 for r in results if r['status'] == 'success')
        total_images = sum(r['count'] for r in results)
        print(f"\n完成! 成功获取 {successful}/{len(results)} 个产品的图片，共下载 {total_images} 张图片")
        
    except KeyboardInterrupt:
        print("\n用户中断操作")
    except Exception as e:
        print(f"发生错误: {e}")


def main_get_empty_product_source_imgs():
    """
    测试get_empty_product_source_imgs_records方法
    """
    print("=== 开始获取product_source_imgs为空的记录 ===")
    
    records = get_empty_product_source_imgs_records()
    
    print("\n=== 结果 ===")
    if records:
        print(f"找到 {len(records)} 条product_source_imgs为空的记录:")
        for i, record in enumerate(records):
            print(f"  {i+1}. product_id: {record['product_id']}, record_id: {record['record_id']}")
    else:
        print("没有找到product_source_imgs为空的记录")
    
    return records

def main_process_empty_product_source_imgs():
    """
    获取product_source_imgs为空的记录并调用scrape_products方法处理
    """
    print("=== 开始处理product_source_imgs为空的记录 ===")
    
    # 1. 获取product_source_imgs为空的记录
    empty_records = get_empty_product_source_imgs_records()
    
    if not empty_records:
        print("没有找到需要处理的记录")
        return
    
    print(f"找到 {len(empty_records)} 条需要处理的记录")
    
    # 2. 读取配置文件获取必要参数
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        feishu_config = config.get('feishu', {})
        bitable_config = config.get('bitable', {})
        
        app_id = feishu_config.get('app_id')
        app_secret = feishu_config.get('app_secret')
        app_token = bitable_config.get('app_token')
        table_id = bitable_config.get('table_id')
        
        if not all([app_id, app_secret, app_token, table_id]):
            print("错误：配置文件缺少必要的参数")
            return
        
    except Exception as e:
        print(f"读取配置文件失败: {str(e)}")
        return
    
    # 3. 初始化FeishuSheet实例
    try:
        feishu_sheet = FeishuSheet(app_id, app_secret)
        print("成功初始化FeishuSheet实例")
    except Exception as e:
        print(f"初始化FeishuSheet失败: {str(e)}")
        return
    
    # 4. 创建TikTokProductScraperPlaywright实例
    try:
        scraper = TikTokProductScraperPlaywright()
        print("成功初始化TikTokProductScraperPlaywright实例")
    except Exception as e:
        print(f"初始化TikTokProductScraperPlaywright失败: {str(e)}")
        return
    
    # 5. 调用scrape_products方法处理记录
    print("\n=== 开始处理记录 ===")
    print(f"共处理 {len(empty_records)} 条记录")
    
    try:
        results = scraper.scrape_products(
            product_ids=empty_records,
            feishu_sheet=feishu_sheet,
            app_token=app_token,
            table_id=table_id,
            download_images=False,
            batch_size=10
        )
        
        # 6. 打印处理结果
        print("\n=== 处理结果 ===")
        successful = sum(1 for r in results if r.get('status') == 'success')
        failed = sum(1 for r in results if r.get('status') == 'error')
        
        print(f"处理完成! 成功: {successful}, 失败: {failed}, 总计: {len(results)}")
        
        if failed > 0:
            print("\n失败的记录:")
            for i, r in enumerate(results):
                if r.get('status') == 'error':
                    print(f"  {i+1}. product_id: {r.get('product_id')}, 错误: {r.get('error')}")
        
    except Exception as e:
        print(f"处理记录时发生错误: {str(e)}")
    finally:
        # 关闭scraper实例
        try:
            scraper.close()
        except:
            pass


if __name__ == "__main__":
    # 可以根据需要选择运行哪个函数
    # main()  # 运行原有的主函数
    # main_get_empty_product_desc()  # 运行获取空product_desc的函数
    # main_process_empty_product_desc()  # 获取空product_desc的记录并调用scrape_products处理
    # main_get_empty_product_source_imgs()  # 运行获取空product_source_imgs的函数
    main_process_empty_product_source_imgs()  # 获取空product_source_imgs的记录并调用scrape_products处理