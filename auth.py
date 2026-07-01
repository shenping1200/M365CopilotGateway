"""
认证管理器 - MSAL + Playwright Chromium 自动登录
支持 MSA (outlook.com) 和 AAD 账号
"""

import json
import time
import sys
import os
import re
import asyncio
import threading
import msal
import config

# 修复 Windows 控制台编码
if sys.stdout:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

def find_system_browser() -> str | None:
    candidates = [
        os.environ.get('M365_BROWSER_PATH', ''),
        r'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
        r'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
        r'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
        r'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


class AccountManager:
    """管理多个 M365 账号的认证和 token"""

    def __init__(self, accounts_file: str = None):
        self.accounts_file = accounts_file or config.ACCOUNTS_FILE
        self.accounts: list[dict] = []
        self.token_cache: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.current_index = 0
        self._refresh_lock = threading.Lock()  # 全局刷新锁，同时只允许1个浏览器
        self._last_refresh_fail: dict[str, float] = {}
        self._refreshing_accounts: set[str] = set()  # 正在后台刷新的账号
        self._auto_refresh_started = False
        # Token 消耗统计
        self._usage: dict[str, dict] = {}  # {username: {input_tokens, output_tokens, call_count}}
        self._usage_date: str = ''  # 当前统计日期
        self._load_accounts()
        self._load_token_cache()
        self._check_daily_reset()
        self._start_auto_refresh_loop()

    # ==================================================================
    # 数据加载
    # ==================================================================

    def _load_accounts(self):
        """从文件加载账号列表"""
        try:
            with open(self.accounts_file, 'r', encoding='utf-8') as f:
                self.accounts = json.load(f)
            print(f"[Auth] 已加载 {len(self.accounts)} 个账号")
        except FileNotFoundError:
            print(f"[Auth] 账号文件不存在: {self.accounts_file}")
            self.accounts = []
        except json.JSONDecodeError:
            print("[Auth] 账号文件格式错误")
            self.accounts = []

    def _load_token_cache(self):
        """从磁盘加载缓存的 token"""
        cache_file = os.path.join(config.DATA_DIR, config.TOKEN_CACHE_FILE)
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                self.token_cache = json.load(f)
            # 清理过期 token
            now = time.time()
            self.token_cache = {
                k: v for k, v in self.token_cache.items()
                if v.get('expires_at', 0) > now + 300
            }
            if self.token_cache:
                print(f"[Auth] 已加载 {len(self.token_cache)} 个缓存 token")
        except (FileNotFoundError, json.JSONDecodeError):
            self.token_cache = {}

    def _save_token_cache(self):
        """将 token 缓存保存到磁盘"""
        cache_file = os.path.join(config.DATA_DIR, config.TOKEN_CACHE_FILE)
        try:
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.token_cache, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[Auth] 保存缓存失败: {e}")


    def invalidate_token(self, username: str):
        """将指定账号的 token 标记为无效（WebSocket 401 时调用）"""
        with self.lock:
            if username in self.token_cache:
                del self.token_cache[username]
                self._save_token_cache()
                print(f"[Auth] {username} token 已失效（从缓存中移除）")
            self._last_refresh_fail.pop(username, None)  # 清除冷却期，允许立即重试

    # ==================================================================
    # 账号管理
    # ==================================================================

    def add_account(self, username: str, password: str, totp_secret: str = '') -> bool:
        """添加账号"""
        # 检查是否已存在
        for acc in self.accounts:
            if acc['username'] == username:
                return False
        self.accounts.append({
            'username': username,
            'password': password,
            'totp_secret': totp_secret,
        })
        self._save_accounts()
        return True

    def remove_account(self, username: str) -> bool:
        """删除账号"""
        original_len = len(self.accounts)
        self.accounts = [a for a in self.accounts if a['username'] != username]
        if len(self.accounts) < original_len:
            self._save_accounts()
            self.token_cache.pop(username, None)
            self._save_token_cache()
            return True
        return False

    def _save_accounts(self):
        """保存账号列表到文件"""
        try:
            with open(self.accounts_file, 'w', encoding='utf-8') as f:
                json.dump(self.accounts, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[Auth] 保存账号文件失败: {e}")

    def _parse_jwt_expires_at(self, token: str) -> float | None:
        """从 JWT token 中解析过期时间戳"""
        try:
            import base64
            parts = token.split('.')
            if len(parts) >= 2:
                payload = parts[1] + '=' * (4 - len(parts[1]) % 4)
                payload_json = json.loads(base64.urlsafe_b64decode(payload))
                exp = payload_json.get('exp')
                if exp:
                    return float(exp)
        except Exception:
            pass
        return None

    def get_accounts(self) -> list[dict]:
        """获取所有账号（脱敏）"""
        return [
            {
                'username': a['username'],
                'has_password': bool(a.get('password')),
                'has_totp': bool(a.get('totp_secret')),
            }
            for a in self.accounts
        ]
    # ==================================================================
    # Auto refresh watchdog
    # ==================================================================

    def _start_auto_refresh_loop(self):
        """Start a daemon watchdog so token renewal is not dependent on API traffic."""
        if self._auto_refresh_started:
            return
        self._auto_refresh_started = True
        interval = getattr(config, 'TOKEN_AUTO_REFRESH_INTERVAL', 60)
        thread = threading.Thread(
            target=self._auto_refresh_loop,
            args=(interval,),
            daemon=True,
            name='token-auto-refresh'
        )
        thread.start()
        print(f"[Auth] auto refresh watchdog started, interval={interval}s")

    def _auto_refresh_loop(self, interval: int):
        time.sleep(5)
        while True:
            try:
                self._auto_refresh_once()
            except Exception as exc:
                print(f"[Auth] auto refresh watchdog error: {exc}")
            time.sleep(max(10, int(interval)))

    def _auto_refresh_once(self):
        now = time.time()
        margin = getattr(config, 'TOKEN_PRE_REFRESH_MARGIN', 300)
        with self.lock:
            accounts = list(self.accounts)
        for account in accounts:
            uname = account.get('username')
            if not uname:
                continue
            with self.lock:
                cached = self.token_cache.get(uname)
                remaining = cached.get('expires_at', 0) - now if cached else 0
                cooling = uname in self._last_refresh_fail and now - self._last_refresh_fail[uname] < config.TOKEN_REFRESH_COOLDOWN
                refreshing = uname in self._refreshing_accounts
            if cooling or refreshing:
                continue
            if not cached or remaining <= margin:
                print(f"[Auth] auto refresh hit: {uname}, remaining={remaining:.0f}s")
                self._trigger_bg_refresh(account)

    # ==================================================================
    # Token access
    # ==================================================================
    def get_token(self, username: str = None, force_relogin: bool = False) -> tuple[str | None, str | None]:
        """
        获取一个有效的 access_token（带轮询）
        返回: (token, username) 或 (None, None)
        注意：不在锁内调用 _playwright_login，避免死锁
        force_relogin: 跳过缓存和 MSAL，直接 Playwright 登录（401 重试时用）
        """
        # 0. 强制重新登录（401 重试场景）
        if force_relogin:
            with self.lock:
                if not self.accounts:
                    return None, None
                if username:
                    account = next((a for a in self.accounts if a['username'] == username), None)
                    if not account:
                        return None, None
                else:
                    account = self.accounts[self.current_index % len(self.accounts)]
                    self.current_index += 1
                uname = account['username']
                self._last_refresh_fail.pop(uname, None)  # 清除冷却期
            print(f"[Auth] 强制 Playwright 重新登录: {uname}")
            token = self._playwright_login(account)
            if token:
                return token, uname
            print(f"[Auth] {uname} Playwright 重新登录失败")
            return None, None

        # 1. 快速检查缓存（持锁）
        cached_result = None  # 第一个需要同步刷新的候选账号（按本次轮询顺序）
        
        with self.lock:
            if not self.accounts:
                print("[Auth] 没有可用账号")
                return None, None

            if username:
                account = next((a for a in self.accounts if a['username'] == username), None)
                if not account:
                    print(f"[Auth] 账号不存在: {username}")
                    return None, None
                accounts_to_try = [account]
            else:
                # 轮询选择账号：每次请求只前进一个起点，避免每轮都从第一个账号开始
                n = len(self.accounts)
                start = self.current_index % n
                self.current_index += 1
                accounts_to_try = [
                    self.accounts[(start + i) % n]
                    for i in range(n)
                ]

            # 按轮询顺序，选第一个有效 token 的账号
            selected_token = None
            selected_uname = None
            for account in accounts_to_try:
                uname = account['username']
                cached = self.token_cache.get(uname)
                remaining = cached.get('expires_at', 0) - time.time() if cached else 0

                if cached and remaining > config.TOKEN_PRE_REFRESH_MARGIN:
                    # token 有效，按轮询顺序选第一个
                    token = cached['access_token']
                    print(f"[Auth] {uname} 缓存 token 可用（剩余 {remaining/60:.0f} 分钟）")
                    if not selected_token:
                        selected_token = token
                        selected_uname = uname
                elif cached and remaining > 0:
                    # token 未过期但即将到期，触发后台刷新
                    print(f"[Auth] {uname} token 即将到期（剩余 {remaining/60:.1f} 分钟），触发后台刷新")
                    self._trigger_bg_refresh(account)
                    if cached_result is None:
                        cached_result = (cached, uname, account)
                else:
                    # token 已过期，触发后台刷新
                    if cached:
                        print(f"[Auth] {uname} token 已过期，触发后台刷新")
                        self._trigger_bg_refresh(account)
                    if cached_result is None:
                        cached_result = (cached, uname, account)

            if selected_token:
                print(f"[Auth] 选择账号: {selected_uname}")
                return selected_token, selected_uname

        # 2. 需要登录（不持 self.lock）
        if cached_result:
            cached, uname, account = cached_result
            token = self._refresh_token_for_account(account)
            if token:
                return token, uname
            # 登录失败，返回过期的缓存 token
            return (cached['access_token'] if cached else None), uname

        return None, None

    def _trigger_bg_refresh(self, account: dict):
        """触发后台刷新线程（不阻塞当前请求）"""
        uname = account['username']
        if uname in self._refreshing_accounts:
            return  # 已在刷新中
        last_fail = self._last_refresh_fail.get(uname, 0)
        if time.time() - last_fail < config.TOKEN_REFRESH_COOLDOWN:
            return  # 冷却期内
        self._refreshing_accounts.add(uname)
        t = threading.Thread(
            target=self._bg_refresh,
            args=(account,),
            daemon=True,
            name=f'refresh-{uname}'
        )
        t.start()
        print(f"[Auth] {uname} 后台刷新线程已启动")

    def _bg_refresh(self, account: dict):
        """后台刷新 token（独立线程）"""
        uname = account['username']
        try:
            print(f"[Auth] {uname} 后台 Playwright 登录开始...")
            token = self._playwright_login(account)  # 自带 _refresh_lock
            if token:
                print(f"[Auth] {uname} 后台刷新成功，新 token 长度 {len(token)}")
            else:
                print(f"[Auth] {uname} 后台刷新失败")
                with self.lock:
                    self._last_refresh_fail[uname] = time.time()
        except Exception as e:
            print(f"[Auth] {uname} 后台刷新异常: {e}")
            with self.lock:
                self._last_refresh_fail[uname] = time.time()
        finally:
            self._refreshing_accounts.discard(uname)

    def _refresh_token_for_account(self, account: dict) -> str | None:
        """为指定账号刷新 token（不持有 self.lock）"""
        uname = account['username']

        # 1. 检查冷却期
        with self.lock:
            last_fail = self._last_refresh_fail.get(uname, 0)
            if time.time() - last_fail < config.TOKEN_REFRESH_COOLDOWN:
                remaining = config.TOKEN_REFRESH_COOLDOWN - (time.time() - last_fail)
                print(f"[Auth] {uname} 冷却中（剩余 {remaining:.0f} 秒）")
                return None

        # 2. Playwright Chromium 自动登录
        print(f"[Auth] {uname} 尝试 Playwright 自动登录...")
        pw_token = self._playwright_login(account)
        if pw_token:
            return pw_token

        # 3. 失败
        with self.lock:
            self._last_refresh_fail[uname] = time.time()
        print(f"[Auth] {uname} 登录失败")
        return None

    def _try_msal_silent(self, username: str) -> str | None:
        """MSAL 静默刷新（当前不可用，保留接口）"""
        # sydney refresh_token 为会话绑定，无法通过 HTTP 复用（AADSTS70000）
        # MSAL 缓存的 refresh_token 只能换取 ChatAI token（JWE 格式），无法用于 WebSocket
        return None

    # ==================================================================
    # Playwright Chromium 自动登录
    # ==================================================================

    def _playwright_login(self, account: dict) -> str | None:
        """
        使用 Playwright Chromium 自动登录并捕获 substrate token
        每次创建全新的浏览器上下文，不保留任何状态
        """
        print(f"[Auth] {account.get('username', '')} waiting for Playwright login lock...")
        self._refresh_lock.acquire()

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(self._do_playwright_login(account))
            finally:
                loop.close()
            return result
        except Exception as e:
            print(f"[Auth] Playwright 登录异常: {e}")
            return None
        finally:
            self._refresh_lock.release()

    async def _do_playwright_login(self, account: dict) -> str | None:
        """Playwright 自动登录核心逻辑"""
        from playwright.async_api import async_playwright

        username = account['username']
        password = account.get('password', '')
        totp_secret = account.get('totp_secret', '')

        captured_tokens = []
        self._last_captured_expires_at = None  # 每次登录重置
        self._last_captured_refresh_token = None  # 重置 sydney refresh_token
        self._captured_refresh_tokens = {}  # access_token → refresh_token 映射

        pw = await async_playwright().start()
        try:
            print(f"[Auth] 启动 Chromium（全新环境）...")
            browser_path = find_system_browser()
            launch_options = {
                'headless': config.PLAYWRIGHT_HEADLESS,
                'args': ['--disable-blink-features=AutomationControlled', '--no-sandbox'],
            }
            if browser_path:
                print(f'[Auth] 使用系统浏览器: {browser_path}')
                launch_options['executable_path'] = browser_path
            else:
                print('[Auth] 未找到系统 Edge/Chrome，尝试使用 Playwright 自带 Chromium')
            browser = await pw.chromium.launch(**launch_options)

            context = await browser.new_context(
                viewport={'width': 1280, 'height': 800},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                locale='zh-CN',
            )

            # 隐藏自动化标识
            page = await context.new_page()
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                window.chrome = { runtime: {} };
            """)

            # 监听 WebSocket 连接捕获 token
            page.on('websocket', lambda ws: self._on_ws_captured(ws, captured_tokens))

            # 同时监听网络请求（备用 token 捕获方式）
            page.on('request', lambda req: self._on_request_captured(req, captured_tokens))

            # 监听网络响应（捕获 negotiate 响应中的 connectionToken）
            page.on('response', lambda resp: self._on_response_captured(resp, captured_tokens))

            # 监听 Microsoft 身份端点响应
            page.on('response', lambda resp: self._on_identity_response(resp, captured_tokens))

            # 导航到 M365 Copilot
            print(f"[Auth] 导航到 M365 Copilot...")
            try:
                await page.goto(
                    'https://m365.cloud.microsoft/chat',
                    timeout=90000,
                    wait_until='domcontentloaded',
                )
            except Exception as e:
                print(f"[Auth] 页面加载: {e}")

            # 等待页面充分加载（重定向到登录页需要时间）
            await asyncio.sleep(2)
            try:
                await page.wait_for_load_state('domcontentloaded', timeout=10000)
            except Exception:
                pass
            await asyncio.sleep(1)

            # 检查是否需要登录
            current_url = page.url
            print(f"[Auth] 当前 URL: {current_url[:120]}")

            if 'login' in current_url.lower() or 'signin' in current_url.lower():
                print(f"[Auth] 需要登录，开始自动填写...")
                # 先等待邮箱输入框出现
                try:
                    await page.wait_for_selector(
                        'input[type="email"], input[name="loginfmt"]',
                        timeout=20000,
                    )
                    print(f"[Auth] 邮箱输入框已就绪")
                except Exception:
                    print(f"[Auth] 等待邮箱输入框超时，尝试继续...")

                login_ok = await self._auto_fill_login(page, username, password, totp_secret)
                if not login_ok:
                    print(f"[Auth] 自动登录填写失败，等待10秒观察...")
                    await asyncio.sleep(10)
                    after_url = page.url
                    print(f"[Auth] 填写后 URL: {after_url[:120]}")
                    if 'login' in after_url.lower() or 'signin' in after_url.lower():
                        print(f"[Auth] still on login page, login failed")
                        await browser.close()
                        await pw.stop()
                        return None

                # 登录成功后等待页面跳转离开登录页
                # 期间持续监听 KMSI 弹窗（弹窗可能在密码提交后才出现）
                print(f"[Auth] 等待登录页跳转...")
                dismiss_task2 = asyncio.create_task(self._dismiss_stay_signed_in(page))
                try:
                    for wait_i in range(30):
                        cur = page.url
                        if 'login.microsoftonline.com' not in cur and 'login.live.com' not in cur:
                            print(f"[Auth] 已跳转离开登录页: {cur[:100]}")
                            break
                        await asyncio.sleep(1)
                    else:
                        print(f"[Auth] 30秒内未跳转离开登录页，当前: {page.url[:100]}")
                finally:
                    if not dismiss_task2.done():
                        dismiss_task2.cancel()
                        try:
                            await dismiss_task2
                        except asyncio.CancelledError:
                            pass

                # 等待页面加载（networkidle 确保 MSAL token 交换完成）
                try:
                    await page.wait_for_load_state('networkidle', timeout=15000)
                    print(f'[Auth] 登录后 networkidle 完成')
                except Exception:
                    print(f'[Auth] 登录后 networkidle 超时，继续...')
                await asyncio.sleep(2)

            # 登录成功后，重新导航到 Copilot 聊天页（关键步骤！）
            # 登录可能停留在中间页面，必须重新导航确保 Copilot 完整加载
            current_url = page.url
            print(f"[Auth] 当前 URL: {current_url[:120]}")
            if 'm365.cloud.microsoft/chat' not in current_url:
                print(f"[Auth] 重新导航到 Copilot 聊天页...")
                try:
                    await page.goto(
                        'https://m365.cloud.microsoft/chat',
                        timeout=30000,
                        wait_until='domcontentloaded',
                    )
                except Exception as e:
                    print(f"[Auth] 重新导航: {e}")
                await asyncio.sleep(2)

            # 等待 Copilot 页面 networkidle（确保 MSAL.js 完成 sydney token 获取）
            print(f"[Auth] 等待 Copilot 页面完全加载（MSAL token 获取）...")
            try:
                await page.wait_for_load_state('networkidle', timeout=15000)
                print(f"[Auth] Copilot 页面 networkidle 完成")
            except Exception:
                print(f"[Auth] Copilot 页面 networkidle 超时，继续...")
            await asyncio.sleep(3)

            # 等待页面加载完成，尝试触发 WS 连接
            print(f"[Auth] 等待 substrate WebSocket 连接...")
            print(f"[Auth] 触发前已有 {len(captured_tokens)} 个 token")
            await self._trigger_chat(page)
            print(f"[Auth] 触发后共有 {len(captured_tokens)} 个 token")

            # 等待 token 捕获（每10秒重新触发一次聊天）
            for i in range(60):
                if captured_tokens:
                    print(f"[Auth] Token 循环在第 {i}s 检测到 {len(captured_tokens)} 个 token")
                    break
                if i > 0 and i % 10 == 0:
                    print(f"[Auth] 等待中... {i}s，重新触发聊天...")
                    try:
                        await self._trigger_chat(page)
                    except Exception:
                        pass
                await asyncio.sleep(1)

            # 如果只捕获到短 token（< 2500），尝试从页面 JS 上下文提取更长的 token
            if captured_tokens and len(captured_tokens[0]) < 2500:
                print(f"[Auth] 只捕获到短 token({len(captured_tokens[0])}字符)，尝试从页面 JS 提取...")
                await self._extract_token_from_page(page, captured_tokens)
            elif not captured_tokens:
                print(f"[Auth] 未捕获到任何 token，尝试从页面 JS 提取...")
                await self._extract_token_from_page(page, captured_tokens)

            print(f"[Auth] 关闭浏览器...")
            await browser.close()
            await pw.stop()
            print(f"[Auth] 浏览器已关闭")

        except Exception as e:
            print(f"[Auth] Playwright 异常: {e}")
            try:
                await pw.stop()
            except:
                pass

        if captured_tokens:
            # 选择最佳 token（优先 sydney/更长的 token）
            best_token = max(captured_tokens, key=len)
            print(f'[Auth] 从 {len(captured_tokens)} 个 token 中选择最长的: {len(best_token)} 字符')
            token = best_token
            # 优先用 JWT 自身的 exp，其次用 identity 响应中捕获的，最后回退到配置默认值
            expires_at = self._parse_jwt_expires_at(token) or getattr(self, '_last_captured_expires_at', None) or (time.time() + config.TOKEN_TTL)
            # 查找最佳 token 对应的 refresh_token
            best_refresh_token = (
                self._captured_refresh_tokens.get(best_token, '')
                or getattr(self, '_last_captured_refresh_token', '')
                or ''
            )
            with self.lock:
                token_info = {
                    'access_token': token,
                    'expires_at': expires_at,
                    'token_type': 'Bearer',
                    'refresh_token': best_refresh_token,
                    'username': username,
                    'source': 'playwright',
                }
                self.token_cache[username] = token_info
                self._save_token_cache()

            # 同时保存到 substrate_token.txt（兼容）
            token_file = os.path.join(config.DATA_DIR, config.SUBSTRATE_TOKEN_FILE)
            with open(token_file, 'w') as f:
                f.write(token)

            print(f"[Auth] Token 捕获成功！长度: {len(token)}")
            return token

        print(f"[Auth] 未捕获到 token")
        return None

    async def _auto_fill_login(self, page, username: str, password: str, totp_secret: str) -> bool:
        """自动填写登录表单"""
        # 从一开始就启动后台弹窗监听（弹窗可能在任何阶段出现）
        dismiss_task = asyncio.create_task(self._dismiss_stay_signed_in(page))

        try:
            # Step 1: 输入邮箱（等待输入框出现）
            print(f"[Auth] 填写邮箱...")
            email_input = page.locator('input[type="email"], input[name="loginfmt"]')
            try:
                await email_input.first.wait_for(state='visible', timeout=15000)
            except Exception:
                print(f"[Auth] 邮箱输入框未出现，当前页面内容片段: {(await page.content())[:200]}")
                return False

            await email_input.first.fill(username)
            await asyncio.sleep(0.5)

            # 点击下一步
            next_btn = page.locator('input[type="submit"], button:has-text("Next"), button:has-text("下一步")')
            if await next_btn.count() > 0:
                await next_btn.first.click()
                try:
                    await page.wait_for_load_state('domcontentloaded', timeout=10000)
                except Exception:
                    pass
                await asyncio.sleep(1)
            else:
                print("[Auth] 未找到下一步按钮")

            # Step 2: 选择账号类型（有时会出现）
            personal_btn = page.locator('button:has-text("Personal"), button:has-text("个人"), a:has-text("Personal")')
            if await personal_btn.count() > 0:
                await personal_btn.first.click()
                await asyncio.sleep(1)

            # Step 3: 输入密码（等待输入框出现）
            print(f"[Auth] 填写密码...")
            pwd_input = page.locator('input[type="password"], input[name="passwd"]')
            try:
                await pwd_input.first.wait_for(state='visible', timeout=20000)
            except Exception:
                print(f"[Auth] 密码输入框未出现")
                return False

            # 密码填写（type 比 fill 更可靠，微软登录页 fill 可能失败）
            try:
                await pwd_input.first.click()
                await pwd_input.first.type(password, delay=50)
                print(f"[Auth] 密码已填写")
            except Exception as e:
                print(f"[Auth] type 填写失败: {e}，尝试 fill")
                await pwd_input.first.fill(password)
            await asyncio.sleep(0.5)

            # 点击登录按钮（多种选择器）
            sign_in_btn = page.locator(
                '#idSIButton9, '
                'input[type="submit"], '
                'button:has-text("Sign in"), button:has-text("登录"), '
                'button:has-text("Sign In"), button:has-text("登入")'
            )
            btn_count = await sign_in_btn.count()
            print(f"[Auth] 找到 {btn_count} 个登录按钮候选")
            if btn_count > 0:
                try:
                    await sign_in_btn.first.click(timeout=5000)
                    print(f"[Auth] 已点击登录按钮")
                except Exception as e:
                    print(f"[Auth] 点击登录按钮失败: {e}，尝试 Enter 键提交")
                    await pwd_input.first.press('Enter')
            else:
                print("[Auth] 未找到登录按钮，尝试 Enter 键提交")
                await pwd_input.first.press('Enter')

            try:
                await page.wait_for_load_state('domcontentloaded', timeout=10000)
            except Exception:
                pass
            await asyncio.sleep(2)

            # Step 4: TOTP 验证码（如果配置了）
            if totp_secret:
                await self._fill_totp(page, totp_secret)

            # Step 5: 等待后台弹窗处理完成（最多等 15 秒）
            if not dismiss_task.done():
                try:
                    await asyncio.wait_for(dismiss_task, timeout=15)
                except asyncio.TimeoutError:
                    dismiss_task.cancel()
                    print("[Auth] Stay signed in 弹窗未出现，跳过")

            # Step 6: 处理 "Your account is ready" 或其他过渡页面
            try:
                continue_btn = page.locator(
                    'button:has-text("Continue"), button:has-text("继续"), '
                    'button:has-text("Get started"), button:has-text("开始")'
                )
                if await continue_btn.count() > 0:
                    await continue_btn.first.click()
                    await asyncio.sleep(2)
            except:
                pass

            # Step 7: 等待跳转离开登录页
            print(f"[Auth] 当前 URL: {page.url[:120]}")
            if 'login.microsoftonline.com' in page.url or 'login.live.com' in page.url:
                print("[Auth] 仍在登录页，等待跳转...")
                for _ in range(15):
                    if 'login.microsoftonline.com' not in page.url and 'login.live.com' not in page.url:
                        break
                    await asyncio.sleep(1)
                print(f"[Auth] 跳转后 URL: {page.url[:120]}")

            print(f"[Auth] 登录表单填写完成")
            return True

        except Exception as e:
            print(f"[Auth] 自动填写登录失败: {e}")
            return False

    async def _dismiss_stay_signed_in(self, page):
        """后台任务：监听并自动关闭“Stay signed in?”/KMSI 弹窗（支持多次出现）
        通过检查页面文本内容来确认 KMSI 页面，避免误点登录表单按钮
        """
        clicked_count = 0
        try:
            for attempt in range(200):  # 最多等 40 秒 (200 * 0.2s)
                await asyncio.sleep(0.2)
                try:
                    # 第 1 步：通过页面文本内容检测 KMSI 页面
                    kmsi_detected = False
                    try:
                        # 只检查登录表单区域（快速）
                        body_text = await page.inner_text('#loginHeader, .row, #i0281, body', timeout=500)
                        if any(kw in body_text for kw in [
                            'Stay signed in', 'stay signed in',
                            '保持登录', '保持登入',
                        ]):
                            kmsi_detected = True
                    except Exception:
                        pass

                    if not kmsi_detected:
                        continue

                    # 第 2 步：确认是 KMSI 页面，点击按钮关闭
                    clicked = False
                    no_selectors = [
                        'input[id="idBtn_Back"]',
                        'input[id="declineButton"]',
                        '#idBtn_Back',
                        'button:has-text("No")',
                        'button:has-text("否")',
                        'input[value="No"]',
                    ]
                    for sel in no_selectors:
                        btn = page.locator(sel)
                        if await btn.count() > 0 and await btn.first.is_visible():
                            print(f"[Auth] KMSI 页面确认，点击“否” ({sel})...")
                            await btn.first.click()
                            clicked = True
                            clicked_count += 1
                            break

                    if clicked:
                        await asyncio.sleep(1)
                        continue

                    # 回退：点击“是”
                    yes_selectors = [
                        'input[id="idSIButton9"]',
                        'input[id="acceptButton"]',
                        '#idSIButton9',
                        'button:has-text("Yes")',
                        'button:has-text("是")',
                        'input[value="Yes"]',
                    ]
                    for sel in yes_selectors:
                        btn = page.locator(sel)
                        if await btn.count() > 0 and await btn.first.is_visible():
                            print(f"[Auth] KMSI 页面确认，点击“是” ({sel})...")
                            await btn.first.click()
                            clicked_count += 1
                            break

                    if clicked_count > 0:
                        await asyncio.sleep(1)
                except Exception:
                    continue
        except asyncio.CancelledError:
            pass
        if clicked_count > 0:
            print(f"[Auth] KMSI 弹窗共处理 {clicked_count} 次")
        return clicked_count > 0

    async def _fill_totp(self, page, totp_secret: str):
        """填写 TOTP 验证码"""
        try:
            import pyotp
            totp = pyotp.TOTP(totp_secret)
            code = totp.now()
            print(f"[Auth] 生成 TOTP 验证码: {code}")

            otp_input = page.locator(
                'input[name="otc"], input[type="tel"], '
                'input[type="text"][id*="otp"], input[aria-label*="code"], '
                'input[placeholder*="code"], input[placeholder*="验证码"]'
            )
            if await otp_input.count() > 0:
                await otp_input.first.fill(code)
                await asyncio.sleep(0.5)
                verify_btn = page.locator(
                    'button:has-text("Verify"), button:has-text("验证"), '
                    'input[type="submit"], button:has-text("Sign in")'
                )
                if await verify_btn.count() > 0:
                    await verify_btn.first.click()
                    await asyncio.sleep(2)
                print("[Auth] TOTP 验证已提交")
            else:
                print("[Auth] 未找到 TOTP 输入框（可能不需要）")
        except Exception as e:
            print(f"[Auth] TOTP 填写失败: {e}")

    async def _trigger_chat(self, page):
        """触发聊天以建立 WebSocket 连接"""
        try:
            # 优先尝试 M365 Copilot 专用输入框（与旧版一致）
            selectors = [
                '#m365-chat-editor-target-element',
                '[role="textbox"]',
                '[contenteditable="true"]',
                'div[class*="composer"]',
                '[data-placeholder*="Copilot"]',
                '[aria-label*="消息"]',
                '[aria-label*="message"]',
                'textarea',
            ]

            clicked = False
            for sel in selectors:
                try:
                    el = page.locator(sel).first
                    if await el.count() > 0 and await el.is_visible():
                        await el.click(force=True)
                        await asyncio.sleep(0.5)
                        clicked = True
                        print(f"[Auth] 找到输入框: {sel}")
                        break
                except:
                    continue

            if clicked:
                await page.keyboard.type('Hi', delay=50)
                await asyncio.sleep(0.5)
                await page.keyboard.press('Enter')
                print("[Auth] 已发送消息触发 WS 连接")
                await asyncio.sleep(2)
            else:
                # 未找到输入框，尝试等待
                print("[Auth] 未找到输入框，等待 5 秒后重试...")
                await asyncio.sleep(5)
                for sel in selectors:
                    try:
                        el = page.locator(sel).first
                        if await el.count() > 0:
                            await el.click(force=True)
                            await asyncio.sleep(0.5)
                            await page.keyboard.type('Hi', delay=50)
                            await asyncio.sleep(0.5)
                            await page.keyboard.press('Enter')
                            print(f"[Auth] 重试成功，找到: {sel}")
                            await asyncio.sleep(2)
                            return
                    except:
                        continue
                print("[Auth] 仍然未找到输入框，等待 WS 自动连接...")
        except Exception as e:
            print(f"[Auth] 触发聊天失败: {e}")

    def _on_ws_captured(self, ws, captured_tokens: list):
        """WebSocket 连接回调，捕获 substrate token"""
        url = ws.url
        print(f"[Auth] WebSocket 连接: {url[:100]}")
        if 'substrate.office.com' in url:
            m = re.search(r'access_token=([^&]+)', url)
            if m:
                token = m.group(1)
                # WebSocket URL 中的 token 优先级最高，插入到列表最前面
                captured_tokens.insert(0, token)
                print(f"[Auth] Substrate token 已捕获！(ws url) 长度={len(token)}")
            else:
                print(f"[Auth] substrate WS 已连接但未找到 access_token 参数")

    def _on_request_captured(self, request, captured_tokens: list):
        """网络请求回调，捕获 substrate token"""
        url = request.url
        if 'substrate.office.com' in url:
            # 打印所有 substrate 请求（调试用）
            auth = request.headers.get('authorization', '')
            token_len = len(auth[7:]) if auth.startswith('Bearer ') else 0
            url_path = url.split('substrate.office.com')[1][:80] if 'substrate.office.com' in url else url[:80]
            print(f"[Auth] substrate 请求: {request.method} {url_path} | auth_token_len={token_len}")

            # 检查 URL 参数中的 token
            m = re.search(r'access_token=([^&]+)', url)
            if m:
                token = m.group(1)
                if token not in captured_tokens:
                    if not captured_tokens or len(token) > len(captured_tokens[0]):
                        captured_tokens.insert(0, token)
                        print(f"[Auth] Substrate token 已捕获！(request url) 长度={len(token)} [插入头部]")
                    else:
                        captured_tokens.append(token)
                        print(f"[Auth] Substrate token 已捕获！(request url) 长度={len(token)} [追加末尾]")
                return
            # 检查 Authorization header
            if auth.startswith('Bearer '):
                token = auth[7:]
                if len(token) > 50 and token not in captured_tokens:
                    if not captured_tokens or len(token) > len(captured_tokens[0]):
                        captured_tokens.insert(0, token)
                        print(f"[Auth] Substrate token 已捕获！(request header) 长度={len(token)} [插入头部]")
                    else:
                        captured_tokens.append(token)
                        print(f"[Auth] Substrate token 已捕获！(request header) 长度={len(token)} [追加末尾]")

    def _on_response_captured(self, response, captured_tokens: list):
        """网络响应回调，从 negotiate 响应中捕获 connectionToken"""
        url = response.url
        if 'substrate.office.com' in url and 'negotiate' in url:
            try:
                # 异步读取响应体（Playwright 的 response.json() 是协程）
                import asyncio as _aio
                async def _read_body():
                    try:
                        data = await response.json()
                        conn_token = data.get('connectionToken', '')
                        conn_id = data.get('connectionId', '')
                        access_token = data.get('accessToken', '')
                        print(f"[Auth] Negotiate 响应: connToken={len(conn_token)}, connId={len(conn_id)}, accessToken={len(access_token)}")
                        # 优先使用 accessToken（如果 negotiate 返回了更长的）
                        if access_token and len(access_token) > 100:
                            if access_token not in captured_tokens:
                                captured_tokens.insert(0, access_token)
                                print(f"[Auth] Substrate token 已捕获！(negotiate accessToken) 长度={len(access_token)}")
                        elif conn_token and len(conn_token) > 100:
                            if conn_token not in captured_tokens:
                                captured_tokens.insert(0, conn_token)
                                print(f"[Auth] Substrate token 已捕获！(negotiate connectionToken) 长度={len(conn_token)}")
                    except Exception as e:
                        print(f"[Auth] Negotiate 响应解析失败: {e}")
                _aio.ensure_future(_read_body())
            except Exception as e:
                print(f"[Auth] Negotiate 响应处理异常: {e}")


    def _on_identity_response(self, response, captured_tokens: list):
        """监听 Microsoft 身份端点响应，捕获 access_token"""
        url = response.url
        if 'login.microsoftonline.com' not in url and 'login.live.com' not in url:
            return
        if '/oauth2/' not in url or response.status != 200:
            return
        try:
            import asyncio as _aio
            async def _read_identity_body():
                try:
                    data = await response.json()
                    access_token = data.get('access_token', '')
                    if access_token and len(access_token) > 100:
                        if access_token not in captured_tokens:
                            import base64
                            parts_jwt = access_token.split('.')
                            audience = 'unknown'
                            token_exp = None
                            if len(parts_jwt) >= 2:
                                payload = parts_jwt[1] + '=' * (4 - len(parts_jwt[1]) % 4)
                                try:
                                    payload_json = json.loads(base64.urlsafe_b64decode(payload))
                                    audience = payload_json.get('aud', 'unknown')
                                    token_exp = payload_json.get('exp')
                                except Exception:
                                    pass
                            print(f"[Auth] Identity token: len={len(access_token)}, aud={audience}")
                            if not captured_tokens or len(access_token) > len(captured_tokens[0]):
                                captured_tokens.insert(0, access_token)
                                print(f"[Auth] Substrate token 已捕获！(identity endpoint) 长度={len(access_token)} [插入头部]")
                            else:
                                captured_tokens.append(access_token)
                                print(f"[Auth] Substrate token 已捕获！(identity endpoint) 长度={len(access_token)} [追加末尾]")
                            # 存储真实过期时间供后续使用
                            if token_exp:
                                self._last_captured_expires_at = float(token_exp)
                            # 捕获 refresh_token（续期关键！）
                            refresh_token = data.get('refresh_token', '')
                            if refresh_token and len(refresh_token) > 50:
                                if not hasattr(self, '_captured_refresh_tokens'):
                                    self._captured_refresh_tokens = {}
                                self._captured_refresh_tokens[access_token] = refresh_token
                                print(f"[Auth] Identity refresh_token 已捕获！长度={len(refresh_token)}, 对应 aud={audience}")
                                # 如果是 sydney token 的 refresh_token，单独保存
                                if 'sydney' in str(audience):
                                    self._last_captured_refresh_token = refresh_token
                except Exception as e:
                    pass
            _aio.ensure_future(_read_identity_body())
        except Exception:
            pass

    async def _extract_token_from_page(self, page, captured_tokens: list):
        """从页面 JS 上下文提取 token（从 MSAL localStorage 中查找）"""
        print(f"[Auth] 开始从页面 JS 提取 token...")
        try:
            # 从 MSAL localStorage 中提取 substrate token
            js_result = await page.evaluate("""
                () => {
                    const tokens = [];
                    for (let i = 0; i < localStorage.length; i++) {
                        const key = localStorage.key(i);
                        const keyLower = key.toLowerCase();
                        // 只查找 MSAL accesstoken 条目（包含 substrate.office.com）
                        if (keyLower.includes('accesstoken') && (keyLower.includes('substrate') || keyLower.includes('sydney'))) {
                            try {
                                const val = JSON.parse(localStorage.getItem(key));
                                const secret = val.secret || '';
                                // Extract scope from key (handles both | and - separators)
                                const parts = key.split(/[|\\-]/);
                                const scope = parts.length > 5 ? parts[5] : key;
                                if (secret && secret.length > 100) {
                                    tokens.push({
                                        scope: scope,
                                        secret: secret,
                                        length: secret.length,
                                    });
                                }
                            } catch (e) {}
                        }
                    }
                    // Also try sessionStorage
                    try {
                        for (let i = 0; i < sessionStorage.length; i++) {
                            const sk = sessionStorage.key(i);
                            const skLower = sk.toLowerCase();
                            if (skLower.includes('token') && (skLower.includes('substrate') || skLower.includes('sydney'))) {
                                try {
                                    const sv = JSON.parse(sessionStorage.getItem(sk));
                                    const ss = sv.secret || sv.accessToken || sv.access_token || '';
                                    if (ss && ss.length > 100) {
                                        tokens.push({ scope: 'session-' + sk, secret: ss, length: ss.length });
                                    }
                                } catch (e) {}
                            }
                        }
                    } catch (e) {}
                    // Sort by length descending (prefer longer tokens = sydney)
                    tokens.sort((a, b) => b.length - a.length);
                    return tokens;
                }
            """)
            print(f"[Auth] JS evaluate 返回: {type(js_result).__name__}, len={len(js_result) if js_result else 0}")
            if js_result:
                print(f"[Auth] MSAL localStorage 中找到 {len(js_result)} 个 substrate token:")
                # 优先选择 sydney scope 的 token
                best = None
                for t in js_result:
                    scope = t.get('scope', '')
                    length = t.get('length', 0)
                    is_sydney = 'sydney' in scope
                    print(f"  - scope: {scope[:60]}... length={length} {'[sydney]' if is_sydney else ''}")
                    if is_sydney:
                        best = t
                    elif not best:
                        best = t

                if best and best.get('secret'):
                    token = best['secret']
                    if token not in captured_tokens:
                        captured_tokens.insert(0, token)
                        print(f"[Auth] Substrate token 已捕获！(MSAL: {best['scope'][:40]}) 长度={len(token)}")
                        return token
            else:
                print(f"[Auth] MSAL localStorage 中未找到 substrate token")
        except Exception as e:
            print(f"[Auth] MSAL token 提取失败: {e}")
        return None

    # ==================================================================
    # MSAL 辅助
    # ==================================================================

    def _load_msal_cache(self) -> msal.SerializableTokenCache:
        """加载 MSAL 持久化缓存"""
        cache = msal.SerializableTokenCache()
        cache_file = os.path.join(config.DATA_DIR, config.MSAL_CACHE_FILE)
        if os.path.exists(cache_file):
            try:
                with open(cache_file, 'r') as f:
                    cache.deserialize(f.read())
            except Exception:
                pass
        return cache

    def _save_msal_cache(self, cache: msal.SerializableTokenCache):
        """保存 MSAL 缓存"""
        if cache.has_state_changed:
            cache_file = os.path.join(config.DATA_DIR, config.MSAL_CACHE_FILE)
            try:
                with open(cache_file, 'w') as f:
                    f.write(cache.serialize())
            except Exception as e:
                print(f"[Auth] MSAL 缓存保存失败: {e}")

    def login_device_code(self, username: str = None) -> tuple[str | None, str]:
        """
        Device Code Flow 登录（备用方案）
        需要在浏览器中手动确认
        """
        try:
            cache_obj = self._load_msal_cache()
            app = msal.PublicClientApplication(
                config.MSAL_CLIENT_ID,
                authority=config.MSAL_AUTHORITY,
                token_cache=cache_obj,
            )

            flow = app.initiate_device_flow(scopes=[config.MSAL_SCOPE])
            if 'user_code' not in flow:
                print("[Auth] Device Code Flow 初始化失败")
                return None, username or ''

            verification_uri = flow.get('verification_uri', 'https://microsoft.com/devicelogin')
            user_code = flow['user_code']

            print(f"\n{'='*50}")
            print(f"  打开: {verification_uri}")
            print(f"  验证码: {user_code}")
            print(f"{'='*50}\n")

            result = app.acquire_token_by_device_flow(flow)
            if 'access_token' in result:
                uname = result.get('id_token_claims', {}).get('preferred_username', username or 'unknown')
                token_info = {
                    'access_token': result['access_token'],
                    'expires_at': time.time() + result.get('expires_in', 3600),
                    'token_type': result.get('token_type', 'Bearer'),
                    'refresh_token': result.get('refresh_token', ''),
                    'username': uname,
                }
                with self.lock:
                    self.token_cache[uname] = token_info
                    self._save_token_cache()
                self._save_msal_cache(cache_obj)
                print(f"[Auth] Device Code 登录成功: {uname}")
                return result['access_token'], uname
            else:
                err = result.get('error', 'unknown')
                print(f"[Auth] Device Code 登录失败: {err}")
                return None, username or ''

        except Exception as e:
            print(f"[Auth] Device Code 异常: {e}")
            return None, username or ''

    def refresh_after_401(self, username: str) -> tuple[str | None, str | None]:
        """
        401 错误后的刷新：Playwright 重新登录
        返回: (token, username) 或 (None, None)
        """
        # 1. 使旧 token 失效
        self.invalidate_token(username)

        # 2. Playwright 重新登录（需要 30~90 秒）
        print(f"[Auth] {username} 401 后 Playwright 重新登录")
        account = next((a for a in self.accounts if a['username'] == username), None)
        if not account:
            return None, None
        token = self._playwright_login(account)
        if token:
            return token, username
        print(f"[Auth] {username} 所有续期方式均失败")
        return None, None

    # ==================================================================
    # 刷新方法（供外部调用）
    # ==================================================================

    def refresh_token(self, username: str = None) -> bool:
        """手动刷新指定账号的 token"""
        if username:
            account = next((a for a in self.accounts if a['username'] == username), None)
            if not account:
                return False
            token = self._playwright_login(account)
            return token is not None

        # 刷新所有过期账号
        success = False
        for account in self.accounts:
            uname = account['username']
            cached = self.token_cache.get(uname)
            if not cached or cached.get('expires_at', 0) < time.time() + 300:
                token = self._playwright_login(account)
                if token:
                    success = True
        return success

    # ==================================================================
    # Token 消耗统计
    # ==================================================================

    def _check_daily_reset(self):
        """检查是否需要重置每日统计（零点重置）"""
        from datetime import datetime
        today = datetime.now().strftime('%Y-%m-%d')
        if self._usage_date and self._usage_date != today:
            # 日期变更，重置所有统计
            self._usage.clear()
            print("[Auth] 每日统计已重置")
        self._usage_date = today

    def record_token_usage(self, username: str, input_tokens: int, output_tokens: int):
        """记录一次请求的 token 消耗"""
        with self.lock:
            self._check_daily_reset()
            if username not in self._usage:
                self._usage[username] = {'input_tokens': 0, 'output_tokens': 0, 'call_count': 0}
            self._usage[username]['input_tokens'] += input_tokens
            self._usage[username]['output_tokens'] += output_tokens
            self._usage[username]['call_count'] += 1

    def get_usage_stats(self, username: str = None) -> dict:
        """获取 token 消耗统计"""
        with self.lock:
            self._check_daily_reset()
            if username:
                return self._usage.get(username, {'input_tokens': 0, 'output_tokens': 0, 'call_count': 0})
            # 汇总
            total_in = sum(u['input_tokens'] for u in self._usage.values())
            total_out = sum(u['output_tokens'] for u in self._usage.values())
            total_calls = sum(u['call_count'] for u in self._usage.values())
            return {
                'input_tokens': total_in,
                'output_tokens': total_out,
                'call_count': total_calls,
            }

    # ==================================================================
    # 状态查询
    # ==================================================================

    def get_all_status(self) -> list[dict]:
        """获取所有账号状态"""
        self._check_daily_reset()
        statuses = []
        for account in self.accounts:
            uname = account['username']
            cached = self.token_cache.get(uname)
            now = time.time()

            if cached and cached.get('expires_at', 0) > now:
                remaining = cached['expires_at'] - now
                status = 'ready'
            elif uname in self._last_refresh_fail and now - self._last_refresh_fail[uname] < config.TOKEN_REFRESH_COOLDOWN:
                remaining = 0
                status = 'cooldown'
            else:
                remaining = 0
                status = 'expired'

            usage = self._usage.get(uname, {'input_tokens': 0, 'output_tokens': 0, 'call_count': 0})

            statuses.append({
                'username': uname,
                'status': status,
                'token_remaining_sec': max(0, remaining),
                'has_totp': bool(account.get('totp_secret')),
                'source': cached.get('source', '-') if cached else '-',
                'daily_calls': usage['call_count'],
                'daily_input_tokens': usage['input_tokens'],
                'daily_output_tokens': usage['output_tokens'],
            })
        return statuses

    def get_daily_stats(self) -> dict:
        """获取统计信息"""
        statuses = self.get_all_status()
        active = sum(1 for s in statuses if s['status'] == 'ready')
        usage = self.get_usage_stats()
        return {
            'total_accounts': len(self.accounts),
            'active_accounts': active,
            'today_calls': usage['call_count'],
            'today_input_tokens': usage['input_tokens'],
            'today_output_tokens': usage['output_tokens'],
            'today_total_tokens': usage['input_tokens'] + usage['output_tokens'],
        }


# 全局实例
_manager = None


def get_account_manager() -> AccountManager:
    global _manager
    if _manager is None:
        _manager = AccountManager()
    return _manager
