# Private friends leaderboard

This free service runs on an existing PC or Raspberry Pi. It has no paid API, account, trial or external hosting requirement. Scores are intentionally self-reported, and Chuddy's own sounds may count. It is not a worldwide deployment.

From the repository root, create a room once:

```powershell
.\.venv\Scripts\python.exe -m friends.service --create-room chuddy-club
.\.venv\Scripts\python.exe -m friends.service
```

The first command prints an invitation token. Share it only with the friends in that room. Enter `http://127.0.0.1:8770`, the room name and token in Chuddy's friends settings. Sync is user-operated. To share on your existing LAN, run with `--host 0.0.0.0` and configure clients with the host's LAN IP (for example `http://192.168.1.20:8770`). The friends default is 8770; the separate Pi companion uses 8766. This software does not change firewall or network settings. LAN HTTP is unencrypted; use a trusted local network. Keep the loopback default when sharing is unnecessary.

Each selected profile uploads only a pseudonym, persistent device/profile IDs and scoring events (kind, provenance, optional confidence and time). Conversations, calendar data, browser URLs, wallet data and raw audio are not sent. Repeating a sync is safe: the server deduplicates immutable event identities. Keep `data/friends.sqlite3` to preserve the rooms and scores. The room token is stored as a SHA-256 digest; it is displayed only when the room is created. Existing rooms are not silently replaced. If a token is lost, create a new room.

The host can instead run the included container, using an existing Docker installation:

```text
docker compose -f friends/compose.yaml build
docker compose -f friends/compose.yaml run --rm leaderboard python -m friends.service --create-room chuddy-club
docker compose -f friends/compose.yaml up -d
```

The compose file binds only loopback. Change the port binding deliberately if you want LAN access. The named volume preserves SQLite data across container recreation. Stop the service before copying the database for a consistent offline backup (or use SQLite's online backup API). Do not delete the volume to update the app.

A practical optional internet hosting path is the same container on a machine you control, persistent `/data` storage and an HTTPS reverse proxy with request limits. Remote clients require HTTPS. Hosting is not performed by this project, and third-party compute may cost money; the complete demo and LAN sharing remain free on existing hardware. Do not expose the HTTP listener directly to the internet.

Protocol: `POST /v1/rooms/{room}/sync`, JSON, `Authorization: Bearer <invitation-token>`. The body is `{installation_id, profile:{id,name}, events:[{id,kind,source,confidence,time}]}`. At most 100 events and 128 KiB per request. Response: `{inserted, leaderboard:[{id,name,burp,fart,total}], scope, self_reported}`. The client resends in batches; events are immutable, so conflicting reused IDs fail atomically. Private rooms have separate invitation tokens and rankings. Room creation is a local administration command, not a public API. `GET /health` reveals no room or score data.
