"""
M365 Copilot 鍙嶄唬鏈嶅姟 v6
鍩轰簬 MSAL + Playwright Chromium 鑷姩鐧诲綍
鏀寔 tools / function calling + 7 绉嶆ā鍨?
"""

import json
import os
import re
import time
import uuid
import asyncio
import queue
import threading
from flask import Flask, request, jsonify, Response
from copilot_client import SubstrateClient
from auth import get_account_manager
from request_logger import get_request_logger
import config
from hybrid.ratelimit import TokenBucket

def _is_401_error(error_str: str) -> bool:
    """妫€娴嬫槸鍚︽槸 WebSocket 401 閿欒"""
    return '401' in error_str and ('rejected' in error_str or 'Unauthorized' in error_str.lower())


app = Flask(__name__)
auth_manager = get_account_manager()
req_logger = get_request_logger()

_rate_rpm = float(os.getenv('M365_RATE_LIMIT_RPM', '30'))
_rate_burst = int(os.getenv('M365_RATE_LIMIT_BURST', '5'))
_rate_limiter = TokenBucket(_rate_rpm, _rate_burst)


def _rate_limited_response():
    allowed, wait = _rate_limiter.try_acquire()
    if allowed:
        return None
    secs = max(1, round(wait))
    return jsonify({
        'error': {
            'message': f'Rate limit exceeded ({_rate_rpm:g} req/min). Retry in {secs}s.',
            'type': 'rate_limit_error',
            'code': 'rate_limit_exceeded',
        }
    }), 429, {'Retry-After': str(secs)}


def _estimate_tokens(text: str) -> int:
    """浼扮畻 token 鏁帮紙涓嫳鏂囨贩鍚堢害 3 瀛楃/token锛?""
    return len(text) // 3 if text else 0


# ======================================================================
# Tools 澶勭悊
# ======================================================================

# 鏈?tools 鏃惰嚜鍔ㄥ崌绾у埌鎺ㄧ悊妯″瀷锛堥潪鎺ㄧ悊妯″紡涓嶅鑱槑锛屾棤娉曢伒寰伐鍏疯皟鐢ㄦ寚浠わ級
TOOL_MODEL_UPGRADE = {
    'copilot-auto': 'copilot-thinking',
    'copilot-quick': 'copilot-thinking',
    'gpt-5.5': 'gpt-5.5-thinking',
    'gpt-5.4': 'gpt-5.5-thinking',
    'gpt-5.4-mini': 'gpt-5.5-thinking',
    'gpt-5.2': 'gpt-5.2-thinking',
    'gpt-4o': 'copilot-thinking',
}

def _summarize_param_schema(pname: str, pinfo: dict, required: set) -> str:
    # Summarize a tool parameter schema in one line.
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
    # Generate a safe example value for a schema.
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
        return 120000 if pname == 'timeout_ms' else 0
    if ptype == 'number':
        return 0
    if ptype == 'array':
        return []
    if ptype == 'object':
        return {}
    return ''


def _build_example_tool_call(tools: list) -> str:
    """鐢ㄧ湡瀹炰紶鍏ョ殑绗竴涓伐鍏?schema 鐢熸垚绀轰緥锛岃€屼笉鏄浐瀹?shell_command銆?""
    for tool in tools or []:
        if tool.get('type') != 'function':
            continue
        fn = tool.get('function', {})
        name = fn.get('name', 'tool_name')
        params = fn.get('parameters', {}) or {}
        props = params.get('properties', {}) or {}
        required = params.get('required', []) or []
        args = {}
        # 浼樺厛濉?required锛涘鏋滄病鏈?required锛屽氨缁?command 杩欑被鍏抽敭鍙傛暟涓€涓ず渚?
        names = list(required)
        if not names:
            for candidate in ('command', 'cmd', 'path', 'query'):
                if candidate in props:
                    names.append(candidate)
                    break
        for pname in names:
            if pname in props:
                args[pname] = _example_value_for_schema(pname, props[pname])
    # Generate a safe example value for a schema.
    return json.dumps({'tool_call': {'name': 'tool_name', 'arguments': {}}}, ensure_ascii=False)


