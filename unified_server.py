"""Unified single-port M365 Copilot gateway.

This entrypoint keeps the original 7 models, tools handling, multi-account
management, and adds:
- token bucket rate limiting
- /health endpoint
- OpenAI Responses compatibility
- permissive API key handling (any value accepted)

It intentionally avoids the corrupted docstrings that were present in the older
server.py edits.
"""
from __future__ import annotations

import json
import re
import asyncio
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

from flask import Flask, request, jsonify, Response

from auth import get_account_manager
from copilot_client import SubstrateClient
from hybrid.ratelimit import TokenBucket
from request_logger import get_request_logger
from runtime_config import ip_allowed, is_loopback_ip, load_runtime_config
import config

app = Flask(__name__)
auth_manager = get_account_manager()
req_logger = get_request_logger()
runtime_cfg = load_runtime_config()

RATE_LIMIT_RPM = float(os.getenv('M365_RATE_LIMIT_RPM', '30'))
RATE_LIMIT_BURST = int(os.getenv('M365_RATE_LIMIT_BURST', '5'))
_rate_limiter = TokenBucket(RATE_LIMIT_RPM, RATE_LIMIT_BURST)

TOOL_MODEL_UPGRADE = {
    'copilot-auto': 'copilot-thinking',
    'copilot-quick': 'copilot-thinking',
    'gpt-5.5': 'gpt-5.5-thinking',
    'gpt-5.4': 'gpt-5.5-thinking',
    'gpt-5.4-mini': 'gpt-5.5-thinking',
    'gpt-5.2': 'gpt-5.2-thinking',
    'gpt-4o': 'copilot-thinking',
}

MODEL_LIST = runtime_cfg.get('models') or []

TOOL_ALIASES = runtime_cfg.get('tool_aliases') or {}

TERMINAL_TOOL_NAMES = {'exec_command', 'shell', 'shell_command', 'terminal', 'run_command', 'bash', 'powershell'}

TERMINAL_COMMAND_FIELDS = ('command', 'cmd', 'script')


def _canonical_tool_name(name: str | None) -> str:
    if not name:
        return ''
    return TOOL_ALIASES.get(str(name).lower(), str(name).lower())


def _client_ip() -> str:
    return (request.headers.get('X-Forwarded-For') or request.remote_addr or '').split(',')[0].strip()


def _request_api_key() -> str:
    auth_header = request.headers.get('Authorization') or ''
    if auth_header.lower().startswith('bearer '):
        return auth_header[7:].strip()
    return request.headers.get('X-API-Key') or request.args.get('api_key') or ''


@app.before_request
def _security_gate():
    if request.path in ('/health',):
        return None
    security = runtime_cfg.get('security') or {}
    client_ip = _client_ip()
    if not security.get('lan_access', True) and not is_loopback_ip(client_ip):
        return jsonify({'error': {'message': 'LAN access is disabled', 'type': 'access_denied'}}), 403
    if not ip_allowed(client_ip, security.get('allowed_ips') or []):
        return jsonify({'error': {'message': f'IP not allowed: {client_ip}', 'type': 'access_denied'}}), 403
    if security.get('require_api_key', False):
        allowed_keys = {str(key) for key in security.get('api_keys') or [] if str(key)}
        if _request_api_key() not in allowed_keys:
            return jsonify({'error': {'message': 'Invalid API key', 'type': 'authentication_error'}}), 401
    return None


def _normalize_content(content) -> str:
    if content is None:
        return ''
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                item_type = item.get('type')
                if item_type in ('text', 'input_text', 'output_text'):
                    parts.append(str(item.get('text', '')))
                elif 'content' in item:
                    parts.append(_normalize_content(item.get('content')))
                elif 'text' in item:
                    parts.append(str(item.get('text', '')))
            else:
                parts.append(str(item))
        return '\n'.join(x for x in parts if x)
    if isinstance(content, dict):
        if 'text' in content:
            return str(content.get('text') or '')
        if 'content' in content:
            return _normalize_content(content.get('content'))
        return json.dumps(content, ensure_ascii=False)
    return str(content)


def _estimate_tokens(text: str) -> int:
    text = _normalize_content(text)
    return len(text) // 3 if text else 0


