# -*- coding: utf-8 -*-
import imaplib
import email
from multiprocessing import parent_process
from pydoc import text
import re
import time
import random
import string
import socket
import socks
import logging
from email.header import decode_header
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, Page
import argparse
import requests

# from hednet.main import REGISTER_URL
"""
    Hednet 积分系统 V2
    参数化 邀请码、真实Gmail邮箱、Gmail 应用专用密码、超时时间
    加入检测条件，通过收件人检验收件人是否一致，增加邮件获取的准确性
"""

parser = argparse.ArgumentParser(description="Hednet 自动化刷分系统")
parser.add_argument("-ref","--referral",required=True,help="邀请码")
parser.add_argument("--real-gmail",help="真实Gmail邮箱")
parser.add_argument("--password",help="Gmail 应用专用密码")
parser.add_argument("--timeout",type=int,default=120,help="超时时间(秒)")
parser.add_argument("--debug",action="store_true",help="调试模式")

args = parser.parse_args()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("hednet.log")
    ]
)

logger = logging.getLogger('GmailVerifier')
logger.info(f'当前邀请码：{args.referral}')
logger.info(f'当前主邮箱：{args.real_gmail}')
logger.info(f'当前专用密码:{args.password}')

REGISTER_URL = f'https://app.hednet.io/register?referral_code={args.referral}'

logger.info(f'{REGISTER_URL}')
# 配置常量
class Config:
    # 代理配置
    USE_PROXY = False # True 开启代理 False 关闭代理
    PROXY_HOST = "127.0.0.1"
    PROXY_PORT = 7892
    PROXY_TYPE = socks.SOCKS5

    # Hednet邮件配置
    HEDNET_SENDER = "Hednet Protocol"
    HEDNET_SUBJECT = "Confirm Your Signup"
    
    # 验证码配置
    VERIFICATION_CODE_LENGTH = 6
    MAX_WAIT_TIME = 180  # 最大等待时间（秒）
    MAIL_CHECK_INTERVAL = 2  # 邮件检查间隔（秒）
    MAIL_LOOKBACK_TIME = 300  # 邮件回溯时间（秒）
    
    # 邮件文件夹列表
    MAIL_FOLDERS = ['INBOX']  # 只使用INBOX文件夹，避免编码和解析问题
    
    # 注册配置
    REGISTER_PASSWORD = "1234567890QAZ@wsx"  # 注册密码
    BROWSER_TIMEOUT = 60000  # 浏览器操作超时时间（毫秒）

class EmailProcessor:
    """邮件处理器，负责邮件的获取和解析"""

    @staticmethod
    def extract_sender(msg):
        """提取邮件发件人"""
        return msg.get("From","")
    
    @staticmethod
    def extract_receive(msg):
        """提取邮件接收人"""
        return msg.get("To","")
    
    @staticmethod
    def extract_subject(msg):
        """提取邮件主题（处理编码问题）"""
        subject = msg.get("Subject","")
        if not subject:
            return ""
        try:
            decoded = decode_header(subject)
            return "".join([
                text.decode(encoding or "utf-8") if isinstance(text,bytes) else text
                for text,encoding in decoded
            ])
        except Exception as e:
            logger.error(f"解析邮件主题时出错：{e}")
            return subject

    @staticmethod
    def extract_html_parts(msg):
        """提取邮件中的HTML部分"""
        html_parts = []
        
        try:
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/html":
                        payload = part.get_payload(decode=True)
                        if payload:
                            html_parts.append(payload.decode(errors='ignore'))
            else:
                if msg.get_content_type() == "text/html":
                    payload = msg.get_payload(decode=True)
                    if payload:
                        html_parts.append(payload.decode(errors='ignore'))
        except Exception as e:
            logger.error(f"提取HTML部分时出错: {e}")
    
        return html_parts

