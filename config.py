# Copilot Proxy configuration
import os
import sys


if getattr(sys, 'frozen', False):
    DATA_DIR = os.path.dirname(sys.executable)
    PROJECT_DIR = sys._MEIPASS
else:
    DATA_DIR = os.path.dirname(os.path.abspath(__file__))
    PROJECT_DIR = DATA_DIR


# Substrate (M365 Premium Copilot)
SUBSTRATE_WS_BASE = "wss://substrate.office.com/m365Copilot/Chathub/00000000-0000-0000-397c-531d34aaff3d@84df9e7f-e9f6-40af-b435-aaaaaaaaaaaa"
SUBSTRATE_ORIGIN = "https://m365.cloud.microsoft"


# MSAL authentication
MSAL_CLIENT_ID = "14638111-3389-403d-b206-a6a71d9f8f16"
MSAL_AUTHORITY = "https://login.microsoftonline.com/common"
MSAL_SCOPE = "140e65af-45d1-4427-bf08-3e7295db6836/ChatAI.ReadWrite"


# Token files
SUBSTRATE_TOKEN_FILE = "substrate_token.txt"
TOKEN_CACHE_FILE = "token_cache.json"
MSAL_CACHE_FILE = "msal_cache.bin"


# Server
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8080


# Dashboard
DASHBOARD_HOST = "0.0.0.0"
DASHBOARD_PORT = 7860


# Accounts
ACCOUNTS_FILE = os.path.join(DATA_DIR, "accounts.json")


# Token policy
TOKEN_TTL = 3600
TOKEN_REFRESH_COOLDOWN = 120
TOKEN_PRE_REFRESH_MARGIN = 300


# Playwright login
PLAYWRIGHT_HEADLESS = os.getenv("M365_PLAYWRIGHT_HEADLESS", "1").lower() in ("1", "true", "yes", "on")
LOGIN_TIMEOUT = int(os.getenv("M365_LOGIN_TIMEOUT", "120"))
LOGIN_RETRIES = int(os.getenv("M365_LOGIN_RETRIES", "2"))
LOGIN_RETRY_DELAY = int(os.getenv("M365_LOGIN_RETRY_DELAY", "8"))
LOGIN_CREDENTIAL_TTL = int(os.getenv("M365_CREDENTIAL_TTL", "180"))


# Logs
LOG_DIR = os.path.join(DATA_DIR, "logs")
MAX_LOG_ENTRIES = 10000


COPILOT_CHAT_TIMEOUT = 120
