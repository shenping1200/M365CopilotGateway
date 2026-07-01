# -*- coding: utf-8 -*-
"""原生桌面管理器：启动 API/面板服务，并在 PyQt 内直接操作控制面板。"""
import json
import os
import sys
import urllib.request
from pathlib import Path

from PyQt5.QtCore import Qt, QProcess, QThread, QTimer, QUrl, QSize, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QPainter, QPixmap, QTextCursor, QDesktopServices
from PyQt5.QtWidgets import (
    QDialog,
    QApplication, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QSplitter, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
os.chdir(str(APP_DIR))
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import config

if '--service' in sys.argv:
    try:
        from app_launcher import main as service_main
        service_main()
    except Exception as exc:
        log_path = APP_DIR / 'service_error.log'
        log_path.write_text(str(exc), encoding='utf-8')
        raise
    raise SystemExit(0)

if '--supervisor' in sys.argv:
    from m365_supervisor import supervise
    command = [sys.executable, '--service'] if getattr(sys, 'frozen', False) else None
    raise SystemExit(supervise(15, 3, command))

APP_TITLE = 'M365 Copilot 反代服务'
API_URL = f'http://127.0.0.1:{config.SERVER_PORT}'
DASH_URL = f'http://127.0.0.1:{config.DASHBOARD_PORT}'
ORIG_STDOUT = sys.stdout

def resolve_icon_path() -> Path | None:
    candidates = [
        APP_DIR / 'app.ico',
        APP_DIR / '_internal' / 'app.ico',
        Path(getattr(sys, '_MEIPASS', APP_DIR)) / 'app.ico',
    ]
    for path in candidates:
        if path.exists():
            return path
    return None

LIGHT_QSS = r'''
QMainWindow { background: #f7f8fb; }
QWidget { color: #0f172a; background: #f7f8fb; font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif; font-size: 14px; }
QLabel#HeroTitle { font-size: 26px; font-weight: 800; color: #111827; }
QLabel#HeroSubTitle { color: #1f2937; font-size: 14px; font-style: italic; padding-top: 8px; }
QLabel#StatLabel, QLabel#MetricLabel { color: #5b5ff6; background: #e8ebff; border-radius: 5px; padding: 5px 7px; font-weight: 700; }
QLabel#StatValue, QLabel#MetricValue { color: #111827; font-size: 14px; padding: 8px 4px 2px 4px; }
QLabel#SectionHint { color: #5b5ff6; font-size: 14px; padding: 8px 0; }
QFrame#HeroCard { background: transparent; border: none; }
QFrame#StatCard, QFrame#MetricCard, QFrame#ActionCard, QFrame#LogCard { background: #ffffff; border: 1px solid #edf0f5; border-radius: 6px; }
QPushButton { background: #ffffff; border: 1px solid #e5e7eb; border-radius: 6px; padding: 9px 16px; color: #111827; font-weight: 700; }
QPushButton:hover { background: #f3f4ff; border-color: #c7cbff; }
QPushButton#PrimaryButton, QPushButton#SuccessButton { background: #5b5ff6; border: 1px solid #5b5ff6; color: white; }
QPushButton#DangerButton { background: #b91c1c; border: 1px solid #b91c1c; color: white; }
QLineEdit, QComboBox, QPlainTextEdit { background: #ffffff; border: 1px solid #e5e7eb; border-radius: 6px; padding: 8px; color: #111827; }
QPlainTextEdit { font-family: Consolas, "Cascadia Code", monospace; font-size: 12px; }
QTabWidget::pane { border-top: 1px solid #d8dce6; background: #f7f8fb; margin-top: -1px; }
QTabBar::tab { background: transparent; color: #111827; padding: 10px 18px 9px 18px; border: none; border-bottom: 2px solid transparent; margin-right: 4px; }
QTabBar::tab:selected { color: #5b5ff6; border-bottom: 2px solid #5b5ff6; }
QTableWidget { background:#ffffff; alternate-background-color:#fbfbfc; border:1px solid #d8dce6; border-radius:4px; gridline-color:#e5e7eb; color:#0f172a; selection-background-color:#eef0ff; selection-color:#111827; }
QHeaderView::section { background:#ffffff; color:#111827; padding:8px; border:1px solid #d8dce6; font-weight:700; }
QSplitter::handle { background: #eef0f5; }
QStatusBar { background: #f7f8fb; color: #475569; }
'''



def http_ok(url: str, timeout: int = 2) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return getattr(resp, 'status', 0) == 200
    except Exception:
        return False


class ApiWorker(QThread):
    success = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, api_url, path, method='GET', payload=None, timeout=180, parent=None):
        super().__init__(parent)
        self.api_url = api_url
        self.path = path
        self.method = method
        self.payload = payload
        self.timeout = timeout

    def run(self):
        try:
            data = None
            headers = {}
            if self.payload is not None:
                data = json.dumps(self.payload).encode('utf-8')
                headers['Content-Type'] = 'application/json'
            req = urllib.request.Request(self.api_url + self.path, data=data, headers=headers, method=self.method)
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode('utf-8', errors='replace')
                self.success.emit(json.loads(raw) if raw else {})
        except Exception as exc:
            self.failed.emit(str(exc))


class StatusWorker(QThread):
    success = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, api_url, parent=None):
        super().__init__(parent)
        self.api_url = api_url

    def _json(self, path, timeout=3):
        req = urllib.request.Request(self.api_url + path, method='GET')
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8', errors='replace')
            return json.loads(raw) if raw else {}

    def run(self):
        try:
            stats = self._json('/status', timeout=3)
            accounts = self._json('/v1/accounts', timeout=3).get('accounts', [])
            self.success.emit({'stats': stats, 'accounts': accounts})
        except Exception as exc:
            self.failed.emit(str(exc))