def build_tools_system_prompt(tools: list, tool_choice) -> str:
    """鎶?OpenAI tools 瀹氫箟杞负绮剧畝 system prompt 娉ㄥ叆缁?Copilot銆?""
    tool_lines = []
    for tool in tools or []:
        if tool.get('type') == 'function':
            fn = tool.get('function', {})
            name = fn.get('name', '')
            desc = (fn.get('description', '') or '').strip().replace('\n', ' ')
            params = fn.get('parameters', {}) or {}
            props = params.get('properties', {}) or {}
            required = set(params.get('required', []) or [])
            param_parts = []
            for pname, pinfo in props.items():
                param_parts.append(_summarize_param_schema(pname, pinfo or {}, required))
    # Summarize a tool parameter schema in one line.
            desc_short = desc[:160] + '...' if len(desc) > 160 else desc
            tool_lines.append(f"- {name}: {desc_short}\n  params: {param_str}")

    tools_list = '\n'.join(tool_lines)
    example_call = _build_example_tool_call(tools)

    prompt = f"""SYSTEM CONTEXT: You are the AI backend for a coding agent CLI tool. The user interacts through a terminal. You do NOT have your own execution environment 鈥?the caller executes your tool calls on the user's actual machine. You MUST use tools when the user asks you to do anything.

AVAILABLE TOOLS:
{tools_list}

TOOL CALLING RULES (CRITICAL - FOLLOW EXACTLY):
- When the user asks you to do ANYTHING (run commands, read/write files, search, etc.), output a tool_call JSON.
- Format: {{"tool_call": {{"name": "tool_name", "arguments": {{"param": "value"}}}}}}
- Use ONLY the tool names and parameter names listed above.
- If a parameter lists enum=..., its value MUST be one of those enum values exactly.
- Do NOT copy environment permission labels such as danger-full-access/unrestricted into tool arguments.
- For sandbox_permissions, if available, ordinary read/list/run operations should use use_default unless the caller explicitly requests escalation.
- Do NOT say "I can't access your computer" or "I'm in a sandbox" 鈥?the caller WILL execute it on the user's machine.
- Do NOT run the command yourself. Do NOT simulate execution. Do NOT provide fake results.
- Do NOT wrap the JSON in code blocks. Output raw JSON only.
- Do NOT add any text before or after the JSON.
- If no tool is needed (simple chat/question), respond normally.

Example tool call using the actual provided schema:
{example_call}