def _responses_input_to_messages(input_value):
    if not isinstance(input_value, list):
        return [{'role': 'user', 'content': _normalize_content(input_value)}]
    messages = []
    for item in input_value:
        if not isinstance(item, dict):
            messages.append({'role': 'user', 'content': _normalize_content(item)})
            continue
        item_type = item.get('type')
        if item_type == 'message':
            messages.append({
                'role': item.get('role', 'user'),
                'content': _normalize_content(item.get('content', '')),
            })
        elif item_type == 'function_call':
            call_id = item.get('call_id') or item.get('id') or f'call_{uuid.uuid4().hex[:12]}'
            messages.append({
                'role': 'assistant',
                'content': None,
                'tool_calls': [{
                    'id': call_id,
                    'type': 'function',
                    'function': {
                        'name': item.get('name'),
                        'arguments': item.get('arguments') or '{}',
                    },
                }],
            })
        elif item_type in ('function_call_output', 'tool_result'):
            messages.append({
                'role': 'tool',
                'tool_call_id': item.get('call_id') or item.get('id'),
                'content': _normalize_content(item.get('output', item.get('content', ''))),
            })
        elif item.get('role'):
            messages.append({
                'role': item.get('role'),
                'content': _normalize_content(item.get('content', '')),
            })
        else:
            messages.append({'role': 'user', 'content': _normalize_content(item)})
    return messages


def _responses_has_tool_output(input_value) -> bool:
    if not isinstance(input_value, list):
        return False
    return any(
        isinstance(item, dict) and item.get('type') in ('function_call_output', 'tool_result')
        for item in input_value
    )

def _rate_limited_response():
    allowed, wait = _rate_limiter.try_acquire()
    if allowed:
        return None
    secs = max(1, round(wait))
    return jsonify({
        'error': {
            'message': f'Rate limit exceeded ({RATE_LIMIT_RPM:g} req/min). Retry in {secs}s.',
            'type': 'rate_limit_error',
            'code': 'rate_limit_exceeded',
        }
    }), 429, {'Retry-After': str(secs)}


def _is_401_error(error_str: str) -> bool:
    return '401' in error_str and ('rejected' in error_str or 'Unauthorized' in error_str.lower())


def _make_tool_call(name: str, arguments: dict) -> dict:
    return {
        'id': f'call_{uuid.uuid4().hex[:12]}',
        'type': 'function',
        'function': {
            'name': name,
            'arguments': json.dumps(arguments, ensure_ascii=False),
        },
    }


def _normalize_tools(tools):
    if not tools:
        return tools
    normalized = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if tool.get('type') == 'function' and isinstance(tool.get('function'), dict):
            normalized.append(tool)
            continue
        if tool.get('name'):
            normalized.append({
                'type': 'function',
                'function': {
                    'name': tool.get('name'),
                    'description': tool.get('description', ''),
                    'parameters': tool.get('parameters') or {},
                },
            })
            continue
        if tool.get('type') == 'function' and tool.get('name'):
            normalized.append({
                'type': 'function',
                'function': {
                    'name': tool.get('name'),
                    'description': tool.get('description', ''),
                    'parameters': tool.get('parameters') or {},
                },
            })
    return normalized


