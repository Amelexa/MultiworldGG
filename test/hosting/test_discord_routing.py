import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from MultiServer import process_client_cmd


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    def setup_server(self):
        sender = SimpleNamespace(auth=True, team=0, slot=1, tags={'DeathLink'})
        recipient = SimpleNamespace(auth=True, team=0, slot=2, tags={'DeathLink'})
        unrelated = SimpleNamespace(auth=True, team=1, slot=3, tags={'DeathLink'})
        ctx = SimpleNamespace(endpoints=[sender, recipient, unrelated], dumper=json.dumps,
                              send_encoded_msgs=AsyncMock(), send_msgs=AsyncMock(),
                              record_deathlink=AsyncMock(), logger=Mock())
        return ctx, sender, recipient

    async def test_one_observation_per_bounce_not_recipient(self):
        ctx, sender, recipient = self.setup_server()
        data = dict(time=1, source='Alice', cause='A funny demise')
        await process_client_cmd(ctx, sender, dict(cmd='Bounce', tags=['DeathLink'], data=data))
        self.assertEqual(ctx.send_encoded_msgs.await_count, 2)
        self.assertEqual(ctx.send_encoded_msgs.await_args_list[1].args[0], recipient)
        ctx.record_deathlink.assert_awaited_once_with(sender, data)

    async def test_observer_failure_does_not_break_routing(self):
        ctx, sender, _ = self.setup_server()
        ctx.record_deathlink.side_effect = OSError('database unavailable')
        await process_client_cmd(ctx, sender, dict(cmd='Bounce', tags=['DeathLink'], data={}))
        self.assertEqual(ctx.send_encoded_msgs.await_count, 2)
        ctx.logger.exception.assert_called_once()

    async def test_non_deathlink_invalid_and_unauthenticated_not_counted(self):
        for args, auth in [(dict(cmd='Bounce', tags=['TrapLink']), True),
                           (dict(cmd='Bounce', tags=['DeathLink'], operator='invalid'), True),
                           (dict(cmd='Bounce', tags='DeathLink'), True),
                           (dict(cmd='Bounce', tags=['DeathLink']), False)]:
            ctx, sender, _ = self.setup_server()
            sender.auth = auth
            await process_client_cmd(ctx, sender, args)
            ctx.record_deathlink.assert_not_called()
