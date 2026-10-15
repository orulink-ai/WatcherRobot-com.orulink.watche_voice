import asyncio

import pytest

import gbot

from agent import GrokAgent
from gateway import GatewayError


class FakeGateway:
    def __init__(self):
        self.sends = 0
        self.reads = 0

    async def call(self, method, body):
        if method == 'sendPrompt':
            self.sends += 1
            self.prompt = body['prompt']
            return {'accepted': True}
        self.reads += 1
        entries = [{'id': 'u1', 'kind': 'message', 'role': 'user',
                    'requestId': 'r1', 'content': self.prompt},
                   {'id': 'a1', 'kind': 'send-message', 'requestId': 'r1',
                    'message': {'content': '我去查一下'}}]
        if self.reads >= 3:
            entries += [{'id': 'foreign', 'kind': 'send-message', 'requestId': 'other',
                         'message': {'content': '别的任务'}},
                        {'id': 'a2', 'kind': 'send-message', 'requestId': 'r1',
                         'message': {'content': '这是查询结果'}}]
        return {'entries': entries}

    async def close(self):
        pass


def test_later_result_delivered_once_without_foreign_reply_or_resend():
    async def scenario():
        gateway = FakeGateway()
        agent = GrokAgent(gateway, poll_interval=.005, followup_seconds=.06)
        later = []
        try:
            assert await agent.ask('b1', '查资料', on_late=later.append) == '我去查一下'
            await asyncio.sleep(.09)
            assert later == ['这是查询结果']
            assert gateway.sends == 1
        finally:
            await agent.close()
    asyncio.run(scenario())


def test_stop_monitoring_discards_late_result():
    async def scenario():
        agent = GrokAgent(FakeGateway(), poll_interval=.02, followup_seconds=.5)
        later = []
        await agent.ask('b1', '查资料', on_late=later.append)
        await agent.cancel_pending()
        await asyncio.sleep(.08)
        assert later == []
        await agent.close()
    asyncio.run(scenario())


def test_followup_monitors_share_one_bot_snapshot_per_poll_interval():
    async def scenario():
        gateway = FakeGateway()
        gateway.prompt = 'test'
        agent = GrokAgent(gateway, poll_interval=1)
        try:
            await asyncio.gather(*(agent._entries('b1') for _ in range(5)))
            assert gateway.reads == 1
        finally:
            await agent.close()
    asyncio.run(scenario())


def test_temporary_read_failure_retries_read_without_sending_again():
    class FlakyGateway(FakeGateway):
        failed = False
        async def call(self, method, body):
            if method == 'getAgentTranscriptTail' and not self.failed:
                self.failed = True
                raise GatewayError('temporary', status=503)
            return await super().call(method, body)
    async def scenario():
        gateway = FlakyGateway()
        agent = GrokAgent(gateway, poll_interval=.001)
        try:
            assert await agent.ask('b1', '查资料') == '我去查一下'
            assert gateway.sends == 1
        finally:
            await agent.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('status', [None, 417, 503])
def test_ambiguous_send_recovers_by_unique_read_without_resending(status):
    class LostReceipt(FakeGateway):
        async def call(self, method, body):
            if method == 'sendPrompt':
                await super().call(method, body)
                raise GatewayError('unknown delivery', status=status, delivery_unknown=True)
            return await super().call(method, body)
    async def scenario():
        gateway = LostReceipt()
        agent = GrokAgent(gateway, poll_interval=.001, confirmation_seconds=.05)
        try:
            assert await agent.ask('b1', '查资料') == '我去查一下'
            assert gateway.sends == 1
        finally:
            await agent.close()
    asyncio.run(scenario())


def test_unknown_send_ignores_foreign_reply_and_times_out_without_resend():
    class Unconfirmed(FakeGateway):
        async def call(self, method, body):
            if method == 'sendPrompt':
                self.sends += 1
                raise GatewayError('unknown delivery', delivery_unknown=True)
            self.reads += 1
            return {'entries': [{'id': 'old-user', 'kind': 'message', 'role': 'user',
                'requestId': 'other', 'content': '[gbot msg=old corr=old hop=0 from=watche@application]\n查资料'},
                {'id': 'foreign', 'kind': 'send-message',
                'requestId': 'other', 'message': {'content': '不可误用'}}]}
    async def scenario():
        gateway = Unconfirmed()
        agent = GrokAgent(gateway, poll_interval=.001, confirmation_seconds=.02)
        try:
            with pytest.raises(gbot.GbotError, match='送达状态仍未确认'):
                await agent.ask('b1', '查资料')
            assert gateway.sends == 1
            assert gateway.reads > 0
        finally:
            await agent.close()
    asyncio.run(scenario())


def test_handshake_failure_is_not_mistaken_for_uncertain_send():
    class NotSent(FakeGateway):
        async def call(self, method, body):
            if method == 'sendPrompt':
                raise GatewayError('handshake failed')
            raise AssertionError('must not reconcile a message that was not sent')
    async def scenario():
        agent = GrokAgent(NotSent())
        with pytest.raises(gbot.GbotError, match='handshake failed'):
            await agent.ask('b1', '查资料')
        await agent.close()
    asyncio.run(scenario())
