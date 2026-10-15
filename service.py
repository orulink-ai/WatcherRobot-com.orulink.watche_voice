"""Half-duplex JoyInside speech with local Grok Bot reasoning.

Device speech sessions and Desktop controls remain owned by this Application.
The Daemon is used only through injected business channels and fixed management
REST. No cloud speech key or bot login credential is handled by this service.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import sys
from collections import deque
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from watcherobot.errors import WatcheRobotError

import gbot
from agent import GrokAgent


BOT_LIST_TIMEOUT = 10.


class Interrupted(Exception):
    """The active turn is obsolete; do not send or play its late result."""


class ReplyReady(Exception):
    """Finish the listening operation before playing a late task result."""


def _settings_path() -> Path:
    if os.name == 'nt':
        root = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local'))
    elif sys.platform == 'darwin':
        root = Path.home() / 'Library' / 'Application Support'
    else:
        root = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
    return root / 'WatcheRobot' / 'grok_voice' / 'settings.json'


def speech_chunks(text: str) -> list[str]:
    """Preserve Unicode text while respecting SDK's <1000 UTF-8 byte limit."""
    result = []
    remaining = text
    while remaining:
        size = 0
        end = 0
        sentence = 0
        for index, character in enumerate(remaining):
            size += len(character.encode('utf-8'))
            if size > 900:
                break
            end = index + 1
            if character in '。！？；.!?;\n':
                sentence = end
        if end < len(remaining) and sentence:
            end = sentence
        result.append(remaining[:end])
        remaining = remaining[end:]
    return result


