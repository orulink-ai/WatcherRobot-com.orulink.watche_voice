import asyncio

import pytest

import gbot


def install_cli(monkeypatch, replies):
    calls = []

    async def run(*args):
        calls.append(args)
        response = replies.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(gbot, '_run_json', run)
    monkeypatch.setattr(gbot, 'POLL_INTERVAL', 0)
    monkeypatch.setattr(gbot.uuid, 'uuid4', lambda: 'test-correlation')
    return calls


def test_list_only_returns_identity(monkeypatch):
    install_cli(monkeypatch, [[{'id': 'b1', 'name': '思考伙伴', 'description': 'private'}]])
    assert asyncio.run(gbot.list_bots()) == [{'id': 'b1', 'name': '思考伙伴'}]


def test_ask_anchors_to_send_receipt_and_ignores_non_replies(monkeypatch):
    calls = install_cli(monkeypatch, [
        {'delivery': 'accepted', 'messageId': 'sent'},
        {'gapReset': False, 'cursor': 'tool', 'transcript': {'entries': [
            {'id': 'tool', 'kind': 'tool-call', 'content': 'do not speak'}]}},
        {'gapReset': False, 'cursor': 'answer', 'transcript': {'entries': [
            {'id': 'answer', 'kind': 'send-message', 'message': {'type': 'text', 'content': '你好'}}]}},
    ])
    assert asyncio.run(gbot.ask('b1', '你好 $() 中文')) == '你好'
    assert calls[0] == ('send', '--gateway', '--no-history', '--json', '--envelope',
                        '--correlation-id', 'test-correlation', 'b1', '--', '你好 $() 中文')
    assert calls[1][calls[1].index('--after') + 1] == 'sent'
    assert calls[2][calls[2].index('--after') + 1] == 'tool'
    assert len([c for c in calls if c[0] == 'send']) == 1


def test_gap_reset_does_not_return_old_reply(monkeypatch):
    monkeypatch.setattr(gbot, 'CURSOR_GRACE', 0)
    calls = install_cli(monkeypatch, [
        {'delivery': 'accepted', 'messageId': 'sent'},
        {'gapReset': True, 'cursor': 'old', 'transcript': {'entries': [
            {'id': 'old', 'role': 'assistant', 'text': 'old private reply'}]}},
    ])
    with pytest.raises(gbot.GbotError, match='游标'):
        asyncio.run(gbot.ask('b1', 'question'))
    assert len(calls) == 2


def test_delayed_receipt_visibility_retries_same_read_not_send(monkeypatch):
    calls = install_cli(monkeypatch, [
        {'delivery': 'accepted', 'messageId': 'sent'},
        {'gapReset': True, 'cursor': 'old', 'transcript': {'entries': [
            {'id': 'old', 'role': 'assistant', 'text': 'private old answer'}]}},
        {'gapReset': False, 'cursor': 'new', 'transcript': {'entries': [
            {'id': 'new', 'role': 'assistant', 'text': 'new answer'}]}},
    ])
    assert asyncio.run(gbot.ask('b1', 'question')) == 'new answer'
    assert calls[1] == calls[2]
    assert len([call for call in calls if call[0] == 'send']) == 1


def test_unknown_delivery_never_resends(monkeypatch):
    calls = install_cli(monkeypatch, [{'delivery': 'unknown'}])
    with pytest.raises(gbot.GbotError, match='重发'):
        asyncio.run(gbot.ask('b1', 'question'))
    assert len(calls) == 1


def test_timeout_never_resends(monkeypatch):
    calls = install_cli(monkeypatch, [TimeoutError()])
    with pytest.raises(TimeoutError):
        asyncio.run(gbot.ask('b1', 'question'))
    assert len(calls) == 1


def test_multiple_bot_messages_are_joined(monkeypatch):
    install_cli(monkeypatch, [
        {'delivery': 'accepted', 'messageId': 'sent'},
        {'gapReset': False, 'cursor': 'b', 'transcript': {'entries': [
            {'id': 'a', 'role': 'assistant', 'text': '第一句'},
            {'id': 'b', 'kind': 'send-message', 'message': {'content': '第二句'}}]}},
    ])
    assert asyncio.run(gbot.ask('b1', 'question')) == '第一句\n第二句'


def test_explicit_entry_uses_node_without_shell(monkeypatch):
    monkeypatch.setenv('GBOT_ENTRY', 'C:/工具/gbot.mjs')
    monkeypatch.delenv('GBOT_BIN', raising=False)
    monkeypatch.setattr(gbot.shutil, 'which', lambda name: 'node.exe' if name == 'node' else None)
    assert gbot._command() == ['node.exe', 'C:/工具/gbot.mjs']


