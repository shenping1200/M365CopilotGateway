"""
M365 Copilot 瀹㈡埛绔?- 鍩轰簬 substrate.office.com SignalR 鍗忚
"""

import json
import uuid
import asyncio
import time
import os
import websockets
import httpx
import config

SEP = '\x1e'  # SignalR record separator

# SignalR negotiate 绔偣
NEGOTIATE_URL = "https://substrate.office.com/m365chat/hub/negotiate?negotiateVersion=1"

# Feature variants (浠庢祻瑙堝櫒鎹曡幏 - 2026-06-12 鏇存柊)
VARIANTS = ','.join([
    'EnableMcpServerWidgets', 'feature.EnableMcpServerWidgets',
    'feature.EnableLuForChatCIQ', 'feature.enableChatCIQPlugin',
    'EnableRequestPlugins', 'feature.EnableSensitivityLabels',
    'EnableUnsupportedUrlDetector', 'feature.IsCustomEngineCopilotEnabled',
    'feature.bizchatfluxv3', 'feature.enablechatpages',
    'feature.enableCodeCanvas', 'feature.turnOnWorkTabRecommendation',
    'feature.turnOnDARecommendation',
    'feature.IsStreamingModeInChatRequestEnabled',
    'IncludeSourceAttributionsConcise', 'SkipPublishEmptyMessage',
    'feature.EnableDeduplicatingSourceAttributions',
    'Enable3PActionProgressMessages', 'feature.enableClientWebRtc',
    'feature.EnableMeetingRecapOfSeriesMeetingWithCiq',
    'feature.cwcfluxv3fem',
    'feature.EnableReferencesListCompleteSignal',
    'feature.StorageMessageSplitDisabled',
    'feature.EnableCuaTakeControlApi',
    'SingletonEnvOn',
    'agt_bizchat_enablePagesCitations',
    'agt_bizchat_enablePagesCitationsForMultiturn',
    'feature.cwcallowedos', 'feature.EnableMergingPureDeltas',
    'feature.disabledisallowedmsgs',
    'feature.enableCitationsForSynthesisData',
    'feature.EnableConversationShareApis',
    'feature.enableGenerateGraphicArtOptionsSet',
    'cdximagen', 'feature.EnableUpdatedUXForConfirmationDialog',
    'feature.EnableContentApiandDocTypeHtmlInRichAnswers',
    'cdxgrounding_api_v2_rich_web_answers_reference_bottom_force',
    'cdxenablerenderforisocomp',
    'feature.EnableClientFileURLSupportForOfficeWebPaidCopilot',
    'feature.EnableDesignEditorImageGrounding', 'feature.EnableDesignerEditor',
    'feature.EnableSkipRehydrationForSpeCIdImages',
    'feature.EnablePersonalization',
    'feature.EnableSkipEmittingMessageOnFlush',
    'feature.EnableRemoveEmptySourceAttributions',
    'feature.EnableRemoveStreamingMode',
    'feature.OfficeWebToHelix', 'feature.OfficeDesktopToHelix',
    'feature.M365TeamsHubToHelix', 'feature.OwaHubToHelix',
    'feature.MonarchHubToHelix', 'feature.Win32OutlookHubToHelix',
    'feature.MacOutlookHubToHelix',
    'Agt_bizchat_enableGpt5ForHelix',
])


# 妯″瀷鍚嶇О 鈫?tone 鏄犲皠
MODEL_TONE_MAP = {
    'copilot-auto':        'Magic',
    'copilot-quick':       'Chat',
    'copilot-thinking':    'Reasoning',
    'gpt-5.5':             'Gpt_5_5_Chat',
    'gpt-5.5-thinking':    'Gpt_5_5_Reasoning',
    'gpt-5.2':             'Gpt_5_2_Chat',
    'gpt-5.2-thinking':    'Gpt_5_2_Reasoning',
    # 鍏煎鏃у悕绉?
    'copilot':             'Magic',
    'gpt-5':               'Magic',
    'm365-copilot':        'Magic',
}

# 瀹屾暣 optionsSets (浠庢祻瑙堝櫒鎹曡幏锛屾墍鏈夋ā鍨嬮€氱敤)
FULL_OPTIONS_SETS = [
    "search_result_progress_messages_with_search_queries",
    "update_textdoc_response_after_streaming",
    "deepleo_networking_timeout_10minutes_canmore",
    "cwc_flux_image", "cwc_code_interpreter",
    "cwc_code_interpreter_amsfix", "enable_msa_user",
    "cwc_code_interpreter_citation_fix",
    "code_interpreter_interactive_charts",
    "cwc_code_interpreter_interactive_charts_inline_image",
    "code_interpreter_matplotlib_patching",
    "cwc_fileupload_odb", "update_memory_plugin",
    "add_custom_instructions",
    "cwc_flux_v3", "flux_v3_progress_messages",
    "enable_batch_token_processing", "enable_gg_gpt",
    "flux_v3_image_gen_enable_non_watermarked_storage",
    "rich_responses",
]