class Card(QFrame):
    def __init__(self, name='ActionCard', parent=None):
        super().__init__(parent)
        self.setObjectName(name)
        self.setFrameShape(QFrame.NoFrame)



class AddAccountDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('?? M365 ??')
        self.setFixedSize(400, 250)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        self.username_input = QLineEdit(); self.username_input.setPlaceholderText('?? (e.g. user@domain.com)')
        self.password_input = QLineEdit(); self.password_input.setPlaceholderText('??'); self.password_input.setEchoMode(QLineEdit.Password)
        self.totp_input = QLineEdit(); self.totp_input.setPlaceholderText('TOTP ?? (??)')

        layout.addWidget(QLabel('??:'))
        layout.addWidget(self.username_input)
        layout.addWidget(QLabel('??:'))
        layout.addWidget(self.password_input)
        layout.addWidget(QLabel('TOTP ??:'))
        layout.addWidget(self.totp_input)

        btns = QHBoxLayout()
        self.btn_ok = QPushButton('??'); self.btn_ok.setObjectName('PrimaryButton')
        self.btn_cancel = QPushButton('??')
        btns.addWidget(self.btn_ok); btns.addWidget(self.btn_cancel)
        layout.addLayout(btns)

        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)

    def get_data(self):
        return {
            'username': self.username_input.text().strip(),
            'password': self.password_input.text().strip(),
            'totp_secret': self.totp_input.text().strip()
        }

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f'{APP_TITLE} · 原生桌面管理器')
        self.resize(1420, 920)
        self.setMinimumSize(QSize(1180, 760))
        self.process = None
        self.services_started = False
        self.started_by_launcher = False
        self.token_worker = None
        self.status_worker = None
        self._detecting_service = False
        self._status_icons = {}
        self.runtime_log_path = APP_DIR / 'desktop_manager_runtime.log'
        icon_path = resolve_icon_path()
        if icon_path:
            self.setWindowIcon(QIcon(str(icon_path)))
        self._build_ui()
        self._bind_events()
        self._start_polling()
        self._detect_existing_service(initial=True)
        self.refresh_native_panel()
        QTimer.singleShot(300, self._auto_start_if_needed)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(42, 0, 42, 0)
        root.setSpacing(14)

        hero = Card('HeroCard')
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(0, 0, 0, 6)
        self.hero_title = QLabel(APP_TITLE)
        self.hero_title.setObjectName('HeroTitle')
        self.hero_subtitle = QLabel('MSAL 自动登录 · 多账号管理 · 7 种模型')
        self.hero_subtitle.setObjectName('HeroSubTitle')
        hero_layout.addWidget(self.hero_title)
        hero_layout.addWidget(self.hero_subtitle)
        root.addWidget(hero)

        stat_grid = QGridLayout(); stat_grid.setHorizontalSpacing(20); stat_grid.setVerticalSpacing(10)
        self.card_api = self._make_stat_card('接口服务', '检测中')
        self.card_panel = self._make_stat_card('管理面板', '检测中')
        self.card_pool = self._make_stat_card('账号池', '检测中')
        stat_grid.addWidget(self.card_api, 0, 0); stat_grid.addWidget(self.card_panel, 0, 1); stat_grid.addWidget(self.card_pool, 0, 2)
        root.addLayout(stat_grid)

        actions = Card('ActionCard')
        actions_layout = QHBoxLayout(actions); actions_layout.setContentsMargins(10, 8, 10, 8)
        self.btn_start = QPushButton('启动服务'); self.btn_start.setObjectName('PrimaryButton')
        self.btn_stop = QPushButton('停止服务'); self.btn_stop.setObjectName('DangerButton'); self.btn_stop.setEnabled(False)
        self.btn_restart = QPushButton('重启服务'); self.btn_restart.setObjectName('PrimaryButton'); self.btn_restart.setEnabled(False)
        self.btn_refresh_native = QPushButton('刷新状态'); self.btn_refresh_native.setObjectName('SuccessButton')
        self.btn_open_api = QPushButton('打开 API 状态')
        self.btn_open_browser = QPushButton('外部浏览器打开面板')
        self.btn_open_logs = QPushButton('打开日志目录')
        self.btn_clear = QPushButton('清空运行日志')
        for btn in [self.btn_start, self.btn_stop, self.btn_restart, self.btn_refresh_native, self.btn_open_api, self.btn_open_browser, self.btn_open_logs, self.btn_clear]:
            actions_layout.addWidget(btn)
        actions_layout.addStretch(1); root.addWidget(actions)

        self.main_splitter = QSplitter(Qt.Vertical); self.main_splitter.addWidget(self._build_native_panel())
        self.log_text = QPlainTextEdit(); self.log_text.setReadOnly(True); self.log_text.setMaximumBlockCount(10000); self.log_text.setMinimumHeight(70)
        self.main_splitter.addWidget(self.log_text); self.main_splitter.setStretchFactor(0, 4); self.main_splitter.setStretchFactor(1, 1); self.main_splitter.setSizes([720, 160])
        root.addWidget(self.main_splitter, 1)
        self.statusBar().showMessage('就绪：等待检测服务状态')

    def _make_stat_card(self, label, value):
        card = Card('StatCard')
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        title = QLabel(label); title.setObjectName('StatLabel')
        val = QLabel(value); val.setObjectName('StatValue'); val.setWordWrap(True)
        layout.addWidget(title, 0, Qt.AlignLeft); layout.addWidget(val)
        card._value_label = val
        return card

    def _setup_table(self, table, stretch_last=False):
        table.setAlternatingRowColors(True)
        table.setShowGrid(True)
        table.verticalHeader().setVisible(True)
        table.horizontalHeader().setStretchLastSection(stretch_last)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)

    def _fit_overview_columns(self):
        if not hasattr(self, 'overview_table'):
            return
        width = max(self.overview_table.viewport().width(), 760)
        ratios = [0.23, 0.10, 0.15, 0.07, 0.12, 0.12, 0.11]
        reserved = 8
        for col, ratio in enumerate(ratios):
            self.overview_table.setColumnWidth(col, max(64, int((width - reserved) * ratio)))
        self.overview_table.horizontalHeader().setStretchLastSection(True)

    def _fit_account_columns(self):
        if not hasattr(self, 'account_table'):
            return
        width = max(self.account_table.viewport().width(), 520)
        ratios = [0.48, 0.18, 0.14, 0.20]
        for col, ratio in enumerate(ratios):
            self.account_table.setColumnWidth(col, max(70, int(width * ratio)))
        self.account_table.horizontalHeader().setStretchLastSection(True)

    def _build_native_panel(self):
        tabs = QTabWidget(); tabs.setDocumentMode(True)

        overview = QWidget(); ov = QVBoxLayout(overview); ov.setContentsMargins(0, 8, 0, 0); ov.setSpacing(10)
        self.native_status_label = QLabel('等待状态刷新...')
        self.native_status_label.setObjectName('SectionHint')
        ov.addWidget(self.native_status_label)
        self.overview_table = QTableWidget(0, 7)
        self._setup_table(self.overview_table, stretch_last=True)
        self.overview_table.setHorizontalHeaderLabels(['账号', '状态', '剩余秒数', '调用', '输入Token', '输出Token', '来源'])
        ov.addWidget(self.overview_table, 1)
        row = QHBoxLayout()
        self.btn_login_selected_overview = QPushButton('\u767b\u5f55\u9009\u4e2d\u8d26\u53f7')
        self.btn_login_selected_overview.setObjectName('PrimaryButton')
        self.btn_login_all = QPushButton('\u6279\u91cf\u767b\u5f55 / \u5237\u65b0 Token')
        self.btn_refresh_overview = QPushButton('\u5237\u65b0\u72b6\u6001')
        row.addWidget(self.btn_login_selected_overview); row.addWidget(self.btn_login_all); row.addWidget(self.btn_refresh_overview); row.addStretch(1)
        ov.addLayout(row)
        tabs.addTab(overview, '状态总览')

        accounts = QWidget(); ac = QVBoxLayout(accounts); ac.setContentsMargins(0, 8, 0, 0); ac.setSpacing(10)
        self.account_table = QTableWidget(0, 4)
        self._setup_table(self.account_table, stretch_last=True)
        self.account_table.setHorizontalHeaderLabels(['账号', '状态', 'TOTP', '来源'])
        ac.addWidget(self.account_table, 1)
        self.selected_account = QLineEdit(); self.selected_account.setPlaceholderText('点击表格选择账号')
        ac.addWidget(self.selected_account)
        row = QHBoxLayout()
        self.btn_login_selected = QPushButton('登录选中账号')
        self.btn_delete_selected = QPushButton('删除选中账号')
        self.btn_refresh_accounts = QPushButton('刷新账号列表')
        self.btn_add_account = QPushButton('????'); self.btn_add_account.setObjectName('PrimaryButton')
        row.addWidget(self.btn_add_account); row.addWidget(self.btn_login_selected); row.addWidget(self.btn_delete_selected); row.addWidget(self.btn_refresh_accounts); row.addStretch(1)
        ac.addLayout(row)
        tabs.addTab(accounts, '账号管理')

        test = QWidget(); te = QVBoxLayout(test)
        top = QHBoxLayout()
        self.model_combo = QComboBox()
        self.model_combo.addItems(['copilot-auto','copilot-quick','copilot-thinking','gpt-5.5','gpt-5.5-thinking','gpt-5.2','gpt-5.2-thinking'])
        self.btn_send_test = QPushButton('发送测试')
        top.addWidget(QLabel('模型：')); top.addWidget(self.model_combo); top.addWidget(self.btn_send_test); top.addStretch(1)
        te.addLayout(top)
        self.test_input = QPlainTextEdit(); self.test_input.setPlainText('1+1等于几？只回答数字'); self.test_input.setMaximumHeight(90)
        self.test_output = QPlainTextEdit(); self.test_output.setReadOnly(True)
        te.addWidget(QLabel('测试消息')); te.addWidget(self.test_input)
        te.addWidget(QLabel('回复')); te.addWidget(self.test_output, 1)
        tabs.addTab(test, '模型测试')

        logs = QWidget(); lo = QVBoxLayout(logs); lo.setContentsMargins(0, 8, 0, 0); lo.setSpacing(10)
        row = QHBoxLayout()
        self.log_filter_combo = QComboBox(); self.log_filter_combo.addItems(['all','today','errors'])
        self.btn_refresh_req_logs = QPushButton('刷新请求日志')
        self.btn_clear_req_logs = QPushButton('清空请求日志')
        row.addWidget(QLabel('过滤：')); row.addWidget(self.log_filter_combo); row.addWidget(self.btn_refresh_req_logs); row.addWidget(self.btn_clear_req_logs); row.addStretch(1)
        lo.addLayout(row)
        self.request_log_table = QTableWidget(0, 6)
        self._setup_table(self.request_log_table, stretch_last=True)
        self.request_log_table.setHorizontalHeaderLabels(['时间', '模型', '账号', '耗时', '状态', '摘要'])
        lo.addWidget(self.request_log_table, 1)
        tabs.addTab(logs, '请求日志')

        cfg = QWidget(); cf = QVBoxLayout(cfg)
        loaded = self._load_dashboard_config()
        self.cfg_api_port = QLineEdit(str(loaded['server']['api_port']))
        self.cfg_ttl = QLineEdit(str(loaded['token']['ttl_seconds']))
        self.cfg_cooldown = QLineEdit(str(loaded['token']['refresh_cooldown_seconds']))
        self.cfg_headless = QCheckBox('无头模式（不显示浏览器窗口）'); self.cfg_headless.setChecked(bool(loaded['playwright']['headless']))
        for label, widget in [('API 端口（重启生效）', self.cfg_api_port), ('Token 有效期秒数', self.cfg_ttl), ('刷新失败冷却秒数', self.cfg_cooldown)]:
            cf.addWidget(QLabel(label)); cf.addWidget(widget)
        cf.addWidget(self.cfg_headless)
        self.btn_save_config = QPushButton('保存配置')
        self.cfg_status = QLabel('')
        cf.addWidget(self.btn_save_config); cf.addWidget(self.cfg_status); cf.addStretch(1)
        tabs.addTab(cfg, '配置管理')
        return tabs

    def _bind_events(self):
        self.btn_start.clicked.connect(lambda: self.start_services())
        self.btn_stop.clicked.connect(lambda: self.stop_services())
        self.btn_restart.clicked.connect(lambda: self.restart_services())
        self.btn_refresh_native.clicked.connect(lambda: self.refresh_native_panel(user_triggered=True))
        self.btn_open_api.clicked.connect(self.open_api)
        self.btn_open_browser.clicked.connect(self.open_browser)
        self.btn_open_logs.clicked.connect(self.open_logs)
        self.btn_clear.clicked.connect(self.log_text.clear)
        self.btn_refresh_overview.clicked.connect(lambda: self.refresh_native_panel(user_triggered=True))
        self.btn_refresh_accounts.clicked.connect(lambda: self.refresh_native_panel(user_triggered=True))
        self.btn_login_all.clicked.connect(lambda: self.refresh_token_native(None))
        self.btn_login_selected.clicked.connect(lambda: self.refresh_token_native(self.selected_account.text().strip()))
        self.btn_login_selected_overview.clicked.connect(lambda: self.refresh_token_native(self.selected_account.text().strip()))
        self.btn_delete_selected.clicked.connect(self.delete_selected_account_native)
        self.btn_send_test.clicked.connect(self.send_test_native)
        self.btn_refresh_req_logs.clicked.connect(self.refresh_request_logs_native)
        self.btn_clear_req_logs.clicked.connect(self.clear_request_logs_native)
        self.btn_save_config.clicked.connect(self.save_native_config)
        self.overview_table.cellClicked.connect(self._select_account_from_overview)
        self.account_table.cellClicked.connect(self._select_account_from_accounts)
        self.btn_add_account.clicked.connect(self.add_account_native)

    def _start_polling(self):
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._detect_existing_service)
        self.poll_timer.start(2500)


    def _auto_start_if_needed(self):
        if self._detecting_service:
            QTimer.singleShot(700, self._auto_start_if_needed)
            return
        if not self.services_started and (self.process is None or self.process.state() == QProcess.NotRunning):
            self.append_log('[Launcher] \u672a\u68c0\u6d4b\u5230\u670d\u52a1\uff0c\u81ea\u52a8\u542f\u52a8\u540e\u7aef...\n')
            self.start_services(silent=True)
    def _set_button_busy(self, button, text):
        if not hasattr(button, '_normal_text'):
            button._normal_text = button.text()
        button.setText(text)
        button.setEnabled(False)

    def _restore_button(self, button, text=None, enabled=True):
        button.setText(text or getattr(button, '_normal_text', button.text()))
        button.setEnabled(enabled)

    def append_log(self, text: str):
        if not text:
            return
        self.log_text.moveCursor(QTextCursor.End)
        self.log_text.insertPlainText(text)
        self.log_text.moveCursor(QTextCursor.End)
        try:
            with open(self.runtime_log_path, 'a', encoding='utf-8', errors='replace') as log_file:
                log_file.write(text)
        except Exception:
            pass
        try:
            if ORIG_STDOUT is not None:
                ORIG_STDOUT.write(text); ORIG_STDOUT.flush()
        except Exception:
            pass

    def _api_json(self, path, method='GET', payload=None, timeout=4):
        data = None; headers = {}
        if payload is not None:
            data = json.dumps(payload).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(API_URL + path, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8', errors='replace')
            return json.loads(raw) if raw else {}

    def _format_remaining(self, seconds):
        try:
            seconds = int(float(seconds))
        except Exception:
            return str(seconds or '-')
        if seconds <= 0:
            return '\u5df2\u8fc7\u671f'
        minutes, remain = divmod(seconds, 60)
        return f'{minutes}\u5206{remain}\u79d2'

    def _status_label(self, value):
        text = str(value).strip().lower()
        raw = str(value).strip()
        if 'ready' in text or 'active' in text or '\u5c31\u7eea' in raw:
            return '\u5c31\u7eea'
        if 'expired' in text or '\u8fc7\u671f' in raw:
            return '\u8fc7\u671f'
        return raw or '-'

    def _circle_pixmap(self, color):
        key = f'pixmap:{color}'
        if key not in self._status_icons:
            pixmap = QPixmap(16, 16)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setBrush(QColor(color))
            painter.setPen(QColor('#111827'))
            painter.drawEllipse(1, 1, 14, 14)
            painter.end()
            self._status_icons[key] = pixmap
        return self._status_icons[key]

    def _make_status_widget(self, value):
        label = self._status_label(value)
        is_ready = label == '\u5c31\u7eea'
        is_expired = label == '\u8fc7\u671f'
        color = '#18d21f' if is_ready else ('#ff1f2d' if is_expired else '#9ca3af')
        wrapper = QWidget()
        wrapper.setStyleSheet('background: transparent;')
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(8, 0, 4, 0)
        layout.setSpacing(7)
        dot = QLabel()
        dot.setStyleSheet('background: transparent;')
        dot.setPixmap(self._circle_pixmap(color))
        dot.setFixedSize(18, 18)
        text = QLabel(label)
        text.setStyleSheet('background: transparent; color: #111827; font-size: 14px;')
        layout.addWidget(dot, 0, Qt.AlignVCenter)
        layout.addWidget(text, 0, Qt.AlignVCenter)
        layout.addStretch(1)
        return wrapper

    def _set_table(self, table, rows):
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                if c == 1:
                    table.setItem(r, c, QTableWidgetItem(''))
                    table.setCellWidget(r, c, self._make_status_widget(val))
                else:
                    table.setItem(r, c, QTableWidgetItem(str(val)))
        if table is self.overview_table:
            self._fit_overview_columns()
        elif table is self.account_table:
            self._fit_account_columns()
        else:
            table.resizeColumnsToContents()
            table.horizontalHeader().setStretchLastSection(True)

    def refresh_native_panel(self, user_triggered=False):
        if self.status_worker is not None and self.status_worker.isRunning():
            return
        if user_triggered:
            self._set_button_busy(self.btn_refresh_native, '\u5237\u65b0\u4e2d...')
            self._set_button_busy(self.btn_refresh_overview, '\u5237\u65b0\u4e2d...')
            self._set_button_busy(self.btn_refresh_accounts, '\u5237\u65b0\u4e2d...')
        self.native_status_label.setText('\u6b63\u5728\u5237\u65b0\u72b6\u6001...')
        self.status_worker = StatusWorker(API_URL, self)
        self.status_worker.success.connect(lambda data, user_triggered=user_triggered: self._on_status_refresh_done(data, user_triggered))
        self.status_worker.failed.connect(lambda error, user_triggered=user_triggered: self._on_status_refresh_failed(error, user_triggered))
        self.status_worker.start()

    def _apply_status_data(self, data):
        stats = data.get('stats') or {}
        accounts = data.get('accounts') or []
        total = stats.get('total_accounts', len(accounts))
        active = stats.get('active_accounts', '-')
        today_calls = stats.get('today_calls', 0)
        today_input = stats.get('today_input_tokens', 0)
        today_output = stats.get('today_output_tokens', 0)
        self.card_api._value_label.setText(f'\u8fd0\u884c\u4e2d:{config.SERVER_PORT}')
        self.card_panel._value_label.setText(f'\u8fd0\u884c\u4e2d:{config.DASHBOARD_PORT}')
        self.card_pool._value_label.setText(f'{total} \u4e2a\u8d26\u53f7')
        self.native_status_label.setText(f'\u603b\u8d26\u53f7\uff1a{total} | \u53ef\u7528\uff1a{active} | \u4eca\u65e5\u8c03\u7528\uff1a{today_calls} | \u4eca\u65e5\u8f93\u5165Token\uff1a{today_input} | \u4eca\u65e5\u8f93\u51faToken\uff1a{today_output}')
        rows, acct_rows = [], []
        for account in accounts:
            status = account.get('status', '')
            if status == 'ready':
                status = '\u5c31\u7eea'
            elif status == 'expired':
                status = '\u5df2\u8fc7\u671f'
            remaining = self._format_remaining(account.get('token_remaining_sec', ''))
            rows.append([account.get('username', ''), status, remaining, account.get('daily_calls', 0), account.get('daily_input_tokens', 0), account.get('daily_output_tokens', 0), account.get('source', '-')])
            acct_rows.append([account.get('username', ''), status, '\u662f' if account.get('has_totp') else '-', account.get('source', '-')])
        self._set_table(self.overview_table, rows or [['\u6682\u65e0\u8d26\u53f7', '-', '-', '-', '-', '-', '-']])
        self._set_table(self.account_table, acct_rows or [['\u6682\u65e0\u8d26\u53f7', '-', '-', '-']])
        self.refresh_request_logs_native()

    def _restore_refresh_buttons(self):
        self._restore_button(self.btn_refresh_native)
        self._restore_button(self.btn_refresh_overview)
        self._restore_button(self.btn_refresh_accounts)

    def _on_status_refresh_done(self, data, user_triggered=False):
        self._apply_status_data(data)
        if user_triggered:
            self.btn_refresh_native.setText('\u5df2\u5237\u65b0')
            self.btn_refresh_overview.setText('\u5df2\u5237\u65b0')
            self.btn_refresh_accounts.setText('\u5df2\u5237\u65b0')
            QTimer.singleShot(900, self._restore_refresh_buttons)
        self.status_worker = None

    def _on_status_refresh_failed(self, error, user_triggered=False):
        self.card_api._value_label.setText(f'\u672a\u8fd0\u884c:{config.SERVER_PORT}')
        self.card_panel._value_label.setText(f'\u672a\u8fd0\u884c:{config.DASHBOARD_PORT}')
        self.card_pool._value_label.setText('0 \u4e2a\u8d26\u53f7')
        self.native_status_label.setText('API \u670d\u52a1\u672a\u542f\u52a8\uff0c\u70b9\u51fb\u4e0a\u65b9\u201c\u542f\u52a8\u670d\u52a1\u201d\u3002')
        self._set_table(self.overview_table, [['\u670d\u52a1\u672a\u542f\u52a8', '-', '-', '-', '-', '-', '-']])
        self._set_table(self.account_table, [['\u670d\u52a1\u672a\u542f\u52a8', '-', '-', '-']])
        self.append_log(f'[NativePanel] \u72b6\u6001\u5237\u65b0\u5931\u8d25\uff1a{error}\n')
        if user_triggered:
            self.btn_refresh_native.setText('\u5237\u65b0\u5931\u8d25')
            self.btn_refresh_overview.setText('\u5237\u65b0\u5931\u8d25')
            self.btn_refresh_accounts.setText('\u5237\u65b0\u5931\u8d25')
            QTimer.singleShot(1200, self._restore_refresh_buttons)
        self.status_worker = None
    def refresh_request_logs_native(self):
        try:
            from request_logger import get_request_logger
            logs = get_request_logger().get_logs(filter_type=self.log_filter_combo.currentText(), limit=100)
            rows = [[l.get('ts',''), l.get('model',''), l.get('account',''), f"{l.get('elapsed_ms',0):.0f}ms", l.get('status',''), (l.get('summary') or '')[:80]] for l in reversed(logs)]
            self._set_table(self.request_log_table, rows or [['暂无日志','-','-','-','-','-']])
        except Exception as exc:
            self.append_log(f'[NativePanel] 请求日志刷新失败：{exc}\n')

    def refresh_token_native(self, username=None):
        username = (username or '').strip()
        if username in ('', '\u6682\u65e0\u8d26\u53f7', '\u670d\u52a1\u672a\u542f\u52a8', '-'):
            if username:
                QMessageBox.information(self, '\u63d0\u793a', '\u8bf7\u5148\u9009\u62e9\u4e00\u4e2a\u6709\u6548\u8d26\u53f7\u3002')
                return
            username = None
        if self.token_worker is not None and self.token_worker.isRunning():
            QMessageBox.information(self, '\u63d0\u793a', '\u5df2\u6709\u767b\u5f55/\u5237\u65b0\u4efb\u52a1\u6b63\u5728\u8fdb\u884c\uff0c\u8bf7\u7a0d\u5019\u3002')
            return
        payload = {'username': username} if username else {}
        target = username or '\u5168\u90e8\u8d26\u53f7'
        self.append_log(f'[NativePanel] \u6b63\u5728\u540e\u53f0\u5237\u65b0 Token\uff1a{target}\n')
        self.btn_login_all.setEnabled(False)
        self.btn_login_selected.setEnabled(False)
        self.btn_login_selected_overview.setEnabled(False)
        self.token_worker = ApiWorker(API_URL, '/refresh', method='POST', payload=payload, timeout=180, parent=self)
        self.token_worker.success.connect(lambda _data, target=target: self._on_token_refresh_done(target))
        self.token_worker.failed.connect(lambda error, target=target: self._on_token_refresh_failed(target, error))
        self.token_worker.finished.connect(self._on_token_worker_finished)
        self.token_worker.start()

    def _on_token_refresh_done(self, target):
        self.append_log(f'[NativePanel] Token \u5237\u65b0\u5b8c\u6210\uff1a{target}\n')
        self.refresh_native_panel()

    def _on_token_refresh_failed(self, target, error):
        QMessageBox.warning(self, '\u5237\u65b0\u5931\u8d25', error)
        self.append_log(f'[NativePanel] Token \u5237\u65b0\u5931\u8d25\uff1a{target}\uff0c{error}\n')
        self.refresh_native_panel()

    def _on_token_worker_finished(self):
        self.btn_login_all.setEnabled(True)
        self.btn_login_selected.setEnabled(True)
        self.btn_login_selected_overview.setEnabled(True)
        self.token_worker = None
        self.status_worker = None
        self._detecting_service = False

    def delete_selected_account_native(self):
        username = self.selected_account.text().strip()
        if not username or username == '暂无账号':
            QMessageBox.information(self, '提示', '请先选择账号。')
            return
        reply = QMessageBox.question(self, '确认删除', f'确定删除账号 {username} 吗？', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        try:
            from auth import get_account_manager
            ok = get_account_manager().remove_account(username)
            self.append_log(f'[NativePanel] 删除账号 {username}: {ok}\n')
        except Exception as exc:
            QMessageBox.warning(self, '删除失败', str(exc))
        self.refresh_native_panel()

    def send_test_native(self):
        model = self.model_combo.currentText()
        message = self.test_input.toPlainText().strip()
        if not message:
            QMessageBox.information(self, '提示', '请输入测试消息。')
            return
        self.test_output.setPlainText('请求中，请稍候...')
        QApplication.processEvents()
        try:
            payload = {'model': model, 'messages': [{'role': 'user', 'content': message}], 'stream': False}
            data = self._api_json('/v1/chat/completions', method='POST', payload=payload, timeout=180)
            content = data.get('choices', [{}])[0].get('message', {}).get('content', '')
            self.test_output.setPlainText(content or json.dumps(data, ensure_ascii=False, indent=2))
        except Exception as exc:
            self.test_output.setPlainText(f'请求失败：{exc}')
        self.refresh_request_logs_native()

    def clear_request_logs_native(self):
        try:
            from request_logger import get_request_logger
            get_request_logger().clear()
            self.refresh_request_logs_native()
            self.append_log('[NativePanel] 请求日志已清空。\n')
        except Exception as exc:
            QMessageBox.warning(self, '清空失败', str(exc))

    def _load_dashboard_config(self):
        path = Path(config.DATA_DIR) / 'dashboard_config.json'
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return {'server': {'api_port': config.SERVER_PORT, 'host': config.SERVER_HOST}, 'token': {'ttl_seconds': config.TOKEN_TTL, 'refresh_cooldown_seconds': config.TOKEN_REFRESH_COOLDOWN}, 'playwright': {'headless': config.PLAYWRIGHT_HEADLESS}}

    def save_native_config(self):
        try:
            cfg = {'server': {'api_port': int(self.cfg_api_port.text().strip()), 'host': config.SERVER_HOST}, 'token': {'ttl_seconds': int(self.cfg_ttl.text().strip()), 'refresh_cooldown_seconds': int(self.cfg_cooldown.text().strip())}, 'playwright': {'headless': bool(self.cfg_headless.isChecked())}}
            path = Path(config.DATA_DIR) / 'dashboard_config.json'
            path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
            config.TOKEN_TTL = cfg['token']['ttl_seconds']
            config.TOKEN_REFRESH_COOLDOWN = cfg['token']['refresh_cooldown_seconds']
            config.PLAYWRIGHT_HEADLESS = cfg['playwright']['headless']
            self.cfg_status.setText('已保存（端口等部分配置需重启生效）')
            self.append_log('[NativePanel] 配置已保存。\n')
        except Exception as exc:
            self.cfg_status.setText(f'保存失败：{exc}')


    def add_account_native(self):
        dlg = AddAccountDialog(self)
        if dlg.exec_() == QDialog.Accepted:
            data = dlg.get_data()
            if not data['username'] or not data['password']:
                QMessageBox.warning(self, '??', '????????')
                return
            ok = self.auth_manager.add_account(data['username'], data['password'], data['totp_secret'])
            if ok:
                self.append_log(f'[NativePanel] ?? {data["username"]} ????\n')
                self.refresh_native_panel()
            else:
                QMessageBox.warning(self, '??', '????????')

    def _select_account_from_overview(self, row, col):
        item = self.overview_table.item(row, 0)
        if item:
            self.selected_account.setText(item.text())

    def _select_account_from_accounts(self, row, col):
        item = self.account_table.item(row, 0)
        if item:
            self.selected_account.setText(item.text())

    def _detect_existing_service(self, initial=False):
        if self._detecting_service:
            return
        self._detecting_service = True
        worker = StatusWorker(API_URL, self)
        worker.success.connect(lambda data, initial=initial, worker=worker: self._on_service_detected(data, initial, worker))
        worker.failed.connect(lambda error, initial=initial, worker=worker: self._on_service_detect_failed(error, initial, worker))
        worker.start()

    def _on_service_detected(self, data, initial, worker):
        self._detecting_service = False
        self.services_started = True
        self.card_api._value_label.setText(f'\u8fd0\u884c\u4e2d:{config.SERVER_PORT}')
        self.card_panel._value_label.setText(f'\u8fd0\u884c\u4e2d:{config.DASHBOARD_PORT}')
        self.btn_open_api.setEnabled(True)
        self.btn_open_browser.setEnabled(True)
        self.btn_stop.setEnabled(bool(self.started_by_launcher and self.process is not None))
        self.btn_restart.setEnabled(True)
        if initial:
            self.append_log('[Launcher] \u670d\u52a1\u68c0\u6d4b\uff1aAPI=True, Dashboard=True\n')
        self.statusBar().showMessage(f'\u8fd0\u884c\u4e2d | API: {API_URL} | \u9762\u677f: {DASH_URL}')
        self._apply_status_data(data)
        worker.deleteLater()

    def _on_service_detect_failed(self, error, initial, worker):
        self._detecting_service = False
        self.services_started = False
        self.card_api._value_label.setText(f'\u672a\u8fd0\u884c:{config.SERVER_PORT}')
        self.card_panel._value_label.setText(f'\u672a\u8fd0\u884c:{config.DASHBOARD_PORT}')
        self.btn_open_api.setEnabled(False)
        self.btn_open_browser.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self.btn_restart.setEnabled(True)
        if initial:
            self.append_log('[Launcher] \u670d\u52a1\u68c0\u6d4b\uff1aAPI=False, Dashboard=False\n')
        self.statusBar().showMessage(f'\u672a\u8fd0\u884c | API: {API_URL} | \u9762\u677f: {DASH_URL}')
        worker.deleteLater()
    def _resolve_python(self):
        if getattr(sys, 'frozen', False):
            return str(Path(sys.executable))
        py313 = Path(r'C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe')
        if py313.exists():
            return str(py313)
        exe = Path(sys.executable)
        if exe.exists() and exe.suffix.lower() == '.exe' and 'python' in exe.name.lower():
            return str(exe)
        return 'python'

    def start_services(self, silent=False):
        if self.process is not None and self.process.state() != QProcess.NotRunning:
            if not silent:
                QMessageBox.information(self, '\u63d0\u793a', '\u5f53\u524d\u542f\u52a8\u5668\u5df2\u7ecf\u62c9\u8d77\u4e86\u4e00\u4e2a\u670d\u52a1\u8fdb\u7a0b\u3002')
            return
        if self.services_started and not self.started_by_launcher:
            if not silent:
                QMessageBox.information(self, '\u63d0\u793a', '\u68c0\u6d4b\u5230\u5df2\u6709\u5916\u90e8\u5b9e\u4f8b\u6b63\u5728\u8fd0\u884c\uff0c\u5df2\u76f4\u63a5\u63a5\u7ba1\u72b6\u6001\u548c\u539f\u751f\u63a7\u5236\u9762\u677f\u3002')
            self.refresh_native_panel()
            return
        self._set_button_busy(self.btn_start, '\u542f\u52a8\u4e2d...')
        self.btn_stop.setEnabled(True)
        self.btn_restart.setEnabled(False)
        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(APP_DIR))
        self.process.setProcessChannelMode(QProcess.SeparateChannels)
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.started.connect(self._on_process_started)
        self.process.finished.connect(self._on_process_finished)
        python_exe = self._resolve_python()
        self.append_log(f'[Launcher] 使用解释器：{python_exe}`n[Launcher] 正在启动 supervisor ...`n')
        if getattr(sys, 'frozen', False):
            self.process.start(python_exe, ['--supervisor'])
        else:
            self.process.start(python_exe, ['-u', 'm365_supervisor.py'] if python_exe.lower().endswith('.exe') else ['m365_supervisor.py'])
        QTimer.singleShot(2500, self._mark_start_feedback)

    def _mark_start_feedback(self):
        if self.process is not None and self.process.state() != QProcess.NotRunning:
            self.btn_start.setText('\u5df2\u542f\u52a8')
            QTimer.singleShot(900, lambda: self._restore_button(self.btn_start, enabled=False))
        self._detect_existing_service()

    def stop_services(self):
        self._set_button_busy(self.btn_stop, '\u505c\u6b62\u4e2d...')
        self.btn_start.setEnabled(False)
        self.btn_restart.setEnabled(False)
        if self.process is not None and self.process.state() != QProcess.NotRunning:
            self.append_log('[Launcher] \u6b63\u5728\u505c\u6b62\u542f\u52a8\u5668\u62c9\u8d77\u7684\u670d\u52a1\u8fdb\u7a0b...\n')
            self.process.kill()
            QTimer.singleShot(1200, self._finish_stop_feedback)
        else:
            self._finish_stop_feedback()
            QMessageBox.information(self, '\u63d0\u793a', '\u5f53\u524d\u6ca1\u6709\u7531\u672c\u542f\u52a8\u5668\u62c9\u8d77\u7684\u53ef\u505c\u6b62\u5b9e\u4f8b\u3002')

    def _finish_stop_feedback(self):
        self.started_by_launcher = False
        self.process = None
        self.btn_stop.setText('\u5df2\u505c\u6b62')
        self.btn_stop.setEnabled(False)
        self._restore_button(self.btn_start, enabled=True)
        self.btn_restart.setEnabled(True)
        QTimer.singleShot(900, lambda: self._restore_button(self.btn_stop, enabled=False))
        self._detect_existing_service()
        self.refresh_native_panel()

    def restart_services(self):
        self._set_button_busy(self.btn_restart, '\u91cd\u542f\u4e2d...')
        self.append_log('[Launcher] \u6b63\u5728\u6267\u884c\u91cd\u542f...\n')
        if self.process is not None and self.process.state() != QProcess.NotRunning:
            self.process.kill()
            QTimer.singleShot(1200, self._start_after_restart)
        elif self.services_started and not self.started_by_launcher:
            self.btn_restart.setText('\u65e0\u6cd5\u91cd\u542f')
            QTimer.singleShot(1200, lambda: self._restore_button(self.btn_restart, enabled=True))
            QMessageBox.information(self, '\u63d0\u793a', '\u68c0\u6d4b\u5230\u5f53\u524d\u670d\u52a1\u4e0d\u662f\u7531\u672c\u542f\u52a8\u5668\u62c9\u8d77\u3002\u4e3a\u907f\u514d\u8bef\u6740\u5916\u90e8\u8fdb\u7a0b\uff0c\u8bf7\u5148\u624b\u52a8\u5173\u95ed\u65e7\u5b9e\u4f8b\uff0c\u6216\u76f4\u63a5\u4f7f\u7528\u5f53\u524d\u5b9e\u4f8b\u3002')
        else:
            self._start_after_restart()

    def _start_after_restart(self):
        self.process = None
        self.started_by_launcher = False
        self._restore_button(self.btn_restart, enabled=False)
        self.start_services(silent=True)
    def _read_stdout(self):
        if self.process:
            self.append_log(bytes(self.process.readAllStandardOutput()).decode('utf-8', errors='replace'))

    def _read_stderr(self):
        if self.process:
            self.append_log(bytes(self.process.readAllStandardError()).decode('utf-8', errors='replace'))

    def _on_process_started(self):
        self.started_by_launcher = True
        self.append_log('[Launcher] 服务进程已启动，正在等待 API 就绪...\n')

    def _on_process_finished(self, exit_code, exit_status):
        self.append_log(f'[Launcher] 服务进程已退出：exit_code={exit_code}, exit_status={int(exit_status)}\n')
        self.btn_start.setEnabled(True); self.btn_stop.setEnabled(False); self.btn_restart.setEnabled(True)
        self.process = None; self.started_by_launcher = False
        self._detect_existing_service(); self.refresh_native_panel()

    def open_logs(self):
        log_dir = Path(getattr(config, 'LOG_DIR', APP_DIR / 'logs'))
        if not log_dir.is_absolute():
            log_dir = APP_DIR / log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir)))

    def open_api(self):
        QDesktopServices.openUrl(QUrl(f'{API_URL}/status'))

    def open_browser(self):
        QDesktopServices.openUrl(QUrl(DASH_URL))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_overview_columns()
        self._fit_account_columns()

    def closeEvent(self, event):
        if self.process is not None and self.process.state() != QProcess.NotRunning:
            reply = QMessageBox.question(self, '退出确认', '检测到启动器拉起的服务仍在运行。\n\n是否退出并终止该进程？', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.stop_services(); event.accept()
            else:
                event.ignore(); return
        event.accept()


def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    icon_path = resolve_icon_path()
    if icon_path:
        app.setWindowIcon(QIcon(str(icon_path)))
    app.setStyle('Fusion')
    app.setStyleSheet(LIGHT_QSS)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()


