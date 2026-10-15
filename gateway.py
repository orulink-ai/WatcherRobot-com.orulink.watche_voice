"""Persistent HTTP pool for the gateway contract used by grok-bot-cli 0.12.4.

Community bridge contract, not an xAI realtime API. Credentials stay in memory.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from urllib.parse import urlsplit

import httpx

from native_session import build_cli_environment


class GatewayError(RuntimeError):
    def __init__(self, message, *, status=None, reason='http_status', delivery_unknown=False):
        super().__init__(message)
        self.status = status
        self.reason = reason
        self.delivery_unknown = delivery_unknown


def validate_url(value: str, *, backend=False) -> str:
    try:
        url = urlsplit(value)
        host = url.hostname or ''
        allowed = host in {'cursor.sh', 'cursor.com'} or host.endswith(('.cursor.sh', '.cursor.com'))
        if not backend:
            allowed = allowed or host.endswith('.cursorvm.com')
        if (url.scheme != 'https' or not allowed or url.username or url.password
                or url.query or url.fragment or url.port not in (None, 443)):
            raise ValueError()
        return value.rstrip('/')
    except (ValueError, TypeError):
        raise GatewayError('Grok Bot 网关地址不受支持。') from None


class Gateway:
    def __init__(self, *, environment=None, transport=None):
        self._environment = environment or (lambda: build_cli_environment(os.environ.copy()))
        options = {'transport': transport} if transport is not None else {}
        self._http = httpx.AsyncClient(timeout=40, follow_redirects=False,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4, keepalive_expiry=300), **options)
        self._session = None
        self._lock = asyncio.Lock()
        self.connections = 0

    async def _post(self, url, headers, body, *, method='EnsureSandBox'):
        stage = {'EnsureSandBox': '建立会话', 'listAgents': '读取 Bot 列表',
                 'getAgentTranscriptTail': '读取回复', 'sendPrompt': '发送消息'}[method]
        suffix = '；消息可能已经送达，请勿自动重发。' if method == 'sendPrompt' else '。'
        try:
            async with self._http.stream('POST', url, headers=headers, json=body) as response:
                if response.status_code >= 300:
                    auth = '请检查本机 Grok Bot 登录状态。' if response.status_code in (401, 403) else ''
                    raise GatewayError(f'Grok Bot {stage}失败（HTTP {response.status_code}）{suffix}{auth}',
                        status=response.status_code, delivery_unknown=method == 'sendPrompt')
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 2 * 1024 * 1024:
                        raise GatewayError(f'Grok Bot 返回数据超出上限{suffix}',
                            reason='response_too_large', delivery_unknown=method == 'sendPrompt')
                return json.loads(data)
        except GatewayError:
            raise
        except (httpx.HTTPError, ValueError) as error:
            reason = next((name for kind, name in (
                (httpx.ConnectTimeout, 'connect_timeout'), (httpx.ReadTimeout, 'read_timeout'),
                (httpx.WriteTimeout, 'write_timeout'), (httpx.PoolTimeout, 'pool_timeout'),
                (httpx.ConnectError, 'connect_error'), (httpx.ReadError, 'read_error'),
                (httpx.WriteError, 'write_error'), (httpx.ProtocolError, 'protocol_error'),
                (ValueError, 'invalid_response')) if isinstance(error, kind)), 'transport_error')
            raise GatewayError(f'Grok Bot {stage}连接失败或超时{suffix}', reason=reason,
                delivery_unknown=method == 'sendPrompt') from None

    async def connect(self):
        async with self._lock:
            if self._session is not None:
                return self._session
            env = await asyncio.to_thread(self._environment)
            url, token = env.get('GROK_BOT_GATEWAY_URL'), env.get('GROK_BOT_GATEWAY_TOKEN')
            extra = {}
            if not (url and token):
                access = env.get('CURSOR_ACCESS_TOKEN', '')
                if not access:
                    raise GatewayError('请先登录本机 Grok Bot。')
                base = validate_url(env.get('CURSOR_API_BASE_URL', 'https://api2.cursor.sh'), backend=True)
                result = await self._post(base + '/aiserver.v1.GrokBotService/EnsureSandBox', {
                    'authorization': 'Bearer ' + access, 'content-type': 'application/json',
                    'connect-protocol-version': '1', 'x-cursor-client-type': 'sand',
                    'x-cursor-client-version': env.get('SAND_CLIENT_VERSION', '0.20.0'),
                    'x-sand-box-namespace': env.get('SAND_BOX_NAMESPACE', 'prod')}, {})
                url = result.get('gatewayUrl', result.get('gateway_url'))
                token = result.get('gatewayToken', result.get('gateway_token'))
                extra = result.get('gatewayHeaders', result.get('gateway_headers', {}))
                if not extra:
                    network = result.get('anyrunNetworkToken', result.get('anyrun_network_token', result.get('networkToken')))
                    if network:
                        extra = {'x-anyrun-network-token': network}
            try:
                extra = {**extra, **json.loads(env.get('GROK_BOT_GATEWAY_HEADERS', '{}'))}
                if not isinstance(token, str) or not token or not isinstance(url, str):
                    raise ValueError()
                headers = {str(k).lower(): str(v) for k, v in extra.items()}
                headers.update({'authorization': 'Bearer ' + token, 'content-type': 'application/json'})
                self._session = (validate_url(url), headers)
            except (TypeError, ValueError):
                raise GatewayError('Grok Bot 网关会话格式不兼容。') from None
            self.connections += 1
            return self._session

    async def call(self, method, body):
        if method not in {'listAgents', 'sendPrompt', 'getAgentTranscriptTail'}:
            raise GatewayError('不支持的 Grok Bot 请求。')
        for attempt in range(2):
            try:
                session = await self.connect()
            except GatewayError as error:
                logging.getLogger(__name__).warning('Grok gateway failed method=EnsureSandBox status=%s reason=%s', error.status, error.reason)
                raise
            url, headers = session
            try:
                return await self._post(url + '/api/' + method, headers, body, method=method)
            except GatewayError as error:
                logging.getLogger(__name__).warning('Grok gateway failed method=%s status=%s reason=%s', method, error.status, error.reason)
                rejected = error.status in (401, 403, 417)
                transient_list_failure = method == 'listAgents' and (
                    error.status in (502, 503, 504) or error.reason in {
                        'connect_timeout', 'read_timeout', 'write_timeout',
                        'connect_error', 'read_error', 'write_error', 'protocol_error'})
                # An older concurrent failure must not invalidate a newer session.
                if rejected and self._session is session:
                    self._session = None
                # Only read operations may be replayed. Even HTTP 417 cannot
                # establish whether a send was accepted by the remote service.
                if method == 'sendPrompt' or attempt or not (rejected or transient_list_failure):
                    raise
                await asyncio.sleep(.25)

    async def close(self):
        self._session = None
        await self._http.aclose()
