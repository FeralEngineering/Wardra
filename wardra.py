#!/usr/bin/env python3
import json
import os
import re
import socket
import subprocess
import time
import threading
import atexit
import signal
import fcntl
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple, List

from PIL import Image, ImageDraw, ImageFont

import sys
sys.path.append("/home/wardra/e-Paper/RaspberryPi_JetsonNano/python/lib")

from waveshare_epd import epd2in13_V3

# ----------------------------
# Config you’ll actually tweak
# ----------------------------
IFACE = "wlan0"

BASE_DIR = os.path.expanduser("~/wardra")
SPRITE_DIR = os.path.join(BASE_DIR, "sprites")
LOG_DIR = os.path.join(BASE_DIR, "logs")

OPEN_LOG   = os.path.join(LOG_DIR, "open_networks.jsonl")
SECURE_LOG = os.path.join(LOG_DIR, "secure_networks.jsonl")
ALL_LOG    = os.path.join(LOG_DIR, "all_networks.jsonl")
STATE_JSON = os.path.join(LOG_DIR, "state.json")

# prevent two systemd instances from writing garbage/interleaving lines
LOCK_FILE = os.path.join(LOG_DIR, "wardra.lock")

SCAN_INTERVAL_SEC = 10

MPS_TO_MPH = 2.2369362920544  # log-only now

# Your thresholds
EVOLVE_THRESHOLDS = [333, 666, 999, 1312]

BORED_AFTER_SEC = 90
SLEEP_AFTER_SEC = 180

EXCITED_FLASH_SEC = 6

ALERT_BLINK_EVERY_SEC = 12
ALERT_BLINK_DURATION_SEC = 1.3

DISPLAY_UPDATE_SEC = 6

# ---- e-paper: reduce flashing ----
USE_PARTIAL_REFRESH = True
FULL_REFRESH_EVERY_SEC = 300   # safety net against ghosting (rare)
CLEAR_EVERY_N_UPDATES = 0       # keep 0; Clear() forces full flash

SPLASH_PATH = os.path.join(SPRITE_DIR, "splash.png")
SPLASH_SHOW_SEC = 3.0
SPLASH_INVERT = True

# ---- GPS: basic gpsd settings ----
GPS_HOST = "127.0.0.1"
GPS_PORT = 2947
GPS_SOCKET_TIMEOUT_SEC = 1.0
GPS_NO_TPV_RECONNECT_SEC = 8.0

# how often UI shows GPS stats (not how often gps is read; that’s in the thread)
GPS_POLL_SEC = 1.0

# if we have coords older than this, we USED TO drop them; now we just use age for display only
GPS_STALE_AFTER_SEC = 8.0

# ---- Power-cut resilience (reduce write frequency) ----
STATE_SAVE_MIN_INTERVAL_SEC = 20.0

# ---- Log durability (reduces weird corruption on power loss) ----
FSYNC_LOG_WRITES = True

# ----------------------------
# Data structures
# ----------------------------
@dataclass
class GpsFix:
    lat: Optional[float] = None
    lon: Optional[float] = None
    alt: Optional[float] = None
    speed: Optional[float] = None
    acc_m: Optional[float] = None
    used_sats: int = 0
    view_sats: int = 0
    mode: int = 0

    # monotonic time when COORDS were last updated (not just any TPV packet)
    ts_mono: float = 0.0
    ts_utc: str = ""
    stale: bool = False
    fix_age_sec: float = 999999.0

@dataclass
class Network:
    ts: str
    bssid: str
    ssid: str
    freq_mhz: Optional[int]
    channel: Optional[int]
    signal_dbm: Optional[int]
    security: str
    encryption: str
    captive_portal: str
    captive_hint: str
    gps: GpsFix

# ----------------------------
# GPS helpers
# ----------------------------
def _valid_lat_lon(lat, lon) -> bool:
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return False
    lat = float(lat)
    lon = float(lon)
    if lat == 0.0 and lon == 0.0:
        return False
    return (-90.0 <= lat <= 90.0) and (-180.0 <= lon <= 180.0)

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _as_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None    

def _compute_acc_m(msg: Dict) -> Optional[float]:
    try:
        epx = msg.get("epx")
        epy = msg.get("epy")
        if isinstance(epx, (int, float)) and isinstance(epy, (int, float)) and epx > 0 and epy > 0:
            return (epx * epx + epy * epy) ** 0.5
    except Exception:
        pass

    eph = msg.get("eph")
    if isinstance(eph, (int, float)) and eph > 0:
        return float(eph)

    return None