Example - user says "1+1":
1+1=2
"""
    return prompt

def extract_tool_calls(text: str, tools: list) -> list | None:
    """浠?Copilot 鍥炲鏂囨湰涓彁鍙?tool_calls"""
    if not tools:
        return None

    allowed_names = set()
    tool_by_name = {}
    for tool in tools:
        if tool.get('type') == 'function':
            name = tool['function'].get('name', '')
            allowed_names.add(name)
            tool_by_name[name] = tool

    # 鍏堝幓鎺?markdown 浠ｇ爜鍧楀寘瑁?
    cleaned = text.strip()
    # 鍖归厤 ```json ... ``` 鎴?``` ... ```
    code_block = re.search(r'```(?:json)?\s*\n?(\{.*?\})\s*\n?```', cleaned, re.DOTALL)
    if code_block:
        cleaned = code_block.group(1)
        print(f"[Tools] Extracted JSON from code block")

    for i, ch in enumerate(cleaned):
        if ch != '{':
            continue
        depth = 0
        for j in range(i, len(cleaned)):
            if cleaned[j] == '{':
                depth += 1
            elif cleaned[j] == '}':
                depth -= 1
                if depth == 0:
                    json_str = cleaned[i:j+1]
                    try:
                        obj = json.loads(json_str)
                    except (json.JSONDecodeError, ValueError):
                        break

                    print(f"[Tools] Found JSON object with keys: {list(obj.keys())}")

                    # Pattern 1: {"tool_call": {"name": ..., "arguments": ...}}
                    for key in ('tool_call', 'function_call', 'tool'):
                        if key in obj and isinstance(obj[key], dict):
                            inner = obj[key]
                            name = inner.get('name', '')
                            args = inner.get('arguments', {})
                            if name in allowed_names:
                                print(f"[Tools] Matched via '{key}': {name}")
                                return [_make_tool_call(name, _sanitize_tool_arguments(name, args, tool_by_name))]

                    # Pattern 2: {"name": "tool_name", "arguments": {...}}
                    name = obj.get('name', '')
                    args = obj.get('arguments')
                    if name in allowed_names and isinstance(args, dict):
                        print(f"[Tools] Matched directly: {name}")
                        return [_make_tool_call(name, _sanitize_tool_arguments(name, args, tool_by_name))]

                    # Pattern 3: {"name": "tool_name", "parameters": {...}}
                    params = obj.get('parameters')
                    if name in allowed_names and isinstance(params, dict):
                        print(f"[Tools] Matched via 'parameters': {name}")
                        return [_make_tool_call(name, _sanitize_tool_arguments(name, params, tool_by_name))]

                    break

    # 鍥為€€鏂规锛氭彁鍙栦唬鐮佸潡骞惰浆涓?shell_command 宸ュ叿璋冪敤
    if 'shell_command' in allowed_names:
        # 鍖归厤 ```powershell / ```bash / ```cmd / ```shell 浠ｇ爜鍧?
        code_match = re.search(r'```(?:powershell|bash|cmd|shell|sh)\s*\n(.*?)```', text, re.DOTALL)
        if code_match:
            cmd = code_match.group(1).strip()
            if cmd and len(cmd) > 3:
                print(f"[Tools] Fallback: extracted code block as shell_command ({len(cmd)} chars)")
                return [_make_tool_call('shell_command', _sanitize_tool_arguments('shell_command', {'command': cmd}, tool_by_name))]

    return None



def _sanitize_tool_arguments(name: str, arguments: dict, tool_by_name: dict) -> dict:
    """鎸夊伐鍏?schema 娓呮礂鍙傛暟锛屼慨澶?鍓旈櫎浼氬鑷村灞傚伐鍏疯В鏋愬け璐ョ殑鏋氫妇鍊笺€?""
    if not isinstance(arguments, dict):
        return {}

    tool = tool_by_name.get(name) or {}
    fn = tool.get('function', {}) or {}
    schema = fn.get('parameters', {}) or {}
    props = schema.get('properties', {}) or {}
    required = set(schema.get('required', []) or [])

    # 娌℃湁 schema 鏃朵繚鎸佸師鏍?
    if not props:
        return dict(arguments)

    cleaned = {}
    for key, value in arguments.items():
        if key not in props:
            # 涓嶈璇嗙殑鍙傛暟瀹规槗璁╀弗鏍煎伐鍏疯В鏋愬け璐ワ紝鐩存帴涓㈠純
            print(f"[Tools] Dropped unknown argument for {name}: {key}")
            continue

        pinfo = props.get(key, {}) or {}
        enum_vals = pinfo.get('enum')
        if enum_vals and value not in enum_vals:
            fixed = None
            # 甯歌鍧戯細鎶婄幆澧冩潈闄?danger-full-access 褰撴垚 sandbox_permissions 鏋氫妇
            if key == 'sandbox_permissions':
                mapping = {
                    'danger-full-access': 'use_default',
                    'unrestricted': 'use_default',
                    'full-access': 'use_default',
                    'full_access': 'use_default',
                    'default': 'use_default',
                }
                fixed = mapping.get(str(value), None)
                if fixed not in enum_vals:
                    fixed = None
                if fixed is None and 'use_default' in enum_vals:
                    fixed = 'use_default'
            if fixed is None and 'default' in pinfo and pinfo['default'] in enum_vals:
                fixed = pinfo['default']
            if fixed is None and key not in required:
                print(f"[Tools] Dropped invalid enum argument for {name}.{key}: {value!r}; allowed={enum_vals}")
                continue
            if fixed is not None:
                print(f"[Tools] Fixed invalid enum argument for {name}.{key}: {value!r} -> {fixed!r}")
                value = fixed
            else:
                print(f"[Tools] Kept invalid required enum for {name}.{key}: {value!r}; allowed={enum_vals}")

        cleaned[key] = value

    # 缁欑己澶辩殑 required 鍙傛暟濉粯璁ゅ€硷紙濡傛灉 schema 鎻愪緵浜嗭級
    for key in required:
        if key not in cleaned and key in props and 'default' in props[key]:
            cleaned[key] = props[key]['default']
            print(f"[Tools] Filled default required argument for {name}.{key}: {cleaned[key]!r}")

    return cleaned

