#!/usr/bin/env python3
"""
Logos Node Dashboard - a self-contained, dependency-free node monitor.

Serves a HiveOS-style web dashboard for a Logos blockchain node running on this
Raspberry Pi. All data is real:
  - sync state / live-slot % / ETA   <- node API + computed network tip
  - hardware (temp, throttle, cpu...) <- /proc, vcgencmd, df
  - peers + world map                 <- node API + peer IPs from logs, geolocated
  - wallet balances                   <- node API

Pure Python 3 standard library (the Pi's pip is externally-managed, so no deps).
"""

import json
import os
import re
import socket
import subprocess
import threading
import time
import datetime
import urllib.request
import urllib.error
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
HOME = os.path.expanduser("~")
NODE_API = os.environ.get("LOGOS_NODE_API", "http://127.0.0.1:8080")
CONFIG_PATH = os.environ.get("LOGOS_CONFIG", os.path.join(HOME, "logos", "user_config.yaml"))
NODE_UNIT = os.environ.get("LOGOS_NODE_UNIT", "logos-node")
APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(APP_DIR, "static")
DATA_DIR = os.path.join(APP_DIR, "data")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
GEO_FILE = os.path.join(DATA_DIR, "peers_geo.json")

LISTEN_PORT = int(os.environ.get("LOGOS_DASH_PORT", "8088"))
POLL_INTERVAL = 4          # seconds between node/system samples
PEER_INTERVAL = 60         # seconds between peer-log scrapes
HISTORY_MAX = 4320         # ~6 hours at 5s
HISTORY_SAVE_EVERY = 30    # seconds

# Network timing, from the testnet deployment-settings (cfgsync):
#   slot_duration: 1s, chain_start_time: 2026-04-13 15:19:31 UTC
GENESIS = datetime.datetime(2026, 4, 13, 15, 19, 31,
                            tzinfo=datetime.timezone.utc).timestamp()
SLOT_DURATION = 1.0
BOOTSTRAP_IPS = {"65.109.51.37"}

# Global uptime leaderboard (leaderboard.logos.live) - we read the lightweight
# source data files that power that site and look up this node's own peer_id.
LEADERBOARD_WINDOWS = {
    "7d": "https://raw.githubusercontent.com/tonicaginlinsky/logos-uptime-leaderboard/main/data/last-7-days.txt",
    "30d": "https://raw.githubusercontent.com/tonicaginlinsky/logos-uptime-leaderboard/main/data/last-30-days.txt",
}
LEADERBOARD_URL = "https://leaderboard.logos.live"
LEADERBOARD_REFRESH = 1800   # seconds between leaderboard refreshes (windows update slowly)

os.makedirs(DATA_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
STATE = {"ts": 0, "node": {"reachable": False}, "sync": {}, "network": {},
         "wallet": [], "system": {}, "peers_count": 0, "version": "0.1.2"}
HISTORY = deque(maxlen=HISTORY_MAX)
PEERS = {}            # ip -> {ip, lat, lon, country, city, isp, is_bootstrap, is_self}
SELF_IP = None
LEADERBOARD = {}      # window ("7d"/"30d") -> {found, rank, top_pct, uptime_pct, hours, flag, ...}
LOCK = threading.Lock()

# deltas for rate calculations
_prev = {"stat": None, "net": None, "proc": None}


def log(*a):
    print("[dashboard]", *a, flush=True)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def http_get_json(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode()), r.status
    except urllib.error.HTTPError as e:
        return None, e.code
    except Exception:
        return None, 0


def http_get_text(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read().decode(), r.status
    except Exception:
        return None, 0


# ---------------------------------------------------------------------------
# Wallet keys
# ---------------------------------------------------------------------------
def load_wallet_keys():
    keys = []
    try:
        with open(CONFIG_PATH) as f:
            txt = f.read()
        m = re.search(r"known_keys:(.*?)(?:\n\s*voucher_master_key_id:|\n\w)",
                      txt, re.S)
        block = m.group(1) if m else txt
        for k in re.findall(r"([0-9a-f]{64})\s*:", block):
            if k not in keys:
                keys.append(k)
        vm = re.search(r"voucher_master_key_id:\s*([0-9a-f]{64})", txt)
        voucher = vm.group(1) if vm else None
    except Exception as e:
        log("wallet key parse failed:", e)
        voucher = None
    return keys, voucher


WALLET_KEYS, VOUCHER_KEY = load_wallet_keys()
_wallet_last = {}   # key -> {balance, status, updated}


# ---------------------------------------------------------------------------
# System metrics
# ---------------------------------------------------------------------------
def read_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000.0, 1)
    except Exception:
        return None


def read_throttled():
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"],
                             capture_output=True, text=True, timeout=4).stdout
        val = int(out.strip().split("=")[1], 16)
    except Exception:
        return None, {}
    bits = {
        "under_voltage_now": bool(val & 0x1),
        "freq_capped_now": bool(val & 0x2),
        "throttled_now": bool(val & 0x4),
        "soft_temp_limit_now": bool(val & 0x8),
        "under_voltage_since_boot": bool(val & 0x10000),
        "freq_capped_since_boot": bool(val & 0x20000),
        "throttled_since_boot": bool(val & 0x40000),
        "soft_temp_limit_since_boot": bool(val & 0x80000),
    }
    return hex(val), bits


