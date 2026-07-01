from __future__ import annotations

import copy
import ipaddress
import json
import os
from pathlib import Path
from typing import Any

import config


CONFIG_FILE = Path(config.DATA_DIR) / 'm365_runtime_config.json'

DEFAULT_CONFIG: dict[str, Any] = {
    'models': [
        {'id': 'copilot-auto', 'object': 'model', 'owned_by': 'microsoft', 'name': '自动选择'},
        {'id': 'copilot-quick', 'object': 'model', 'owned_by': 'microsoft', 'name': '快速答复'},
        {'id': 'copilot-thinking', 'object': 'model', 'owned_by': 'microsoft', 'name': '深度思考'},
        {'id': 'gpt-5.5', 'object': 'model', 'owned_by': 'microsoft', 'name': 'GPT 5.5 快速响应'},
        {'id': 'gpt-5.5-thinking', 'object': 'model', 'owned_by': 'microsoft', 'name': 'GPT 5.5 深度思考'},
        {'id': 'gpt-5.2', 'object': 'model', 'owned_by': 'microsoft', 'name': 'GPT 5.2 快速响应'},
        {'id': 'gpt-5.2-thinking', 'object': 'model', 'owned_by': 'microsoft', 'name': 'GPT 5.2 深度思考'},
    ],
    'tool_aliases': {
        'exec_command': 'terminal',
        'shell': 'terminal',
        'shell_command': 'terminal',
        'terminal': 'terminal',
        'run_command': 'terminal',
        'bash': 'terminal',
        'powershell': 'terminal',
        'computer_use': 'desktop',
        'computer': 'desktop',
        'desktop': 'desktop',
        'browser': 'browser',
    },
    'security': {
        'lan_access': True,
        'require_api_key': False,
        'api_keys': ['120'],
        'allowed_ips': [],
    },
    'logging': {
        'include_user_agent': True,
        'include_tools': True,
    },
}


def _deep_merge(default: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(default)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_runtime_config() -> dict[str, Any]:
    try:
        with CONFIG_FILE.open('r', encoding='utf-8') as handle:
            loaded = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        loaded = {}
    cfg = _deep_merge(DEFAULT_CONFIG, loaded)
    save_runtime_config(cfg)
    return cfg


def save_runtime_config(cfg: dict[str, Any]) -> None:
    os.makedirs(CONFIG_FILE.parent, exist_ok=True)
    with CONFIG_FILE.open('w', encoding='utf-8') as handle:
        json.dump(cfg, handle, ensure_ascii=False, indent=2)


def is_loopback_ip(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return ip in ('localhost', '::1')


def ip_allowed(ip: str, allowed: list[str]) -> bool:
    if not allowed:
        return True
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for rule in allowed:
        try:
            if '/' in rule and address in ipaddress.ip_network(rule, strict=False):
                return True
            if address == ipaddress.ip_address(rule):
                return True
        except ValueError:
            continue
    return False
