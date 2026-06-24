# Logos Node Dashboard

A self-contained, dependency-free web dashboard for a [Logos blockchain](https://logos.co) node running on a Raspberry Pi (or any Linux box). It shows your node's sync state, the Raspberry Pi's health, your wallet balances, and a live world map of the peers your node has met — all from real data, styled to match the Logos design system, with a light/dark toggle.

Built by [@fspseva](https://github.com/fspseva). MIT licensed.

```
┌─ Logos Node   ● ONLINE   peer…   v0.1.2   uptime…        ☾  built by fspseva ─┐
├──────────────────────┬─────────────────────┬──────────────────────────────────┤
│  CHAIN SYNC          │  RASPBERRY PI HEALTH │  NETWORK                         │
│  ◯ 100%  height/slot │  ◯temp ◯cpu          │  peers / conns / pending / I/O   │
│  speed / ETA         │  ◯ram  ◯disk         │  ── WALLET ── keys + balances    │
├──────────────────────┴─────────────────────┴──────────────────────────────────┤
│  LIVE METRICS  [Temp][Catch-up][CPU][RAM]  │   THE NETWORK AROUND YOU (map)    │
│  switchable timeline + throttle threshold  │   geolocated peers worldwide      │
└────────────────────────────────────────────┴────────────────────────────────────┘
```

## What it shows

- **Chain sync** — mode, block height, tip slot vs. the live network slot, a true sync **percentage** and **ETA**, and how far behind the live chain you are (in time). The percentage is computed from the network's genesis time and slot duration, not guessed.
- **Live metrics timeline** — one switchable chart for **Temperature, Catch-up, CPU, RAM, and Peers**. The temperature view draws a dashed throttle line at 80 °C and turns red wherever the Pi was actually throttling, so you can watch it heat up and cool down in real time.
- **Raspberry Pi health** — temperature, CPU, RAM, and disk gauges (color-coded by threshold), load average, the node process's own CPU/RAM, and a banner when the Pi is throttling or under-volting.
- **Network** — connected peers, connections, pending, and live throughput.
- **Wallet** — each of your node's keys with its balance and status.
- **World map** — every peer your node has met, geolocated and plotted, with your own node highlighted. A sense of the global network from your living room.
- **Uptime leaderboard standing** — your node's rank on [leaderboard.logos.live](https://leaderboard.logos.live), shown above the map: your country, rank and percentile, uptime %, and hours tracked, with a **7d / 30d** toggle. Looked up by your node's own peer ID.
- **Copy buttons** — one-click copy for your full peer ID (in the header) and each wallet address.

Everything is **real data** pulled from the node's local API and the Pi's own sensors. No mock data, no external dashboards.

## Requirements

- A running **Logos node** with its HTTP API reachable (default `http://localhost:8080`). See the official guide: [Run a Logos node from the CLI](https://github.com/logos-co/logos-docs/blob/main/docs/blockchain/get-started/run-a-logos-blockchain-node-from-cli.md).
- **Python 3** (standard library only — no `pip install` needed).
- For the world map, the user running the dashboard needs to be able to read the node's logs (`journalctl -u logos-node`). On Raspberry Pi OS the default user can; otherwise add it to the `adm`/`systemd-journal` group.
- Internet access on the host (to geolocate peer IPs via [ip-api.com](https://ip-api.com), to fetch your leaderboard standing from [leaderboard.logos.live](https://leaderboard.logos.live), and to fetch the front-end assets once).

## Install

```bash
git clone https://github.com/fspseva/logos-node-dashboard.git
cd logos-node-dashboard
./setup.sh          # downloads Leaflet, Chart.js and the Logos fonts into ./static
```

`setup.sh` keeps the repo lean by fetching the front-end assets at install time. If the fonts can't be fetched the dashboard still works — it falls back to system fonts.

## Run it

### 1. Quick start (foreground)

```bash
python3 dashboard.py
```

Then open **`http://localhost:8088`** on the Pi, or **`http://<pi-ip>:8088`** from any device on your network.

### 2. As a desktop shortcut

To get a double-click launcher on the Pi's desktop:

```bash
./create-shortcut.sh
```

This adds a **"Logos Node"** icon to your desktop that opens the dashboard in a clean browser window. Pass a URL if you want it to point somewhere other than `http://localhost:8088`:

```bash
./create-shortcut.sh http://192.168.1.50:8088
```

### 3. On startup, alongside the node (recommended)

To run the dashboard as a background service that starts on boot and restarts on failure — coming up automatically together with your `logos-node` service:

```bash
sudo ./install-service.sh
```

It installs a `logos-dashboard` systemd unit ordered **after** `logos-node`, enables it, and starts it now. From then on, both the node and its dashboard come up on every boot. Manage it like any service:

```bash
systemctl status logos-dashboard
journalctl -u logos-dashboard -f
sudo systemctl restart logos-dashboard
```

> Combine 2 + 3 for the full appliance experience: the service runs the dashboard on boot, and the desktop shortcut opens it full-screen with one click.

## Configuration

All optional, set as environment variables (e.g. in the systemd unit or before launching):

| Variable | Default | Purpose |
|---|---|---|
| `LOGOS_NODE_API` | `http://127.0.0.1:8080` | Node HTTP API base URL |
| `LOGOS_CONFIG` | `~/logos/user_config.yaml` | Node config, read for wallet keys |
| `LOGOS_NODE_UNIT` | `logos-node` | systemd unit name, scraped for peer IPs |
| `LOGOS_DASH_PORT` | `8088` | Port the dashboard listens on |

The sync percentage and ETA are computed from the testnet's genesis time and slot duration, set near the top of `dashboard.py` (`GENESIS`, `SLOT_DURATION`). Update these if the network parameters change.

## How it works

`dashboard.py` is a single Python file using only the standard library. A background thread polls the node API and the Pi's `/proc`, `vcgencmd`, and `df` every few seconds, keeps a rolling time-series (persisted to `data/`), and scrapes peer IPs from the node's logs to geolocate them (cached, so geolocation runs at most once per IP). It also periodically fetches the [uptime leaderboard](https://leaderboard.logos.live) source data and looks up this node's standing by peer ID. It serves a small JSON API (`/api/state`, `/api/history`, `/api/peers`, `/api/leaderboard`) and a single static page. The front end is vanilla JavaScript with Leaflet for the map and Chart.js for the timeline.

## Themes

A sun/moon toggle in the header switches between a dark theme and a light theme modeled on [build.logos.co](https://build.logos.co). Your choice is remembered in the browser. Both themes share one set of CSS variables, including the map tiles and chart colors.

## Troubleshooting

- **"node unreachable" banner** — the node API isn't answering on `:8080`. Check `systemctl status logos-node`.
- **Map is empty** — peer IPs come from the node logs; confirm the dashboard user can run `journalctl -u logos-node`, and give it a minute to geolocate.
- **Wallet shows "syncing"** — the node hasn't caught up to the block holding your funds yet; it resolves once it's in sync.
- **Fonts look generic** — the brand fonts didn't download; re-run `./setup.sh`. The dashboard works fine either way.

## Credits

- Built by [@fspseva](https://github.com/fspseva).
- Fonts, colors, and the mark follow the [Logos](https://logos.co) design system. Logos name and brand assets belong to Logos.
- Maps © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors, tiles by [CARTO](https://carto.com/). Peer geolocation by [ip-api.com](https://ip-api.com).

MIT licensed — see [LICENSE](LICENSE).