def read_meminfo():
    info = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                p = line.split()
                if p[0] in ("MemTotal:", "MemAvailable:"):
                    info[p[0][:-1]] = int(p[1]) * 1024
    except Exception:
        pass
    return info


def cpu_percent():
    try:
        with open("/proc/stat") as f:
            parts = f.readline().split()[1:]
        nums = list(map(int, parts))
        idle = nums[3] + nums[4]
        total = sum(nums)
        prev = _prev["stat"]
        _prev["stat"] = (total, idle)
        if prev:
            dt = total - prev[0]
            di = idle - prev[1]
            if dt > 0:
                return round(100.0 * (dt - di) / dt, 1)
    except Exception:
        pass
    return None


def net_rates():
    try:
        rx = tx = 0
        with open("/proc/net/dev") as f:
            for line in f:
                if ":" not in line:
                    continue
                name, rest = line.split(":")
                name = name.strip()
                if name in ("lo",):
                    continue
                cols = rest.split()
                rx += int(cols[0])
                tx += int(cols[8])
        now = time.time()
        prev = _prev["net"]
        _prev["net"] = (now, rx, tx)
        rate_rx = rate_tx = None
        if prev:
            dt = now - prev[0]
            if dt > 0:
                rate_rx = max(0, (rx - prev[1]) / dt)
                rate_tx = max(0, (tx - prev[2]) / dt)
        return rx, tx, rate_rx, rate_tx
    except Exception:
        return None, None, None, None


def node_pid():
    try:
        out = subprocess.run(["pgrep", "-f", "logos-blockchain-node"],
                             capture_output=True, text=True, timeout=4).stdout
        for line in out.split():
            return int(line)
    except Exception:
        pass
    return None


def node_proc_stats(pid):
    cpu = mem = None
    if not pid:
        return cpu, mem
    try:
        with open(f"/proc/{pid}/stat") as f:
            p = f.read().split()
        utime, stime = int(p[13]), int(p[14])
        clk = os.sysconf("SC_CLK_TCK")
        now = time.time()
        prev = _prev["proc"]
        _prev["proc"] = (now, utime + stime)
        if prev:
            dt = now - prev[0]
            if dt > 0:
                cpu = round(100.0 * ((utime + stime) - prev[1]) / clk / dt, 1)
    except Exception:
        pass
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    mem = int(line.split()[1]) * 1024
                    break
    except Exception:
        pass
    return cpu, mem


def disk_usage(path):
    try:
        st = os.statvfs(path)
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = total - free
        return total, used
    except Exception:
        return None, None


def uptime_s():
    try:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])
    except Exception:
        return None


def loadavg():
    try:
        with open("/proc/loadavg") as f:
            p = f.read().split()
        return float(p[0]), float(p[1]), float(p[2])
    except Exception:
        return None, None, None


# ---------------------------------------------------------------------------
# Sync math
# ---------------------------------------------------------------------------
def live_slot_now():
    return int((time.time() - GENESIS) / SLOT_DURATION)


def slot_rate_from_history():
    """slots/sec measured over the most recent ~60s window."""
    with LOCK:
        if len(HISTORY) < 2:
            return None
        newest = HISTORY[-1]
        target = newest["t"] - 60
        old = HISTORY[0]
        for s in HISTORY:
            if s["t"] >= target:
                old = s
                break
        dt = newest["t"] - old["t"]
        ds = newest["slot"] - old["slot"]
    if dt <= 0:
        return None
    return ds / dt