# ----------------------------
# GPSD background reader (simple, no coord-wiping)
# ----------------------------
class GPSDReader:
    """
    Keeps a persistent gpsd connection in a background thread.
    Always provides the latest fix instantly to the main loop.

    NO last-known-good from disk. NO age-based coord wiping.
    Whatever gpsd last told us (lat/lon), we keep and log.
    """
    def __init__(self, state: Dict, host=GPS_HOST, port=GPS_PORT, timeout=GPS_SOCKET_TIMEOUT_SEC):
        # 'state' is unused now, but we keep the argument so main() doesn't change.
        self.host = host
        self.port = port
        self.timeout = timeout

        self._lock = threading.Lock()
        self._sock: Optional[socket.socket] = None
        self._buf = b""

        self._used_sats = 0
        self._view_sats = 0
        self._last_tpv_mono = 0.0

        # Start with "no fix"
        self._latest = GpsFix()

        self._stop = threading.Event()
        self._thr: Optional[threading.Thread] = None

    def start(self):
        if self._thr and self._thr.is_alive():
            return
        self._thr = threading.Thread(target=self._run, name="gpsd-reader", daemon=True)
        self._thr.start()

    def stop(self):
        self._stop.set()
        self._close()

    def _close(self):
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
        self._sock = None
        self._buf = b""

    def _connect(self) -> bool:
        self._close()
        try:
            self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            self._sock.settimeout(self.timeout)
            # scaled=true gives us floats in SI units
            self._sock.sendall(b'?WATCH={"enable":true,"json":true,"scaled":true}\n')
            self._buf = b""
            return True
        except Exception:
            self._close()
            return False

    def _run(self):
        backoff = 0.5
        while not self._stop.is_set():
            if not self._sock:
                if not self._connect():
                    time.sleep(backoff)
                    backoff = min(8.0, backoff * 1.5)
                    continue
                backoff = 0.5

            try:
                chunk = self._sock.recv(4096)
                if not chunk:
                    raise ConnectionError("gpsd socket closed")
                self._buf += chunk

                while b"\n" in self._buf:
                    line, self._buf = self._buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line.decode("utf-8", errors="ignore"))
                    except Exception:
                        continue

                    cls = msg.get("class")
                    if cls == "SKY":
                        u = msg.get("uSat")
                        n = msg.get("nSat")
                        if isinstance(u, int):
                            self._used_sats = u
                        if isinstance(n, int):
                            self._view_sats = n

                    elif cls == "TPV":
                        self._last_tpv_mono = time.monotonic()

                        mode = int(msg.get("mode", 0) or 0)
                        sp = _as_float(msg.get("speed"))
                        a = _compute_acc_m(msg)

                        raw_lat = _as_float(msg.get("lat"))
                        raw_lon = _as_float(msg.get("lon"))
                        raw_alt = _as_float(msg.get("alt"))
                        now_m = time.monotonic()
                        ts_utc = str(msg.get("time") or _utc_now_iso())

                        with self._lock:
                            cur = self._latest

                            # if we have fresh coords, overwrite; otherwise keep previous coords
                            if _valid_lat_lon(raw_lat, raw_lon):
                                lat = raw_lat
                                lon = raw_lon
                                alt = raw_alt
                                ts_mono = now_m
                            else:
                                lat = cur.lat
                                lon = cur.lon
                                alt = cur.alt
                                ts_mono = cur.ts_mono or now_m

                            live = GpsFix(
                                lat=lat,
                                lon=lon,
                                alt=alt,
                                speed=sp,
                                acc_m=a,
                                used_sats=int(self._used_sats or 0),
                                view_sats=int(self._view_sats or 0),
                                mode=int(mode),
                                ts_mono=ts_mono,
                                ts_utc=ts_utc if _valid_lat_lon(lat, lon) else cur.ts_utc,
                                stale=False,          # we DO NOT mark stale based on age anymore
                                fix_age_sec=0.0,      # will be recomputed in get_latest
                            )
                            self._latest = live

            except socket.timeout:
                # If gpsd goes quiet for too long, reconnect
                if self._last_tpv_mono > 0 and (time.monotonic() - self._last_tpv_mono) >= GPS_NO_TPV_RECONNECT_SEC:
                    self._connect()
                continue
            except Exception:
                self._connect()
                continue

    def get_latest(self) -> GpsFix:
        with self._lock:
            g = self._latest

        # Age of coords based on when we last updated them
        if g.ts_mono and g.ts_mono > 0:
            age = max(0.0, time.monotonic() - g.ts_mono)
        else:
            age = 999999.0

        has_coords = _valid_lat_lon(g.lat, g.lon)

        # IMPORTANT: we DO NOT clear coords or mark stale based on age.
        # We only track age for display/logging.
        stale = False if has_coords else True
        fix_age = age if has_coords else 999999.0

        return GpsFix(
            lat=g.lat,
            lon=g.lon,
            alt=g.alt,
            speed=g.speed,
            acc_m=g.acc_m,
            used_sats=int(g.used_sats or 0),
            view_sats=int(g.view_sats or 0),
            mode=int(g.mode or 0),
            ts_mono=float(g.ts_mono or 0.0),
            ts_utc=str(g.ts_utc or ""),
            stale=stale,
            fix_age_sec=fix_age,
        )