class VoiceService:
    def __init__(self, context: Any, *, settings_path: Path | None = None,
                 ask: Callable[[str, str], Awaitable[str]] | None = None,
                 list_bots: Callable[[], Awaitable[list[dict[str, str]]]] | None = None) -> None:
        self.context = context
        self.settings_path = settings_path or _settings_path()
        self.agent = GrokAgent()
        self.ask_bot = ask or self._ask_agent
        self.fetch_bots = list_bots or self.agent.list_bots
        self._late_replies: deque[str] = deque(maxlen=80)
        self._reply_epoch = 0
        self.bot = ''
        self.bot_name = ''
        self.enabled = False
        self.muted = False
        self.phase = 'idle'
        self.connected = False
        self.error = ''
        self.speech_failure: dict[str, str] | None = None
        self.generation = 0
        self.task: asyncio.Task[None] | None = None
        self.session: Any = None
        self.cleanup_pending = False
        self.shutdown_requested = False
        self.metrics: dict[str, Any] = {'turns': 0, 'agent_response_ms': None}
        self.messages: deque[dict[str, Any]] = deque(maxlen=80)
        self._message_id = 0
        self._pending_text: str | None = None
        self._device_id: str | None = None
        self._lock = asyncio.Lock()
        self._text_task: asyncio.Task[str] | None = None
        try:
            value = json.loads(self.settings_path.read_text('utf-8'))
            if isinstance(value, dict) and isinstance(value.get('bot'), str) and isinstance(value.get('bot_name'), str):
                self.bot, self.bot_name = value['bot'][:200], value['bot_name'][:200]
        except (OSError, ValueError):
            pass

    def snapshot(self) -> dict[str, Any]:
        info = self.context.robot.device_info
        return {'enabled': self.enabled, 'phase': self.phase, 'connected': self.connected,
                'configured': bool(self.bot), 'muted': self.muted, 'error': self.error,
                'text_active': self._text_task is not None,
                'bot': self.bot, 'bot_name': self.bot_name,
                'device_id': info.get('device_id', ''), 'cleanup_pending': self.cleanup_pending,
                'speech_failure': self.speech_failure,
                'metrics': dict(self.metrics), 'messages': list(self.messages)}

    async def bots(self) -> list[dict[str, str]]:
        return await self.fetch_bots()

    async def configure(self, bot: str) -> None:
        async with self._lock:
            if self.enabled or self.cleanup_pending or (self.task and not self.task.done()) or self._text_task:
                raise RuntimeError('请先结束对话，再更换 Grok Bot。')
            if not isinstance(bot, str) or not bot.strip() or len(bot) > 200:
                raise ValueError('请选择有效的 Grok Bot。')
            choice = next((item for item in await self.bots() if item['id'] == bot), None)
            if choice is None:
                raise ValueError('所选 Grok Bot 已不可用，请刷新列表。')
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            temporary: str | None = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', delete=False,
                        dir=self.settings_path.parent, prefix='.settings-', suffix='.tmp') as stream:
                    temporary = stream.name
                    json.dump({'bot': choice['id'], 'bot_name': choice['name']}, stream, ensure_ascii=False)
                os.replace(temporary, self.settings_path)
                temporary = None
            finally:
                if temporary:
                    Path(temporary).unlink(missing_ok=True)
            self.bot, self.bot_name = choice['id'], choice['name']
            self.error = ''

    async def _refresh(self) -> None:
        try:
            await asyncio.to_thread(self.context.robot.refresh_device_info, timeout=1)
            self.connected = bool(self.context.robot.device_info.get('device_id'))
        except Exception:
            self.connected = False
        if self.enabled and (not self.connected or self.context.robot.device_info.get('device_id') != self._device_id):
            self.enabled = False
            self.generation += 1
            self.error = '机器人连接已改变，请确认原设备并重新开始对话。'
            self.phase = 'stopping'

    async def start(self) -> None:
        async with self._lock:
            if self.shutdown_requested or self.context.shutdown_requested:
                raise RuntimeError('应用正在退出。')
            if self.enabled:
                return
            if self._text_task:
                raise RuntimeError('文字诊断正在等待回复，请结束后再开始语音。')
            if self.cleanup_pending or (self.task and not self.task.done()):
                raise RuntimeError('语音资源仍在释放，请稍后重试。')
            if not self.bot:
                raise ValueError('请先选择一个 Grok Bot。')
            await self._refresh()
            if not self.connected:
                raise RuntimeError('机器人未连接，请先完成配对。')
            self._device_id = self.context.robot.device_info['device_id']
            self.error = ''
            self.muted = False
            self.enabled = True
            self.phase = 'connecting'
            self.generation += 1
            self.task = asyncio.create_task(self._conversation())

    async def stop(self) -> None:
        async with self._lock:
            self.enabled = False
            self.generation += 1
            self._late_replies.clear()
            self._reply_epoch += 1
            await self.agent.cancel_pending()
            self._pending_text = None
            if self._text_task:
                self._text_task.cancel()
                await asyncio.gather(self._text_task, return_exceptions=True)
                self._text_task = None
            task = self.task
            if task and not task.done():
                self.phase = 'stopping'
                # The worker cancels the SDK operation and waits for its real
                # thread to finish. asyncio Task.cancel alone cannot do that.
                await task
            elif self.session is not None or self.cleanup_pending:
                await self._close_session()
            self.task = None
            self.muted = False
            if self.cleanup_pending:
                raise RuntimeError(self.error or '语音资源释放尚未确认，请重试释放语音。')
            if not self.cleanup_pending:
                self.phase = 'idle'
                self.error = ''
                self.speech_failure = None

    async def mute(self, muted: bool) -> None:
        if not isinstance(muted, bool):
            raise ValueError('muted 必须是布尔值。')
        if not self.enabled:
            raise RuntimeError('请先开始对话。')
        self.muted = muted
        if muted and self.phase in {'listening', 'connecting'}:
            self.generation += 1

    async def interrupt(self) -> None:
        if not self.enabled:
            raise RuntimeError('请先开始对话。')
        self.generation += 1
        self._reply_epoch += 1
        self._late_replies.clear()
        await self.agent.cancel_pending()

    async def text(self, text: str) -> None:
        if not self.enabled:
            raise RuntimeError('请先开始对话。')
        if not isinstance(text, str) or not text.strip() or len(text) > 12000:
            raise ValueError('请输入 1 到 12000 字的对话内容。')
        if self._pending_text is not None:
            raise RuntimeError('已有文字等待处理，请稍后发送。')
        self._pending_text = text.strip()
        self.generation += 1

    async def ask_text(self, text: str) -> str:
        """Explicit standalone text diagnostic; never acquires robot speech.

        This checks the Grok Bot bridge only and is not a speech end-to-end test.
        """
        async with self._lock:
            if (self.enabled or self.cleanup_pending or self._text_task
                    or (self.task and not self.task.done()) or self.shutdown_requested):
                raise RuntimeError('请先结束当前对话，再进行文字诊断。')
            if not self.bot:
                raise ValueError('请先选择一个 Grok Bot。')
            if not isinstance(text, str) or not text.strip() or len(text) > 12000:
                raise ValueError('请输入 1 到 12000 字的对话内容。')
            generation = self.generation
            self._message('user', text.strip())
            operation = self._text_task = asyncio.create_task(self.ask_bot(self.bot, text.strip()))
        started = asyncio.get_running_loop().time()
        try:
            answer = await operation
            if self.generation != generation or self.shutdown_requested:
                raise asyncio.CancelledError()
            if not isinstance(answer, str) or not answer.strip():
                raise gbot.GbotError('Grok Bot 未返回文字回答。')
            self.metrics['agent_response_ms'] = round((asyncio.get_running_loop().time() - started) * 1000)
            self._message('assistant', answer)
            return answer
        finally:
            if self._text_task is operation:
                self._text_task = None

    async def pair(self, code: str) -> dict[str, Any]:
        async with self._lock:
            if not isinstance(code, str) or not re.fullmatch(r'[0-9]{6}', code):
                raise ValueError('请输入六位数字配对码。')
            if self.enabled or (self.task and not self.task.done()) or self._text_task:
                raise RuntimeError('请先结束对话并等待资源释放，再连接机器人。')
            url = os.environ.get('WATCHER_APP_DEVICE_STATUS_URL', '')
            parsed = urlsplit(url)
            if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost'}
                    or not parsed.port or parsed.path != '/daemon/devices' or parsed.query
                    or parsed.fragment or parsed.username or parsed.password):
                raise RuntimeError('应用未获得 Daemon 设备管理入口，请通过 SDK app run 启动。')
            try:
                async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                    response = await client.post(url + '/pair', json={'pairing_code': code, 'target_mode': 'python_sdk'})
                    response.raise_for_status()
            except httpx.HTTPError:
                raise RuntimeError('配对请求失败，请核对机器人 SDK 控制及同一局域网。') from None
            await self._refresh()
            # Acceptance is not proof of online pairing; expose actual state.
            return {'accepted': True, 'connected': self.connected}

    def shutdown(self) -> None:
        self.shutdown_requested = True
        self.enabled = False
        self.generation += 1

    def _alive(self, generation: int) -> bool:
        return (self.enabled and not self.shutdown_requested and not self.context.shutdown_requested
                and self.generation == generation)

    def _message(self, role: str, text: str) -> None:
        self._message_id += 1
        self.messages.append({'id': self._message_id, 'role': role, 'text': text[:20000]})

    async def _ask_agent(self, bot: str, text: str) -> str:
        epoch = self._reply_epoch
        def later(reply: str) -> None:
            if self._reply_epoch != epoch or self.shutdown_requested or self.bot != bot:
                return
            self._message('assistant', reply)
            if self.enabled:
                self._late_replies.append(reply)
        return await self.agent.ask(bot, text, on_late=later)

    async def _cancel_session(self) -> None:
        if self.session is not None:
            if self.context.robot.device_info.get('device_id') != self._device_id:
                raise RuntimeError('请恢复原设备连接后释放语音资源。')
            try:
                await asyncio.to_thread(self.session.cancel)
            except Exception:
                # Close is the SDK fallback that also wakes an active waiter.
                await asyncio.to_thread(self.session.close)

    async def _call(self, generation: int, function: Callable[..., Any], **kwargs: Any) -> Any:
        operation = asyncio.create_task(asyncio.to_thread(function, **kwargs))
        try:
            while not operation.done():
                if self.phase == 'listening' and self._late_replies and self._alive(generation):
                    await self._cancel_session()
                    await asyncio.gather(operation, return_exceptions=True)
                    raise ReplyReady
                if not self._alive(generation):
                    try:
                        await self._cancel_session()
                    finally:
                        await asyncio.gather(operation, return_exceptions=True)
                    raise Interrupted
                await asyncio.sleep(.02)
            if not self._alive(generation):
                await asyncio.gather(operation, return_exceptions=True)
                raise Interrupted
            result = await operation
            return result
        except asyncio.CancelledError:
            try:
                await self._cancel_session()
            finally:
                await asyncio.gather(operation, return_exceptions=True)
            raise

    async def _open_session(self, generation: int) -> None:
        # Do not lose a successful session when stop wins the opening race.
        self.cleanup_pending = True
        opening = asyncio.create_task(asyncio.to_thread(self.context.robot.speech.open))
        try:
            self.session = await asyncio.shield(opening)
        except asyncio.CancelledError:
            try:
                self.session = await opening
            finally:
                await self._close_session()
            raise
        if not self._alive(generation):
            raise Interrupted

    async def _close_session(self) -> None:
        if not self.cleanup_pending and self.session is None:
            return
        try:
            if self.context.robot.device_info.get('device_id') != self._device_id:
                raise RuntimeError('Robot identity changed')
            await asyncio.to_thread(self.session.close if self.session is not None else self.context.robot.speech.close)
        except Exception as error:
            self._record_speech_failure('close', error)
            self.error = ('机器人语音资源释放尚未确认。设备在线时请点击“重试释放语音”；'
                          '若已断开，请恢复原设备连接后重试。')
            self.enabled = False
            self.phase = 'error'
            raise RuntimeError(self.error) from None
        else:
            self.session = None
            self.cleanup_pending = False

    def _record_speech_failure(self, stage: str, error: Exception) -> None:
        # Keep bounded protocol reasons only; SDK exceptions may otherwise carry
        # device data. Never log arbitrary exception strings or a traceback.
        reason = getattr(error, 'reason', '')
        known = {'session_mismatch', 'busy', 'audio_busy', 'unsupported_command',
                 'unknown_type', 'invalid_argument', 'no_capacity', 'cancelled',
                 'not_configured', 'joyinside_not_configured', 'operation_timeout',
                 'cloud_protocol_failed', 'cloud_connection_lost', 'cloud_connect_failed',
                 'cloud_interrupt_failed', 'microphone_start_failed', 'microphone_stop_failed',
                 'microphone_read_failed', 'microphone_upload_failed'}
        if not reason and isinstance(error, WatcheRobotError) and str(error) in known:
            reason = str(error)
        if reason not in known:
            reason = 'timeout' if isinstance(error, TimeoutError) else 'unclassified'
        kind = type(error).__name__
        self.speech_failure = {'stage': stage, 'kind': kind, 'reason': reason}
        self.context.logger.warning('Speech failure stage=%s kind=%s reason=%s', stage, kind, reason)

    async def _ask(self, generation: int, text: str) -> str:
        operation = asyncio.create_task(self.ask_bot(self.bot, text))
        try:
            while not operation.done():
                if not self._alive(generation):
                    operation.cancel()
                    await asyncio.gather(operation, return_exceptions=True)
                    raise Interrupted
                await asyncio.sleep(.02)
            if not self._alive(generation):
                await asyncio.gather(operation, return_exceptions=True)
                raise Interrupted
            result = await operation
            if not isinstance(result, str) or not result.strip():
                raise gbot.GbotError('Grok Bot 未返回可以朗读的回答。')
            return result
        finally:
            if not operation.done():
                operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)

    async def _conversation(self) -> None:
        try:
            while self.enabled and not self.shutdown_requested and not self.context.shutdown_requested:
                if self.muted and self._pending_text is None:
                    self.phase = 'listening'
                    await asyncio.sleep(.05)
                    continue
                generation = self.generation
                try:
                    self.phase = 'connecting'
                    await self._open_session(generation)
                    if self._late_replies:
                        reply = self._late_replies.popleft()
                        self.phase = 'speaking'
                        for chunk in speech_chunks(reply):
                            if chunk.strip():
                                await self._call(generation, self.session.say, text=chunk, timeout=120)
                        continue
                    text = self._pending_text
                    self._pending_text = None
                    if text is None:
                        self.phase = 'listening'
                        try:
                            text = await self._call(generation, self.session.recognize, timeout=30)
                        except TimeoutError:
                            # Only silence during ASR is retryable. Bot timeout
                            # cannot retry a submitted prompt automatically.
                            continue
                        except WatcheRobotError as error:
                            if str(error) == 'operation_timeout':
                                continue
                            raise
                    if not self._alive(generation):
                        raise Interrupted
                    if not isinstance(text, str) or not text.strip():
                        continue
                    self._message('user', text)
                    self.phase = 'thinking'
                    started = asyncio.get_running_loop().time()
                    reply = await self._ask(generation, text)
                    self.metrics['agent_response_ms'] = round((asyncio.get_running_loop().time() - started) * 1000)
                    self._message('assistant', reply)
                    self.phase = 'speaking'
                    for chunk in speech_chunks(reply):
                        if chunk.strip():
                            await self._call(generation, self.session.say, text=chunk, timeout=120)
                    self.metrics['turns'] += 1
                except (Interrupted, ReplyReady):
                    pass
                except Exception as error:
                    self._record_speech_failure(self.phase, error)
                    self.enabled = False
                    self.error = (str(error) if isinstance(error, gbot.GbotError)
                                  else '对话失败，请检查机器人、JoyInside 语音与本机 Grok Bot；消息可能已经送达，请勿自动重发。')
                    self.phase = 'error'
                finally:
                    try:
                        await self._close_session()
                    except RuntimeError:
                        pass
                if self.cleanup_pending:
                    break
        finally:
            self.enabled = False
            try:
                await self._close_session()
            except RuntimeError:
                pass
            self.phase = 'error' if self.error else 'idle'

    async def run(self) -> None:
        async def warm():
            try:
                await self.agent.gateway.connect()
            except Exception:
                self.context.logger.warning('Grok Bot 连接预热未完成，将在实际请求时重新连接。')
        warming = asyncio.create_task(warm())
        try:
            while not self.shutdown_requested and not self.context.shutdown_requested:
                await self._refresh()
                await asyncio.sleep(1)
        finally:
            warming.cancel()
            await asyncio.gather(warming, return_exceptions=True)
            try:
                await self.stop()
            finally:
                await self.agent.close()

    async def desktop_controls(self) -> None:
        desktop = getattr(self.context, 'desktop', None)
        if desktop is None:
            return
        while not self.shutdown_requested and not self.context.shutdown_requested:
            try:
                frame = await desktop.receive(timeout=.25)
            except TimeoutError:
                continue
            try:
                message = json.loads(frame) if isinstance(frame, str) else None
            except ValueError:
                continue
            if isinstance(message, dict):
                try:
                    if message.get('type') == 'ctrl.microphone.open':
                        if self.enabled:
                            await self.mute(False)
                        else:
                            await self.start()
                    elif message.get('type') == 'ctrl.microphone.close':
                        await self.stop()
                except (ValueError, RuntimeError) as error:
                    self.error = str(error)


