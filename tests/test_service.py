import asyncio
import json
import logging
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import httpx
from fastapi.testclient import TestClient

from service import VoiceService, create_web_app


class Speech:
    def __init__(self):
        self.cancelled = threading.Event()
        self.opened = threading.Event()
        self.closed = 0
        self.recognitions = 0
        self.spoken = []
        self.inputs = ['你好', '继续聊聊']

    def open(self):
        self.opened.set()
        return self

    def recognize(self, *, timeout=30):
        self.recognitions += 1
        if self.inputs:
            return self.inputs.pop(0)
        self.cancelled.wait(2)
        raise TimeoutError()

    def say(self, *, text, timeout=60):
        self.spoken.append(text)

    def cancel(self):
        self.cancelled.set()

    def close(self):
        self.closed += 1


def context(speech=None):
    speech = speech or Speech()
    return SimpleNamespace(robot=SimpleNamespace(device_info={'device_id': 'test'},
            speech=speech, refresh_device_info=lambda **kw: None),
        shutdown_requested=False, logger=logging.getLogger('test'))


async def bots():
    return [{'id': 'b1', 'name': '思考伙伴'}]


def test_bot_list_timeout_returns_actionable_error_before_browser_deadline(monkeypatch, tmp_path):
    import service as service_module
    monkeypatch.setattr(service_module, 'BOT_LIST_TIMEOUT', .01, raising=False)
    cancelled = threading.Event()
    async def stalled_bots():
        try:
            await asyncio.sleep(.05)
            return []
        finally:
            cancelled.set()
    service = VoiceService(context(), settings_path=tmp_path / 'settings.json', list_bots=stalled_bots)
    with TestClient(create_web_app(service, Path(__file__).parents[1] / 'web')) as client:
        response = client.get('/api/bots')
        assert response.status_code == 504
        assert '超时' in response.json()['detail']
        assert '登录' not in response.json()['detail']
        assert cancelled.is_set()


async def wait_until(predicate):
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(.005)
    assert predicate()


def test_later_agent_result_is_shown_and_spoken_without_another_user_turn(tmp_path):
    async def run():
        speech = Speech()
        speech.inputs = ['查资料']
        ctx = context(speech)
        service = VoiceService(ctx, settings_path=tmp_path / 'settings.json', list_bots=bots)
        async def ask(bot, text, *, on_late):
            async def later():
                await asyncio.sleep(.08)
                on_late('这是完整结果')
            asyncio.create_task(later())
            return '我去查一下'
        service.agent.ask = ask
        await service.configure('b1')
        await service.start()
        await wait_until(lambda: '这是完整结果' in speech.spoken)
        await service.stop()
        assert speech.spoken == ['我去查一下', '这是完整结果']
        assert [m['text'] for m in service.messages if m['role'] == 'assistant'] == speech.spoken
        await service.agent.close()
    asyncio.run(run())


def test_device_recognition_silence_timeout_keeps_conversation_alive(tmp_path):
    from watcherobot.errors import WatcheRobotError
    class QuietSpeech(Speech):
        def recognize(self, *, timeout=30):
            if not self.recognitions:
                self.recognitions += 1
                raise WatcheRobotError('operation_timeout')
            return super().recognize(timeout=timeout)
    async def run():
        speech = QuietSpeech()
        async def ask(bot, text):
            return '仍在听'
        service = VoiceService(context(speech), settings_path=tmp_path / 'settings.json',
                               list_bots=bots, ask=ask)
        await service.configure('b1')
        await service.start()
        await wait_until(lambda: speech.spoken or service.error)
        assert not service.error
        assert speech.spoken
        await service.stop()
        await service.agent.close()
    asyncio.run(run())


def test_default_idle_and_missing_bot_never_opens_device(tmp_path):
    async def run():
        ctx = context()
        service = VoiceService(ctx, settings_path=tmp_path / 'settings.json', list_bots=bots)
        assert not service.snapshot()['enabled']
        assert not service.snapshot()['configured']
        with pytest.raises(ValueError):
            await service.start()
        assert service.task is None
        assert not ctx.robot.speech.opened.is_set()
    asyncio.run(run())


def test_two_rounds_use_device_stt_bot_then_device_tts(tmp_path):
    async def run():
        ctx = context()
        asked = []
        async def ask(bot, text):
            asked.append((bot, text))
            return '回答：' + text
        service = VoiceService(ctx, settings_path=tmp_path / 'settings.json', list_bots=bots, ask=ask)
        await service.configure('b1')
        await service.start()
        await wait_until(lambda: service.metrics['turns'] >= 2)
        await service.stop()
        assert asked == [('b1', '你好'), ('b1', '继续聊聊')]
        assert ctx.robot.speech.spoken == ['回答：你好', '回答：继续聊聊']
        assert service.phase == 'idle'
        assert service.session is None
        assert not service.enabled
        assert ctx.robot.speech.closed >= 2
        assert service.task is None
    asyncio.run(run())


