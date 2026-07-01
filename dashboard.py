from __future__ import annotations

import asyncio
import json
import os
import time

import gradio as gr

import config
from copilot_client import MODEL_TONE_MAP, SubstrateClient
from runtime_config import CONFIG_FILE, load_runtime_config, save_runtime_config


auth_mgr = None
req_logger = None
_config_file = os.path.join(config.DATA_DIR, 'dashboard_config.json')


def _status_text(status: str) -> str:
    return {
        'ready': '可用',
        'expired': '过期',
        'cooldown': '冷却中',
    }.get(status, '未知')


def get_dashboard_data():
    stats = auth_mgr.get_daily_stats()
    statuses = auth_mgr.get_all_status()
    rows = []
    for item in statuses:
        seconds = item.get('token_remaining_sec', 0)
        countdown = f'{int(seconds // 60)}分{int(seconds % 60)}秒' if seconds > 0 else '已过期'
        rows.append([
            item['username'],
            _status_text(item['status']),
            countdown,
            item.get('daily_calls', 0),
            item.get('daily_input_tokens', 0),
            item.get('daily_output_tokens', 0),
            item.get('source', '-'),
        ])
    return (
        stats['total_accounts'],
        stats['active_accounts'],
        stats.get('today_calls', 0),
        stats.get('today_input_tokens', 0),
        stats.get('today_output_tokens', 0),
        stats.get('today_total_tokens', 0),
        rows or [['暂无账号', '-', '-', 0, 0, 0, '-']],
    )


def get_account_table():
    rows = []
    for item in auth_mgr.get_all_status():
        rows.append([
            item['username'],
            _status_text(item['status']),
            '是' if item.get('has_totp') else '否',
            item.get('source', '-'),
        ])
    return rows or [['暂无账号', '-', '-', '-']]


def _selected_username(data, evt: gr.SelectData):
    try:
        row_index = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index
        if hasattr(data, 'iloc'):
            value = data.iloc[row_index, 0]
        else:
            value = data[row_index][0]
        return '' if value == '暂无账号' else str(value)
    except Exception:
        return ''


def add_account(username, password, totp_secret):
    if not username or not password:
        gr.Warning('邮箱和密码不能为空')
        return get_account_table(), *get_dashboard_data()
    ok = auth_mgr.add_account(username.strip(), password, (totp_secret or '').strip())
    gr.Info('账号添加成功' if ok else '账号添加失败，可能已经存在')
    return get_account_table(), *get_dashboard_data()


def remove_account(username):
    if not username:
        gr.Warning('请先选择账号')
        return get_account_table(), *get_dashboard_data()
    ok = auth_mgr.remove_account(username)
    gr.Info('账号已删除' if ok else '删除失败')
    return get_account_table(), *get_dashboard_data()


def login_account(username):
    if not username:
        gr.Warning('请先选择账号')
        return get_account_table(), *get_dashboard_data()
    ok = auth_mgr.refresh_token(username)
    gr.Info('登录成功' if ok else '登录失败，请检查账号或密码')
    return get_account_table(), *get_dashboard_data()


def login_all_accounts():
    ok = auth_mgr.refresh_token()
    gr.Info('批量登录完成' if ok else '批量登录未成功')
    return get_account_table(), *get_dashboard_data()


def test_model(model, message):
    if not message:
        return '请输入测试消息', {}
    token, used_account = auth_mgr.get_token()
    if not token:
        return '没有可用 token，请先登录账号', {}
    client = SubstrateClient(token, used_account)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        start = time.time()
        result = loop.run_until_complete(client.chat(message, model=model))
        elapsed = time.time() - start
    finally:
        loop.close()
    meta = {
        'account': used_account,
        'model_tone': MODEL_TONE_MAP.get(model, 'Magic'),
        'elapsed_seconds': round(elapsed, 2),
    }
    if result.get('error'):
        return f"错误: {result['error']}", meta
    return result.get('content', '(空回复)'), meta