def gps_snapshot(g: GpsFix) -> GpsFix:
    """
    Snapshot of current GPS fix for logging.

    NO age-based coord dropping. Whatever we have in g, we log.
    """
    return GpsFix(
        lat=g.lat,
        lon=g.lon,
        alt=g.alt,
        speed=g.speed,
        acc_m=g.acc_m,
        used_sats=g.used_sats,
        view_sats=g.view_sats,
        mode=g.mode,
        ts_mono=g.ts_mono,
        ts_utc=g.ts_utc,
        stale=g.stale,
        fix_age_sec=g.fix_age_sec,
    )

# ----------------------------
# iw scan parsing
# ----------------------------
BSS_RE = re.compile(r"^BSS\s+([0-9a-f:]{17})", re.IGNORECASE)
FREQ_RE = re.compile(r"^\s*freq:\s*(\d+)")
SSID_RE = re.compile(r"^\s*SSID:\s*(.*)$")
SIGNAL_RE = re.compile(r"^\s*signal:\s*(-?\d+)\.?\d*\s*dBm")

RSN_RE = re.compile(r"^\s*RSN:", re.IGNORECASE)
WPA_RE = re.compile(r"^\s*WPA:", re.IGNORECASE)

WEP_HINT_RE = re.compile(r"^\s*capability:\s*(.*)$", re.IGNORECASE)
AUTH_RE     = re.compile(r"^\s*\*?\s*Authentication suites:\s*(.*)$", re.IGNORECASE)

HS20_RE = re.compile(r"^\s*HS20:\s*(.*)$", re.IGNORECASE)
INTERWORK_RE = re.compile(r"^\s*Interworking:\s*(.*)$", re.IGNORECASE)
ADV_PROTO_RE = re.compile(r"^\s*Advertisement Protocol:\s*(.*)$", re.IGNORECASE)

def freq_to_channel(freq: int) -> Optional[int]:
    if 2412 <= freq <= 2472:
        return (freq - 2407) // 5
    if freq == 2484:
        return 14
    return None

def run_iw_scan(iface: str) -> str:
    cmd = ["sudo", "iw", "dev", iface, "scan"]
    try:
        return subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True, timeout=12)
    except subprocess.TimeoutExpired:
        return ""