def test_stop_cancels_and_waits_for_capture_thread_before_release(tmp_path):
    async def run():
        speech = Speech()
        speech.inputs = []
        finished = threading.Event()
        original = speech.recognize
        def recognize(**kwargs):
            try:
                return original(**kwargs)
            finally:
                finished.set()
        speech.recognize = recognize
        service = VoiceService(context(speech), settings_path=tmp_path / 'settings.json', list_bots=bots)
        await service.configure('b1')
        await service.start()
        await wait_until(lambda: speech.recognitions > 0)
        await service.start()
        await service.stop()
        assert finished.is_set()
        assert speech.closed == 1
        assert service.task is None
        assert service.phase == 'idle'
    asyncio.run(run())


def test_interrupt_discards_late_bot_reply_without_tts(tmp_path):
    async def run():
        entered = asyncio.Event()
        async def ask(bot, text):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return '迟到的旧回答'
        speech = Speech()
        speech.inputs = ['你好']
        service = VoiceService(context(speech), settings_path=tmp_path / 'settings.json', list_bots=bots, ask=ask)
        await service.configure('b1')
        await service.start()
        await entered.wait()
        await service.interrupt()
        await wait_until(lambda: speech.recognitions >= 2)
        await service.stop()
        assert speech.spoken == []
        assert service.metrics['turns'] == 0
    asyncio.run(run())


def test_bot_error_stops_instead_of_resubmitting_audio(tmp_path):
    async def run():
        calls = []
        async def ask(bot, text):
            calls.append(text)
            raise RuntimeError('private-token-in-error')
        service = VoiceService(context(), settings_path=tmp_path / 'settings.json', list_bots=bots, ask=ask)
        await service.configure('b1')
        await service.start()
        await wait_until(lambda: service.phase == 'error')
        assert not service.enabled
        assert calls == ['你好']
        assert 'private-token-in-error' not in service.snapshot()['error']
        await service.stop()
    asyncio.run(run())


def test_settings_persist_only_id_name_and_cannot_change_active_bot(tmp_path):
    async def run():
        path = tmp_path / 'settings.json'
        service = VoiceService(context(), settings_path=path, list_bots=bots)
        await service.configure('b1')
        assert json.loads(path.read_text('utf-8')) == {'bot': 'b1', 'bot_name': '思考伙伴'}
        restored = VoiceService(context(), settings_path=path, list_bots=bots)
        assert restored.snapshot()['configured']
        await service.start()
        with pytest.raises(RuntimeError):
            await service.configure('b1')
        await service.stop()
    asyncio.run(run())


def test_web_api_origin_input_and_settings_contract(tmp_path):
    service = VoiceService(context(), settings_path=tmp_path / 'settings.json', list_bots=bots)
    with TestClient(create_web_app(service, Path(__file__).parents[1] / 'web')) as client:
        assert client.get('/').status_code == 200
        assert client.get('/api/bots').json()['bots'][0]['name'] == '思考伙伴'
        assert client.post('/api/settings', json={'bot': 'b1'}).status_code == 200
        assert client.get('/api/state').json()['configured']
        assert client.post('/api/settings', json={'api_key': 'secret'}).status_code == 400
        assert client.post('/api/pair', json={'code': 'invalid'}).status_code == 400
        assert client.post('/api/text', json={'text': '你好'}).status_code == 409
        assert client.post('/api/start', headers={'Origin': 'https://evil.example'}).status_code == 403
        assert client.get('/api/state', headers={'Host': 'evil.example'}).status_code == 403
        assert client.post('/api/stop', headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403
        assert client.get('/api/state').headers['Cache-Control'] == 'no-store'


def test_long_reply_respects_sdk_utf8_limit_and_preserves_every_character(tmp_path):
    async def run():
        answer = '机器人你好！' * 180
        async def ask(bot, text):
            return answer
        speech = Speech()
        speech.inputs = ['你好']
        service = VoiceService(context(speech), settings_path=tmp_path / 'settings.json', list_bots=bots, ask=ask)
        await service.configure('b1')
        await service.start()
        await wait_until(lambda: service.metrics['turns'] == 1)
        await service.stop()
        assert all(len(text.encode('utf-8')) < 1000 for text in speech.spoken)
        assert ''.join(speech.spoken) == answer
    asyncio.run(run())


def test_mute_cancels_capture_and_prevents_more_capture_until_resume(tmp_path):
    async def run():
        speech = Speech()
        speech.inputs = []
        service = VoiceService(context(speech), settings_path=tmp_path / 'settings.json', list_bots=bots)
        await service.configure('b1')
        await service.start()
        await wait_until(lambda: speech.recognitions == 1)
        await service.mute(True)
        await wait_until(lambda: service.session is None)
        await asyncio.sleep(.05)
        assert speech.recognitions == 1
        assert service.enabled
        await service.mute(False)
        await wait_until(lambda: speech.recognitions >= 2)
        await service.stop()
    asyncio.run(run())


def test_stop_during_open_closes_late_session(tmp_path):
    async def run():
        entered = threading.Event()
        release = threading.Event()
        speech = Speech()
        def open_session():
            entered.set()
            release.wait(2)
            return speech
        speech.open = open_session
        service = VoiceService(context(speech), settings_path=tmp_path / 'settings.json', list_bots=bots)
        await service.configure('b1')
        await service.start()
        await wait_until(entered.is_set)
        stopping = asyncio.create_task(service.stop())
        await asyncio.sleep(.02)
        assert not stopping.done()
        release.set()
        await stopping
        assert speech.closed == 1
        assert speech.recognitions == 0
        assert service.task is None
    asyncio.run(run())


def test_pair_uses_only_daemon_injected_management_endpoint(monkeypatch, tmp_path):
    async def run():
        requests = []
        def handle(request):
            requests.append(request)
            return httpx.Response(200, json={'device': {'state': 'connecting'}})
        original = httpx.AsyncClient
        monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs:
            original(**kwargs, transport=httpx.MockTransport(handle)))
        monkeypatch.setenv('WATCHER_APP_DEVICE_STATUS_URL', 'http://127.0.0.1:12345/daemon/devices')
        service = VoiceService(context(), settings_path=tmp_path / 'settings.json')
        result = await service.pair('123456')
        assert result['accepted']
        assert len(requests) == 1
        assert str(requests[0].url) == 'http://127.0.0.1:12345/daemon/devices/pair'
        assert json.loads(requests[0].content) == {'pairing_code': '123456', 'target_mode': 'python_sdk'}
        assert '123456' not in json.dumps(service.snapshot())
        monkeypatch.setenv('WATCHER_APP_DEVICE_STATUS_URL', 'https://evil.example/daemon/devices')
        with pytest.raises(RuntimeError):
            await service.pair('123456')
        assert len(requests) == 1
    asyncio.run(run())