class CaptchaExtractor:
    """验证码提取器，负责从HTML中提取验证码"""
    
    @staticmethod
    def normalize_text(text):
        """规范化文本，处理空白字符"""
        # 替换所有空白字符（包括&nbsp;）为空格，然后去除
        normalized = re.sub(r'\s+', '', text)
        # 去掉所有非数字
        return re.sub(r'\D', '', normalized)
    
    @staticmethod
    def extract_from_yellow_button(soup):
        """从黄色按钮中提取验证码"""
        # 尝试多种方式匹配黄色背景的div
        yellow_divs = []
        # 1.1 直接匹配背景色#ffa918
        yellow_divs.extend(soup.find_all("div", style=lambda s: s and "#ffa918" in s))
        # 1.2 匹配背景色#FFA918（大写）
        yellow_divs.extend(soup.find_all("div", style=lambda s: s and "#FFA918" in s))
        # 1.3 匹配background-color样式
        yellow_divs.extend(soup.find_all("div", style=lambda s: s and "background-color" in s and ("#ffa918" in s or "#FFA918" in s)))
        
        for i, button in enumerate(yellow_divs):
            try:
                # 获取按钮的原始HTML内容用于调试
                button_html = str(button)
                # 特别处理包含大量&nbsp;的情况
                raw = button.get_text()
                # 规范化文本
                normalized = CaptchaExtractor.normalize_text(raw)
                
                if len(normalized) == Config.VERIFICATION_CODE_LENGTH:
                    logger.debug(f"方法1-{i+1}匹配到验证码: {normalized}")
                    logger.debug(f"原始文本: '{raw}'")
                    logger.debug(f"规范化后: '{normalized}'")
                    logger.debug(f"按钮HTML: '{button_html[:100]}...'")
                    return normalized
            except Exception as e:
                logger.error(f"处理黄色按钮时出错: {e}")
        
        return None
    
    @staticmethod
    def extract_from_large_font(soup):
        """从大字体和letter-spacing元素中提取验证码"""
        try:
            for div in soup.find_all("div"):
                style = div.get("style")
                if not style:
                    continue
                
                # 检查是否有letter-spacing或大字体
                if "letter-spacing" in style or any(
                    keyword in style for keyword in ["font-size:2", "font-size:3", "font-size: 2", "font-size: 3"]
                ):
                    # 特别处理包含大量&nbsp;的情况
                    raw = div.get_text()
                    # 规范化文本
                    normalized = CaptchaExtractor.normalize_text(raw)
                    
                    if len(normalized) == Config.VERIFICATION_CODE_LENGTH:
                        logger.debug(f"方法2匹配到验证码: {normalized}")
                        logger.debug(f"原始文本: '{raw}'")
                        logger.debug(f"规范化后: '{normalized}'")
                        logger.debug(f"样式: '{style}'")
                        return normalized
        except Exception as e:
            logger.error(f"从大字体元素提取验证码时出错: {e}")
        
        return None
    
    @staticmethod
    def extract_from_global_text(soup):
        """从全局文本中提取验证码"""
        try:
            text = soup.get_text()
            # 打印文本的前200个字符用于调试
            logger.debug(f"页面文本前200字符: '{text[:200]}...'")
            # 把所有非数字之间的空格干掉，变成连续数字
            cleaned = re.sub(r'\D+', '', text)
            logger.debug(f"清理后数字序列: '{cleaned}'")
            
            match = re.search(r'\d{6}', cleaned)
            if match:
                logger.debug(f"方法3匹配到验证码: {match.group()}")
                return match.group()
        except Exception as e:
            logger.error(f"从全局文本提取验证码时出错: {e}")
        
        return None
    
    @staticmethod
    def extract_from_raw_html(html_content):
        """直接从原始HTML中提取验证码"""
        try:
            # 查找包含#ffa918的div及其内容（不区分大小写）
            yellow_div_pattern = r'<div[^>]*#[Ff]{0,1}[Ff]{0,1}[Aa][Aa]918[^>]*>(.*?)</div>'
            yellow_match = re.search(yellow_div_pattern, html_content, re.DOTALL)
            
            if yellow_match:
                div_content = yellow_match.group(1)
                # 移除所有HTML标签
                clean_content = re.sub(r'<[^>]*>', '', div_content)
                # 移除所有非数字字符
                code = re.sub(r'\D', '', clean_content)
                
                if len(code) == Config.VERIFICATION_CODE_LENGTH:
                    logger.debug(f"方法4匹配到验证码: {code}")
                    logger.debug(f"div内容: '{clean_content}'")
                    return code
        except Exception as e:
            logger.error(f"从原始HTML提取验证码时出错: {e}")
        
        return None
    
    @staticmethod
    def extract_directly_from_html(html_content):
        """直接搜索整个HTML中的6位数字模式"""
        try:
            # 更宽松的模式，不要求数字前后必须有非数字字符
            direct_match = re.search(r'(\d{6})', html_content)
            if direct_match:
                code = direct_match.group(1)
                logger.debug(f"方法5直接匹配到验证码: {code}")
                
                # 检查这个位置周围的HTML上下文
                start = max(0, direct_match.start() - 20)
                end = min(len(html_content), direct_match.end() + 20)
                context = html_content[start:end]
                logger.debug(f"上下文: '{context}'")
                
                return code
        except Exception as e:
            logger.error(f"直接从HTML搜索验证码时出错: {e}")
        
        return None
    
    @staticmethod
    def extract_from_raw_text(html_content):
        """从原始文本中提取数字序列"""
        try:
            # 直接从原始HTML中提取文本，然后查找6位数字
            raw_text = BeautifulSoup(html_content, "html.parser").get_text()
            # 查找所有4位以上的数字序列
            all_numbers = re.findall(r'\d{4,}', raw_text)
            logger.debug(f"找到的所有数字序列: {all_numbers}")
            
            for num_str in all_numbers:
                # 查找其中的6位数字
                six_digit = re.search(r'\d{6}', num_str)
                if six_digit:
                    code = six_digit.group(1)
                    logger.debug(f"方法6匹配到验证码: {code}")
                    return code
        except Exception as e:
            logger.error(f"从原始文本提取验证码时出错: {e}")
        
        return None
    
    @classmethod
    def extract_code_from_html(cls, html_content):
        """从HTML内容中提取验证码，尝试多种方法"""
        logger.debug("开始提取验证码...")
        
        try:
            soup = BeautifulSoup(html_content, "html.parser")
            
            # 按优先级尝试各种提取方法
            extraction_methods = [
                ("黄色按钮", lambda: cls.extract_from_yellow_button(soup)),
                ("大字体元素", lambda: cls.extract_from_large_font(soup)),
                ("全局文本", lambda: cls.extract_from_global_text(soup)),
                ("原始HTML", lambda: cls.extract_from_raw_html(html_content)),
                ("HTML直接搜索", lambda: cls.extract_directly_from_html(html_content)),
                ("原始文本数字序列", lambda: cls.extract_from_raw_text(html_content))
            ]
            
            for method_name, method_func in extraction_methods:
                try:
                    code = method_func()
                    if code:
                        logger.info(f"使用{method_name}方法成功提取验证码: {code}")
                        return code
                except Exception as e:
                    logger.error(f"{method_name}方法执行出错: {e}")
            
            logger.warning("所有验证码提取方法都失败了")
            return None
        except Exception as e:
            logger.error(f"提取验证码时发生错误: {e}")
            return None