# ---------------------------------------------------------------------------
# Geolocation (ip-api.com free batch endpoint, server-side, cached)
# ---------------------------------------------------------------------------
def geolocate(ips):
    ips = [ip for ip in ips if ip not in PEERS]
    if not ips:
        return
    for i in range(0, len(ips), 100):
        chunk = ips[i:i + 100]
        body = json.dumps([{"query": ip,
                            "fields": "status,country,countryCode,city,lat,lon,isp,query"}
                           for ip in chunk]).encode()
        try:
            req = urllib.request.Request("http://ip-api.com/batch", data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as r:
                results = json.loads(r.read().decode())
            for res in results:
                ip = res.get("query")
                entry = {"ip": ip, "is_bootstrap": ip in BOOTSTRAP_IPS,
                         "is_self": ip == SELF_IP}
                if res.get("status") == "success":
                    entry.update({"lat": res.get("lat"), "lon": res.get("lon"),
                                  "country": res.get("country"),
                                  "country_code": res.get("countryCode"),
                                  "city": res.get("city"), "isp": res.get("isp")})
                with LOCK:
                    PEERS[ip] = entry
            time.sleep(1.5)   # be gentle with the free rate limit
        except Exception as e:
            log("geolocate failed:", e)
            return
    save_json(GEO_FILE, PEERS)


def scrape_peer_ips():
    ips = set(BOOTSTRAP_IPS)
    try:
        out = subprocess.run(
            ["journalctl", "-u", NODE_UNIT, "--no-pager", "-n", "8000"],
            capture_output=True, text=True, timeout=20).stdout
        ips |= set(re.findall(r"/ip4/(\d+\.\d+\.\d+\.\d+)", out))
    except Exception as e:
        log("journalctl scrape failed:", e)
    # drop private / local ranges
    public = {ip for ip in ips if not (ip.startswith("10.") or ip.startswith("192.168.")
              or ip.startswith("127.") or re.match(r"172\.(1[6-9]|2\d|3[01])\.", ip))}
    return public


def discover_self_ip():
    global SELF_IP
    for url in ("http://api.ipify.org", "http://ifconfig.me/ip",
                "http://icanhazip.com"):
        txt, code = http_get_text(url, timeout=8)
        if txt and re.match(r"^\d+\.\d+\.\d+\.\d+$", txt.strip()):
            SELF_IP = txt.strip()
            log("public IP:", SELF_IP)
            return


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def save_json(path, obj):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(obj, f)
        os.replace(tmp, path)
    except Exception as e:
        log("save failed", path, e)


def load_state():
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE) as f:
                for s in json.load(f):
                    HISTORY.append(s)
            log("loaded", len(HISTORY), "history samples")
    except Exception as e:
        log("history load failed:", e)
    try:
        if os.path.exists(GEO_FILE):
            with open(GEO_FILE) as f:
                PEERS.update(json.load(f))
            log("loaded", len(PEERS), "geolocated peers")
    except Exception as e:
        log("geo load failed:", e)


# ---------------------------------------------------------------------------
# Uptime leaderboard
# ---------------------------------------------------------------------------
def flag_to_iso(flag):
    """Two regional-indicator symbols (e.g. 🇵🇹) -> ISO alpha-2 code ('PT')."""
    try:
        if flag and len(flag) >= 2:
            a, b = ord(flag[0]) - 0x1F1E6, ord(flag[1]) - 0x1F1E6
            if 0 <= a < 26 and 0 <= b < 26:
                return chr(65 + a) + chr(65 + b)
    except Exception:
        pass
    return None


def parse_leaderboard(text, peer_id):
    """Parse a leaderboard data file and extract this node's standing."""
    out = {"found": False}
    mt = re.search(r"(\d+)\s+peers seen", text)
    out["total_peers"] = int(mt.group(1)) if mt else None
    mw = re.search(r"—\s*(Last[^()]*?)\s*\((\d+)h", text)
    out["window_label"] = mw.group(1).strip() if mw else None
    out["window_hours"] = int(mw.group(2)) if mw else None
    mr = re.search(r"^Window:\s*(.+?)\s*$", text, re.M)
    out["window_range"] = mr.group(1).strip() if mr else None
    if peer_id:
        for line in text.splitlines():
            if peer_id not in line:
                continue
            mm = re.search(r"(\d+)\s+" + re.escape(peer_id) + r"\s+(\d+)\s+([\d.]+)%", line)
            if not mm:
                continue
            rank, hours, upct = int(mm.group(1)), int(mm.group(2)), float(mm.group(3))
            fm = re.match(r"\s*([\U0001F1E6-\U0001F1FF]{2})", line)
            flag = fm.group(1) if fm else None
            medal = next((m for m, s in (("gold", "🥇"), ("silver", "🥈"),
                                         ("bronze", "🥉")) if s in line), None)
            out.update({"found": True, "rank": rank, "hours": hours,
                        "uptime_pct": upct, "flag": flag,
                        "country_code": flag_to_iso(flag), "medal": medal,
                        "top_pct": round(100.0 * rank / out["total_peers"], 1)
                        if out["total_peers"] else None})
            break
    return out


