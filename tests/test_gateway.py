import asyncio
import json

import httpx
import pytest

from gateway import Gateway, GatewayError, validate_url


def test_gateway_reuses_session_and_keeps_raw_request_identity():
    calls = []
    def handler(request):
        calls.append(request)
        if 'EnsureSandBox' in request.url.path:
            return httpx.Response(200, json={'gatewayUrl': 'https://test.cursorvm.com',
                'gatewayToken': 'private', 'anyrunNetworkToken': 'network-private'})
        assert request.headers['authorization'] == 'Bearer private'
        return httpx.Response(200, json={'entries': [{'id': 'u1', 'requestId': 'request-1'}]})
    async def scenario():
        client = Gateway(environment=lambda: {'CURSOR_ACCESS_TOKEN': 'access-private'},
                         transport=httpx.MockTransport(handler))
        a = await client.call('getAgentTranscriptTail', {'id': 'b'})
        b = await client.call('getAgentTranscriptTail', {'id': 'b'})
        assert a == b == {'entries': [{'id': 'u1', 'requestId': 'request-1'}]}
        assert sum('EnsureSandBox' in r.url.path for r in calls) == 1
        await client.close()
    asyncio.run(scenario())


def test_ambiguous_send_is_not_retried_or_leaked():
    count = 0
    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(503, text='private-token')
    async def scenario():
        client = Gateway(environment=lambda: {'GROK_BOT_GATEWAY_URL': 'https://test.cursorvm.com',
            'GROK_BOT_GATEWAY_TOKEN': 'private'}, transport=httpx.MockTransport(handler))
        with pytest.raises(GatewayError) as caught:
            await client.call('sendPrompt', {'prompt': 'one'})
        assert 'private-token' not in str(caught.value)
        assert count == 1
        await client.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('failure', [502, 503, 504, 'read_timeout', 'connect_error'])
@pytest.mark.parametrize('recovers', [True, False])
def test_bot_list_recovers_once_from_transient_failures_without_reauth(failure, recovers):
    handshakes, reads = 0, 0
    def handler(request):
        nonlocal handshakes, reads
        if 'EnsureSandBox' in request.url.path:
            handshakes += 1
            return httpx.Response(200, json={'gatewayUrl': 'https://test.cursorvm.com',
                'gatewayToken': 'private'})
        reads += 1
        if recovers and reads == 2:
            return httpx.Response(200, json={'agents': [{'id': 'b1', 'name': 'Bot'}]})
        if failure == 'read_timeout':
            raise httpx.ReadTimeout('private detail', request=request)
        if failure == 'connect_error':
            raise httpx.ConnectError('private detail', request=request)
        return httpx.Response(failure, text='private detail')
    async def scenario():
        client = Gateway(environment=lambda: {'CURSOR_ACCESS_TOKEN': 'access'},
                         transport=httpx.MockTransport(handler))
        try:
            if recovers:
                result = await client.call('listAgents', {})
                assert result['agents'][0]['id'] == 'b1'
            else:
                with pytest.raises(GatewayError) as caught:
                    await client.call('listAgents', {})
                assert '登录' not in str(caught.value)
                assert 'private detail' not in str(caught.value)
            assert reads == 2
            assert handshakes == 1
        finally:
            await client.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('url', ['http://test.cursorvm.com', 'https://test.cursorvm.com.evil.test',
                                 'https://user:secret@test.cursorvm.com', 'https://127.0.0.1'])
def test_rejects_untrusted_gateway(url):
    with pytest.raises(GatewayError):
        validate_url(url)


@pytest.mark.parametrize('status', [401, 403, 417])
def test_read_reconnects_once_after_rejected_session(status):
    handshakes, reads = 0, 0
    def handler(request):
        nonlocal handshakes, reads
        if 'EnsureSandBox' in request.url.path:
            handshakes += 1
            return httpx.Response(200, json={'gatewayUrl': 'https://test.cursorvm.com',
                'gatewayToken': str(handshakes)})
        reads += 1
        return httpx.Response(status, text='private-detail') if reads == 1 else httpx.Response(200, json={'agents': []})
    async def scenario():
        client = Gateway(environment=lambda: {'CURSOR_ACCESS_TOKEN': 'access'}, transport=httpx.MockTransport(handler))
        assert await client.call('listAgents', {}) == {'agents': []}
        assert handshakes == reads == 2
        await client.close()
    asyncio.run(scenario())


def test_417_send_never_retries_but_next_read_gets_new_session():
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if 'EnsureSandBox' in request.url.path:
            return httpx.Response(200, json={'gatewayUrl': 'https://test.cursorvm.com', 'gatewayToken': 'private'})
        if request.url.path.endswith('sendPrompt'):
            return httpx.Response(417, text='private-detail')
        return httpx.Response(200, json={'agents': []})
    async def scenario():
        client = Gateway(environment=lambda: {'CURSOR_ACCESS_TOKEN': 'access'}, transport=httpx.MockTransport(handler))
        with pytest.raises(GatewayError, match='勿自动重发'):
            await client.call('sendPrompt', {'prompt': 'one'})
        await client.call('listAgents', {})
        assert calls.count('/api/sendPrompt') == 1
        assert sum('EnsureSandBox' in p for p in calls) == 2
        await client.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('handshake_failure', [False, True])
def test_read_or_handshake_failure_has_accurate_message_and_bounded_recovery(handshake_failure):
    count = 0
    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(417, text='private-detail')
    async def scenario():
        env = {'CURSOR_ACCESS_TOKEN': 'access'} if handshake_failure else {
            'GROK_BOT_GATEWAY_URL': 'https://test.cursorvm.com', 'GROK_BOT_GATEWAY_TOKEN': 'private'}
        client = Gateway(environment=lambda: env, transport=httpx.MockTransport(handler))
        with pytest.raises(GatewayError) as caught:
            await client.call('sendPrompt' if handshake_failure else 'listAgents', {})
        assert '可能已经送达' not in str(caught.value)
        assert 'private-detail' not in str(caught.value)
        assert count == (1 if handshake_failure else 2)
        await client.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('failure,reason', [(httpx.ReadTimeout, 'read_timeout'),
    (httpx.ConnectTimeout, 'connect_timeout'), (httpx.RemoteProtocolError, 'protocol_error')])
def test_send_transport_failure_exposes_only_safe_category(failure, reason, caplog):
    def handler(request):
        raise failure('private-token and private-prompt', request=request)
    async def scenario():
        client = Gateway(environment=lambda: {'GROK_BOT_GATEWAY_URL': 'https://test.cursorvm.com',
            'GROK_BOT_GATEWAY_TOKEN': 'private'}, transport=httpx.MockTransport(handler))
        with pytest.raises(GatewayError) as caught:
            await client.call('sendPrompt', {'prompt': 'one'})
        assert caught.value.delivery_unknown is True
        assert caught.value.reason == reason
        assert 'private-token' not in str(caught.value)
        assert reason in caplog.text
        assert 'private-token' not in caplog.text
        await client.close()
    asyncio.run(scenario())