def test_windows_cmd_resolves_npm_entry_without_cmd_shell(monkeypatch, tmp_path):
    shim = tmp_path / 'gbot.cmd'
    shim.write_text('@echo off')
    entry = tmp_path / 'node_modules' / 'grok-bot-cli' / 'artifact' / 'bin' / 'gbot.mjs'
    entry.parent.mkdir(parents=True)
    entry.write_text('')
    monkeypatch.delenv('GBOT_ENTRY', raising=False)
    monkeypatch.setenv('GBOT_BIN', str(shim))
    monkeypatch.setattr(gbot.shutil, 'which', lambda name: 'node.exe' if name == 'node' else None)
    assert gbot._command() == ['node.exe', str(entry)]


def test_cli_errors_do_not_expose_stderr_or_inherited_sender(monkeypatch):
    monkeypatch.setattr(gbot, 'build_cli_environment', lambda environment: environment)
    observed = {}

    class Process:
        returncode = 1

        async def communicate(self):
            return b'', b'authorization=SECRET private history'

    async def spawn(*args, **kwargs):
        observed.update(kwargs)
        return Process()

    monkeypatch.setenv('CODEX_THREAD_ID', 'private-thread')
    monkeypatch.setattr(gbot, '_command', lambda: ['node', 'gbot.mjs'])
    monkeypatch.setattr(gbot.asyncio, 'create_subprocess_exec', spawn)
    with pytest.raises(gbot.GbotError) as error:
        asyncio.run(gbot._run_json('bots', 'list'))
    assert 'SECRET' not in str(error.value)
    assert 'CODEX_THREAD_ID' not in observed['env']
    assert 'shell' not in observed


def test_cli_cancellation_kills_local_process(monkeypatch):
    monkeypatch.setattr(gbot, 'build_cli_environment', lambda environment: environment)
    class Process:
        returncode = None
        killed = False
        communications = 0

        async def communicate(self):
            self.communications += 1
            if self.communications == 1:
                raise asyncio.CancelledError()
            return b'', b''

        def kill(self):
            self.killed = True

    process = Process()

    async def spawn(*args, **kwargs):
        return process

    monkeypatch.setattr(gbot, '_command', lambda: ['node', 'gbot.mjs'])
    monkeypatch.setattr(gbot.asyncio, 'create_subprocess_exec', spawn)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(gbot._run_json('send'))
    assert process.killed
    assert process.communications == 2


def test_native_login_is_confined_to_child_environment(monkeypatch):
    observed = {}
    monkeypatch.delenv('CURSOR_ACCESS_TOKEN', raising=False)
    monkeypatch.setattr(gbot, 'build_cli_environment', lambda environment:
        {**environment, 'CURSOR_ACCESS_TOKEN': 'fake-private-token'})
    monkeypatch.setattr(gbot, '_command', lambda: ['node', 'gbot.mjs'])
    class Process:
        returncode = 0
        async def communicate(self):
            return b'[]', b''
    async def spawn(*args, **kwargs):
        observed['args'] = args
        observed['env'] = kwargs['env']
        return Process()
    monkeypatch.setattr(gbot.asyncio, 'create_subprocess_exec', spawn)
    assert asyncio.run(gbot._run_json('bots', 'list')) == []
    assert observed['env']['CURSOR_ACCESS_TOKEN'] == 'fake-private-token'
    assert 'fake-private-token' not in repr(observed['args'])
    assert 'CURSOR_ACCESS_TOKEN' not in gbot.os.environ


def test_new_accepted_receipt_anchors_full_header_and_request_id(monkeypatch):
    header = '[gbot msg=envelope-1 corr=test-correlation hop=0 from=user@machine]\nquestion'
    calls = install_cli(monkeypatch, [
        {'delivery': 'unknown', 'result': {'accepted': True}, 'envelopeId': 'envelope-1'},
        {'transcript': {'entries': [
            {'id': 'old', 'kind': 'send-message', 'requestId': 'old-request', 'message': {'content': 'old private'}},
            {'id': 't1u', 'kind': 'message', 'role': 'user', 'content': header, 'requestId': 'r1'},
            {'id': 'other', 'kind': 'send-message', 'requestId': 'other-request', 'message': {'content': 'not ours'}},
            {'id': 't1s0', 'kind': 'send-message', 'requestId': 'r1', 'message': {'type': 'text', 'content': 'new answer'}},
        ]}},
    ])
    assert asyncio.run(gbot.ask('b1', 'question')) == 'new answer'
    assert '--after' not in calls[1]
    assert len([call for call in calls if call[0] == 'send']) == 1