class ImapConnection:
    """IMAP连接管理"""
    
    def __init__(self):
        self.imap = None
    
    def connect(self):
        """建立IMAP连接"""
        if self.imap:
            logger.debug("IMAP连接已存在，跳过连接建立")
            return True
        
        max_retries = 3
        retry_delay = 5
        
        for retry in range(max_retries):
            try:
                # 配置代理
                if Config.USE_PROXY:
                    logger.debug(f"使用代理: {Config.PROXY_HOST}:{Config.PROXY_PORT}")
                    socks.set_default_proxy(Config.PROXY_TYPE, Config.PROXY_HOST, Config.PROXY_PORT)
                    socket.socket = socks.socksocket
                
                # 建立连接
                logger.debug(f"正在连接到Gmail IMAP服务器 (尝试 {retry+1}/{max_retries})...")
                # 增加超时时间
                self.imap = imaplib.IMAP4_SSL('imap.gmail.com', timeout=60)
                
                # 登录
                logger.debug("正在登录Gmail账户...")
                self.imap.login(args.real_gmail, args.password)
                logger.info("Gmail连接成功！")
                return True
            except Exception as e:
                logger.error(f"建立IMAP连接时出错 (尝试 {retry+1}/{max_retries}): {e}")
                self.imap = None
                
                if retry < max_retries - 1:
                    logger.info(f"{retry_delay}秒后重试...")
                    time.sleep(retry_delay)
        
        return False
    
    def close(self):
        """关闭IMAP连接"""
        if self.imap:
            try:
                # 先尝试关闭当前文件夹（如果有）
                try:
                    self.imap.close()
                except:
                    # 如果close失败，可能是因为当前不在SELECTED状态，直接忽略
                    pass
                # 然后执行logout
                self.imap.logout()
                logger.debug("IMAP连接已关闭")
            except Exception as e:
                logger.error(f"关闭IMAP连接时出错: {e}")
            finally:
                self.imap = None