class SubstrateClient:
    """M365 Copilot 瀹㈡埛绔?(substrate.office.com SignalR)"""

    def __init__(self, access_token: str, username: str = None):
        self.access_token = access_token
        self.username = username

    async def _negotiate(self) -> dict | None:
        """
        SignalR negotiate: 鐢?AAD token 鎹㈠彇 WS 杩炴帴鍑瘉
        杩斿洖 {connectionId, connectionToken, url} 鎴?None
        """
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Origin': config.SUBSTRATE_ORIGIN,
            'Content-Type': 'application/json',
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(NEGOTIATE_URL, headers=headers)
                if resp.status_code != 200:
                    print(f"[WS] Negotiate failed: HTTP {resp.status_code}")
                    return None
                data = resp.json()
                print(f"[WS] Negotiate OK: keys={list(data.keys())}")
                conn_id = data.get('connectionId', '')
                conn_token = data.get('connectionToken', '')
                # 濡傛灉杩斿洖 url 瀛楁锛堥噸瀹氬悜锛夛紝涔熻褰曚笅鏉?
                url = data.get('url', '')
                at = data.get('accessToken', '')
                if at and len(at) > len(self.access_token):
                    print(f"[WS] Negotiate 杩斿洖浜嗘洿闀跨殑 token: {len(at)} chars")
                    self.access_token = at
                result = {
                    'connectionId': conn_id,
                    'connectionToken': conn_token or conn_id,
                    'url': url,
                }
                print(f"[WS] Negotiate result: connId={conn_id[:20]}..., connToken={len(conn_token)} chars")
                return result
        except Exception as e:
            print(f"[WS] Negotiate error: {e}")
            return None

    def _build_ws_url(self, negotiate_info: dict = None) -> str:
        """鏋勯€?WebSocket URL锛堝彲閫変娇鐢?negotiate 杩斿洖鐨勫嚟璇侊級"""
        sid = uuid.uuid4().hex
        sid_dashed = str(uuid.UUID(sid))
        conv_id = str(uuid.uuid4())
        params = {
            'chatsessionid': sid,
            'XRoutingParameterSessionKey': sid,
            'clientrequestid': sid,
            'X-SessionId': sid_dashed,
            'ConversationId': conv_id,
            'access_token': self.access_token,
            'variants': VARIANTS,
            'source': '"officeweb"',
            'product': 'Office',
            'agentHost': 'Bizchat.FullScreen',
            'licenseType': 'Starter',
            'isEdu': 'false',
            'agent': 'web',
            'scenario': 'OfficeWebIncludedCopilot',
        }
        # 濡傛灉鏈?negotiate 淇℃伅锛屾坊鍔?connectionId
        if negotiate_info:
            ct = negotiate_info.get('connectionToken', '')
            ci = negotiate_info.get('connectionId', '')
            if ct:
                params['id'] = ct
            if ci:
                params['connectionId'] = ci
        qs = '&'.join(f'{k}={v}' for k, v in params.items())
        return f'{config.SUBSTRATE_WS_BASE}?{qs}'

    def _build_chat_message(self, text: str, session_id: str, tone: str = 'Magic'):
        """鏋勯€?SignalR chat 娑堟伅"""
        request_id = uuid.uuid4().hex
        sid_dashed = str(uuid.UUID(session_id))

        msg = {
            "arguments": [{
                "source": "officeweb",
                "clientCorrelationId": request_id,
                "sessionId": sid_dashed,
                "optionsSets": FULL_OPTIONS_SETS,
                "streamingMode": "ConciseWithPadding",
                "spokenTextMode": "None",
                "options": {},
                "extraExtensionParameters": {},
                "allowedMessageTypes": [
                    "Chat", "Suggestion", "InternalSearchQuery",
                    "Disengaged", "InternalLoaderMessage", "Progress",
                    "GeneratedCode", "RenderCardRequest", "SearchQuery",
                    "ConfirmationCard", "GenerateContentQuery",
                    "GenerateGraphicArt", "ReferencesListComplete",
                    "SwitchRespondingEndpoint",
                ],
                "sliceIds": [],
                "threadLevelGptId": {},
                "traceId": request_id,
                "isStartOfSession": True,
                "clientInfo": {
                    "clientPlatform": "mcmcopilot-web",
                    "clientAppName": "Office",
                    "clientEntrypoint": "mcmcopilot-officeweb",
                    "clientSessionId": sid_dashed,
                    "ProductCategory": "Chat",
                    "clientAppType": "Web",
                    "productEntryPoint": "ChatPanel",
                    "deviceOS": "Windows",
                    "deviceType": "Desktop",
                    "clientPlatformVersion": "10",
                },
                "message": {
                    "author": "user",
                    "inputMethod": "Keyboard",
                    "text": text,
                    "entityAnnotationTypes": [
                        "People", "File", "Event", "Email", "TeamsMessage",
                    ],
                    "requestId": request_id,
                    "locationInfo": {
                        "timeZoneOffset": 8,
                        "timeZone": "Asia/Shanghai",
                    },
                    "locale": "zh-cn",
                    "messageType": "Chat",
                    "experienceType": "Default",
                    "adaptiveCards": [],
                    "clientPreferences": {},
                },
                "plugins": [{"Id": "BingWebSearch", "Source": "BuiltIn"}],
                "isSbsSupported": True,
                "tone": tone,
                "renderReferencesBehindEOS": True,
                "disconnectBehavior": "continue",
            }],
            "invocationId": "0",
            "target": "chat",
            "type": 4,
        }
        return msg, request_id

    async def chat(self, message: str, system_prompt: str = None,
                   history: list = None, stream_callback=None,
                   model: str = 'copilot-auto', tools_prompt: str = None) -> dict:
        """
        鍙戦€佹秷鎭苟鑾峰彇瀹屾暣鍥炲

        message: 鐢ㄦ埛娑堟伅
        system_prompt: 绯荤粺鎻愮ず璇?
        history: 鍘嗗彶娑堟伅鍒楄〃 [{"role":"user","content":"..."}, ...]
        stream_callback: async callable(chunk_text) - 姣忔敹鍒颁竴涓祦寮?chunk 鏃惰皟鐢?
        tools_prompt: 宸ュ叿璋冪敤鎸囦护锛堟斁鍦ㄧ敤鎴锋秷鎭揣鍓嶉潰锛?
        杩斿洖: {"content": "...", "chunks": [...], "error": None|str}
        """
        # 缁勮鏈€缁堝彂閫佹枃鏈紙绯荤粺鎻愮ず + 鍘嗗彶 + 宸ュ叿鎸囦护 + 褰撳墠娑堟伅锛?
        parts = []
        if system_prompt:
            parts.append(f"CONTEXT:\n{system_prompt}")
        if history:
            for msg in history:
                role = msg.get('role', 'user')
                content = msg.get('content', '')
                if role == 'user':
                    parts.append(f"[User]: {content}")
                elif role == 'assistant':
                    parts.append(f"[Assistant]: {content}")
                elif role == 'tool':
                    parts.append(f"[Tool Result]: {content}")
                elif role == 'system':
                    parts.append(f"[System]: {content}")
        # 宸ュ叿鎸囦护鏀惧湪鐢ㄦ埛娑堟伅绱у墠闈紙鑰屼笉鏄紑澶达紝閬垮厤琚?38K 鏂囨湰娣规病锛?
        if tools_prompt:
            parts.append(tools_prompt)
        parts.append(f"USER:\n{message}")
        # 瑙ｆ瀽 tone
        tone = MODEL_TONE_MAP.get(model, 'Magic')

        combined_text = '\n\n'.join(parts)
        print(f"[WS] combined_text length: {len(combined_text)} chars, tone: {tone}")

        session_id = uuid.uuid4().hex

        # Step 1: 灏濊瘯 negotiate锛堢敤 AAD token 鎹㈠彇 WS 杩炴帴鍑瘉锛?
        negotiate_info = await self._negotiate()

        if not negotiate_info:
            print('[WS] negotiate unavailable, using direct WS URL fallback')
        url = self._build_ws_url(negotiate_info)
        headers = {
            'Origin': config.SUBSTRATE_ORIGIN,
            'Authorization': f'Bearer {self.access_token}',
        }

        chunks = []
        full_content = ''

        # Track error from Type2 completion
        server_error = None
        started_at = time.time()
        max_chat_seconds = getattr(config, 'COPILOT_CHAT_TIMEOUT', 300)
        received_count = 0

        try:
            async with websockets.connect(
                url,
                additional_headers=headers,
                max_size=10 * 1024 * 1024,
                ping_interval=30,
                ping_timeout=10,
            ) as ws:
                # SignalR handshake
                await ws.send(json.dumps({"protocol": "json", "version": 1}) + SEP)
                resp = await asyncio.wait_for(ws.recv(), timeout=10)
                if resp.strip() != '{}':
                    return {'content': '', 'chunks': [], 'error': f'Handshake failed: {resp[:200]}'}

                # Ping
                await ws.send(json.dumps({"type": 6}) + SEP)

                # Send chat
                chat_msg, request_id = self._build_chat_message(combined_text, session_id, tone=tone)
                await ws.send(json.dumps(chat_msg) + SEP)

                # Receive streaming response
                done = False
                while not done:
                    if time.time() - started_at > max_chat_seconds:
                        error = f'Copilot response timeout after {max_chat_seconds}s'
                        print(f'[WS] {error}')
                        break
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=60)
                    except asyncio.TimeoutError:
                        break

                    received_count += 1
                    if received_count <= 20 or received_count % 20 == 0:
                        print(f'[WS] recv frame #{received_count}, raw_len={len(raw)}')
                    for msg_str in raw.split(SEP):
                        msg_str = msg_str.strip()
                        if not msg_str:
                            continue

                        try:
                            msg = json.loads(msg_str)
                        except json.JSONDecodeError:
                            continue


                        msg_type = msg.get('type')
                        target = msg.get('target', '')

                        # Keep-alive ping
                        if msg_type == 6:
                            await ws.send(json.dumps({"type": 6}) + SEP)
                            continue

                        # Server close
                        if msg_type == 7:
                            done = True
                            break

                        # Type2: completion result (contains final messages and errors)
                        if msg_type == 2:
                            item = msg.get('item', {})
                            turn_state = item.get('turnState', '')
                            for m in item.get('messages', []):
                                if m.get('author') == 'bot':
                                    bot_text = m.get('text', '')
                                    if turn_state == 'Failed' or m.get('turnState') == 'Failed':
                                        server_error = bot_text or 'Copilot 璇锋眰澶辫触'
                                    elif bot_text and len(bot_text) > len(full_content):
                                        full_content = bot_text
                                        chunks = [full_content]
                            if turn_state == 'Failed':
                                done = True
                                break

                        # Update messages
                        if msg_type == 1 and target == 'update':
                            args = msg.get('arguments', [{}])
                            if not args:
                                continue
                            arg = args[0]

                            # Streaming text delta
                            if 'writeAtCursor' in arg:
                                chunk = arg['writeAtCursor']
                                chunks.append(chunk)
                                full_content += chunk
                                if stream_callback:
                                    result = stream_callback(chunk)
                                    if asyncio.iscoroutine(result):
                                        await result

                            # Final message with full text
                            if arg.get('isLastUpdate'):
                                # Extract final text from messages if available
                                for m in arg.get('messages', []):
                                    if m.get('text') and len(m['text']) > len(full_content):
                                        full_content = m['text']
                                        chunks = [full_content]
                                done = True
                                break

        except Exception as e:
            error = str(e)
            if not full_content:
                return {'content': '', 'chunks': [], 'error': error}

        if server_error and not full_content:
            return {'content': '', 'chunks': [], 'error': server_error}


        try:
            print(f'[WS] Copilot response ({len(full_content)} chars): ' + full_content[:500].encode('utf-8','replace').decode('utf-8','replace'))
        except UnicodeEncodeError:
            print(f'[WS] Copilot response ({len(full_content)} chars) [emoji omitted]')
        return {
            'content': full_content.strip(),
            'chunks': chunks,
            'error': None,
        }

    async def chat_stream(self, message: str):
        """
        寮傛鐢熸垚鍣?- 閫?chunk 浜у嚭娴佸紡鏂囨湰
        yield: (chunk_text, is_done)
        """
        queue = asyncio.Queue()

        async def on_chunk(text):
            await queue.put(text)

        # Run chat in background
        chat_task = asyncio.create_task(self.chat(message, stream_callback=on_chunk))

        while not chat_task.done():
            try:
                chunk = await asyncio.wait_for(queue.get(), timeout=1.0)
                yield chunk, False
            except asyncio.TimeoutError:
                continue

        # Drain remaining
        while not queue.empty():
            chunk = await queue.get()
            yield chunk, False

        result = chat_task.result()
        if result.get('error'):
            yield f"[Error: {result['error']}]", True
        else:
            yield '', True


def load_token() -> str | None:
    """浠庢枃浠跺姞杞?substrate token"""
    path = os.path.join(os.path.dirname(__file__), config.SUBSTRATE_TOKEN_FILE)
    if os.path.exists(path):
        with open(path) as f:
            token = f.read().strip()
            if token:
                return token
    return None