def build_tools_system_prompt(tools, tool_choice=None):
    if not tools:
        return None
    tool_names = []
    lines = [
        'IMPORTANT: You cannot execute local tools yourself. When a local tool is needed, return ONLY a tool-call JSON block.',
        'Do not explain the tool call in natural language.',
        'Use exactly one of these formats:',
        '[Tool Call]: [{"id":"call_x","type":"function","function":{"name":"tool_name","arguments":"{\\"arg\\":\\"value\\"}"}}]',
        'or:',
        '{"tool_calls":[{"id":"call_x","type":"function","function":{"name":"tool_name","arguments":{"arg":"value"}}}]}',
        '',
        'Available tools:'
    ]
    for tool in tools:
        if tool.get('type') != 'function':
            continue
        fn = tool.get('function', {})
        name = fn.get('name', 'tool')
        tool_names.append(name)
        desc = fn.get('description', '')
        params = fn.get('parameters') or {}
        canonical = _canonical_tool_name(name)
        alias_note = f' [category: {canonical}]' if canonical != name.lower() else ''
        lines.append(f'- {name}{alias_note}: {desc}')
        if params:
            lines.append('  parameters: ' + json.dumps(params, ensure_ascii=False))
    lower_tool_names = {name.lower() for name in tool_names}
    desktop_tools = [name for name in tool_names if any(key in name.lower() for key in ('computer', 'desktop', 'gui', 'browser', 'mouse', 'keyboard', 'screen'))]
    if desktop_tools:
        lines.append('')
        lines.append('Tool routing rules:')
        lines.append('- For requests involving the visible desktop, Windows UI, screenshots, mouse/keyboard actions, or controlling the user computer, prefer these desktop tools: ' + ', '.join(desktop_tools))
        lines.append('- Use terminal only for shell commands inside the terminal backend. Do not assume terminal can access the Windows host or C: drive unless the tool description says it can.')
    elif any(_canonical_tool_name(name) == 'terminal' for name in lower_tool_names):
        lines.append('')
        lines.append('Terminal environment rule:')
        lines.append('- Terminal aliases include exec_command, shell, shell_command, and terminal. Use the exact tool name provided by the client.')
        lines.append('- The terminal tool may run in a sandbox or Linux-like backend. For host state, trust only the actual command result.')
    if tool_choice and tool_choice not in ('auto', 'none'):
        lines.append('')
        lines.append('Tool choice constraint: ' + json.dumps(tool_choice, ensure_ascii=False))
    lines.append('')
    lines.append('If a user asks you to use, run, click, type, open, read, write, list, inspect, screenshot, or control the computer, you MUST return only a tool call. Never invent the tool result.')
    return '\n'.join(lines)


def _coerce_tool_arguments(arguments):
    if arguments is None:
        return '{}'
    if isinstance(arguments, str):
        stripped = arguments.strip()
        if not stripped:
            return '{}'
        try:
            parsed = json.loads(stripped)
            return json.dumps(parsed, ensure_ascii=False)
        except Exception:
            return stripped
    return json.dumps(arguments, ensure_ascii=False)


def _normalize_tool_call(call: dict, allowed_names: set) -> dict | None:
    if not isinstance(call, dict):
        return None
    fn = call.get('function') or {}
    name = fn.get('name') or call.get('name')
    if not name or (allowed_names and name not in allowed_names):
        return None
    arguments = fn.get('arguments', call.get('arguments', {}))
    return {
        'id': call.get('id') or f'call_{uuid.uuid4().hex[:12]}',
        'type': 'function',
        'function': {
            'name': name,
            'arguments': _coerce_tool_arguments(arguments),
        },
    }


def _repair_json_candidate(candidate: str) -> str:
    text = (candidate or '').strip()
    if not text:
        return text
    opens = {'{': '}', '[': ']'}
    stack = []
    in_string = False
    escape = False
    for ch in text:
        if escape:
            escape = False
            continue
        if ch == '\\':
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in opens:
            stack.append(opens[ch])
        elif stack and ch == stack[-1]:
            stack.pop()
    if in_string:
        text += '"'
    while stack:
        text += stack.pop()
    return text
def _extract_json_objects(text: str):
    candidates = []
    if not text:
        return candidates
    stripped = text.strip()
    code_blocks = re.findall(r'```(?:json)?\s*(.*?)```', stripped, flags=re.S | re.I)
    candidates.extend(code_blocks)
    marker = re.search(r'\[?Tool Call\]?\s*:\s*(\[.*\]|\{.*\})', stripped, flags=re.S | re.I)
    if marker:
        candidates.append(_repair_json_candidate(marker.group(1)))
    partial = re.search(r'(?:"?tool_calls"?\s*:\s*\[.*\])\s*}?', stripped, flags=re.S)
    if partial:
        partial_text = partial.group(0).strip()
        if not partial_text.startswith('{'):
            if partial_text.startswith('"'):
                partial_text = '{' + partial_text
            else:
                partial_text = '{"' + partial_text
        if not partial_text.endswith('}'):
            partial_text = partial_text + '}'
        candidates.append(_repair_json_candidate(partial_text))
    first_object = re.search(r'(\{.*\}|\[.*\])', stripped, flags=re.S)
    if first_object:
        candidates.append(_repair_json_candidate(first_object.group(1)))
    candidates.append(_repair_json_candidate(stripped))
    return candidates