class GmailVerifier:
    """Gmail验证码验证器，用于生成邮箱别名、自动注册和获取验证码"""
    def __init__(self):
        self.imap_conn = ImapConnection()
    def realistic_type(self, page: Page, selector: str, text: str, delay: float = 0.1):
        """模拟真实用户输入文本"""
        page.fill(selector, "")  # 先清空输入框
        page.type(selector, text, delay=delay)  # 逐字符输入，模拟真实用户
    def register_on_hednet(self, email: str):
        """使用Playwright自动在Hednet网站上注册账号"""
        logger.info(f"开始在Hednet网站注册，使用邮箱: {email}")
        
        try:
            playwright = sync_playwright().start()
            logger.info("Playwright启动成功")
            
            # 启动浏览器（使用无头模式可以提高速度）
            browser = playwright.chromium.launch(
                headless=True,  # 设置为True可以隐藏浏览器窗口
                slow_mo=100,  # 放慢操作速度，提高稳定性
                timeout=Config.BROWSER_TIMEOUT,
                # 增加启动参数以提高稳定性
                args=[
                    '--disable-gpu',
                    '--disable-extensions',
                    '--disable-dev-shm-usage',
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-features=site-per-process'
                ]
            )
            
            # 创建新页面
            logger.info("打开Hednet注册页面")
            page = browser.new_page()
            
            # 设置页面的超时时间
            page.set_default_timeout(Config.BROWSER_TIMEOUT)
            
            # 尝试多次访问注册页面，提高成功率
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    logger.info(f"尝试访问注册页面 (尝试 {attempt + 1}/{max_retries})...")
                    page.goto(REGISTER_URL, wait_until="domcontentloaded")
                    
                    # 等待页面加载完成
                    logger.info("等待注册页面元素加载...")
                    page.wait_for_load_state("networkidle")
                    break
                except Exception as e:
                    logger.warning(f"访问注册页面失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                    if attempt == max_retries - 1:
                        logger.error("多次尝试访问注册页面失败，程序退出")
                        browser.close()
                        playwright.stop()
                        return False, None, None, None
                    logger.info("等待5秒后重试...")
                    time.sleep(5)
            
            # 尝试多种方式定位邮箱输入框
            email_selectors = [
                "input#email",
                "input[type='email']",
                "input[placeholder*='email']",
                "input[placeholder*='Email']"
            ]
            
            password_selectors = [
                "input#password",
                "input[type='password']",
                "input[placeholder*='password']",
                "input[placeholder*='Password']"
            ]
            
            checkbox_selectors = [
                "input.custom-checkbox",
                "input[type='checkbox']",
                "input[aria-label*='terms']"
            ]
            
            register_button_selectors = [
                "button[type='submit']:has-text('Register')",
                "button[type='submit']",
                "button:has-text('Register')"
            ]
            
            # 输入邮箱 - 尝试多种选择器
            email_selector = None
            for selector in email_selectors:
                if page.locator(selector).count() > 0:
                    email_selector = selector
                    break
            
            if email_selector:
                logger.info(f"使用邮箱选择器: {email_selector}")
                self.realistic_type(page, email_selector, email)
                logger.info("邮箱已输入")
            else:
                logger.error("未找到邮箱输入框")
                browser.close()
                playwright.stop()
                return False, None, None, None
            
            # 输入密码 - 尝试多种选择器
            password_selector = None
            for selector in password_selectors:
                if page.locator(selector).count() > 0:
                    password_selector = selector
                    break
            
            if password_selector:
                logger.info(f"使用密码选择器: {password_selector}")
                self.realistic_type(page, password_selector, Config.REGISTER_PASSWORD)
                logger.info("密码已输入")
            else:
                logger.error("未找到密码输入框")
                browser.close()
                playwright.stop()
                return False, None, None, None
            
            # 勾选协议 - 尝试多种选择器
            checkbox_clicked = False
            for selector in checkbox_selectors:
                checkbox = page.locator(selector)
                if checkbox.count() > 0:
                    try:
                        checkbox.check()
                        logger.info(f"协议已勾选 (选择器: {selector})")
                        checkbox_clicked = True
                        break
                    except Exception as e:
                        logger.warning(f"勾选协议失败 (选择器: {selector}): {e}")
                        continue
            
            if not checkbox_clicked:
                logger.error("未找到可勾选的协议复选框")
                browser.close()
                playwright.stop()
                return False, None, None, None
            
            # 点击注册按钮 - 尝试多种选择器
            register_clicked = False
            for selector in register_button_selectors:
                button = page.locator(selector)
                if button.count() > 0:
                    try:
                        button.click()
                        logger.info(f"注册按钮已点击 (选择器: {selector})")
                        register_clicked = True
                        break
                    except Exception as e:
                        logger.warning(f"点击注册按钮失败 (选择器: {selector}): {e}")
                        continue
            
            if not register_clicked:
                logger.error("未找到可点击的注册按钮")
                browser.close()
                playwright.stop()
                return False, None, None, None
            
            # 等待注册请求完成
            logger.info("等待注册请求处理...")
            page.wait_for_load_state("networkidle")
            
            logger.info("Hednet注册流程第一步已完成，等待验证码")
            
            # 不关闭浏览器，保持会话打开
            logger.info("浏览器保持打开状态，等待验证码输入")
            
            return True, browser, page, playwright
            
        except Exception as e:
            logger.error(f"Hednet注册过程中发生错误: {e}")
            return False, None, None, None
    def complete_registration_with_code(self, browser, page, playwright, code):
        """使用获取到的验证码完成注册流程"""
        logger.info(f"开始完成注册流程，使用验证码: {code}")
        
        try:
            # 使用已打开的浏览器和页面，不需要重新启动
            logger.info("使用已打开的浏览器和页面进行验证码输入")
            
            # 等待验证码输入框加载
            logger.info("等待验证码输入框加载...")
            time.sleep(3)  # 额外等待，确保验证码输入框完全加载
            
            # 扩展验证码选择器列表
            code_selectors = [
                "input[name='code']",
                "input[type='text'][name*='code']",
                "input[placeholder*='code']",
                "input[placeholder*='Code']",
                "input[type='text']",
                "input[autocomplete*='one-time-code']",
                "input[aria-label*='code']",
                "input[aria-label*='Code']",
                "#verification-code",
                "[data-testid*='verification-code']"
            ]
            
            # 1. 尝试单个验证码输入框
            code_entered = False
            for selector in code_selectors:
                if page.locator(selector).count() > 0:
                    try:
                        logger.info(f"尝试单个验证码输入框: {selector}")
                        self.realistic_type(page, selector, code)
                        code_entered = True
                        break
                    except Exception as e:
                        logger.warning(f"输入验证码失败 (选择器: {selector}): {e}")
                        continue
            
            # 2. 尝试处理多个验证码输入框（每个数字一个输入框）
            if not code_entered:
                try:
                    logger.info("尝试查找多个验证码输入框...")
                    
                    # 查找可能的验证码输入框组
                    possible_input_groups = [
                        "div[class*='verification-code'] input",
                        "div[class*='code-input'] input",
                        "form[action*='verify'] input[type='text']",
                        "input[maxlength='1']",  # 单个数字输入框通常maxlength为1
                        "input[type='text']:not(#email):not(#password)"  # 排除邮箱和密码框
                    ]
                    
                    for group_selector in possible_input_groups:
                        inputs = page.locator(group_selector)
                        count = inputs.count()
                        
                        # 如果找到6个或接近6个输入框，很可能是验证码输入框组
                        if 4 <= count <= 8:
                            logger.info(f"发现{count}个可能的验证码输入框，尝试输入...")
                            
                            # 逐个输入数字
                            for i in range(min(count, len(code))):
                                try:
                                    input_field = inputs.nth(i)
                                    input_field.scroll_into_view_if_needed()
                                    input_field.click()
                                    input_field.fill("")
                                    input_field.type(code[i], delay=100)
                                    time.sleep(0.05)
                                except Exception as e:
                                    logger.warning(f"输入第{i+1}个数字失败: {e}")
                                    continue
                            
                            code_entered = True
                            logger.info("验证码已通过多个输入框输入")
                            break
                except Exception as e:
                    logger.warning(f"处理多个验证码输入框失败: {e}")
            
            # 3. 尝试直接使用键盘输入（如果焦点已经在验证码输入区域）
            if not code_entered:
                try:
                    logger.info("尝试直接使用键盘输入验证码...")
                    page.keyboard.press("Tab")  # 尝试切换到验证码输入区域
                    time.sleep(0.5)
                    
                    # 逐个输入数字
                    for char in code:
                        page.keyboard.type(char, delay=100)
                        time.sleep(0.05)
                    
                    code_entered = True
                    logger.info("验证码已通过键盘直接输入")
                except Exception as e:
                    logger.warning(f"直接键盘输入失败: {e}")
            
            if not code_entered:
                logger.error("未找到可输入验证码的输入框")
                # 尝试截图以便调试
                try:
                    page.screenshot(path="verification_page.png")
                    logger.info("已保存验证码页面截图到 verification_page.png")
                except Exception:
                    pass
                # 关闭浏览器
                browser.close()
                playwright.stop()
                return False

            logger.info("已输入验证码")
            
            # ------------------ 点击注册/确认按钮完成注册 ------------------
            logger.info("尝试点击注册/确认按钮完成注册...")
            
            # 定义注册按钮的选择器
            register_complete_selectors = [
                "button[data-slot='button']:has-text('Register')",
                "button:has-text('Register')",
                "button[type='submit']",
                "button[class*='bg-primary']",
                "button[class*='bg-[linear-gradient']",
                "button:has(svg[xmlns='http://www.w3.org/2000/svg']) ",
                "button:has-text('Confirm')",
                "button:has-text('Submit')"
            ]
            
            register_button_clicked = False
            for selector in register_complete_selectors:
                try:
                    button = page.locator(selector)
                    if button.count() > 0:
                        logger.info(f"找到注册按钮，使用选择器: {selector}")
                        button.scroll_into_view_if_needed()
                        button.click()
                        logger.info("注册按钮已点击，完成注册流程")
                        register_button_clicked = True
                        break
                except Exception as e:
                    logger.warning(f"点击注册按钮失败 (选择器: {selector}): {e}")
                    continue
            
            if not register_button_clicked:
                logger.error("未找到可点击的注册完成按钮")
                # 尝试截图以便调试
                try:
                    page.screenshot(path="register_complete_page.png")
                    logger.info("已保存注册完成页面截图到 register_complete_page.png")
                except Exception:
                    pass
            else:
                logger.info("注册流程完成")

            # 等待注册完成
            page.wait_for_load_state("networkidle")
            time.sleep(5)
            
            # 注册完成后关闭浏览器和Playwright
            browser.close()
            playwright.stop()
            
            return register_button_clicked
            
        except Exception as e:
            logger.error(f"完成注册过程中发生错误: {e}")
            return False

    def generate_alias(self):
        """生成Gmail别名邮箱地址"""
        mail = (args.real_gmail).split("@")
        suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
        alias = f"{mail[0]}+{suffix}@gmail.com"
        logger.info(f"生成邮箱别名: {alias}")
        return alias
    def get_mail_cutoff(self):
        """获取邮件搜索的时间截止点"""
        return time.strftime("%d-%b-%Y", time.localtime(time.time() - Config.MAIL_LOOKBACK_TIME))
    
    def search_emails(self, folder, start_time):
        """搜索指定文件夹中的邮件，只查找当前会话开始后的邮件"""
        try:
            # 跳过中文文件夹，避免编码问题
            if any(ord(c) > 128 for c in folder):
                logger.debug(f"跳过中文文件夹: {folder}")
                return []
            
            # 处理特殊文件夹名的引用问题
            if folder == '[Gmail]/All Mail':
                # 使用特殊方式选择[Gmail]/All Mail文件夹
                folder_encoded = 'All Mail'
            elif folder == '[Gmail]/Sent Mail':
                folder_encoded = 'Sent Mail'
            else:
                folder_encoded = folder
            
            # 选择邮件文件夹
            logger.debug(f"尝试选择文件夹: {folder_encoded}")
            select_status = self.imap_conn.imap.select(folder_encoded, readonly=True)
            if select_status[0] != 'OK':
                # 如果直接选择失败，尝试用引号包裹
                logger.debug(f"尝试用引号包裹文件夹名: {folder}")
                select_status = self.imap_conn.imap.select(f'"{folder}"', readonly=True)
                if select_status[0] != 'OK':
                    logger.debug(f"无法选择文件夹 {folder}: {select_status[1]}")
                    return []
            
            # 转换为IMAP需要的时间格式
            cutoff = time.strftime("%d-%b-%Y", time.localtime(start_time))
            
            # 先尝试搜索未读邮件
            status, data = self.imap_conn.imap.search(None, f'SINCE "{cutoff}"', 'UNSEEN')
            
            if status != 'OK' or not data[0]:
                logger.debug(f"文件夹 {folder} 无未读邮件，尝试所有邮件...")
                # 如果没有未读邮件，尝试所有邮件
                status, data = self.imap_conn.imap.search(None, f'SINCE "{cutoff}"')
                
                if status != 'OK' or not data[0]:
                    logger.debug(f"文件夹 {folder} 没有符合条件的邮件")
                    # 关闭当前文件夹
                    self.imap_conn.imap.close()
                    return []
            
            # 获取邮件ID列表
            ids = data[0].split()
            logger.debug(f"文件夹 {folder} 找到 {len(ids)} 封邮件")
            
            # 关闭当前文件夹
            self.imap_conn.imap.close()
            
            return ids
        except UnicodeEncodeError:
            logger.error(f"文件夹名编码错误 (文件夹: {folder}): 文件夹名包含非ASCII字符")
            return []
        except Exception as e:
            logger.error(f"搜索邮件时出错 (文件夹: {folder}): {e}")
            try:
                # 尝试关闭当前文件夹以恢复状态
                self.imap_conn.imap.close()
            except:
                pass
            return []

    def process_email(self, eid, start_time, current_alias):
        """处理单个邮件，提取并验证验证码，确保只处理当前会话期间的邮件"""
        try:
            # 重新选择默认文件夹来获取邮件内容
            select_status = self.imap_conn.imap.select('INBOX', readonly=True)
            if select_status[0] != 'OK':
                logger.debug(f"无法选择INBOX文件夹: {select_status[1]}")
                return None
            
            # 获取邮件内容
            status, msg_data = self.imap_conn.imap.fetch(eid, '(RFC822)')
            if status != 'OK':
                logger.debug(f"获取邮件 {eid} 失败")
                # 关闭当前文件夹
                self.imap_conn.imap.close()
                return None
            
            # 解析邮件
            msg = email.message_from_bytes(msg_data[0][1])
            
            # 检查邮件的接收时间
            date_str = msg.get('Date')
            if date_str:
                try:
                    # 解析邮件时间
                    msg_time = email.utils.parsedate_to_datetime(date_str)
                    # 转换为时间戳进行比较
                    msg_timestamp = msg_time.timestamp()
                    
                    # 如果邮件在会话开始前发送，跳过
                    if msg_timestamp < start_time:
                        logger.debug(f"跳过旧邮件：发送时间 {msg_time} 在会话开始前")
                        self.imap_conn.imap.close()
                        return None
                except Exception as e:
                    logger.warning(f"解析邮件时间失败：{e}")
            
            # 提取发件人、主题和收件人
            sender = EmailProcessor.extract_sender(msg)
            subject = EmailProcessor.extract_subject(msg)
            recipient = EmailProcessor.extract_receive(msg)
            
            logger.debug(f"检查邮件: 发件人='{sender}', 主题='{subject}', 收件人='{recipient}'")
            
            # 检查是否为Hednet的验证邮件，并且收件人是当前使用的邮件别名
            if Config.HEDNET_SENDER not in sender or Config.HEDNET_SUBJECT not in subject or current_alias not in recipient:
                logger.debug("跳过邮件: 发件人不匹配、主题不匹配或收件人不是当前使用的邮件别名")
                self.imap_conn.imap.close()
                return None
            
            logger.info("找到匹配的Hednet邮件！")
            
            # 提取所有HTML部分
            html_parts = EmailProcessor.extract_html_parts(msg)
            
            # 遍历每个HTML部分提取验证码
            for html in html_parts:
                code = CaptchaExtractor.extract_code_from_html(html)
                if code:
                    logger.info(f"成功捕获验证码 → {code}")
                    self.imap_conn.imap.close()
                    return code
            
            self.imap_conn.imap.close()
            return None
        except Exception as e:
            logger.error(f"处理邮件 {eid} 时出错: {e}")
            return None
    def wait_for_code(self, timeout=Config.MAX_WAIT_TIME):
        """生成邮箱别名，自动注册，然后等待并获取验证码，最后完成注册"""
        # 记录会话开始时间，确保邮件搜索范围是从代码执行开始的时间
        start_time = time.time()
        
        # 生成邮箱别名
        alias = self.generate_alias()
        logger.info(f"生成的邮箱别名：{alias}")

        # 自动在Hednet网站注册
        success, browser, page, playwright = self.register_on_hednet(alias)
        if not success:
            logger.error("注册失败，程序退出")
            return None

        logger.info(f"注册第一步成功！开始扫描验证码（已适配 Hednet 验证码），最多 {timeout} 秒...")
        
        # 建立IMAP连接
        if not self.imap_conn.connect():
            logger.error("无法建立Gmail连接，程序退出")
            # 关闭浏览器资源
            if browser:
                browser.close()
                playwright.stop()
            return None
        # 只搜索当前会话开始后收到的邮件，使用当前时间作为搜索起点
        current_time = time.strftime("%d-%b-%Y", time.localtime(start_time))
        current_hour = time.strftime("%H:%M:%S", time.localtime(start_time))
        logger.info(f"开始搜索验证码邮件，搜索范围：{current_time} {current_hour}之后的邮件")

        # 循环扫描邮件
        while time.time() - start_time < timeout:
            elapsed = int(time.time() - start_time)
            
            # 遍历所有邮件文件夹
            for folder in Config.MAIL_FOLDERS:
                # 搜索邮件，传入当前会话开始时间
                ids = self.search_emails(folder, start_time)
                if not ids:
                    continue
                
                # 从最新邮件开始检查
                for eid in reversed(ids):
                    code = self.process_email(eid, start_time, alias)
                    if code:
                        # 使用获取到的验证码完成注册
                        if self.complete_registration_with_code(browser, page, playwright, code):
                            logger.info("注册流程完全完成！")
                        else:
                            logger.error("验证码输入或注册完成失败！")
                        return code
            
            logger.info(f"[{elapsed}s] 暂无新邮件，继续等待...")
            time.sleep(Config.MAIL_CHECK_INTERVAL)

        logger.warning("超时未收到验证码")
        return None
    
    def close(self):
        """关闭资源"""
        # 关闭IMAP连接
        self.imap_conn.close()
 # ====================== 运行 ======================
if __name__ == "__main__":
    verifier = GmailVerifier()
    try:
        code = verifier.wait_for_code(timeout=Config.MAX_WAIT_TIME)
        if code:
            print(f"\n最终验证码：{code}")
            logger.info(f"程序执行成功，验证码: {code}")
        else:
            print("\n未能获取验证码")
            logger.info("程序执行完成，但未能获取验证码")
    except KeyboardInterrupt:
        logger.info("程序被用户中断")
    except Exception as e:
        logger.error(f"程序执行时发生错误: {e}")
    finally:
        verifier.close()
        logger.info("程序执行结束")