def get_log_data(filter_type):
    if not req_logger:
        return [['暂无日志', '-', '-', '-', '-', '-', '-', '-', '-', '-']]
    rows = []
    for item in reversed(req_logger.get_logs(filter_type=filter_type, limit=200)):
        rows.append([
            item.get('ts', '-'),
            item.get('client_ip', '-'),
            item.get('api', '-'),
            item.get('model', '-'),
            item.get('account', '-'),
            f"{item.get('elapsed_ms', 0):.0f}ms",
            item.get('status', '-'),
            ', '.join(item.get('tools') or []) or '-',
            '是' if item.get('has_tool_output') else '否',
            (item.get('summary') or '-')[:120],
        ])
    return rows or [['暂无日志', '-', '-', '-', '-', '-', '-', '-', '-', '-']]


def clear_logs():
    if req_logger:
        req_logger.clear()
    gr.Info('内存日志已清空，历史 JSONL 文件保留')
    return get_log_data('all')


def load_dashboard_config():
    try:
        with open(_config_file, 'r', encoding='utf-8') as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            'server': {'api_port': config.SERVER_PORT, 'host': config.SERVER_HOST},
            'token': {'ttl_seconds': config.TOKEN_TTL, 'refresh_cooldown_seconds': config.TOKEN_REFRESH_COOLDOWN},
            'playwright': {'headless': config.PLAYWRIGHT_HEADLESS},
        }


def save_dashboard_config(api_port, ttl, cooldown, headless):
    cfg = load_dashboard_config()
    cfg['server']['api_port'] = int(api_port)
    cfg['token']['ttl_seconds'] = int(ttl)
    cfg['token']['refresh_cooldown_seconds'] = int(cooldown)
    cfg['playwright']['headless'] = bool(headless)
    with open(_config_file, 'w', encoding='utf-8') as handle:
        json.dump(cfg, handle, ensure_ascii=False, indent=2)
    config.TOKEN_TTL = int(ttl)
    config.TOKEN_REFRESH_COOLDOWN = int(cooldown)
    config.PLAYWRIGHT_HEADLESS = bool(headless)
    return '已保存，端口类配置重启后生效'


def get_runtime_config_text():
    return json.dumps(load_runtime_config(), ensure_ascii=False, indent=2)


def save_runtime_config_text(text):
    try:
        cfg = json.loads(text)
    except json.JSONDecodeError as exc:
        gr.Warning(f'配置 JSON 格式错误: {exc}')
        return text, '保存失败：JSON 格式错误'
    save_runtime_config(cfg)
    return json.dumps(cfg, ensure_ascii=False, indent=2), '已保存，重启后完全生效'


def get_connection_guide():
    models = [item.get('id') for item in load_runtime_config().get('models', [])]
    thinking = 'gpt-5.5-thinking' if 'gpt-5.5-thinking' in models else (models[0] if models else 'copilot-auto')
    quick = 'gpt-5.5' if 'gpt-5.5' in models else thinking
    return f'''### 接入地址

- 本机：`http://127.0.0.1:{config.SERVER_PORT}/v1`
- 局域网：`http://你的Windows主机IP:{config.SERVER_PORT}/v1`
- API Key：默认随意填写，开启安全开关后需填配置里的 key

### 推荐模型

- `{thinking}`：深度思考
- `{quick}`：快速响应
- `copilot-auto`：自动选择

### 客户端

- Hermes：Base URL 填 `/v1`，模型填 `{thinking}`
- Windows Codex：`wire_api = "responses"`，Base URL 填 `/v1`
- Mac Codex：局域网地址填 Windows 主机 IP，`exec_command` 已兼容
'''


CUSTOM_JS = """
function() {
  document.addEventListener('dblclick', function(e) {
    const cell = e.target.closest('td, .cell, [data-testid]');
    if (!cell) return;
    const text = cell.textContent.trim();
    if (!text) return;
    navigator.clipboard.writeText(text).catch(() => {});
  });
}
"""