def parse_iw_scan(output: str, gps: GpsFix) -> List[Network]:
    nets: List[Network] = []
    cur = None

    has_rsn = False
    has_wpa = False
    cap_line = ""
    auth_suites = ""
    captive_hints: List[str] = []

    def flush():
        nonlocal cur, has_rsn, has_wpa, cap_line, auth_suites, captive_hints
        if not cur:
            return

        ssid = cur.get("ssid", "")
        bssid = cur.get("bssid", "")
        freq = cur.get("freq")
        sig = cur.get("signal")
        ch = freq_to_channel(freq) if freq else None

        security = "OPEN"
        encryption = "NONE"

        if has_rsn or has_wpa:
            security = "SECURE"
            enc_parts = []
            a = (auth_suites or "").upper()

            if "SAE" in a:
                enc_parts.append("WPA3-SAE")
            if "PSK" in a:
                enc_parts.append("WPA2-PSK" if has_rsn else "WPA-PSK")
            if "802.1X" in a or "EAP" in a:
                enc_parts.append("WPA2-ENTERPRISE" if has_rsn else "WPA-ENTERPRISE")
            if "OWE" in a:
                enc_parts.append("OWE")

            if not enc_parts:
                enc_parts.append("WPA2/RSN" if has_rsn else "WPA")

            encryption = "+".join(dict.fromkeys(enc_parts))

        cap = (cap_line or "").lower()
        if security == "OPEN" and "privacy" in cap:
            security = "SECURE"
            encryption = "UNKNOWN"

        hint = " | ".join(captive_hints) if captive_hints else ""

        nets.append(Network(
            ts=datetime.now(timezone.utc).isoformat(),
            bssid=bssid,
            ssid=ssid,
            freq_mhz=freq,
            channel=ch,
            signal_dbm=sig,
            security=security,
            encryption=encryption,
            captive_portal="unknown",
            captive_hint=hint,
            gps=gps_snapshot(gps),
        ))

        cur = None
        has_rsn = False
        has_wpa = False
        cap_line = ""
        auth_suites = ""
        captive_hints = []

    for line in output.splitlines():
        m = BSS_RE.match(line)
        if m:
            flush()
            cur = {"bssid": m.group(1).lower(), "ssid": "", "freq": None, "signal": None}
            continue

        if not cur:
            continue

        m = FREQ_RE.match(line)
        if m:
            cur["freq"] = int(m.group(1))
            continue

        m = SSID_RE.match(line)
        if m:
            cur["ssid"] = (m.group(1) or "").strip()
            continue

        m = SIGNAL_RE.match(line)
        if m:
            cur["signal"] = int(m.group(1))
            continue

        if RSN_RE.match(line):
            has_rsn = True
            continue
        if WPA_RE.match(line):
            has_wpa = True
            continue

        m = WEP_HINT_RE.match(line)
        if m:
            cap_line = m.group(1) or ""
            continue

        m = AUTH_RE.match(line)
        if m:
            auth_suites = m.group(1) or ""
            continue

        if HS20_RE.match(line):
            captive_hints.append("HS20")
        if INTERWORK_RE.match(line):
            captive_hints.append("Interworking")
        if ADV_PROTO_RE.match(line):
            captive_hints.append("AdvProto")

    flush()
    return nets

# ----------------------------
# Persistence
# ----------------------------
def load_state() -> Dict:
    if not os.path.exists(STATE_JSON):
        return {
            "seen_bssids": {},
            "open_unique_count": 0,
            "secure_unique_count": 0,
            "all_unique_count": 0,
            "last_new_any_ts": 0.0,
            "last_new_open_ts": 0.0,
            "last_good_gps": None,  # legacy; no longer used
        }
    with open(STATE_JSON, "r", encoding="utf-8") as f:
        state = json.load(f)
    # keep keys for compatibility but we don't use last_good_gps anymore
    state.setdefault("last_good_gps", None)
    state.setdefault("seen_bssids", {})
    return state

def save_state(state: Dict):
    tmp = STATE_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, STATE_JSON)

def append_jsonl_batch(path: str, objs: List[Dict]):
    if not objs:
        return
    with open(path, "a", encoding="utf-8") as f:
        for obj in objs:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        f.flush()
        if FSYNC_LOG_WRITES:
            try:
                os.fsync(f.fileno())
            except Exception:
                pass