def extract_tool_calls(content: str, tools):
    if not tools or not content:
        return []
    allowed_names = {t.get('function', {}).get('name') for t in tools if t.get('type') == 'function'}
    allowed_names.discard(None)
    for candidate in _extract_json_objects(content):
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        raw_calls = None
        if isinstance(parsed, dict):
            raw_calls = parsed.get('tool_calls') or parsed.get('toolCalls')
            if raw_calls is None and (parsed.get('function') or parsed.get('name')):
                raw_calls = [parsed]
        elif isinstance(parsed, list):
            raw_calls = parsed
        if not raw_calls:
            continue
        calls = []
        for call in raw_calls:
            normalized = _normalize_tool_call(call, allowed_names)
            if normalized:
                calls.append(normalized)
        if calls:
            return calls
    return []


def _summarize_param_schema(pname: str, pinfo: dict, required: set) -> str:
    ptype = pinfo.get('type', 'any')
    req = '*' if pname in required else ''
    parts = [f"{pname}{req}:{ptype}"]
    enum_vals = pinfo.get('enum')
    if enum_vals:
        parts.append('enum=' + '/'.join(str(v) for v in enum_vals))
    if 'default' in pinfo:
        parts.append(f"default={pinfo.get('default')}")
    desc = (pinfo.get('description') or '').strip().replace('\n', ' ')
    if desc:
        parts.append(desc[:80])
    return '[' + '; '.join(parts) + ']'


def _example_value_for_schema(pname: str, pinfo: dict):
    if 'default' in pinfo:
        return pinfo['default']
    enum_vals = pinfo.get('enum')
    if enum_vals:
        if pname == 'sandbox_permissions' and 'use_default' in enum_vals:
            return 'use_default'
        return enum_vals[0]
    ptype = pinfo.get('type')
    if pname in ('command', 'cmd'):
        return 'Get-ChildItem -Name'
    if pname in ('workdir', 'cwd'):
        return '.'
    if ptype == 'boolean':
        return False
    if ptype == 'integer':
        return 1
    if ptype == 'number':
        return 1.0
    if ptype == 'array':
        return []
    if ptype == 'object':
        return {}
    return 'value'


def _build_tools_schema_prompt(tools):
    if not tools:
        return None
    lines = ['TOOLS:']
    for tool in tools:
        fn = tool.get('function', {})
        lines.append(f"- {fn.get('name', 'tool')}: {fn.get('description', '')}")
    return '\n'.join(lines)


def _build_client(token, used_account):
    return SubstrateClient(token, used_account)


@app.route('/health', methods=['GET'])
def health():
    account_count = len(getattr(auth_manager, 'accounts', []) or [])
    cached_tokens = len(getattr(auth_manager, 'token_cache', {}) or {})
    return jsonify({
        'ok': True,
        'service': 'M365 Copilot Unified Gateway',
        'base_url': f'http://{request.host}/v1',
        'port': config.SERVER_PORT,
        'accounts': {'count': account_count},
        'token': {'cached_count': cached_tokens},
        'rate_limiter': {
            'enabled': _rate_limiter.enabled,
            'rpm': RATE_LIMIT_RPM,
            'burst': RATE_LIMIT_BURST,
        },
        'models': [m['id'] for m in MODEL_LIST],
        'api_key_policy': 'any value accepted',
    })

@app.route('/v1/models', methods=['GET'])
def list_models():
    return jsonify({'object': 'list', 'data': MODEL_LIST})


@app.route('/v1/accounts', methods=['GET'])
def list_accounts():
    return jsonify({'accounts': auth_manager.get_all_status()})


@app.route('/status', methods=['GET'])
def status():
    return jsonify(auth_manager.get_daily_stats())


@app.route('/refresh', methods=['POST'])
def refresh():
    data = request.get_json(silent=True) or {}
    username = data.get('username')
    try:
        ok = auth_manager.refresh_token(username)
    except Exception as exc:
        message = f'Token refresh exception: {exc}'
        print(f'[Refresh] {message}')
        return jsonify({'error': message}), 500
    if ok:
        return jsonify({'message': 'Token refresh succeeded'})
    if username and not any(a.get('username') == username for a in getattr(auth_manager, 'accounts', [])):
        return jsonify({'error': f'Account not found: {username}'}), 404
    return jsonify({'error': 'Token refresh failed. Check account password, TOTP, network, and browser availability.'}), 500


