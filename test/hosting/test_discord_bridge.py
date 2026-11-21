"""Isolated bridge tests: no live webhook and no production game traffic."""
import io
import json
import sqlite3
import tempfile
import unittest
import urllib.error
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from mwgg_discord import Bridge


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / 'events.sqlite')
        self.bridge = Bridge(self.path)
        self.room = str(uuid.uuid4())

    def death(self, stamp=1, **kwargs):
        return self.bridge.record_death(self.room, 0, 1, 'Alice',
                                       dict(time=stamp, source='Alice', cause='Alice fell into the abyss!') | kwargs)

    def queued(self):
        with self.bridge.connect() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT payload FROM outbox')]

    def test_dedupe_persistence_message_and_counts(self):
        self.assertTrue(self.death())
        self.assertFalse(self.death())
        self.bridge = Bridge(self.path)
        self.assertFalse(self.death())
        self.assertTrue(self.death(2))
        self.assertEqual(self.bridge.leaderboard(self.room, 0), [(1, 'Alice', 2)])
        payload = self.queued()[-1]
        self.assertIn('Alice fell into the abyss!', payload['embeds'][0]['description'])
        self.assertIn('**2** in this room', payload['embeds'][0]['description'])
        self.assertEqual(payload['allowed_mentions'], {'parse': []})

    def test_goal_persistence_counts_and_team_scope(self):
        self.death()
        self.assertTrue(self.bridge.record_goal(self.room, 0, 1, 'Alice'))
        self.assertFalse(Bridge(self.path).record_goal(self.room, 0, 1, 'Alice'))
        payload = self.queued()[-1]
        self.assertEqual(payload['embeds'][0]['title'], '🏆 Goal achieved!')
        self.assertIn('**1** reported DeathLink deaths', payload['embeds'][0]['description'])
        self.assertTrue(self.bridge.record_goal(self.room, 1, 1, '@everyone'))
        self.assertEqual(self.queued()[-1]['allowed_mentions'], {'parse': []})
        self.assertIn('**0** reported DeathLink deaths', self.queued()[-1]['embeds'][0]['description'])

    def test_concurrent_duplicates(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.death(), range(20)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(len(self.queued()), 1)

    def test_authenticated_slot_not_source_determines_player(self):
        self.death(source='Bob')
        self.bridge.record_death(self.room, 0, 2, 'Bob', dict(time=1, source='Alice', cause='Oops'))
        self.assertEqual(len(self.bridge.leaderboard(self.room, 0)), 2)
        self.assertEqual(self.bridge.leaderboard(self.room, 1), [])
        self.assertEqual(self.bridge.leaderboard(str(uuid.uuid4()), 0), [])

    def test_invalid_payloads_and_long_message(self):
        for data in (None, [], {}, dict(time=True, source='a'), dict(time=float('nan'), source='a'),
                     dict(time=float('inf'), source='a'), dict(time=1, source=[]), dict(time=1, source='a', cause=[])):
            self.assertFalse(self.bridge.record_death(self.room, 0, 1, 'Alice', data))
        self.death(cause='@everyone ' + 'x' * 6000)
        self.assertLessEqual(len(self.queued()[0]['embeds'][0]['description']), 4096)

    def test_delivery_success_does_not_resend(self):
        self.death()
        calls = []
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
        def opener(request, timeout):
            calls.append(json.loads(request.data))
            return Response()
        self.assertTrue(self.bridge.deliver_one('https://example.invalid/test', opener, now=10))
        self.assertFalse(self.bridge.deliver_one('https://example.invalid/test', opener, now=11))
        self.assertEqual(len(calls), 1)

    def test_rate_limit_and_network_retry(self):
        self.death()
        def limited(*args, **kwargs):
            raise urllib.error.HTTPError('redacted', 429, 'limited', {}, io.BytesIO(b'{"retry_after": 30}'))
        self.assertFalse(self.bridge.deliver_one('https://example.invalid/test', limited, now=10))
        with self.bridge.connect() as db:
            self.assertEqual(db.execute('SELECT attempts,due,sent FROM outbox').fetchone(), (1, 40.0, None))
        def failed(*args, **kwargs): raise OSError('offline')
        self.assertFalse(self.bridge.deliver_one('https://example.invalid/test', failed, now=41))
        self.assertEqual(self.bridge.leaderboard(self.room, 0)[0][2], 1)

    def test_lifecycle_baseline_restart_and_new_records(self):
        path = str(Path(self.tmp.name) / 'source.sqlite')
        sid, rid, lid = [uuid.uuid4().bytes for _ in range(3)]
        with sqlite3.connect(path) as db:
            db.executescript('CREATE TABLE Seed(id BLOB); CREATE TABLE Room(id BLOB,seed BLOB); '
                             'CREATE TABLE LobbyPlayer(id INTEGER,lobby BLOB,player_name TEXT);')
            db.execute('INSERT INTO Seed VALUES (?)', (sid,))
        self.bridge.poll_lifecycle(path)
        self.assertEqual(self.queued(), [])
        with sqlite3.connect(path) as db:
            db.execute('INSERT INTO Room VALUES (?,?)', (rid,sid))
            db.execute('INSERT INTO LobbyPlayer VALUES (1,?,?)', (lid,'@everyone'))
        self.bridge.poll_lifecycle(path)
        self.assertEqual([p['embeds'][0]['title'] for p in self.queued()], ['🌌 Room created','👋 Adventurer joined'])
        Bridge(self.path).poll_lifecycle(path)
        self.assertEqual(len(self.queued()), 2)


if __name__ == '__main__':
    unittest.main()
