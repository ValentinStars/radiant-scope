"""Dependency-free Linux telemetry and bounded TShark capture service."""
import argparse
import atexit
from collections import Counter, deque
import csv
import io
from importlib.resources import files
import json
import math
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ROOT = files("radiant_scope") / "web"
SYS = Path("/sys/class/net")
FIELDS = ["frame.time_epoch", "_ws.col.Source", "_ws.col.Destination", "_ws.col.Protocol", "frame.len", "_ws.col.Info", "radiotap.dbm_antsignal"]


def run(args, timeout=4):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.stdout if p.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def read(path, default=""):
    try:
        return path.read_text().strip()
    except OSError:
        return default


def interfaces(demo=False):
    if demo:
        return [{"name": "demo0", "kind": "wifi", "state": "up", "mac": "02:00:00:00:00:01", "addresses": ["192.0.2.10"]}, {"name": "demo1", "kind": "ethernet", "state": "down", "mac": "02:00:00:00:00:02", "addresses": []}]
    try:
        addresses = {x["ifname"]: x for x in json.loads(run(["ip", "-j", "address", "show"]) or "[]")}
    except (ValueError, KeyError):
        addresses = {}
    result = []
    for p in sorted(SYS.iterdir()):
        a = addresses.get(p.name, {})
        result.append({"name": p.name, "kind": "wifi" if (p / "wireless").exists() or (p / "phy80211").exists() else "loopback" if p.name == "lo" else "ethernet", "state": read(p / "operstate"), "mac": read(p / "address"), "addresses": [x["local"] for x in a.get("addr_info", [])]})
    return result


def wireless(name):
    info = run(["iw", "dev", name, "info"])
    link = run(["iw", "dev", name, "link"])
    station = run(["iw", "dev", name, "station", "dump"])
    def match(pattern, source, cast=str):
        m = re.search(pattern, source)
        return cast(m.group(1)) if m else None
    return {"ssid": match(r"SSID: (.+)", link), "bssid": match(r"Connected to (\S+)", link), "rssi": match(r"signal:\s*(-?[\d.]+)", link + station, float), "frequency": match(r"freq:\s*(\d+)", link, int), "channel": match(r"channel (\d+)", info, int), "width": match(r"width: ([^,\n]+)", info), "mode": match(r"type (\S+)", info), "bitrate": match(r"tx bitrate:\s*([\d.]+)", link + station, float), "retries": match(r"tx retries:\s*(\d+)", station, int), "failed": match(r"tx failed:\s*(\d+)", station, int)}


def parse_packet(line):
    cells = next(csv.reader([line], delimiter="\t"))
    cells += [""] * (7 - len(cells))
    try:
        return {"time": float(cells[0]), "source": cells[1], "destination": cells[2], "protocol": cells[3] or "OTHER", "length": int(cells[4]), "info": cells[5][:1000], "rssi": float(cells[6].split(",")[0]) if cells[6] else None}
    except (ValueError, IndexError):
        return None