def _make_tool_call(name: str, arguments: dict) -> dict:
    return {
        'id': f'call_{uuid.uuid4().hex[:12]}',
        'type': 'function',
        'function': {
            'name': name,
            'arguments': json.dumps(arguments, ensure_ascii=False),
        },
    }


# ======================================================================
# OpenAI 鍏煎鎺ュ彛
# ======================================================================

@app.route('/v1/chat/completions', methods=['POST'])
def chat_completions():
    data = request.get_json(silent=True) or {}
    messages = data.get('messages', [])
    stream = data.get('stream', False)
    model = data.get('model', 'copilot-auto')
    tools = data.get('tools')
    tool_choice = data.get('tool_choice')

        print(f"\n[Request] model={model}, stream={stream}, tools={len(tools) if tools else 0}")

    limited = _rate_limited_response()
    if limited is not None:
        return limited
    # 鏈?tools 鏃惰嚜鍔ㄥ崌绾у埌鎺ㄧ悊妯″瀷
    if tools:
        upgraded = TOOL_MODEL_UPGRADE.get(model)
        if upgraded:
            print(f"[Tools] 鑷姩鍗囩骇妯″瀷: {model} 鈫?{upgraded}")
            model = upgraded
    print(f"[Request] messages ({len(messages)}):")
    for m in messages:
        role = m.get('role', '?')
        content = (m.get('content', '') or '')[:100]
        tc = m.get('tool_calls')
        if tc:
            print(f"  [{role}] tool_calls: {json.dumps(tc, ensure_ascii=False)[:200]}")
        else:
            print(f"  [{role}] {content}")
    if tools:
        tool_names = [t.get('function', {}).get('name', '?') for t in tools if t.get('type') == 'function']
        print(f"[Request] available tools: {tool_names}")

    if not messages:
        return jsonify({'error': {'message': 'messages 涓嶈兘涓虹┖'}}), 400

    # 鍒嗙娑堟伅
    system_msgs = []
    user_message = ''
    history = []

    for msg in messages:
        role = msg.get('role', '')
        content = msg.get('content', '') or ''
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
        return jsonify({'error': {'message': '娌℃湁鐢ㄦ埛娑堟伅'}}), 400

    system_prompt = '\n\n'.join(system_msgs) if system_msgs else None
    tools_prompt = build_tools_system_prompt(tools, tool_choice) if tools else None

    # 鏈?tools 浣嗗巻鍙蹭腑娌℃湁宸ュ叿璋冪敤绀轰緥鏃讹紝娉ㄥ叆涓€涓ず渚嬭妯″瀷瀛︿細鏍煎紡
    if tools and not any('[Tool Call]' in (m.get('content', '') or '') for m in history if m.get('role') == 'assistant'):
        # 鎵剧涓€涓彲鐢ㄧ殑宸ュ叿鍚?
        first_tool = tool_names[0] if tool_names else 'shell_command'
        history.insert(0, {'role': 'user', 'content': 'list the files in current directory'})
        history.insert(1, {'role': 'assistant', 'content': f'[Tool Call]: [{{"id": "call_example", "type": "function", "function": {{"name": "{first_tool}", "arguments": "{{\"command\": \"ls\"}}"}}}}]'})
        history.insert(2, {'role': 'tool', 'content': 'file1.txt\nfile2.py\nREADME.md'})
        history.insert(3, {'role': 'assistant', 'content': 'The current directory contains: file1.txt, file2.py, README.md'})
        print(f"[Tools] Injected tool call example into history (tool: {first_tool})")

    # 鑾峰彇 token
    start_time = time.time()
    token, used_account = auth_manager.get_token()
    if not token:
        return jsonify({
            'error': {'message': '娌℃湁鍙敤鐨?token锛岃鍦?Dashboard 涓坊鍔犺处鍙峰苟鐧诲綍'}
        }), 503

    print(f"[Request] 浣跨敤璐﹀彿: {used_account}")

    client = SubstrateClient(token, used_account)
    completion_id = f'chatcmpl-{uuid.uuid4().hex[:12]}'
    timestamp = int(time.time())

    if stream:
        return Response(
            _stream_chat(client, user_message, system_prompt, history,
                         completion_id, timestamp, model, tools,
                         used_account, start_time, tools_prompt),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
                'Connection': 'keep-alive',
            },
        )

    # 闈炴祦寮忥紙甯?401 鑷姩閲嶈瘯锛?
    result = None
    loop = asyncio.new_event_loop()
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

            # 妫€娴?401 閿欒锛岃嚜鍔ㄥ埛鏂?token 骞堕噸璇?
            if result.get('error') and _is_401_error(result['error']) and attempt == 0:
                print(f"[Request] WebSocket 401 妫€娴嬪埌锛屼娇 {used_account} token 澶辨晥骞堕噸璇?..")
                new_token, new_account = auth_manager.refresh_after_401(used_account)
                if new_token and new_account:
                    used_account = new_account
                    client = SubstrateClient(new_token, new_account)
                    print(f"[Request] 閲嶈瘯浣跨敤璐﹀彿: {new_account}")
                    continue
                else:
                    print(f"[Request] 鏃犳硶鑾峰彇鏂?token锛屾斁寮冮噸璇?)
                    break
            break
    finally:
        loop.close()

    if result.get('error') and not result.get('content'):
        elapsed = (time.time() - start_time) * 1000
        req_logger.log_request(model, used_account, elapsed, False, error=result['error'])
        return jsonify({'error': {'message': result['error']}}), 500

    content = result['content']
    elapsed = (time.time() - start_time) * 1000

    # 妫€鏌?tool calls
    tool_calls = extract_tool_calls(content, tools)

    # 璁板綍璇锋眰鏃ュ織
    elapsed_for_log = (time.time() - start_time) * 1000
    input_tk = _estimate_tokens(user_message)
    output_tk = _estimate_tokens(content)
    auth_manager.record_token_usage(used_account, input_tk, output_tk)
    if tool_calls:
        print(f"[Response] tool_calls detected: {json.dumps(tool_calls, ensure_ascii=False)[:300]}")
        req_logger.log_request(model, used_account, elapsed_for_log, True,
                               request_summary=f'tool_call: {tool_calls[0]["function"]["name"]}')
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
                'prompt_tokens': _estimate_tokens(user_message),
                'completion_tokens': _estimate_tokens(content),
                'total_tokens': _estimate_tokens(user_message) + _estimate_tokens(content),
            },
        })

    print(f"[Response] text reply ({len(content)} chars): {content[:100]}...")
    req_logger.log_request(model, used_account, elapsed_for_log, True,
                           request_summary=f'{content[:80]}...' if len(content) > 80 else content)
    return jsonify({
        'id': completion_id,
        'object': 'chat.completion',
        'created': timestamp,
        'model': model,
        'choices': [{
            'index': 0,
            'message': {
                'role': 'assistant',
                'content': content,
            },
            'finish_reason': 'stop',
        }],
        'usage': {
            'prompt_tokens': _estimate_tokens(user_message),
            'completion_tokens': _estimate_tokens(content),
            'total_tokens': _estimate_tokens(user_message) + _estimate_tokens(content),
        },
    })