def _tool_names(tools) -> set:
    return {t.get('function', {}).get('name') for t in (tools or []) if t.get('type') == 'function'} - {None}


def _find_tool_by_canonical(tools, canonical_name: str) -> str | None:
    for name in _tool_names(tools):
        if _canonical_tool_name(name) == canonical_name:
            return name
    return None


def _terminal_arguments_for_tool(tool_name: str, command: str, timeout: int = 30) -> dict:
    if tool_name == 'exec_command':
        return {'cmd': command, 'timeout_ms': timeout * 1000}
    if tool_name == 'shell_command':
        return {'command': command}
    return {'command': command, 'timeout': timeout}


def _tool_log_details(tools) -> dict:
    names = sorted(_tool_names(tools))
    categories = sorted({_canonical_tool_name(name) for name in names if name})
    return {
        'tools_count': len(names),
        'tools': names[:20],
        'tool_categories': categories[:20],
    }


def _request_log_extra(api: str, tools=None, stream: bool = False, has_tool_output: bool = False) -> dict:
    extra = {
        'api': api,
        'client_ip': _client_ip(),
        'method': request.method,
        'path': request.path,
        'stream': bool(stream),
        'has_tool_output': bool(has_tool_output),
        'user_agent': (request.headers.get('User-Agent') or '')[:120],
    }
    extra.update(_tool_log_details(tools))
    return extra


def _simple_local_tool_call(user_message: str, tools):
    terminal_tool = _find_tool_by_canonical(tools, 'terminal')
    if not terminal_tool:
        return None
    msg = (user_message or '').lower()
    compact = re.sub(r'\s+', '', msg)
    if ('c盘' in compact or 'cdrive' in compact or 'c:' in compact) and any(x in compact for x in ('占用', '空间', 'usage', 'space', 'free', '剩余')):
        return _make_tool_call(terminal_tool, _terminal_arguments_for_tool(terminal_tool, 'df -h /mnt/c', 30))
    return None