def update_leaderboard(peer_id):
    result = {}
    for win, url in LEADERBOARD_WINDOWS.items():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "logos-dashboard/1.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                txt = r.read().decode()
            res = parse_leaderboard(txt, peer_id)
            res["updated"] = time.time()
            result[win] = res
        except Exception as e:
            log("leaderboard fetch failed", win, e)
    if result:
        with LOCK:
            LEADERBOARD.clear()
            LEADERBOARD.update(result)
        seven = result.get("7d", {})
        if seven.get("found"):
            log("leaderboard: rank", seven["rank"], "of", seven["total_peers"],
                "(top", str(seven["top_pct"]) + "%)")
    return bool(result)


# ---------------------------------------------------------------------------
# Pollers
# ---------------------------------------------------------------------------
def poll_loop():
    last_save = 0
    while True:
        try:
            sample = collect_sample()
            if sample:
                with LOCK:
                    HISTORY.append(sample)
            if time.time() - last_save > HISTORY_SAVE_EVERY:
                with LOCK:
                    snap = list(HISTORY)
                save_json(HISTORY_FILE, snap)
                last_save = time.time()
        except Exception as e:
            log("poll error:", e)
        time.sleep(POLL_INTERVAL)


def collect_sample():
    info, _ = http_get_json(NODE_API + "/cryptarchia/info")
    net, _ = http_get_json(NODE_API + "/network/info")
    headers, _ = http_get_json(NODE_API + "/cryptarchia/headers")

    node = {"reachable": info is not None}
    sync = {}
    if info:
        node.update({"mode": info.get("mode"), "height": info.get("height"),
                     "slot": info.get("slot"), "tip": info.get("tip"),
                     "lib_slot": info.get("lib_slot"),
                     "headers_count": len(headers) if isinstance(headers, list) else None})
        live = live_slot_now()
        nslot = info.get("slot") or 0
        behind = max(0, live - nslot)
        rate = slot_rate_from_history()
        net_close = (rate - 1.0) if rate else None   # live slot also advances 1/s
        eta = None
        if behind > 120 and net_close and net_close > 0.5:
            eta = int(behind / net_close)
        sync = {"live_slot": live, "slots_behind": behind,
                "seconds_behind": behind,   # 1s slots
                "percent": round(min(100.0, 100.0 * nslot / live), 3) if live else None,
                "slot_rate": round(rate, 1) if rate else None,
                "eta_seconds": eta,
                "caught_up": behind <= 120}

    network = {}
    if net:
        network = {"n_peers": net.get("n_peers"),
                   "n_connections": net.get("n_connections"),
                   "n_pending": net.get("n_pending_connections"),
                   "peer_id": net.get("peer_id"),
                   "listen": net.get("listen_addresses")}

    # wallet balances (handle frequent 500/404 during heavy sync gracefully)
    wallet = []
    for k in WALLET_KEYS:
        data, code = http_get_json(NODE_API + f"/wallet/{k}/balance", timeout=4)
        cur = _wallet_last.get(k, {})
        if code == 200 and data is not None:
            cur = {"balance": data.get("balance"), "status": "ok",
                   "updated": time.time()}
        elif code == 404 and "balance" not in cur:
            cur = {"balance": 0, "status": "unfunded", "updated": time.time()}
        elif code in (500, 0) and not cur:
            cur = {"balance": None, "status": "syncing", "updated": time.time()}
        _wallet_last[k] = cur
        wallet.append({"key": k, "short": k[:10],
                       "is_voucher": (k == VOUCHER_KEY), **cur})

    # system
    pid = node_pid()
    ncpu, nmem = node_proc_stats(pid)
    mem = read_meminfo()
    thr_hex, thr_bits = read_throttled()
    rx, tx, rrx, rtx = net_rates()
    dtotal, dused = disk_usage(os.path.join(HOME, "logos"))
    l1, l5, l15 = loadavg()
    system = {"temp_c": read_temp(), "throttled": thr_hex, "throttle_flags": thr_bits,
              "cpu_percent": cpu_percent(), "nproc": os.cpu_count(),
              "load1": l1, "load5": l5, "load15": l15,
              "mem_total": mem.get("MemTotal"),
              "mem_used": (mem.get("MemTotal", 0) - mem.get("MemAvailable", 0))
              if mem else None,
              "disk_total": dtotal, "disk_used": dused,
              "net_rx_rate": rrx, "net_tx_rate": rtx,
              "uptime_s": uptime_s(), "node_pid": pid,
              "node_cpu": ncpu, "node_mem": nmem}

    snap = {"ts": time.time(), "node": node, "sync": sync, "network": network,
            "wallet": wallet, "system": system, "peers_count": len(PEERS),
            "version": STATE["version"]}
    with LOCK:
        STATE.update(snap)

    mem_pct = (round(100.0 * system["mem_used"] / system["mem_total"], 1)
               if system.get("mem_total") else None)
    thr_now = 1 if any(thr_bits.get(k) for k in
                       ("throttled_now", "soft_temp_limit_now",
                        "freq_capped_now", "under_voltage_now")) else 0
    return {"t": round(time.time()), "height": node.get("height") or 0,
            "slot": node.get("slot") or 0,
            "seconds_behind": sync.get("seconds_behind"),
            "temp": system.get("temp_c"), "cpu": system.get("cpu_percent"),
            "mem_pct": mem_pct, "n_peers": network.get("n_peers"),
            "thr": thr_now, "node_mem": system.get("node_mem")}


