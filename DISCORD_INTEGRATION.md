# Discord notifications and reported DeathLink counts

The local deployment observes **authenticated DeathLink Bounce packets** after
normal routing, covering every WebHost room without a bot occupying player slots.
`mwgg_discord.py` uses Python's standard library only. An existing standalone
tracker (freyacodes/apbot) was considered, but room-by-room client configuration
is not adequate for this all-room hosting integration.

## Behaviour

- Each origin report increments the authenticated sender's room/team/slot count.
  Payload `source` is not trusted for attribution; its original `cause` is retained
  in the Discord notification (truncated only for Discord's embed limit).
- `(room, team, slot, DeathLink timestamp)` deduplicates repeated reports,
  including across process restarts. Different slots dying simultaneously count
  independently. It counts reports, not unobservable deaths in recipient games,
  non-DeathLink deaths, or deaths hidden by game-specific amnesty settings.
- `!deaths` in an Archipelago client lists its room/team leaderboard. Counts begin
  when this integration was enabled; historical deaths are not reconstructed.
- Counts and outbox insertion commit in one independent SQLite transaction at
  `mwgg-data/discord-events.sqlite3`; no change to the game database schema.
- A single `discord-worker` container delivers queued messages and retries
  outages/rate limits in order. Mentions are disabled. Webhook requests never run
  on the game-server loop. Observer errors are logged without breaking routing.
- Successful delivery is marked after Discord acknowledges it. As with most
  webhook queues, a crash/lost response after Discord accepts a request can cause
  a repeated **message**; it cannot increment the death count again.
- The worker polls committed Seed, Room and LobbyPlayer metadata read-only.
  Existing records are baselined once, without historical spam. Later records
  produce Seed Generated, purple 🌌 Room created and teal 👋 Adventurer joined embeds. Very short-lived
  records deleted between polls cannot be observed. No spoiler data is read.

## Deployment

`../compose.yaml` mounts the three modified Python files read-only into the
existing image and runs the worker with the same image. No image rebuild or
unrelated source changes are required. The webhook lives in
`../discord-webhook.secret` (0600), mounted read-only **only into the worker**;
never commit or print it. Both services restart unless stopped.

Commands from the deployment directory:

```sh
docker compose ps
docker compose logs --tail 30 discord-worker
docker compose up -d --no-build multiworldgg discord-worker
```

Inspect queue status without exposing credentials:

```sql
SELECT count(*) FROM deaths;
SELECT count(*) FROM outbox WHERE sent IS NULL;
SELECT id,attempts,last_status FROM outbox WHERE sent IS NULL;
```

The pre-change compose file is `../compose.yaml.pre-discord-20261121`.
To roll back, stop/remove only `discord-worker`, remove only the three Python
bind mounts and worker service from the current compose (or restore the backup
if no later config changes exist), and recreate `multiworldgg`. Keep the event
DB and secret recoverable. Do not reset counts to troubleshoot delivery.

## Verification

10 isolated tests cover persistence, concurrent duplicate reports, attribution,
invalid payloads, cause text, safe mentions, lifecycle baselining, retry handling,
authentication, and actual server command routing including observer failure.
The 36 existing BounceTarget routing tests also pass. Tests run in an isolated
container with no network; production counts are not used for synthetic deaths.

Deployment verified 2026-11-21: local HTTP and public HTTPS both returned 200,
the web container reported healthy, and the running worker delivered one labelled
non-death test notification (Discord HTTP 200), independently read back in the
archipelago channel. The deployed WebHostContext observer was exercised against
an isolated temporary database, including duplicate suppression. Production
death count remained zero. The initial startup probes briefly returned 000/502
while the web service loaded, then recovered to 200. No real player death was
induced for testing.

## Goal announcements (2026-11-21)

The WebHost status-change hook queues a gold 🏆 Goal achieved! card containing
player, team, room link and reported DeathLink count. These are tracked reports,
not all in-game deaths. Writes run off the game loop and observer errors are
logged without interrupting gameplay. A persistent room/team/slot key suppresses
repeated completion reports, including across reconnects and restarts. Existing
completed saves are not replayed. Room/join baselining and disabled mentions are
unchanged. No real goal or death was induced for verification.

12 isolated bridge/routing tests pass, including the actual WebHost status hook,
non-goal filtering, goal deduplication, team isolation and tracked death counts.