class Observatory:
    def __init__(self, demo=False):
        self.lock = threading.RLock()
        self.transition = threading.Lock()
        self.demo = demo
        self.token = secrets.token_urlsafe(32)
        self.started = time.time()
        self.items = interfaces(demo)
        self.interface = next((x["name"] for x in self.items if x["state"] == "up" and x["kind"] == "wifi"), self.items[0]["name"] if self.items else "lo")
        self.history = deque(maxlen=600)
        self.packets = deque(maxlen=2000)
        self.protocols = Counter()
        self.talkers = Counter()
        self.sequence = 0
        self.packet_bytes = 0
        self.process = None
        self.capture_error = ""
        self.capture_filter = ""
        self.capture_started = None
        self.capture_ended = None
        self.last = None
        self.wifi = {}
        self.networks = []
        self.scan_time = None
        self.scan_error = ""
        self.stop_event = threading.Event()
        threading.Thread(target=self.sample_loop, daemon=True).start()

    def append_packet(self, packet):
        with self.lock:
            self.sequence += 1
            packet["id"] = self.sequence
            self.packets.append(packet)
            self.packet_bytes += packet["length"]
            self.protocols[packet["protocol"]] += 1
            # Keep aggregate memory bounded as well as the packet buffer.
            key = packet["source"]
            if key in self.talkers or len(self.talkers) < 2048:
                self.talkers[key] += packet["length"]

    def sample_loop(self):
        tick = 0
        while not self.stop_event.is_set():
            begin = time.monotonic()
            with self.lock:
                name = self.interface
            base = SYS / name / "statistics"
            counts = {k: int(read(base / k, "0")) for k in ["rx_bytes", "tx_bytes", "rx_packets", "tx_packets", "rx_dropped", "tx_dropped", "rx_errors", "tx_errors"]}
            wifi = wireless(name) if tick % 3 == 0 and not self.demo else None
            with self.lock:
                if name != self.interface:
                    continue
                if wifi is not None:
                    self.wifi = wifi
                now = time.monotonic()
                previous = self.last
                dt = max(now - previous[0], .01) if previous else 1
                sample = {"time": time.time(), **counts, "rx": max(0, counts["rx_bytes"] - previous[1]["rx_bytes"]) / dt if previous else 0, "tx": max(0, counts["tx_bytes"] - previous[1]["tx_bytes"]) / dt if previous else 0, "pps": max(0, counts["rx_packets"] + counts["tx_packets"] - previous[1]["rx_packets"] - previous[1]["tx_packets"]) / dt if previous else 0, "rssi": self.wifi.get("rssi")}
                if self.demo:
                    sample.update(rx_bytes=round((tick + 1) * 1800000), tx_bytes=round((tick + 1) * 300000), rx_packets=(tick + 1) * 200, tx_packets=(tick + 1) * 50)
                    sample.update(rx=(1.8 + math.sin(tick / 7) + .3 * math.sin(tick * 2)) * 900000, tx=(.6 + .4 * math.sin(tick / 4)) * 500000, pps=240 + 90 * math.sin(tick / 5), rssi=round(-53 + 5 * math.sin(tick / 9), 1))
                    self.wifi = {"ssid": "Radiant demo lab", "bssid": "02:00:00:00:00:01", "channel": 36, "frequency": 5180, "width": "80 MHz", "mode": "managed", "bitrate": 866.7, "rssi": sample["rssi"], "retries": 12, "failed": 0}
                    if self.capture_started and not self.capture_ended:
                        for n in range(5):
                            self.append_packet({"time": time.time(), "source": f"192.0.2.{10+n}", "destination": "198.51.100.20", "protocol": ["TCP", "TLSv1.3", "DNS", "UDP", "QUIC"][(tick+n) % 5], "length": 80 + (tick * 137 + n * 91) % 1400, "info": "Synthetic demonstration packet", "rssi": sample["rssi"]})
                self.last = (now, counts)
                self.history.append(sample)
            tick += 1
            self.stop_event.wait(max(.05, 1 - (time.monotonic() - begin)))

    def select(self, name):
        with self.transition:
            if name not in {x["name"] for x in interfaces(self.demo)}:
                raise ValueError("Interface does not exist")
            self.stop_capture()
            with self.lock:
                self.interface = name
                self.last = None
                self.wifi = {}
                self.history.clear()
                self.networks = []
                self.scan_time = None
                self.clear()

    def clear(self):
        with self.lock:
            self.packets.clear()
            self.protocols.clear()
            self.talkers.clear()
            self.sequence = self.packet_bytes = 0

    def start_capture(self, bpf="", monitor=False):
        if not isinstance(bpf, str) or len(bpf) > 512:
            raise ValueError("Capture filter must be at most 512 characters")
        with self.transition:
            self.stop_capture()
            if not self.demo and not shutil.which("tshark"):
                raise ValueError("TShark is not installed. See Settings → setup.")
            args = ["tshark", "-l", "-n", "-i", self.interface, "-s", "256", "-B", "8", "-a", "duration:3600", "-T", "fields", "-E", "separator=/t", "-E", "quote=d", "-E", "occurrence=f"]
            if monitor:
                args.append("-I")
            if bpf.strip():
                args += ["-f", bpf.strip()]
            for field in FIELDS:
                args += ["-e", field]
            with self.lock:
                self.clear()
                self.capture_started = time.time()
                self.capture_ended = None
                self.capture_filter = bpf
                self.capture_error = ""
                if self.demo:
                    return
                try:
                    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
                except OSError as e:
                    self.capture_ended = time.time()
                    raise ValueError(str(e)) from e
                self.process = proc
            threading.Thread(target=self.consume, args=(proc,), daemon=True).start()

    def consume(self, proc):
        errors = deque(maxlen=16)
        def drain():
            for line in proc.stderr:
                errors.append(line.strip())
        stderr_thread = threading.Thread(target=drain, daemon=True)
        stderr_thread.start()
        for line in proc.stdout:
            packet = parse_packet(line)
            if packet:
                with self.lock:
                    if self.process is proc:
                        self.append_packet(packet)
        code = proc.wait()
        stderr_thread.join(timeout=1)
        with self.lock:
            if self.process is proc:
                self.capture_ended = time.time()
                if code != 0:
                    self.capture_error = "\n".join(errors)[-2400:] or "Capture failed. Check dumpcap permissions."
                self.process = None
        proc.stdout.close()
        proc.stderr.close()

    def stop_capture(self):
        with self.lock:
            proc = self.process
            self.process = None
            if self.capture_started:
                self.capture_ended = time.time()
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    def scan(self):
        # NetworkManager's cache avoids disrupting an associated radio with a forced scan.
        if self.demo:
            networks = [{"ssid": "Radiant demo lab", "bssid": "02:00:00:00:00:01", "channel": 36, "signal": 82, "security": "WPA2", "frequency": "5180 MHz", "in_use": True}, {"ssid": "Studio · demo", "bssid": "02:00:00:00:00:02", "channel": 6, "signal": 54, "security": "WPA2", "frequency": "2437 MHz", "in_use": False}]
        else:
            name = self.interface
            raw = run(["nmcli", "-t", "--escape", "yes", "-f", "IN-USE,SSID,BSSID,CHAN,SIGNAL,SECURITY,FREQ", "device", "wifi", "list", "ifname", name, "--rescan", "no"], timeout=8)
            networks = []
            for line in raw.splitlines():
                fields = [re.sub(r"\\(.)", r"\1", x) for x in re.split(r"(?<!\\):", line)]
                if len(fields) == 7:
                    try:
                        networks.append(dict(zip(["in_use", "ssid", "bssid", "channel", "signal", "security", "frequency"], [fields[0] == "*", fields[1] or "Hidden network", fields[2], int(fields[3]), int(fields[4]), fields[5], fields[6]])))
                    except ValueError:
                        continue
            if name != self.interface:
                return
        with self.lock:
            self.networks = sorted(networks, key=lambda n: -n["signal"])
            self.scan_time = time.time()
            self.scan_error = "" if networks else "No cached access points. Check Wi-Fi, NetworkManager and interface selection."

    def snapshot(self):
        with self.lock:
            return {"demo": self.demo, "interface": self.interface, "uptime": time.time() - self.started, "wifi": dict(self.wifi), "history": list(self.history), "packets": list(self.packets)[-500:], "protocols": dict(self.protocols), "talkers": self.talkers.most_common(8), "networks": list(self.networks), "scan_time": self.scan_time, "scan_error": self.scan_error, "capture": {"running": self.process is not None or bool(self.demo and self.capture_started and not self.capture_ended), "error": self.capture_error, "filter": self.capture_filter, "started": self.capture_started, "ended": self.capture_ended, "count": self.sequence, "bytes": self.packet_bytes, "buffered": len(self.packets)}}

    def close(self):
        self.stop_event.set()
        self.stop_capture()