def _chat_impl(data: Dict[str, Any]):
    messages = data.get('messages', [])
    stream = data.get('stream', False)
    model = data.get('model', 'copilot-auto')
    tools = _normalize_tools(data.get('tools'))
    tool_choice = data.get('tool_choice')
    log_extra = data.get('_log_extra') or _request_log_extra('chat.completions', tools, stream)
    if tools:
        try:
            names = [t.get('function', {}).get('name') for t in tools if t.get('type') == 'function']
            print('[Tools] client provided: ' + ', '.join([n for n in names if n]))
        except Exception:
            pass

    limited = _rate_limited_response()
    if limited is not None:
        return limited

    if not messages:
        return jsonify({'error': {'message': 'messages cannot be empty'}}), 400

    system_msgs = []
    user_message = ''
    history = []

    for msg in messages:
        role = msg.get('role', '')
        content = _normalize_content(msg.get('content', ''))
        if role == 'system':
            system_msgs.append(content)
        elif role == 'user':
            if history or user_message:
                history.append({'role': 'user', 'content': user_message if user_message else content})
            user_message = content
        elif role == 'assistant':
            tc = msg.get('tool_calls')
            if tc:
                history.append({'role': 'assistant', 'content': f'[Tool Call]: {json.dumps(tc, ensure_ascii=False)}'})
            else:
                history.append({'role': 'assistant', 'content': content})
        elif role == 'tool':
            history.append({'role': 'tool', 'content': content})

    if not user_message:
        return jsonify({'error': {'message': 'missing user message'}}), 400

    system_prompt = '\n\n'.join(system_msgs) if system_msgs else None
    tools_prompt = build_tools_system_prompt(tools, tool_choice) if tools else None

    if False and tools and not any('[Tool Call]' in (m.get('content', '') or '') for m in history if m.get('role') == 'assistant'):
        first_tool = 'shell_command'
        history.insert(0, {'role': 'user', 'content': 'list the files in current directory'})
        history.insert(1, {'role': 'assistant', 'content': f'[Tool Call]: [{{"id": "call_example", "type": "function", "function": {{"name": "{first_tool}", "arguments": "{{\\"command\\": \\\"ls\\\"}}"}}}}]'} )
        history.insert(2, {'role': 'tool', 'content': 'file1.txt\nfile2.py\nREADME.md'})
        history.insert(3, {'role': 'assistant', 'content': 'The current directory contains: file1.txt, file2.py, README.md'})

    has_tool_history = any(
        m.get('role') == 'tool' or '[Tool Call]' in (m.get('content', '') or '')
        for m in history
    )
    simple_tool_call = None if has_tool_history else _simple_local_tool_call(user_message, tools)
    if simple_tool_call:
        completion_id = f'chatcmpl-{uuid.uuid4().hex[:12]}'
        timestamp = int(time.time())
        input_tk = _estimate_tokens(user_message)
        req_logger.log_request(model, None, 0, True, request_summary='simple_tool_call: ' + simple_tool_call['function']['name'], extra=log_extra)
        return jsonify({
            'id': completion_id,
            'object': 'chat.completion',
            'created': timestamp,
            'model': model,
            'choices': [{
                'index': 0,
                'message': {'role': 'assistant', 'content': None, 'tool_calls': [simple_tool_call]},
                'finish_reason': 'tool_calls',
            }],
            'usage': {'prompt_tokens': input_tk, 'completion_tokens': 0, 'total_tokens': input_tk},
        })
    start_time = time.time()
    token, used_account = auth_manager.get_token()
    if not token:
        return jsonify({'error': {'message': 'no available token; please add an account and login in Dashboard'}}), 503

    client = _build_client(token, used_account)
    completion_id = f'chatcmpl-{uuid.uuid4().hex[:12]}'
    timestamp = int(time.time())
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = {'content': '', 'error': None}
    try:
        for attempt in range(2):
            try:
                result = loop.run_until_complete(
                    client.chat(user_message, system_prompt=system_prompt,
                                history=history, model=model,
                                tools_prompt=tools_prompt)
                )
            except Exception as e:
                result = {'error': str(e)}

            if result.get('error') and _is_401_error(result['error']) and attempt == 0:
                new_token, new_account = auth_manager.refresh_after_401(used_account)
                if new_token and new_account:
                    used_account = new_account
                    client = _build_client(new_token, new_account)
                    continue
                break
            break
    finally:
        loop.close()

    if result.get('error') and not result.get('content'):
        elapsed = (time.time() - start_time) * 1000
        req_logger.log_request(model, used_account, elapsed, False, error=result['error'], extra=log_extra)
        return jsonify({'error': {'message': result['error']}}), 500

    content = result['content']
    elapsed = (time.time() - start_time) * 1000
    tool_calls = extract_tool_calls(content, tools)
    elapsed_for_log = (time.time() - start_time) * 1000
    input_tk = _estimate_tokens(user_message)
    output_tk = _estimate_tokens(content)
    auth_manager.record_token_usage(used_account, input_tk, output_tk)

    if tool_calls:
        req_logger.log_request(model, used_account, elapsed_for_log, True,
                               request_summary=f'tool_call: {tool_calls[0]["function"]["name"]}',
                               extra=log_extra)
        return jsonify({
            'id': completion_id,
            'object': 'chat.completion',
            'created': timestamp,
            'model': model,
            'choices': [{
                'index': 0,
                'message': {
                    'role': 'assistant',
                    'content': None,
                    'tool_calls': tool_calls,
                },
                'finish_reason': 'tool_calls',
            }],
            'usage': {
                'prompt_tokens': input_tk,
                'completion_tokens': output_tk,
                'total_tokens': input_tk + output_tk,
            },
        })

    req_logger.log_request(model, used_account, elapsed_for_log, True,
                           request_summary=f'{content[:80]}...' if len(content) > 80 else content,
                           extra=log_extra)
    return jsonify({
        'id': completion_id,
        'object': 'chat.completion',
        'created': timestamp,
        'model': model,
        'conversation_id': result.get('conversation_id'),
        'choices': [{
            'index': 0,
            'message': {'role': 'assistant', 'content': content},
            'finish_reason': 'stop',
        }],
        'usage': {
            'prompt_tokens': input_tk,
            'completion_tokens': output_tk,
            'total_tokens': input_tk + output_tk,
        },
    })


@app.route('/v1/chat/completions', methods=['POST'])
def chat_completions():
    data = request.get_json(silent=True) or {}
    stream = data.get('stream', False)
    if stream:
        result = _chat_impl({**data, 'stream': False})
        if isinstance(result, tuple):
            return result
        payload = result.get_json()
        return Response(_stream_chat_proxy(payload), mimetype='text/event-stream', headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        })
    return _chat_impl(data)


