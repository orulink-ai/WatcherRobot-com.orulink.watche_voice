"""Single-send, request-scoped Grok Bot turns with bounded follow-up delivery."""
from __future__ import annotations

import asyncio
import logging
import uuid

import gbot
from gateway import Gateway, GatewayError


class GrokAgent:
    def __init__(self, gateway=None, *, poll_interval=1., followup_seconds=300., confirmation_seconds=12.):
        self.gateway = gateway or Gateway()
        self.poll_interval = poll_interval
        self.followup_seconds = followup_seconds
        self.confirmation_seconds = confirmation_seconds
        self._monitors = set()
        self._read_lock = asyncio.Lock()
        self._pages = {}

    async def list_bots(self):
        try:
            data = await self.gateway.call('listAgents', {})
            rows = data if isinstance(data, list) else data.get('agents', [])
            return [{'id': r['id'], 'name': r['name']} for r in rows
                    if isinstance(r.get('id'), str) and isinstance(r.get('name'), str)]
        except GatewayError as error:
            raise gbot.GbotError(str(error)) from None

    async def _entries(self, bot):
        # Follow-up tasks and the active turn share a bounded snapshot, avoiding
        # one gateway poll per pending task. Never cache a send operation.
        async with self._read_lock:
            now = asyncio.get_running_loop().time()
            cached = self._pages.get(bot)
            if cached and now - cached[0] < self.poll_interval:
                return cached[1]
            for attempt in range(2):
                try:
                    page = await self.gateway.call('getAgentTranscriptTail', {'id': bot, 'limit': 200})
                    break
                except GatewayError as error:
                    if attempt or error.status not in {None, 429, 502, 503, 504}:
                        raise
                    await asyncio.sleep(self.poll_interval)
            entries = gbot._entries({'transcript': page})
            if len(self._pages) >= 16 and bot not in self._pages:
                self._pages.pop(next(iter(self._pages)))
            self._pages[bot] = (asyncio.get_running_loop().time(), entries)
            return entries

    async def _confirm_delivery(self, bot, correlation, text, envelope):
        # Lost send receipts are ambiguous. Only the exact original envelope
        # can confirm receipt; neither matching text nor a nearby reply suffices.
        try:
            async with asyncio.timeout(self.confirmation_seconds):
                while True:
                    try:
                        entries = await self._entries(bot)
                        _, request_id = gbot._correlated_replies(entries, correlation, text, envelope, None)
                        if request_id is not None:
                            return request_id
                    except GatewayError:
                        pass
                    await asyncio.sleep(self.poll_interval)
        except TimeoutError:
            return None

    async def ask(self, bot, text, *, on_late=None):
        if not isinstance(bot, str) or not bot.strip() or bot.startswith('-'):
            raise gbot.GbotError('请选择有效的 Grok Bot。')
        if not isinstance(text, str) or not text.strip() or len(text) > 12000:
            raise gbot.GbotError('请输入 1 到 12000 字的对话内容。')
        if on_late is not None and len(self._monitors) >= 64:
            raise gbot.GbotError('等待后续结果的任务过多，请结束对话后重试。')
        correlation, envelope = str(uuid.uuid4()), str(uuid.uuid4())
        prompt = f'[gbot msg={envelope} corr={correlation} hop=0 from=watche@application]\n{text.strip()}'
        request_id = None
        try:
            try:
                receipt = await self.gateway.call('sendPrompt', {
                    'agentId': bot, 'prompt': prompt, 'clientNonce': envelope})
            except GatewayError as error:
                if not error.delivery_unknown:
                    raise
                request_id = await self._confirm_delivery(bot, correlation, text, envelope)
                if request_id is None:
                    raise gbot.GbotError('发送响应异常，已只读核对，送达状态仍未确认；请查看 Grok Bot，勿自动重发。') from None
                logging.getLogger(__name__).info('Grok send receipt recovered by exact envelope; no resend')
            else:
                if not isinstance(receipt, dict) or not (receipt.get('accepted') is True or receipt.get('messageId')):
                    raise gbot.GbotError('消息送达状态未知，请先查看 Grok Bot，禁止自动重发。')
            async with asyncio.timeout(gbot.REPLY_TIMEOUT):
                while True:
                    entries = await self._entries(bot)
                    _, request_id = gbot._correlated_replies(entries, correlation, text, envelope, request_id)
                    replies = [e for e in entries if e.get('kind') == 'send-message'
                               and request_id is not None and e.get('requestId') == request_id
                               and gbot._reply_text(e)]
                    if replies:
                        answer = '\n'.join(gbot._reply_text(e) for e in replies)
                        if len(answer) > 20000:
                            raise gbot.GbotError('Grok Bot 回复过长，请在本机对话中查看。')
                        if on_late is not None:
                            task = asyncio.create_task(self._follow(bot, request_id,
                                {e['id'] for e in replies}, on_late))
                            self._monitors.add(task)
                            task.add_done_callback(self._monitors.discard)
                        return answer
                    await asyncio.sleep(self.poll_interval)
        except GatewayError as error:
            raise gbot.GbotError(str(error)) from None
        except TimeoutError:
            raise gbot.GbotError('等待 Grok Bot 回复超时；消息已经发送，请勿自动重发。') from None

    async def _follow(self, bot, request_id, seen, callback):
        deadline = asyncio.get_running_loop().time() + self.followup_seconds
        try:
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(self.poll_interval)
                entries = await self._entries(bot)
                for entry in entries:
                    if (entry.get('kind') == 'send-message' and entry.get('requestId') == request_id
                            and entry['id'] not in seen):
                        text = gbot._reply_text(entry)
                        if text:
                            if len(text) > 20000 or len(seen) >= 200:
                                raise gbot.GbotError('后续回复超出上限，请查看 Grok Bot。')
                            seen.add(entry['id'])
                            callback(text)
        except (GatewayError, gbot.GbotError):
            logging.getLogger(__name__).warning('Grok follow-up monitoring stopped; check the desktop for later results.')

    async def cancel_pending(self):
        tasks = list(self._monitors)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._pages.clear()

    async def close(self):
        await self.cancel_pending()
        await self.gateway.close()
