# Copilot Proxy 配置
import os
import sys

# 项目根目录（区分打包和开发环境）
if getattr(sys, 'frozen', False):
    # PyInstaller 打包后：exe 同级目录可写，_MEIPASS 只读
    DATA_DIR = os.path.dirname(sys.executable)
    PROJECT_DIR = sys._MEIPASS
else:
    DATA_DIR = os.path.dirname(os.path.abspath(__file__))
    PROJECT_DIR = DATA_DIR

# ======================================================================
# Substrate (M365 Premium Copilot) 配置
# ======================================================================
SUBSTRATE_WS_BASE = "wss://substrate.office.com/m365Copilot/Chathub/00000000-0000-0000-397c-531d34aaff3d@84df9e7f-e9f6-40af-b435-aaaaaaaaaaaa"
SUBSTRATE_ORIGIN = "https://m365.cloud.microsoft"

# ======================================================================
# MSAL 认证配置
# ======================================================================
MSAL_CLIENT_ID = "14638111-3389-403d-b206-a6a71d9f8f16"  # Copilot 客户端 ID
MSAL_AUTHORITY = "https://login.microsoftonline.com/common"
MSAL_SCOPE = "140e65af-45d1-4427-bf08-3e7295db6836/ChatAI.ReadWrite"

# ======================================================================
# Token 文件
# ======================================================================
SUBSTRATE_TOKEN_FILE = "substrate_token.txt"
TOKEN_CACHE_FILE = "token_cache.json"
MSAL_CACHE_FILE = "msal_cache.bin"

# ======================================================================
# 反代服务器配置
# ======================================================================
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8080

# ======================================================================
# Dashboard 配置
# ======================================================================
DASHBOARD_HOST = "0.0.0.0"
DASHBOARD_PORT = 7860

# ======================================================================
# 账号管理
# ======================================================================
ACCOUNTS_FILE = os.path.join(DATA_DIR, "accounts.json")

# ======================================================================
# Token 策略
# ======================================================================
TOKEN_TTL = 3600            # Token 有效期（秒）
TOKEN_REFRESH_COOLDOWN = 120  # 刷新失败冷却（秒）
TOKEN_PRE_REFRESH_MARGIN = 300  # 提前刷新余量（秒）

# ======================================================================
# Playwright（自动登录用 Chromium）
# ======================================================================
PLAYWRIGHT_HEADLESS = False  # False 显示浏览器窗口，方便观察

# ======================================================================
# 日志
# ======================================================================
LOG_DIR = os.path.join(DATA_DIR, "logs")
MAX_LOG_ENTRIES = 10000

# Copilot ?? WebSocket ???????????
COPILOT_CHAT_TIMEOUT = 120