# ----------------------------
# Display (boot-safe + partial if available)
# ----------------------------
class Display:
    STATS_LINES = 8
    BOLD_HEADERS = True
    SEPARATOR_PX = 1

    def __init__(self):
        self.epd = epd2in13_V3.EPD()
        self.LW = self.epd.height   # 250
        self.LH = self.epd.width    # 122

        self.update_count = 0
        self._last_full_refresh_ts = 0.0

        self._partial_ready = False
        self._base_set = False

        try:
            self.font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 12)
            self.font_tiny  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 10)
        except Exception:
            self.font_small = ImageFont.load_default()
            self.font_tiny  = ImageFont.load_default()

        self._full_init(clear=True)

        if USE_PARTIAL_REFRESH:
            self._enter_partial_mode()
            self._set_white_base()

    def cleanup(self):
        try:
            if hasattr(self.epd, "sleep"):
                self.epd.sleep()
        except Exception:
            pass
        try:
            if hasattr(self.epd, "Dev_exit"):
                self.epd.Dev_exit()
        except Exception:
            pass

    def _land_to_buf(self, land: Image.Image):
        out = land.rotate(90, expand=True)  # (122x250)
        return self.epd.getbuffer(out)

    def _full_init(self, clear: bool = True):
        try:
            self.epd.init()
        except Exception:
            pass
        if clear:
            try:
                self.epd.Clear(0xFF)
            except Exception:
                pass

        self._last_full_refresh_ts = time.time()
        self._partial_ready = False
        self._base_set = False

    def _enter_partial_mode(self):
        ok = False
        try:
            self.epd.init()

            set_lut = getattr(self.epd, "SetLut", None)
            lut_part = getattr(self.epd, "lut_partial_update", None)

            if callable(set_lut) and lut_part is not None:
                set_lut(lut_part)
                ok = True
            else:
                if callable(getattr(self.epd, "displayPartial", None)) and callable(getattr(self.epd, "displayPartBaseImage", None)):
                    ok = True
        except Exception:
            ok = False

        self._partial_ready = ok
        self._base_set = False

    def _set_white_base(self):
        if not self._partial_ready:
            return
        try:
            white = Image.new("1", (self.LW, self.LH), 255)
            buf = self._land_to_buf(white)
            fn = getattr(self.epd, "displayPartBaseImage", None)
            if callable(fn):
                fn(buf)
                self._base_set = True
        except Exception:
            self._base_set = False

    def _push(self, land: Image.Image):
        buf = self._land_to_buf(land)

        if self._partial_ready:
            if not self._base_set:
                self._set_white_base()

            if self._base_set:
                try:
                    fn_part = getattr(self.epd, "displayPartial", None)
                    if callable(fn_part):
                        fn_part(buf)
                        return
                except Exception:
                    self._partial_ready = False
                    self._base_set = False

        try:
            self.epd.display(buf)
        except Exception:
            pass

    def show_splash(self, splash_path: str, hold_sec: float = 2.0, invert: bool = False):
        if not splash_path or not os.path.exists(splash_path):
            return
        try:
            img = Image.open(splash_path).convert("1")
            if img.size != (self.LW, self.LH):
                img = img.resize((self.LW, self.LH), resample=Image.NEAREST)
            if invert:
                img = Image.eval(img, lambda p: 255 - p)

            self._full_init(clear=True)
            try:
                self.epd.display(self._land_to_buf(img))
            except Exception:
                pass

            if hold_sec and hold_sec > 0:
                time.sleep(hold_sec)

            if USE_PARTIAL_REFRESH:
                self._enter_partial_mode()
                self._set_white_base()

        except Exception:
            return

    @staticmethod
    def _parse_bold_markup(s: str) -> List[Tuple[str, bool]]:
        if not s:
            return [("", False)]
        out: List[Tuple[str, bool]] = []
        i = 0
        bold = False
        while i < len(s):
            if s.startswith("{b}", i):
                bold = True
                i += 3
                continue
            if s.startswith("{/b}", i):
                bold = False
                i += 4
                continue
            j = i
            while j < len(s) and not s.startswith("{b}", j) and not s.startswith("{/b}", j):
                j += 1
            chunk = s[i:j]
            if chunk:
                out.append((chunk, bold))
            i = j
        if not out:
            out.append(("", False))
        return out

    def _draw_runs(self, d: ImageDraw.ImageDraw, x: int, y: int, s: str):
        runs = self._parse_bold_markup(s)
        cur_x = x
        for text, is_bold in runs:
            if not text:
                continue
            if is_bold and self.BOLD_HEADERS:
                d.text((cur_x, y), text, font=self.font_tiny, fill=0)
                d.text((cur_x + 1, y), text, font=self.font_tiny, fill=0)
            else:
                d.text((cur_x, y), text, font=self.font_tiny, fill=0)

            try:
                w = d.textlength(text, font=self.font_tiny)
            except Exception:
                w = len(text) * 6
            cur_x += int(w)

    def _double_border(self, draw: ImageDraw.ImageDraw, box: Tuple[int,int,int,int]):
        x0, y0, x1, y1 = box
        draw.rectangle(box, outline=0, width=1)
        draw.rectangle((x0+2, y0+2, x1-2, y1-2), outline=0, width=1)

    def render(self, stats: Dict, sprite_path: str, invert_flash: bool = False):
        now = time.time()

        if FULL_REFRESH_EVERY_SEC and (now - self._last_full_refresh_ts) >= FULL_REFRESH_EVERY_SEC:
            self._full_init(clear=True)
            if USE_PARTIAL_REFRESH:
                self._enter_partial_mode()
                self._set_white_base()

        land = Image.new("1", (self.LW, self.LH), 255)
        d = ImageDraw.Draw(land)

        pad = 6
        stats_w = 132

        stats_box = (pad, pad, stats_w - pad, self.LH - pad)
        self._double_border(d, stats_box)

        pet_x0 = stats_w + 2
        pet_box = (pet_x0, pad, self.LW - pad, self.LH - pad)
        d.rectangle(pet_box, outline=0, width=1)

        tx = stats_box[0] + 6
        inner_top = stats_box[1] + 6
        inner_bot = stats_box[3] - 6
        usable_h = inner_bot - inner_top

        lines = stats.get("lines", [])[:self.STATS_LINES]
        n = max(1, min(self.STATS_LINES, len(lines)))

        sep = int(self.SEPARATOR_PX or 0)
        sep_after = set()
        if sep > 0 and n >= 4:
            for k in (2, 4, 6):
                if k < n:
                    sep_after.add(k)

        total_sep_h = len(sep_after) * sep
        usable_for_lines = max(1, usable_h - total_sep_h)

        step = max(9, usable_for_lines // n)
        block_h = (step * n) + total_sep_h
        y0 = inner_top + max(0, (usable_h - block_h) // 2)

        y = y0
        for i, s in enumerate(lines, start=1):
            self._draw_runs(d, tx, y, s)
            y += step
            if i in sep_after:
                x0 = tx
                x1 = stats_box[2] - 6
                d.line((x0, y, x1, y), fill=0)
                y += sep

        try:
            spr = Image.open(sprite_path).convert("1")
            if spr.size != (96, 96):
                spr = spr.resize((96, 96), resample=Image.NEAREST)

            px0, py0, px1, py1 = pet_box
            cx = (px0 + px1) // 2
            cy = (py0 + py1) // 2
            land.paste(spr, (cx - 48, cy - 48))
        except Exception:
            d.text((pet_x0 + 8, pad + 8), "SPRITE\nMISSING", font=self.font_small, fill=0)

        if invert_flash:
            land = Image.eval(land, lambda p: 255 - p)

        self.update_count += 1
        if CLEAR_EVERY_N_UPDATES and (self.update_count % CLEAR_EVERY_N_UPDATES == 0):
            self._full_init(clear=True)
            if USE_PARTIAL_REFRESH:
                self._enter_partial_mode()
                self._set_white_base()

        self._push(land)

# ----------------------------
# Pet logic
# ----------------------------
def stage_from_open_count(open_count: int) -> int:
    for i, thr in enumerate(EVOLVE_THRESHOLDS):
        if open_count < thr:
            return i + 1
    return 5

def remaining_to_evolve(open_count: int) -> int:
    stage = stage_from_open_count(open_count)
    if stage >= 5:
        return 0
    next_thr = EVOLVE_THRESHOLDS[stage - 1]
    return max(0, int(next_thr) - int(open_count))

def sprite_path(stage: int, mood: str, sleep_toggle: int = 1) -> str:
    if mood == "sleep":
        return os.path.join(SPRITE_DIR, f"stage{stage}_sleep{sleep_toggle}.png")
    return os.path.join(SPRITE_DIR, f"stage{stage}_{mood}.png")

def fmt_k(n: int, allow_decimal: bool = False) -> str:
    try:
        n = int(n)
    except Exception:
        return "0"
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n/1000.0:.1f}K" if allow_decimal else f"{n//1000}K"
    return f"{n/1_000_000.0:.1f}M" if allow_decimal else f"{n//1_000_000}M"

# ----------------------------
# Main
# ----------------------------
def main():
    os.makedirs(LOG_DIR, exist_ok=True)

    # --- single-instance lock (prevents log garbage from overlapping systemd runs) ---
    lock_fd = open(LOCK_FILE, "a+")
    try:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("[wardra] another instance is running; exiting to prevent log corruption")
        raise SystemExit(1)

    state = load_state()
    seen: Dict[str, Dict] = state.get("seen_bssids", {})
    state["seen_bssids"] = seen

    state_dirty = False
    last_state_save_mono = time.monotonic()

    def save_state_throttled(force: bool = False):
        nonlocal state_dirty, last_state_save_mono
        if not state_dirty and not force:
            return
        now_m = time.monotonic()
        if (not force) and ((now_m - last_state_save_mono) < STATE_SAVE_MIN_INTERVAL_SEC):
            return
        try:
            state["seen_bssids"] = seen
            save_state(state)
            last_state_save_mono = now_m
            state_dirty = False
        except Exception:
            pass

    disp = Display()
    atexit.register(disp.cleanup)

    gps = GPSDReader(state)
    gps.start()
    atexit.register(gps.stop)

    def _final_flush():
        try:
            state["seen_bssids"] = seen
        except Exception:
            pass
        save_state_throttled(force=True)

    atexit.register(_final_flush)

    def _handle_term(signum, frame):
        _final_flush()
        raise SystemExit(0)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_term)
        except Exception:
            pass

    disp.show_splash(SPLASH_PATH, hold_sec=SPLASH_SHOW_SEC, invert=SPLASH_INVERT)

    last_display = 0.0
    last_scan = 0.0
    last_alert_blink = 0.0
    alert_blink_until = 0.0

    sleep_toggle = 1

    evolution_flash_until = 0.0
    last_stage = stage_from_open_count(int(state.get("open_unique_count", 0)))

    last_scan_status = "ok"   # ok / TO / ER
    last_scan_bss_count = 0

    print("[wardra] starting…")
    next_hb = time.time() + 30

    last_gps_poll = 0.0
    fix = gps.get_latest()

    while True:
        now = time.time()

        if now >= next_hb:
            print("[wardra] alive")
            next_hb = now + 30

        if now - last_gps_poll >= GPS_POLL_SEC:
            last_gps_poll = now
            fix = gps.get_latest()

        if now - last_scan >= SCAN_INTERVAL_SEC:
            last_scan = now

            batch_all: List[Dict] = []
            batch_open: List[Dict] = []
            batch_secure: List[Dict] = []

            try:
                out = run_iw_scan(IFACE)

                if out == "":
                    print("[wardra] scan timed out — skipping this scan cycle")
                    nets = []
                    last_scan_status = "TO"
                    last_scan_bss_count = 0
                else:
                    nets = parse_iw_scan(out, fix)
                    last_scan_status = "ok"
                    last_scan_bss_count = len(nets)

                for n in nets:
                    if not n.bssid:
                        continue
                    if n.bssid in seen:
                        continue

                    seen[n.bssid] = {
                        "first_seen": n.ts,
                        "ssid": n.ssid,
                        "security": n.security,
                        "encryption": n.encryption,
                    }

                    state["all_unique_count"] = int(state.get("all_unique_count", 0)) + 1
                    if n.security == "OPEN":
                        state["open_unique_count"] = int(state.get("open_unique_count", 0)) + 1
                        state["last_new_open_ts"] = now
                    else:
                        state["secure_unique_count"] = int(state.get("secure_unique_count", 0)) + 1

                    state["last_new_any_ts"] = now
                    state_dirty = True

                    obj = {
                        "ts": n.ts,
                        "bssid": n.bssid,
                        "ssid": n.ssid,
                        "freq_mhz": n.freq_mhz,
                        "channel": n.channel,
                        "signal_dbm": n.signal_dbm,
                        "security": n.security,
                        "encryption": n.encryption,
                        "captive_portal": n.captive_portal,
                        "captive_hint": n.captive_hint,
                        "gps": {
                            "mode": n.gps.mode,
                            "lat": n.gps.lat,
                            "lon": n.gps.lon,
                            "alt": n.gps.alt,
                            "speed": n.gps.speed,
                            "acc_m": n.gps.acc_m,
                            "used_sats": n.gps.used_sats,
                            "view_sats": n.gps.view_sats,
                            "stale": bool(n.gps.stale),
                            "fix_age_sec": float(getattr(n.gps, "fix_age_sec", 999999.0) or 999999.0),
                            "last_good_ts": str(getattr(n.gps, "ts_utc", "") or ""),
                        }
                    }

                    batch_all.append(obj)
                    if n.security == "OPEN":
                        batch_open.append(obj)
                    else:
                        batch_secure.append(obj)

                append_jsonl_batch(ALL_LOG, batch_all)
                append_jsonl_batch(OPEN_LOG, batch_open)
                append_jsonl_batch(SECURE_LOG, batch_secure)

                save_state_throttled(force=False)

            except subprocess.CalledProcessError:
                last_scan_status = "ER"
                last_scan_bss_count = 0
            except Exception:
                last_scan_status = "ER"
                last_scan_bss_count = 0

        # mood logic
        open_count = int(state.get("open_unique_count", 0))
        stage = stage_from_open_count(open_count)

        if stage != last_stage and stage > last_stage:
            evolution_flash_until = now + 2.0
            last_stage = stage

        last_any = float(state.get("last_new_any_ts", 0.0))
        last_open = float(state.get("last_new_open_ts", 0.0))
        since_any = (now - last_any) if last_any > 0 else 999999
        since_open = (now - last_open) if last_open > 0 else 999999

        if since_open <= EXCITED_FLASH_SEC:
            mood = "excited"
        else:
            if since_any >= SLEEP_AFTER_SEC:
                mood = "sleep"
            elif since_any >= BORED_AFTER_SEC:
                mood = "bored"
            else:
                if now - last_alert_blink >= ALERT_BLINK_EVERY_SEC:
                    last_alert_blink = now
                    alert_blink_until = now + ALERT_BLINK_DURATION_SEC
                mood = "alert" if now <= alert_blink_until else "base"

        if mood == "sleep":
            sleep_toggle = 1 if (int(now) % 2 == 0) else 2
            spr = sprite_path(stage, "sleep", sleep_toggle=sleep_toggle)
        else:
            spr = sprite_path(stage, mood)

        # --- GPS UI + COORD line (your rules) ---
        has_coords = _valid_lat_lon(fix.lat, fix.lon)
        gps_status = "OK" if (has_coords and not fix.stale) else ("STALE" if has_coords else "WAIT")
        gps_fix = f"{fix.mode}D" if fix.mode in (2, 3) else "NO"

        used = int(getattr(fix, "used_sats", 0) or 0)
        view = int(getattr(fix, "view_sats", 0) or 0)

        acc = getattr(fix, "acc_m", None)
        acc_str = f"{int(round(acc))}m" if isinstance(acc, (int, float)) and acc > 0 else "--"

        scan_tag = "ok"
        if last_scan_status == "TO":
            scan_tag = "TO"
        elif last_scan_status == "ER":
            scan_tag = "ER"

        evol_left = remaining_to_evolve(open_count)

        ALLOW_DECIMAL_K = False
        o_disp = fmt_k(int(state.get("open_unique_count", 0)), allow_decimal=ALLOW_DECIMAL_K)
        s_disp = fmt_k(int(state.get("secure_unique_count", 0)), allow_decimal=ALLOW_DECIMAL_K)
        a_disp = fmt_k(int(state.get("all_unique_count", 0)), allow_decimal=ALLOW_DECIMAL_K)

        age_s = int(getattr(fix, "fix_age_sec", 999999) or 999999)
        coord_age = f"{age_s:>3d}s" if has_coords else "---"
        coord_line = f"{{b}}COORD{{/b}} {coord_age}" + (" {{b}}STL{{/b}} Y" if (has_coords and fix.stale) else "")

        lines = [
            f"{{b}}GPS{{/b}} {gps_status} {gps_fix} {{b}}U{{/b}}:{used:02d}/{view:02d}",
            f"{{b}}ACC{{/b}} {acc_str}",
            f"{{b}}O{{/b}} {o_disp} {{b}}S{{/b}} {s_disp} {{b}}A{{/b}} {a_disp}",
            f"{{b}}APs{{/b}} SEEN {int(last_scan_bss_count)}",
            f"{{b}}SCAN{{/b}} {scan_tag} {SCAN_INTERVAL_SEC}s",
            f"{{b}}MOOD{{/b}} {mood.upper()} {{b}}STG{{/b}} {stage}",
            f"{{b}}EVOL{{/b}} +{int(evol_left)}",
            coord_line,
        ]

        stats = {"lines": lines}

        if now - last_display >= DISPLAY_UPDATE_SEC:
            last_display = now
            invert = (now <= evolution_flash_until) and (int(now * 4) % 2 == 0)
            disp.render(stats, spr, invert_flash=invert)

        time.sleep(0.2)

if __name__ == "__main__":
    main()