def _stream_chat_proxy(payload: Dict[str, Any]):
    choice = payload.get('choices', [{}])[0]
    message = choice.get('message', {}) or {}
    content = message.get('content') or ''
    tool_calls = message.get('tool_calls') or []
    cid = payload.get('id', f'chatcmpl-{uuid.uuid4().hex[:12]}')
    created = payload.get('created', int(time.time()))
    model = payload.get('model')
    yield f"data: {json.dumps({'id': cid, 'object': 'chat.completion.chunk', 'created': created, 'model': model, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"
    if tool_calls:
        for idx, call in enumerate(tool_calls):
            fn = call.get('function', {}) or {}
            call_id = call.get('id') or f'call_{uuid.uuid4().hex[:12]}'
            name_delta = {
                'tool_calls': [{
                    'index': idx,
                    'id': call_id,
                    'type': 'function',
                    'function': {'name': fn.get('name'), 'arguments': ''},
                }]
            }
            args_delta = {
                'tool_calls': [{
                    'index': idx,
                    'function': {'arguments': fn.get('arguments') or '{}'},
                }]
            }
            yield f"data: {json.dumps({'id': cid, 'object': 'chat.completion.chunk', 'created': created, 'model': model, 'choices': [{'index': 0, 'delta': name_delta, 'finish_reason': None}]}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'id': cid, 'object': 'chat.completion.chunk', 'created': created, 'model': model, 'choices': [{'index': 0, 'delta': args_delta, 'finish_reason': None}]}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'id': cid, 'object': 'chat.completion.chunk', 'created': created, 'model': model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls'}]})}\n\n"
        yield 'data: [DONE]\n\n'
        return
    yield f"data: {json.dumps({'id': cid, 'object': 'chat.completion.chunk', 'created': created, 'model': model, 'choices': [{'index': 0, 'delta': {'content': content}, 'finish_reason': None}]})}\n\n"
    yield f"data: {json.dumps({'id': cid, 'object': 'chat.completion.chunk', 'created': created, 'model': model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
    yield 'data: [DONE]\n\n'

def _responses_payload_from_chat(payload: Dict[str, Any], model: str) -> Dict[str, Any]:
    message = payload.get('choices', [{}])[0].get('message', {}) or {}
    tool_calls = message.get('tool_calls') or []
    response_id = f'resp-{uuid.uuid4().hex}'
    created = int(time.time())
    if tool_calls:
        output = []
        for call in tool_calls:
            fn = call.get('function', {}) or {}
            call_id = call.get('id') or f'call_{uuid.uuid4().hex[:12]}'
            output.append({
                'type': 'function_call',
                'id': call_id,
                'call_id': call_id,
                'name': fn.get('name'),
                'arguments': fn.get('arguments') or '{}',
                'status': 'completed',
            })
        return {
            'id': response_id,
            'object': 'response',
            'created_at': created,
            'model': model,
            'output': output,
            'status': 'completed',
        }
    content = message.get('content') or ''
    return {
        'id': response_id,
        'object': 'response',
        'created_at': created,
        'model': model,
        'output': [{
            'type': 'message',
            'id': f'msg_{uuid.uuid4().hex[:12]}',
            'role': 'assistant',
            'status': 'completed',
            'content': [{'type': 'output_text', 'text': content}],
        }],
        'status': 'completed',
    }