def peer_loop():
    discover_self_ip()
    if SELF_IP:
        geolocate([SELF_IP])
    last_lb = 0
    while True:
        try:
            ips = scrape_peer_ips()
            new = [ip for ip in ips if ip not in PEERS]
            if new:
                log("geolocating", len(new), "new peers")
                geolocate(new)
        except Exception as e:
            log("peer loop error:", e)
        try:
            if time.time() - last_lb > LEADERBOARD_REFRESH:
                with LOCK:
                    peer_id = STATE.get("network", {}).get("peer_id")
                if peer_id and update_leaderboard(peer_id):
                    last_lb = time.time()
        except Exception as e:
            log("leaderboard loop error:", e)
        time.sleep(PEER_INTERVAL)


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------
CTYPES = {".html": "text/html", ".js": "application/javascript",
          ".css": "text/css", ".png": "image/png", ".svg": "image/svg+xml",
          ".json": "application/json", ".woff2": "font/woff2", ".woff": "font/woff"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/" or path == "/index.html":
            return self._serve_static("index.html")
        if path == "/api/state":
            with LOCK:
                st = dict(STATE)
                st["leaderboard"] = dict(LEADERBOARD)
            return self._send(200, st)
        if path == "/api/leaderboard":
            with LOCK:
                return self._send(200, dict(LEADERBOARD))
        if path == "/api/history":
            with LOCK:
                return self._send(200, list(HISTORY))
        if path == "/api/peers":
            with LOCK:
                peers = [p for p in PEERS.values() if p.get("lat") is not None]
            return self._send(200, {"peers": peers, "count": len(peers),
                                    "self_ip": SELF_IP})
        if path.startswith("/static/"):
            return self._serve_static(path[len("/static/"):])
        self._send(404, {"error": "not found"})

    def _serve_static(self, name):
        name = name.replace("..", "")
        full = os.path.join(STATIC_DIR, name)
        if not os.path.isfile(full):
            return self._send(404, {"error": "not found"})
        ext = os.path.splitext(full)[1]
        try:
            with open(full, "rb") as f:
                data = f.read()
        except Exception:
            return self._send(500, {"error": "read failed"})
        self._send(200, data, CTYPES.get(ext, "application/octet-stream"))


def main():
    log("starting Logos node dashboard")
    log("wallet keys:", len(WALLET_KEYS), "voucher:", VOUCHER_KEY)
    load_state()
    threading.Thread(target=poll_loop, daemon=True).start()
    threading.Thread(target=peer_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), Handler)
    log(f"listening on http://0.0.0.0:{LISTEN_PORT}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
