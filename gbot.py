"""Local Grok Bot CLI adapter; one submission, receipt-anchored reply polling.

The installed CLI owns authentication; an internal Windows safeStorage adapter
can reuse the signed-in desktop account. No automatic relay, retries of sends,
or surrounding conversation export are performed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import uuid
import re
from pathlib import Path
from typing import Any

from native_session import NativeSessionError, build_cli_environment

POLL_INTERVAL = 1.0
REPLY_TIMEOUT = 180.0
CLI_TIMEOUT = 40.0
CURSOR_GRACE = 10.0


class GbotError(RuntimeError):
    """A safe user-visible error without CLI output or authentication details."""

    def __init__(self, message: str, *, code: str = 'unknown'):
        super().__init__(message)
        self.code = code


def _cli_failure(stderr: bytes, stage: str, exit_code: int) -> GbotError:
    # Match known transport signatures only. Never expose or persist raw output,
    # which may contain credentials, conversation text or remote error bodies.
    diagnostic = stderr[:65536].decode('utf-8', errors='replace').lower()
    if re.search(r'\b(?:401|403)\b|unauthorized|unauthenticated', diagnostic):
        code, reason = 'authentication', '登录认证被拒绝，请打开本机 Grok Bot 确认登录状态'
    elif re.search(r'timeouterror|timed? out|timeout', diagnostic):
        code, reason = 'timeout', 'Grok Bot 网关请求超时'
    elif re.search(r'fetch failed|econnreset|enotfound|econnrefused|enetunreach', diagnostic):
        code, reason = 'network', '连接 Grok Bot 网关失败，请检查网络'
    elif re.search(r'\b(?:429|502|503|504)\b', diagnostic):
        code, reason = 'unavailable', 'Grok Bot 网关暂时不可用，请稍后检查'
    else:
        code, reason = 'unknown', 'CLI 未提供可识别原因，请检查本机 Grok Bot'
    safe_stage = stage if stage in {'send', 'thread', 'bots'} else 'other'
    logging.getLogger(__name__).warning('Grok CLI failed stage=%s exit=%s category=%s',
                                      safe_stage, exit_code, code)
    label = {'send': '发送消息', 'thread': '读取回复', 'bots': '读取 Bot 列表'}.get(safe_stage, '操作')
    return GbotError(f'{label}失败：{reason}。消息可能已经送达，请先查看 Grok Bot 对话，勿直接重发。', code=code)


def _node_entry(entry: str) -> list[str]:
    node = shutil.which('node')
    if not node:
        raise GbotError('未找到 Node.js，请安装 Grok Bot CLI 所需的 Node.js。')
    return [node, entry]


def _command() -> list[str]:
    entry = os.environ.get('GBOT_ENTRY', '').strip()
    if entry:
        return _node_entry(entry)
    binary = os.environ.get('GBOT_BIN', '').strip() or shutil.which('gbot')
    if not binary:
        raise GbotError('未找到 Grok Bot CLI，请安装 gbot 或配置 GBOT_ENTRY。')
    if Path(binary).suffix.lower() in {'.js', '.mjs'}:
        return _node_entry(binary)
    if Path(binary).suffix.lower() in {'.cmd', '.bat', '.ps1'}:
        # npm Windows launchers require a shell. Invoke their actual JS entry
        # directly so Chinese, quotes and shell metacharacters stay one argument.
        package = Path(binary).parent / 'node_modules' / 'grok-bot-cli'
        for relative in ('dist/bin/gbot.mjs', 'artifact/bin/gbot.mjs'):
            candidate = package / relative
            if candidate.is_file():
                return _node_entry(str(candidate))
        raise GbotError('无法定位 gbot 的 JavaScript 入口，请设置 GBOT_ENTRY。')
    return [binary]


async def _run_json(*args: str) -> Any:
    command = _command()
    try:
        environment = await asyncio.to_thread(build_cli_environment, os.environ.copy())
    except NativeSessionError as error:
        raise GbotError(str(error)) from None
    # The Application is the sender. An inherited Codex terminal identity would
    # tell Grok to return its answer into a Codex thread instead of this channel.
    environment.pop('CODEX_THREAD_ID', None)
    process = await asyncio.create_subprocess_exec(
        *command, *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=environment,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), CLI_TIMEOUT)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        if process.returncode is None:
            process.kill()
        await process.communicate()
        raise
    if process.returncode:
        raise _cli_failure(stderr, args[0] if args else 'other', process.returncode)
    if len(stdout) > 2 * 1024 * 1024:
        raise GbotError('Grok Bot 返回数据超出上限。')
    try:
        result = json.loads(stdout.decode('utf-8'))
    except (ValueError, UnicodeError) as error:
        raise GbotError('Grok Bot CLI 未返回有效 JSON。') from error
    if isinstance(result, dict) and result.get('exitCode', 0) != 0:
        raise GbotError('Grok Bot 未确认操作成功，请查看本机应用；请勿自动重发。')
    return result


async def list_bots() -> list[dict[str, str]]:
    rows = await _run_json('bots', 'list', '--gateway', '--json')
    if not isinstance(rows, list):
        raise GbotError('Grok Bot 列表格式不兼容。')
    result = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not isinstance(row.get('name'), str):
            raise GbotError('Grok Bot 列表缺少有效身份。')
        result.append({'id': row['id'], 'name': row['name']})
    return result


def _reply_text(entry: dict[str, Any]) -> str:
    if entry.get('role') == 'user':
        return ''
    if entry.get('role') != 'assistant' and entry.get('kind') != 'send-message':
        return ''
    for field in ('text', 'content', 'message'):
        value = entry.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    message = entry.get('message')
    if isinstance(message, dict) and isinstance(message.get('content'), str):
        return message['content'].strip()
    return ''


def _entries(page: Any) -> list[dict[str, Any]]:
    transcript = page.get('transcript') if isinstance(page, dict) else None
    entries = transcript.get('entries') if isinstance(transcript, dict) else None
    if not isinstance(entries, list) or len(entries) > 200:
        raise GbotError('Grok Bot 回复格式不兼容。')
    if any(not isinstance(entry, dict) or not isinstance(entry.get('id'), str) for entry in entries):
        raise GbotError('Grok Bot 回复缺少有效消息身份。')
    return entries


def _correlated_replies(entries: list[dict[str, Any]], correlation: str,
                        text: str, envelope: Any, request_id: str | None) -> tuple[list[str], str | None]:
    header = re.compile(r'\[gbot msg=([A-Za-z0-9_.:-]{1,128}) corr='
        + re.escape(correlation) + r' hop=0 from=[A-Za-z0-9_.:-]{1,64}@[A-Za-z0-9_.:-]{1,64}\]')
    anchors = []
    for entry in entries:
        content = entry.get('content')
        if entry.get('kind') != 'message' or entry.get('role') != 'user' or not isinstance(content, str):
            continue
        first, delimiter, body = content.partition('\n')
        match = header.fullmatch(first)
        if (match and delimiter and body == text.strip()
                and (not isinstance(envelope, str) or match[1] == envelope)):
            anchors.append(entry)
    if len(anchors) > 1:
        raise GbotError('无法确定本次消息的唯一请求，请查看 Grok Bot 对话；禁止自动重发。')
    if anchors:
        anchor_request = anchors[0].get('requestId')
        if not isinstance(anchor_request, str) or not anchor_request or len(anchor_request) > 1024:
            raise GbotError('本次 Grok Bot 消息缺少有效请求身份，请查看本机对话。')
        if request_id is not None and request_id != anchor_request:
            raise GbotError('本次 Grok Bot 消息请求身份已改变，请查看本机对话。')
        request_id = anchor_request
    if request_id is None:
        return [], None
    # Only the server requestId derived from our unique user entry scopes the
    # response. Local envelope/correlation IDs are never server cursor IDs.
    replies = [_reply_text(entry) for entry in entries
               if entry.get('kind') == 'send-message' and entry.get('requestId') == request_id]
    return [reply for reply in replies if reply], request_id


async def ask(bot: str, text: str) -> str:
    if not isinstance(bot, str) or not bot.strip() or bot.startswith('-'):
        raise GbotError('请选择有效的 Grok Bot。')
    if not isinstance(text, str) or not text.strip() or len(text) > 12000:
        raise GbotError('请输入 1 到 12000 字的对话内容。')
    # Flags precede `--` so option-like spoken text remains a literal positional.
    correlation = str(uuid.uuid4())
    receipt = await _run_json('send', '--gateway', '--no-history', '--json', '--envelope',
                              '--correlation-id', correlation, bot, '--', text)
    if not isinstance(receipt, dict):
        raise GbotError('消息送达状态未知，请先检查 Grok Bot 对话，禁止自动重发。')
    cursor = receipt.get('messageId') if receipt.get('delivery') == 'accepted' else None
    new_acceptance = (receipt.get('delivery') == 'unknown' and isinstance(receipt.get('result'), dict)
                      and receipt['result'].get('accepted') is True)
    if cursor is None and not new_acceptance:
        raise GbotError('消息送达状态未知，请先检查 Grok Bot 对话，禁止自动重发。')
    if cursor is not None and (not isinstance(cursor, str) or not cursor or len(cursor) > 1024):
        raise GbotError('Grok Bot 回执游标无效，请勿自动重发。')
    request_id = None
    deadline = asyncio.get_running_loop().time() + REPLY_TIMEOUT
    cursor_deadline = asyncio.get_running_loop().time() + CURSOR_GRACE
    read_retries = 0
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise GbotError('等待 Grok Bot 回复超时；消息已经发送，请勿自动重发。')
        arguments = ['thread', bot, '--gateway', '--no-history', '--json', '--limit', '200']
        if cursor is not None:
            arguments.extend(['--after', cursor])
        try:
            page = await asyncio.wait_for(_run_json(*arguments), remaining)
        except GbotError as error:
            if error.code not in {'network', 'timeout', 'unavailable'} or read_retries >= 1:
                raise
            # Recover one transient read failure using the same receipt. Sending
            # remains outside this loop; an ambiguous submission is never retried.
            read_retries += 1
            await asyncio.sleep(min(POLL_INTERVAL, max(0, deadline - asyncio.get_running_loop().time())))
            continue
        except asyncio.TimeoutError as error:
            raise GbotError('等待 Grok Bot 回复超时；消息已经发送，请勿自动重发。') from error
        if cursor is None:
            replies, request_id = _correlated_replies(_entries(page), correlation, text,
                                                      receipt.get('envelopeId'), request_id)
            if replies:
                answer = '\n'.join(replies)
                if len(answer) > 20000:
                    raise GbotError('Grok Bot 回复过长，请在本机对话中查看。')
                return answer
            await asyncio.sleep(min(POLL_INTERVAL, max(0, deadline - asyncio.get_running_loop().time())))
            continue
        if isinstance(page, dict) and page.get('gapReset') is True and asyncio.get_running_loop().time() < cursor_deadline:
            # The accepted user message may not yet be visible in the tail.
            # Discard this entire snapshot, retain the receipt cursor and retry
            # only the read. Never infer that submission failed or resend it.
            await asyncio.sleep(min(POLL_INTERVAL, max(0, deadline - asyncio.get_running_loop().time())))
            continue
        if not isinstance(page, dict) or page.get('gapReset') is not False:
            raise GbotError('Grok Bot 回复游标失效，为避免朗读旧消息已停止等待；请查看本机对话。')
        entries = _entries(page)
        replies = []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get('id'), str):
                raise GbotError('Grok Bot 回复缺少有效消息身份。')
            if entry['id'] != cursor:
                reply = _reply_text(entry)
                if reply:
                    replies.append(reply)
        if replies:
            answer = '\n'.join(replies)
            if len(answer) > 20000:
                raise GbotError('Grok Bot 回复过长，请在本机对话中查看。')
            return answer
        next_cursor = page.get('cursor')
        if not isinstance(next_cursor, str) or not next_cursor or len(next_cursor) > 1024:
            raise GbotError('Grok Bot 回复游标无效。')
        cursor = next_cursor
        await asyncio.sleep(min(POLL_INTERVAL, max(0, deadline - asyncio.get_running_loop().time())))