class Server(ThreadingHTTPServer):
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    @property
    def app(self):
        return self.server.app

    def send(self, data, content_type="application/json", status=200, attachment=None):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        if attachment:
            self.send_header("Content-Disposition", f'attachment; filename="{attachment}"')
        self.end_headers()
        self.wfile.write(data)

    def trusted_host(self):
        return self.headers.get("Host") in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}", f"[::1]:{self.server.server_port}"}

    def do_GET(self):
        if not self.trusted_host():
            return self.send({"error": "Local access only"}, status=403)
        path = urlsplit(self.path).path
        if path == "/api/init":
            return self.send({"token": self.app.token, "interfaces": interfaces(self.app.demo), "tools": {x: bool(shutil.which(x)) for x in ["iw", "nmcli", "tshark", "dumpcap"]}})
        if path == "/api/state":
            return self.send(self.app.snapshot())
        if path == "/api/export.json":
            return self.send(self.app.snapshot(), attachment="radiant-session.json")
        if path == "/api/export.csv":
            stream = io.StringIO()
            writer = csv.DictWriter(stream, fieldnames=["id", "time", "source", "destination", "protocol", "length", "rssi", "info"])
            writer.writeheader()
            with self.app.lock:
                rows = list(self.app.packets)
            # Neutralize spreadsheet formulas originating in untrusted packet text.
            for row in rows:
                writer.writerow({k: "'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@", "\t", "\r")) else v for k, v in row.items()})
            return self.send(stream.getvalue().encode(), "text/csv; charset=utf-8", attachment="radiant-packets.csv")
        assets = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/icon.svg": "icon.svg"}
        if path in assets:
            p = ROOT / assets[path]
            mime = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}[Path(assets[path]).suffix]
            return self.send(p.read_bytes(), mime)
        self.send({"error": "Not found"}, status=404)

    def do_POST(self):
        if not self.trusted_host() or not secrets.compare_digest(self.headers.get("X-Radiant-Token", ""), self.app.token):
            return self.send({"error": "Invalid local session"}, status=403)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                raise ValueError("Invalid request size")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Expected an object")
            path = urlsplit(self.path).path
            if path == "/api/interface":
                self.app.select(body.get("name", ""))
            elif path == "/api/capture/start":
                self.app.start_capture(body.get("filter", ""), bool(body.get("monitor", False)))
            elif path == "/api/capture/stop":
                self.app.stop_capture()
            elif path == "/api/scan":
                self.app.scan()
            else:
                return self.send({"error": "Not found"}, status=404)
            self.send({"ok": True})
        except (ValueError, TypeError) as e:
            self.send({"error": str(e)}, status=400)


def main():
    parser = argparse.ArgumentParser(description="Radiant Scope · Linux network observatory")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--demo", action="store_true", help="Explicit synthetic-data demo")
    args = parser.parse_args()
    app = Observatory(args.demo)
    server = Server(("127.0.0.1", args.port), Handler)
    server.app = app
    atexit.register(app.close)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    print(f"Radiant Scope → http://127.0.0.1:{server.server_port}" + (" [DEMO]" if args.demo else ""), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
        server.server_close()

if __name__ == "__main__":
    main()
