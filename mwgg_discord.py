"""Durable DeathLink statistics and Discord delivery, without changing bounce routing.

Run one worker with `python mwgg_discord.py`. Game host processes only write the
local SQLite outbox; no network request ever runs on the game server event loop.
"""
import base64
import json
import logging
import math
import os
import sqlite3
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path

LOG = logging.getLogger('mwgg_discord')


def short_id(value):
    if isinstance(value, bytes):
        value = uuid.UUID(bytes=value)
    else:
        value = uuid.UUID(str(value))
    return base64.urlsafe_b64encode(value.bytes).decode().rstrip('=')


def safe_name(value):
    return str(value).replace('\\', '\\\\').replace('*', '\\*').replace('_', '\\_').replace('`', '\\`')[:200]


class Bridge:
    def __init__(self, path=None, base_url=None):
        self.path = path or os.environ.get('MWGG_DISCORD_DB', '/app/data/discord-events.sqlite3')
        self.base_url = (base_url or os.environ.get('MWGG_PUBLIC_URL', 'https://mgg.amelix.xyz')).rstrip('/')
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS deaths (
                    room TEXT NOT NULL, team INTEGER NOT NULL, slot INTEGER NOT NULL,
                    stamp REAL NOT NULL, name TEXT NOT NULL, cause TEXT NOT NULL,
                    PRIMARY KEY(room, team, slot, stamp));
                CREATE TABLE IF NOT EXISTS outbox (
                    id INTEGER PRIMARY KEY, payload TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    due REAL NOT NULL DEFAULT 0, sent REAL, last_status TEXT);
                CREATE TABLE IF NOT EXISTS seen (kind TEXT, id TEXT, PRIMARY KEY(kind,id));
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def payload(self, title, description, route=None, colour=0x808080):
        embed = {'title': title, 'description': description[:4096], 'color': colour}
        if route:
            embed['url'] = self.base_url + route
        return {'username': 'Archipelago', 'allowed_mentions': {'parse': []}, 'embeds': [embed]}

    @staticmethod
    def enqueue(db, payload):
        db.execute('INSERT INTO outbox(payload) VALUES (?)', (json.dumps(payload),))

    def record_death(self, room, team, slot, name, data):
        if not isinstance(data, dict):
            return False
        stamp, source, cause = data.get('time'), data.get('source'), data.get('cause', '')
        if (type(stamp) not in (int, float) or not math.isfinite(stamp)
                or not isinstance(source, str) or not isinstance(cause, str)):
            return False
        room = str(room)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            result = db.execute('INSERT OR IGNORE INTO deaths VALUES (?,?,?,?,?,?)',
                                (room, team, slot, stamp, str(name), cause))
            if not result.rowcount:
                return False
            player = db.execute('SELECT count(*) FROM deaths WHERE room=? AND team=? AND slot=?',
                                (room, team, slot)).fetchone()[0]
            total = db.execute('SELECT count(*) FROM deaths WHERE room=?', (room,)).fetchone()[0]
            text = cause[:3000] or f'{safe_name(source)} died.'
            description = (f'**{safe_name(name)}** · Team {team + 1}\n{text}\n\n'
                           f'Reported deaths: **{player}** for this player · **{total}** in this room')
            self.enqueue(db, self.payload('💀 DeathLink', description, '/room/' + short_id(room), 0xE74C3C))
        return True

    def record_goal(self, room, team, slot, name):
        room = str(room)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            key = f'{room}:{team}:{slot}'
            if not db.execute("INSERT OR IGNORE INTO seen VALUES ('goal',?)", (key,)).rowcount:
                return False
            deaths = db.execute('SELECT count(*) FROM deaths WHERE room=? AND team=? AND slot=?',
                                (room, team, slot)).fetchone()[0]
            description = (f'**{safe_name(name)}** has completed their goal!\n'
                           f'Team **{team + 1}** · **{deaths}** reported DeathLink deaths')
            self.enqueue(db, self.payload('Goal achieved', description,
                                         '/room/' + short_id(room), 0xF1C40F))
        return True

    def leaderboard(self, room, team):
        with self.connect() as db:
            return db.execute('SELECT slot, max(name), count(*) FROM deaths WHERE room=? AND team=? '
                              'GROUP BY slot ORDER BY count(*) DESC,slot', (str(room), team)).fetchall()

    def poll_lifecycle(self, source_path):
        # Only read committed metadata; never inspect spoiler or multidata content.
        with sqlite3.connect(Path(source_path).resolve().as_uri() + '?mode=ro', uri=True, timeout=10) as source:
            rows = []
            for ident, in source.execute('SELECT id FROM Seed'):
                sid = short_id(ident)
                rows.append(('seed', sid, self.payload('Seed Generated', f'Seed **{sid}** is ready.', '/seed/' + sid, 0x00FF00)))
            for ident, seed in source.execute('SELECT id,seed FROM Room'):
                rid, sid = short_id(ident), short_id(seed)
                rows.append(('room', rid, self.payload('Room created', f'Room: **{rid}**\nSeed: **{sid}**', '/room/' + rid, 0x9900FF)))
            for ident, lobby, name in source.execute('SELECT id,lobby,player_name FROM LobbyPlayer'):
                rows.append(('join', str(ident), self.payload('Lobby created', f'**{safe_name(name)}** joined the lobby.', '/lobby/' + short_id(lobby), 0x1ABC9C)))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            initial = not db.execute("SELECT 1 FROM settings WHERE key='baseline'").fetchone()
            for kind, ident, payload in rows:
                added = db.execute('INSERT OR IGNORE INTO seen VALUES (?,?)', (kind, ident)).rowcount
                if added and not initial:
                    self.enqueue(db, payload)
            db.execute("INSERT OR IGNORE INTO settings VALUES ('baseline','1')")

    def deliver_one(self, webhook, opener=urllib.request.urlopen, now=None):
        now = time.time() if now is None else now
        with self.connect() as db:
            row = db.execute('SELECT id,payload,attempts,due FROM outbox WHERE sent IS NULL ORDER BY id LIMIT 1', ()).fetchone()
        if row is None or row[3] > now:
            return False
        ident, payload, attempts, _ = row
        request = urllib.request.Request(webhook + ('&' if '?' in webhook else '?') + 'wait=true',
                                         data=payload.encode(), headers={'Content-Type': 'application/json',
                                         'User-Agent': 'MultiworldGG-Discord/1.0'}, method='POST')
        delay, status = min(300, 2 ** min(attempts + 1, 8)), 'network error'
        try:
            with opener(request, timeout=15) as response:
                status = str(response.status)
                if not 200 <= response.status < 300:
                    raise ValueError('Unexpected webhook status')
            with self.connect() as db:
                db.execute('UPDATE outbox SET sent=?,last_status=? WHERE id=?', (now, status, ident))
            return True
        except urllib.error.HTTPError as error:
            status = str(error.code)
            if error.code == 429:
                try:
                    delay = max(1, min(3600, float(json.loads(error.read()).get('retry_after', delay))))
                except (ValueError, TypeError):
                    pass
            elif error.code in (401, 403, 404):
                delay = 300
        except (OSError, ValueError):
            pass
        # Do not log exceptions containing the credential-bearing request URL.
        LOG.warning('Webhook delivery deferred: event=%s status=%s', ident, status)
        with self.connect() as db:
            db.execute('UPDATE outbox SET attempts=attempts+1,due=?,last_status=? WHERE id=?', (now + delay, status, ident))
        return False


def main():
    logging.basicConfig(level=logging.INFO)
    webhook = Path(os.environ.get('MWGG_WEBHOOK_FILE', '/run/secrets/discord-webhook')).read_text().strip()
    if not webhook.startswith('https://discord.com/api/webhooks/'):
        raise ValueError('Expected Discord webhook URL')
    bridge = Bridge()
    source = os.environ.get('MWGG_SOURCE_DB', '/app/data/ap.db3')
    bridge.poll_lifecycle(source)
    LOG.info('Discord worker ready; existing lifecycle records baselined, no historical spam')
    while True:
        try:
            bridge.poll_lifecycle(source)
            delivered = bridge.deliver_one(webhook)
        except sqlite3.Error:
            LOG.warning('Database unavailable; retrying')
            delivered = False
        time.sleep(0.6 if delivered else 2)


if __name__ == '__main__':
    main()