def test_new_receipt_waits_for_anchor_without_reading_old_messages(monkeypatch):
    anchor = {'id': 't1u', 'kind': 'message', 'role': 'user', 'requestId': 'r1',
              'content': '[gbot msg=m1 corr=test-correlation hop=0 from=user@machine]\nquestion'}
    calls = install_cli(monkeypatch, [
        {'delivery': 'unknown', 'result': {'accepted': True}},
        {'transcript': {'entries': [{'id': 'old', 'role': 'assistant', 'text': 'do not return'}]}},
        {'transcript': {'entries': [anchor]}},
        {'transcript': {'entries': [anchor, {'id': 'reply', 'kind': 'send-message', 'requestId': 'r1',
                                             'message': {'content': 'correct'}}]}},
    ])
    assert asyncio.run(gbot.ask('b1', 'question')) == 'correct'
    assert len([call for call in calls if call[0] == 'send']) == 1


def test_new_receipt_rejects_duplicate_header_anchors(monkeypatch):
    content = '[gbot msg=m1 corr=test-correlation hop=0 from=user@machine]\nquestion'
    calls = install_cli(monkeypatch, [
        {'delivery': 'unknown', 'result': {'accepted': True}},
        {'transcript': {'entries': [
            {'id': 'u1', 'kind': 'message', 'role': 'user', 'requestId': 'r1', 'content': content},
            {'id': 'u2', 'kind': 'message', 'role': 'user', 'requestId': 'r2', 'content': content},
        ]}},
    ])
    with pytest.raises(gbot.GbotError, match='唯一'):
        asyncio.run(gbot.ask('b1', 'question'))
    assert len(calls) == 2


def test_new_receipt_without_anchor_times_out_without_resending(monkeypatch):
    monkeypatch.setattr(gbot, 'REPLY_TIMEOUT', 0)
    calls = install_cli(monkeypatch, [{'delivery': 'unknown', 'result': {'accepted': True}}])
    with pytest.raises(gbot.GbotError, match='超时'):
        asyncio.run(gbot.ask('b1', 'question'))
    assert len(calls) == 1


@pytest.mark.parametrize(('diagnostic', 'code', 'phrase'), [
    (b'EnsureSandBox failed: 401 secret', 'authentication', '登录'),
    (b'TimeoutError: operation timed out secret', 'timeout', '超时'),
    (b'TypeError: fetch failed secret', 'network', '网络'),
    (b'gateway failed: 503 secret', 'unavailable', '暂时不可用'),
    (b'unrecognized secret', 'unknown', '未提供可识别原因'),
])
def test_cli_failure_has_safe_reason_and_stage(monkeypatch, caplog, diagnostic, code, phrase):
    monkeypatch.setattr(gbot, 'build_cli_environment', lambda environment: environment)
    monkeypatch.setattr(gbot, '_command', lambda: ['node', 'gbot.mjs'])
    class Process:
        returncode = 1
        async def communicate(self):
            return b'', diagnostic
    async def spawn(*args, **kwargs):
        return Process()
    monkeypatch.setattr(gbot.asyncio, 'create_subprocess_exec', spawn)
    with caplog.at_level('WARNING'), pytest.raises(gbot.GbotError) as caught:
        asyncio.run(gbot._run_json('thread', 'b1'))
    assert caught.value.code == code
    assert phrase in str(caught.value)
    assert '读取回复' in str(caught.value)
    assert 'secret' not in str(caught.value) + caplog.text
    assert 'stage=thread' in caplog.text


def test_transient_reply_read_retries_without_resending(monkeypatch):
    calls = install_cli(monkeypatch, [
        {'delivery': 'accepted', 'messageId': 'sent'},
        gbot.GbotError('temporary', code='network'),
        {'gapReset': False, 'cursor': 'reply', 'transcript': {'entries': [
            {'id': 'reply', 'role': 'assistant', 'text': 'recovered'}]}},
    ])
    assert asyncio.run(gbot.ask('b1', 'question')) == 'recovered'
    assert calls[1] == calls[2]
    assert sum(call[0] == 'send' for call in calls) == 1


def test_transient_send_failure_is_not_retried(monkeypatch):
    calls = install_cli(monkeypatch, [gbot.GbotError('temporary', code='network')])
    with pytest.raises(gbot.GbotError):
        asyncio.run(gbot.ask('b1', 'question'))
    assert len(calls) == 1


def test_reply_retry_is_bounded(monkeypatch):
    calls = install_cli(monkeypatch, [
        {'delivery': 'accepted', 'messageId': 'sent'},
        gbot.GbotError('temporary', code='network'),
        gbot.GbotError('still offline', code='network'),
    ])
    with pytest.raises(gbot.GbotError, match='still offline'):
        asyncio.run(gbot.ask('b1', 'question'))
    assert len(calls) == 3