def _sse_event(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _stream_responses_payload(payload: Dict[str, Any]):
    response_id = payload.get('id') or f'resp-{uuid.uuid4().hex}'
    model = payload.get('model')
    created = payload.get('created_at') or int(time.time())
    sequence = 0

    def emit(event: str, data: Dict[str, Any]):
        nonlocal sequence
        data.setdefault('sequence_number', sequence)
        sequence += 1
        return _sse_event(event, data)

    base_response = {
        'id': response_id,
        'object': 'response',
        'created_at': created,
        'model': model,
        'status': 'in_progress',
        'output': [],
    }
    yield emit('response.created', {'type': 'response.created', 'response': base_response})
    yield emit('response.in_progress', {'type': 'response.in_progress', 'response': base_response})

    for output_index, item in enumerate(payload.get('output') or []):
        item = dict(item)
        item.setdefault('id', f'item_{uuid.uuid4().hex[:12]}')
        item_id = item['id']
        yield emit('response.output_item.added', {
            'type': 'response.output_item.added',
            'output_index': output_index,
            'item': item,
        })
        if item.get('type') == 'message':
            for content_index, part in enumerate(item.get('content') or []):
                part = dict(part)
                text = part.get('text') or ''
                yield emit('response.content_part.added', {
                    'type': 'response.content_part.added',
                    'item_id': item_id,
                    'output_index': output_index,
                    'content_index': content_index,
                    'part': part,
                })
                if text:
                    yield emit('response.output_text.delta', {
                        'type': 'response.output_text.delta',
                        'item_id': item_id,
                        'output_index': output_index,
                        'content_index': content_index,
                        'delta': text,
                        'logprobs': [],
                    })
                yield emit('response.output_text.done', {
                    'type': 'response.output_text.done',
                    'item_id': item_id,
                    'output_index': output_index,
                    'content_index': content_index,
                    'text': text,
                })
                yield emit('response.content_part.done', {
                    'type': 'response.content_part.done',
                    'item_id': item_id,
                    'output_index': output_index,
                    'content_index': content_index,
                    'part': part,
                })
        elif item.get('type') == 'function_call':
            yield emit('response.function_call_arguments.delta', {
                'type': 'response.function_call_arguments.delta',
                'item_id': item_id,
                'output_index': output_index,
                'delta': item.get('arguments') or '{}',
            })
            yield emit('response.function_call_arguments.done', {
                'type': 'response.function_call_arguments.done',
                'item_id': item_id,
                'output_index': output_index,
                'arguments': item.get('arguments') or '{}',
            })
        item['status'] = 'completed'
        yield emit('response.output_item.done', {
            'type': 'response.output_item.done',
            'output_index': output_index,
            'item': item,
        })

    completed = dict(base_response)
    completed['status'] = 'completed'
    completed['output'] = payload.get('output') or []
    yield emit('response.completed', {'type': 'response.completed', 'response': completed})
    yield 'data: [DONE]\n\n'


@app.route('/v1/responses', methods=['POST'])
def responses_api():
    data = request.get_json(silent=True) or {}
    model = data.get('model', 'copilot-auto')
    input_value = data.get('input', '')
    messages = _responses_input_to_messages(input_value)
    has_tool_output = _responses_has_tool_output(input_value)
    response_tools = None if has_tool_output else _normalize_tools(data.get('tools'))
    log_tools = _normalize_tools(data.get('tools'))
    instructions = _normalize_content(data.get('instructions'))
    if instructions:
        messages = [{'role': 'system', 'content': instructions}] + messages
    result = _chat_impl({
        'model': model,
        'messages': messages,
        'stream': False,
        'tools': response_tools,
        'tool_choice': data.get('tool_choice'),
        '_log_extra': _request_log_extra('responses', log_tools, data.get('stream', False), has_tool_output),
    })
    if isinstance(result, tuple):
        return result
    response_payload = _responses_payload_from_chat(result.get_json(), model)
    if data.get('stream'):
        return Response(_stream_responses_payload(response_payload), mimetype='text/event-stream', headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        })
    return jsonify(response_payload)


@app.route('/v1/messages', methods=['POST'])
def anthropic_messages():
    data = request.get_json(silent=True) or {}
    model = data.get('model', 'copilot-auto')
    messages = data.get('messages') or []
    if data.get('system'):
        messages = [{'role': 'system', 'content': data['system']}] + messages
    result = _chat_impl({
        'model': model,
        'messages': messages,
        'stream': False,
        '_log_extra': _request_log_extra('messages', None, False, False),
    })
    if isinstance(result, tuple):
        return result
    payload = result.get_json()
    content = payload.get('choices', [{}])[0].get('message', {}).get('content') or ''
    return jsonify({
        'id': f'msg_{uuid.uuid4().hex}',
        'type': 'message',
        'role': 'assistant',
        'model': model,
        'content': [{'type': 'text', 'text': content}],
        'stop_reason': 'end_turn',
        'usage': {'input_tokens': 0, 'output_tokens': 0},
    })


if __name__ == '__main__':
    app.run(host=config.SERVER_HOST, port=config.SERVER_PORT, debug=False, threaded=True)


