def create_dashboard(manager, logger=None):
    global auth_mgr, req_logger
    auth_mgr = manager
    req_logger = logger
    cfg = load_dashboard_config()
    runtime_cfg = load_runtime_config()
    model_choices = [item.get('id') for item in runtime_cfg.get('models', [])] or list(MODEL_TONE_MAP.keys())[:7]

    with gr.Blocks(title='M365 Copilot 管理面板') as demo:
        gr.Markdown('# M365 Copilot 管理面板')
        gr.Markdown('统一 8080 入口 / 多账号 / OpenAI 兼容 / 工具调用 / 局域网接入')

        with gr.Row():
            gr.Textbox(label='API 服务', value=f'运行中 :{config.SERVER_PORT}', interactive=False)
            gr.Textbox(label='Dashboard', value=f'运行中 :{config.DASHBOARD_PORT}', interactive=False)
            gr.Textbox(label='账号池', value=f'{len(manager.accounts)} 个账号', interactive=False)

        with gr.Tabs():
            with gr.Tab('状态总览'):
                with gr.Row():
                    stat_total = gr.Number(label='总账号数', value=0, interactive=False)
                    stat_active = gr.Number(label='可用账号', value=0, interactive=False)
                    stat_calls = gr.Number(label='今日调用', value=0, interactive=False)
                    stat_total_tokens = gr.Number(label='今日 Token', value=0, interactive=False)
                with gr.Row():
                    stat_in_tokens = gr.Number(label='今日输入 Token', value=0, interactive=False)
                    stat_out_tokens = gr.Number(label='今日输出 Token', value=0, interactive=False)
                status_table = gr.Dataframe(
                    headers=['账号', '状态', '令牌剩余', '调用', '输入Token', '输出Token', '来源'],
                    datatype=['str', 'str', 'str', 'number', 'number', 'number', 'str'],
                    interactive=False,
                    wrap=True,
                )
                selected_display = gr.Textbox(label='当前选中', interactive=False)
                with gr.Row():
                    refresh_single_btn = gr.Button('登录选中账号', variant='primary')
                    refresh_all_btn = gr.Button('批量登录')
                    refresh_table_btn = gr.Button('刷新状态')

                dash_outputs = [stat_total, stat_active, stat_calls, stat_in_tokens, stat_out_tokens, stat_total_tokens, status_table]
                status_table.select(fn=_selected_username, inputs=[status_table], outputs=[selected_display])
                refresh_single_btn.click(fn=login_account, inputs=[selected_display], outputs=[status_table, *dash_outputs])
                refresh_all_btn.click(fn=login_all_accounts, outputs=[status_table, *dash_outputs])
                refresh_table_btn.click(fn=get_dashboard_data, outputs=dash_outputs)
                demo.load(fn=get_dashboard_data, outputs=dash_outputs)

            with gr.Tab('账号管理'):
                with gr.Row():
                    with gr.Column():
                        new_username = gr.Textbox(label='邮箱地址')
                        new_password = gr.Textbox(label='密码', type='password')
                        new_totp = gr.Textbox(label='TOTP 密钥，可选')
                        add_btn = gr.Button('添加账号', variant='primary')
                    with gr.Column(scale=2):
                        account_table = gr.Dataframe(
                            headers=['账号', '状态', 'TOTP', '来源'],
                            datatype=['str', 'str', 'str', 'str'],
                            interactive=False,
                            wrap=True,
                        )
                account_selected = gr.Textbox(label='当前选中', interactive=False)
                with gr.Row():
                    login_btn = gr.Button('自动登录', variant='primary')
                    delete_btn = gr.Button('删除', variant='stop')
                account_outputs = [account_table, stat_total, stat_active, stat_calls, stat_in_tokens, stat_out_tokens, stat_total_tokens, status_table]
                account_table.select(fn=_selected_username, inputs=[account_table], outputs=[account_selected])
                add_btn.click(fn=add_account, inputs=[new_username, new_password, new_totp], outputs=account_outputs)
                delete_btn.click(fn=remove_account, inputs=[account_selected], outputs=account_outputs)
                login_btn.click(fn=login_account, inputs=[account_selected], outputs=account_outputs)
                demo.load(fn=get_account_table, outputs=[account_table])

            with gr.Tab('模型测试'):
                test_model_dd = gr.Dropdown(choices=model_choices, value=model_choices[0], label='模型')
                test_message = gr.Textbox(label='测试消息', lines=3, value='你好，简单回复一句')
                test_btn = gr.Button('发送测试', variant='primary')
                test_output = gr.Textbox(label='回复', lines=12)
                test_meta = gr.JSON(label='元数据')
                test_btn.click(fn=test_model, inputs=[test_model_dd, test_message], outputs=[test_output, test_meta])

            with gr.Tab('日志查看'):
                with gr.Row():
                    log_filter = gr.Dropdown(choices=[('全部', 'all'), ('仅错误', 'errors'), ('今日', 'today')], value='all', label='过滤')
                    refresh_log_btn = gr.Button('刷新')
                    clear_log_btn = gr.Button('清空内存日志')
                log_table = gr.Dataframe(
                    headers=['时间', '来源IP', '接口', '模型', '账号', '耗时', '状态', '工具', '工具结果', '摘要'],
                    datatype=['str', 'str', 'str', 'str', 'str', 'str', 'str', 'str', 'str', 'str'],
                    label='请求日志',
                    interactive=False,
                    wrap=True,
                )
                log_filter.change(fn=get_log_data, inputs=[log_filter], outputs=[log_table])
                refresh_log_btn.click(fn=get_log_data, inputs=[log_filter], outputs=[log_table])
                clear_log_btn.click(fn=clear_logs, outputs=[log_table])
                demo.load(fn=lambda: get_log_data('all'), outputs=[log_table])

            with gr.Tab('配置管理'):
                with gr.Accordion('接入说明', open=True):
                    guide_md = gr.Markdown(get_connection_guide())
                    gr.Button('刷新接入说明').click(fn=get_connection_guide, outputs=[guide_md])

                with gr.Row():
                    cfg_api_port = gr.Number(label='API 端口', value=cfg['server']['api_port'])
                    cfg_ttl = gr.Number(label='令牌有效期（秒）', value=cfg['token']['ttl_seconds'])
                    cfg_cooldown = gr.Number(label='失败冷却（秒）', value=cfg['token']['refresh_cooldown_seconds'])
                    cfg_headless = gr.Checkbox(label='浏览器无头模式', value=cfg['playwright']['headless'])
                cfg_status = gr.Textbox(label='保存状态', interactive=False)
                gr.Button('保存基础配置', variant='primary').click(
                    fn=save_dashboard_config,
                    inputs=[cfg_api_port, cfg_ttl, cfg_cooldown, cfg_headless],
                    outputs=[cfg_status],
                )

                with gr.Accordion('高级运行配置：模型 / 工具别名 / 局域网安全', open=False):
                    gr.Markdown(f'配置文件：`{CONFIG_FILE}`')
                    runtime_config_box = gr.Code(
                        label='m365_runtime_config.json',
                        value=get_runtime_config_text(),
                        language='json',
                        lines=24,
                    )
                    runtime_status = gr.Textbox(label='保存状态', interactive=False)
                    with gr.Row():
                        gr.Button('读取配置').click(fn=get_runtime_config_text, outputs=[runtime_config_box])
                        gr.Button('保存运行配置', variant='primary').click(
                            fn=save_runtime_config_text,
                            inputs=[runtime_config_box],
                            outputs=[runtime_config_box, runtime_status],
                        )

        timer = gr.Timer(30, active=True)
        timer.tick(fn=get_dashboard_data, outputs=dash_outputs)

    return demo