def _stream_chat(client, user_message, system_prompt, history,
                 completion_id, timestamp, model, tools,
                 used_account=None, start_time=None, tools_prompt=None):
    """娴佸紡 SSE 鍝嶅簲锛堟敮鎸?tools锛?""
    chunk_queue = queue.Queue()
    output_buf = []

    def run_chat():
        nonlocal client, used_account
        loop = asyncio.new_event_loop()

        def on_chunk(text):
            chunk_queue.put(('chunk', text))

        try:
            for attempt in range(2):
                try:
                    result = loop.run_until_complete(
                        client.chat(user_message, system_prompt=system_prompt,
                                   history=history, stream_callback=on_chunk,
                                   model=model, tools_prompt=tools_prompt)
                    )
                    # 妫€娴?401 閿欒锛岃嚜鍔ㄩ噸璇?
                    if result.get('error') and _is_401_error(result['error']) and attempt == 0:
                        print(f"[Stream] WebSocket 401 妫€娴嬪埌锛屼娇 {used_account} token 澶辨晥骞堕噸璇?..")
                        new_token, new_account = auth_manager.refresh_after_401(used_account)
                        if new_token and new_account:
                            used_account = new_account
                            client = SubstrateClient(new_token, new_account)
                            print(f"[Stream] 閲嶈瘯浣跨敤璐﹀彿: {new_account}")
                            continue
                    chunk_queue.put(('done', result))
                    break
                except Exception as e:
                    err_str = str(e)
                    if _is_401_error(err_str) and attempt == 0:
                        print(f"[Stream] WebSocket 401 寮傚父锛屼娇 {used_account} token 澶辨晥骞堕噸璇?..")
                        new_token, new_account = auth_manager.refresh_after_401(used_account)
                        if new_token and new_account:
                            used_account = new_account
                            client = SubstrateClient(new_token, new_account)
                            print(f"[Stream] 閲嶈瘯浣跨敤璐﹀彿: {new_account}")
                            continue
                    chunk_queue.put(('error', err_str))
                    break
        finally:
            loop.close()

    t = threading.Thread(target=run_chat, daemon=True)
    t.start()

    if tools:
        buffered_chunks = []
        while True:
            try:
                kind, item = chunk_queue.get(timeout=120)
            except queue.Empty:
                break

            if kind == 'chunk':
                buffered_chunks.append(item)
                output_buf.append(item)
            elif kind == 'done':
                full_content = item.get('content', '')
                tool_calls = extract_tool_calls(full_content, tools)

                if tool_calls:
                    print(f"[Stream] tool_calls detected: {json.dumps(tool_calls, ensure_ascii=False)[:300]}")
                    tc_chunk = {
                        'id': completion_id,
                        'object': 'chat.completion.chunk',
                        'created': timestamp,
                        'model': model,
                        'choices': [{
                            'index': 0,
                            'delta': {
                                'role': 'assistant',
                                'content': None,
                                'tool_calls': tool_calls,
                            },
                            'finish_reason': 'tool_calls',
                        }],
                    }
                    yield f'data: {json.dumps(tc_chunk)}\n\n'
                else:
                    for chunk_text in buffered_chunks:
                        c = {
                            'id': completion_id,
                            'object': 'chat.completion.chunk',
                            'created': timestamp,
                            'model': model,
                            'choices': [{
                                'index': 0,
                                'delta': {'content': chunk_text},
                                'finish_reason': None,
                            }],
                        }
                        yield f'data: {json.dumps(c)}\n\n'
                    print(f"[Stream] text reply ({len(full_content)} chars)")

                if item.get('error'):
                    print(f"[Stream] error: {item['error']}")
                    err_c = {
                        'id': completion_id,
                        'object': 'chat.completion.chunk',
                        'created': timestamp,
                        'model': model,
                        'choices': [{
                            'index': 0,
                            'delta': {'content': f'\n\n[Error: {item["error"]}]'},
                            'finish_reason': None,
                        }],
                    }
                    yield f'data: {json.dumps(err_c)}\n\n'
                break
            elif kind == 'error':
                print(f"[Stream] ERROR: {item}")
                err_c = {
                    'id': completion_id,
                    'object': 'chat.completion.chunk',
                    'created': timestamp,
                    'model': model,
                    'choices': [{
                        'index': 0,
                        'delta': {'content': f'\n\n[Error: {item}]'},
                        'finish_reason': None,
                    }],
                }
                yield f'data: {json.dumps(err_c)}\n\n'
                break
    else:
        while True:
            try:
                kind, item = chunk_queue.get(timeout=120)
            except queue.Empty:
                break

            if kind == 'chunk':
                c = {
                    'id': completion_id,
                    'object': 'chat.completion.chunk',
                    'created': timestamp,
                    'model': model,
                    'choices': [{
                        'index': 0,
                        'delta': {'content': item},
                        'finish_reason': None,
                    }],
                }
                yield f'data: {json.dumps(c)}\n\n'
                output_buf.append(item)
            elif kind == 'done':
                if item.get('error'):
                    print(f"[Stream] error: {item['error']}")
                    err_c = {
                        'id': completion_id,
                        'object': 'chat.completion.chunk',
                        'created': timestamp,
                        'model': model,
                        'choices': [{
                            'index': 0,
                            'delta': {'content': f'\n\n[Error: {item["error"]}]'},
                            'finish_reason': None,
                        }],
                    }
                    yield f'data: {json.dumps(err_c)}\n\n'
                else:
                    print(f"[Stream] text reply ({len(item.get('content',''))} chars)")
                break
            elif kind == 'error':
                print(f"[Stream] ERROR: {item}")
                err_c = {
                    'id': completion_id,
                    'object': 'chat.completion.chunk',
                    'created': timestamp,
                    'model': model,
                    'choices': [{
                        'index': 0,
                        'delta': {'content': f'\n\n[Error: {item}]'},
                        'finish_reason': None,
                    }],
                }
                yield f'data: {json.dumps(err_c)}\n\n'
                break

    end_chunk = {
        'id': completion_id,
        'object': 'chat.completion.chunk',
        'created': timestamp,
        'model': model,
        'choices': [{
            'index': 0,
            'delta': {},
            'finish_reason': 'stop',
        }],
    }
    yield f'data: {json.dumps(end_chunk)}\n\n'
    yield 'data: [DONE]\n\n'

    # 娴佸紡璇锋眰鏃ュ織
    if start_time:
        elapsed_for_log = (time.time() - start_time) * 1000
        full_content = ''.join(output_buf)
        input_tk = _estimate_tokens(user_message)
        output_tk = _estimate_tokens(full_content)
        if auth_manager and used_account:
            auth_manager.record_token_usage(used_account, input_tk, output_tk)
        if req_logger:
            req_logger.log_request(model, used_account, elapsed_for_log, True,
                                   request_summary=f'[stream] {full_content[:80]}...' if len(full_content) > 80 else f'[stream] {full_content}')


# ======================================================================
# 妯″瀷鍒楄〃
# ======================================================================




@app.route('/v1/models', methods=['GET'])
def list_models():
    models = [
        {'id': 'copilot-auto',       'object': 'model', 'owned_by': 'microsoft', 'name': '鑷姩閫夋嫨'},
        {'id': 'copilot-quick',      'object': 'model', 'owned_by': 'microsoft', 'name': '蹇€熺瓟澶?},
        {'id': 'copilot-thinking',   'object': 'model', 'owned_by': 'microsoft', 'name': '娣卞害鎬濊€?},
        {'id': 'gpt-5.5',            'object': 'model', 'owned_by': 'microsoft', 'name': 'GPT 5.5 蹇€熷搷搴?},
        {'id': 'gpt-5.5-thinking',   'object': 'model', 'owned_by': 'microsoft', 'name': 'GPT 5.5 娣卞害鎬濊€?},
        {'id': 'gpt-5.2',            'object': 'model', 'owned_by': 'microsoft', 'name': 'GPT 5.2 蹇€熷搷搴?},
        {'id': 'gpt-5.2-thinking',   'object': 'model', 'owned_by': 'microsoft', 'name': 'GPT 5.2 娣卞害鎬濊€?},
    ]
    return jsonify({'object': 'list', 'data': models})


@app.route('/v1/accounts', methods=['GET'])
def list_accounts():
    return jsonify({'accounts': auth_manager.get_all_status()})


# ======================================================================
# 绠＄悊鎺ュ彛
# ======================================================================

@app.route('/status', methods=['GET'])
def status():
    stats = auth_manager.get_daily_stats()
    return jsonify(stats)


@app.route('/refresh', methods=['POST'])
def refresh():
    data = request.get_json(silent=True) or {}
    username = data.get('username')
    ok = auth_manager.refresh_token(username)
    if ok:
        return jsonify({'message': 'Token 鍒锋柊鎴愬姛'})
    return jsonify({'error': 'Token 鍒锋柊澶辫触'}), 500


@app.route('/v1/responses', methods=['POST'])
def responses_api():
    data = request.get_json(silent=True) or {}
    model = data.get('model', 'copilot-auto')
    input_value = data.get('input', '')
    if isinstance(input_value, list):
        messages = input_value
    else:
        messages = [{'role': 'user', 'content': str(input_value)}]

    with app.test_request_context('/v1/chat/completions', method='POST', json={
        'model': model,
        'messages': messages,
        'stream': False,
    }):
        resp = chat_completions()

    flask_resp = resp[0] if isinstance(resp, tuple) else resp
    status_code = resp[1] if isinstance(resp, tuple) and len(resp) > 1 else getattr(flask_resp, 'status_code', 200)
    if status_code >= 400:
        return resp

    payload = flask_resp.get_json()
    content = payload.get('choices', [{}])[0].get('message', {}).get('content') or ''
    return jsonify({
        'id': f'resp-{uuid.uuid4().hex}',
        'object': 'response',
        'created_at': int(time.time()),
        'model': model,
        'output': [{
            'type': 'message',
            'role': 'assistant',
            'content': [{'type': 'output_text', 'text': content}],
        }],
    })


@app.route('/health', methods=['GET'])
def health():
    token, used_account = auth_manager.get_token()
    return jsonify({
        'ok': bool(token),
        'service': 'M365 Copilot Unified Gateway',
        'base_url': f'http://{request.host}/v1',
        'port': config.SERVER_PORT,
        'account': used_account,
        'token': {'has_token': bool(token)},
        'rate_limiter': {
            'enabled': _rate_limiter.enabled,
            'rpm': _rate_rpm,
            'burst': _rate_burst,
        },
        'models': [
            'copilot-auto', 'copilot-quick', 'copilot-thinking',
            'gpt-5.5', 'gpt-5.5-thinking', 'gpt-5.2', 'gpt-5.2-thinking',
        ],
        'api_key_policy': 'any value accepted',
    })


# ======================================================================
# 鍏ュ彛
# ======================================================================

if __name__ == '__main__':
    print(f"""
鈺斺晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晽
鈺?     M365 Copilot 鍙嶄唬鏈嶅姟 v6.0                  鈺?
鈺?     MSAL + Playwright Chromium 鑷姩鐧诲綍         鈺?
鈺?     7 绉嶆ā鍨?路 宸ュ叿璋冪敤 路 澶氳处鍙疯疆璇?           鈺?
鈺?                                                 鈺?
鈺? 鎺ュ彛:   POST /v1/chat/completions               鈺?
鈺? 妯″瀷:   GET  /v1/models  (7 绉?                鈺?
鈺? 璐﹀彿:   GET  /v1/accounts                       鈺?
鈺? 鐘舵€?   GET  /status                            鈺?
鈺? 鍒锋柊:   POST /refresh                           鈺?
鈺氣晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨暆
""")
    app.run(
        host=config.SERVER_HOST,
        port=config.SERVER_PORT,
        debug=False,
        threaded=True,
    )

