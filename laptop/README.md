# LanLink laptop companion

This is the laptop side of the LAN-only LanLink MVP. It uses Python's standard
library, so no `pip install` step is required.

## Run

```powershell
cd D:\App\LanLink\laptop
python server.py
```

The console prints:

- a six-digit pairing code;
- the laptop IP address(es) to enter on Android;
- a small laptop UI at `http://127.0.0.1:8765/`.

Keep the server window open. Both devices must be connected to the same Wi-Fi
and the Windows network profile should allow Python through the firewall on a
Private network. A different port can be selected with `python server.py
--port 9000`.

## API

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/info` | Server name and LAN addresses |
| POST | `/api/pair` | Exchange the six-digit code for a session token |
| GET | `/api/messages?token=...&since=...` | Poll new messages |
| POST | `/api/messages` | Add a text message |

The server keeps at most 500 messages in memory and clears them when it exits.
It is intentionally designed for a trusted home/office LAN, not for exposure
to the public Internet.

## Tests

```powershell
cd D:\App\LanLink\laptop
python -m unittest discover -s tests -v
```