ConversationService = VoiceService


def create_web_app(service: VoiceService, web_root: Path) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware('http')
    async def local_origin(request: Request, call_next: Any) -> Any:
        origin = request.headers.get('origin')
        if (request.url.hostname not in {'127.0.0.1', 'localhost', 'testserver'}
                or (origin and origin != str(request.base_url).rstrip('/'))
                or request.headers.get('sec-fetch-site') == 'cross-site'):
            return JSONResponse({'error': '仅允许本机 Application 页面访问'}, status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'"
        return response

    @app.get('/')
    async def index() -> Any:
        return FileResponse(web_root / 'index.html')

    @app.get('/api/state')
    async def state() -> Any:
        return service.snapshot()

    @app.get('/api/bots')
    async def bots() -> Any:
        try:
            # Return a structured error before the page's 12-second deadline,
            # including the gateway's bounded read recovery in this budget.
            async with asyncio.timeout(BOT_LIST_TIMEOUT):
                return {'bots': await service.bots()}
        except TimeoutError:
            raise HTTPException(504, '读取 Grok Bot 列表超时，请稍后刷新列表。') from None
        except gbot.GbotError as error:
            raise HTTPException(503, str(error)) from None

    async def perform(action: Callable[[], Awaitable[Any]], *, return_result: bool = False) -> Any:
        try:
            result = await action()
            return result if return_result else service.snapshot()
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        except gbot.GbotError as error:
            raise HTTPException(503, str(error)) from None
        except RuntimeError as error:
            raise HTTPException(409, str(error)) from None
        except (OSError, TimeoutError):
            raise HTTPException(503, '暂时无法完成操作，请检查本机配置与连接。') from None

    async def payload(request: Request, fields: set[str]) -> dict[str, Any]:
        try:
            value = await request.json()
        except ValueError:
            raise HTTPException(400, '请输入有效 JSON。') from None
        if not isinstance(value, dict) or set(value) != fields:
            raise HTTPException(400, '请求字段不正确。')
        return value

    @app.post('/api/settings')
    async def settings(request: Request) -> Any:
        value = await payload(request, {'bot'})
        return await perform(lambda: service.configure(value['bot']))

    @app.post('/api/start')
    async def start() -> Any:
        return await perform(service.start)

    @app.post('/api/stop')
    async def stop() -> Any:
        return await perform(service.stop)

    @app.post('/api/mute')
    async def mute(request: Request) -> Any:
        value = await payload(request, {'muted'})
        return await perform(lambda: service.mute(value['muted']))

    @app.post('/api/interrupt')
    async def interrupt() -> Any:
        return await perform(service.interrupt)

    @app.post('/api/text')
    async def text(request: Request) -> Any:
        value = await payload(request, {'text'})
        return await perform(lambda: service.text(value['text']))

    @app.post('/api/ask')
    async def ask(request: Request) -> Any:
        value = await payload(request, {'text'})
        try:
            return {'reply': await perform(lambda: service.ask_text(value['text']), return_result=True)}
        except asyncio.CancelledError:
            raise HTTPException(409, '文字验证已取消；消息可能已经送达，请勿自动重发。') from None

    @app.post('/api/pair')
    async def pair(request: Request) -> Any:
        value = await payload(request, {'code'})
        return await perform(lambda: service.pair(value['code']))

    app.mount('/assets', StaticFiles(directory=web_root), name='assets')
    return app