def test_failed_release_blocks_restart_without_losing_session(tmp_path):
    async def run():
        speech = Speech()
        speech.inputs = []
        original = speech.close
        def close():
            raise RuntimeError('no ack')
        speech.close = close
        service = VoiceService(context(speech), settings_path=tmp_path / 'settings.json', list_bots=bots)
        await service.configure('b1')
        await service.start()
        await wait_until(lambda: speech.recognitions > 0)
        with pytest.raises(RuntimeError, match='释放'):
            await service.stop()
        assert service.cleanup_pending
        assert service.session is speech
        with pytest.raises(RuntimeError):
            await service.start()
        speech.close = original
        await service.stop()
        assert not service.cleanup_pending
        assert service.session is None
        assert service.error == ''
    asyncio.run(run())


def test_cleanup_error_preserves_safe_diagnostic(tmp_path, caplog):
    from watcherobot.errors import CommandError
    async def run():
        speech = Speech()
        def close():
            raise CommandError('ctrl.speech.close', 'session_mismatch')
        speech.close = close
        service = VoiceService(context(speech), settings_path=tmp_path / 'settings.json')
        service.session = speech
        service.cleanup_pending = True
        service._device_id = 'test'
        with pytest.raises(RuntimeError):
            await service.stop()
        assert service.snapshot()['speech_failure'] == {
            'stage': 'close', 'kind': 'CommandError', 'reason': 'session_mismatch'}
        assert 'Speech failure stage=close' in caplog.text
        assert 'session_mismatch' in caplog.text
    asyncio.run(run())


def test_standalone_bot_text_diagnostic_does_not_acquire_robot_speech(tmp_path):
    async def run():
        speech = Speech()
        async def ask(bot, text):
            assert bot == 'b1'
            return '独立文字回答'
        service = VoiceService(context(speech), settings_path=tmp_path / 'settings.json', list_bots=bots, ask=ask)
        await service.configure('b1')
        assert await service.ask_text('文字诊断') == '独立文字回答'
        assert not speech.opened.is_set()
        assert not service.enabled
        assert service.snapshot()['metrics']['agent_response_ms'] >= 0
        assert 'first_audio_ms' not in service.snapshot()['metrics']
    asyncio.run(run())


def test_standalone_text_request_cannot_race_voice_or_bot_change(tmp_path):
    async def run():
        entered = asyncio.Event()
        async def ask(bot, text):
            entered.set()
            await asyncio.Event().wait()
        service = VoiceService(context(), settings_path=tmp_path / 'settings.json', list_bots=bots, ask=ask)
        await service.configure('b1')
        diagnostic = asyncio.create_task(service.ask_text('question'))
        await entered.wait()
        with pytest.raises(RuntimeError):
            await service.start()
        with pytest.raises(RuntimeError):
            await service.configure('b1')
        await service.stop()
        with pytest.raises(asyncio.CancelledError):
            await diagnostic
    asyncio.run(run())
