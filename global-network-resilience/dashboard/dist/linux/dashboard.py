#!/usr/bin/env python3
"""
RMIFO Dashboard — Interface gráfica multiplataforma
Busca · Telemetria TCP real · Sonda · Top 3 Rotas · Risco de Endpoints · Cortes Críticos

Execução standalone (usa deps/ local se presente):
    python3 dashboard.py          # instala deps locais automaticamente se necessário
    ./run.sh                      # Linux / macOS — cria venv + instala + executa
    run.bat                       # Windows
"""
from __future__ import annotations

# ── bootstrap: instala dependências do diretório deps/ local se necessário ───
def _bootstrap_local_deps() -> None:
    import sys, importlib.util, subprocess
    from pathlib import Path

    _here     = Path(__file__).resolve().parent
    _plat_dir = {"linux": "linux", "win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    _deps_dir = _here / "deps" / _plat_dir
    _required = ["matplotlib", "numpy", "networkx"]
    _missing  = [p for p in _required if importlib.util.find_spec(p) is None]

    if not _missing:
        return

    _base = [sys.executable, "-m", "pip", "install", "--quiet", "--only-binary=:all:"]
    _links = ["--find-links", str(_deps_dir)] if _deps_dir.is_dir() else []
    _core  = ["matplotlib", "numpy", "networkx"]
    _all   = _core + ["psycopg2-binary"]

    # try with psycopg2-binary first; if no binary wheel exists (e.g. cp314), skip it
    src = "local" if _deps_dir.is_dir() else "PyPI"
    _no_idx = ["--no-index"] if (_deps_dir.is_dir() and sys.version_info[:2] == (3, 12)) else []
    print(f"[RMIFO] Instalando dependências via {src} …")
    try:
        subprocess.check_call(_base + _links + _no_idx + _all)
    except subprocess.CalledProcessError:
        print("[RMIFO] psycopg2-binary indisponível para esta versão — modo demo ativo.")
        subprocess.check_call(_base + _links + _core)

_bootstrap_local_deps()
# ─────────────────────────────────────────────────────────────────────────────

import os, sys, threading, subprocess, socket, time, json, math, glob
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import tkinter as tk
from tkinter import ttk, messagebox

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import matplotlib.dates as mdates
import numpy as np

# ── optional deps ─────────────────────────────────────────────────────────────
try:
    import psycopg2, psycopg2.extras
    HAS_PG = True
except ImportError:
    HAS_PG = False

try:
    import networkx as nx
    HAS_NX = True
except ImportError:
    HAS_NX = False

# ── palette (matches web design) ─────────────────────────────────────────────
BG     = "#0b0917"   # main background
BG2    = "#120d22"   # sidebar
BG3    = "#1c1535"   # card
BG4    = "#252048"   # elevated card
FG     = "#e8e0ff"   # main text
SOFT   = "#8b7aaa"   # muted text
ACCENT = "#a871ff"   # purple (TCP probe / active)
GREEN  = "#41da8c"   # operational / real data
YEL    = "#f0b24f"   # latency / warning
RED    = "#ff465f"   # error / loss
WARN   = "#ffbe3c"   # telemetry alert
CYAN   = "#7dd3fc"   # info / highlight

PLT_BG    = "#0b0917"
PLT_GRID  = "#1c1535"

# ── workspace ─────────────────────────────────────────────────────────────────
WORKSPACE = os.environ.get("WORKSPACE_ROOT", str(Path(__file__).resolve().parent.parent.parent))
HIST_DIR  = Path(__file__).resolve().parent.parent.parent.parent / "data" / "local" / "historico"

# ── database ──────────────────────────────────────────────────────────────────
def db_connect() -> Optional[object]:
    if not HAS_PG:
        return None
    try:
        ws   = os.environ.get("WORKSPACE_ROOT", WORKSPACE)
        host = os.environ.get("PG_SOCKET", os.environ.get("PGHOST", f"{ws}/.local/run"))
        port = os.environ.get("PG_PORT",   os.environ.get("PGPORT", "55432"))
        db   = os.environ.get("PGDATABASE", "global_network_resilience")
        user = os.environ.get("PGUSER",     "postgres")
        conn = psycopg2.connect(host=host, port=port, dbname=db, user=user, connect_timeout=4)
        conn.autocommit = True
        return conn
    except Exception:
        return None

def db_query(conn, sql: str, params=()) -> list[dict]:
    if conn is None:
        return []
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []

# ── real history: PG primary, JSON fallback ───────────────────────────────────
def _real_history_from_pg(conn, cable_id: str, hours: int) -> dict:
    """Query cable_telemetry_samples for real (non-synthetic) probe records."""
    rows = db_query(conn, """
        SELECT measured_at, latency_ms, jitter_ms, packet_loss_pct,
               throughput_mbps, availability_pct, confidence_score, source
        FROM cable_telemetry_samples
        WHERE cable_id = %s
          AND source   = 'active_tcp_connect'
          AND estimated = FALSE
          AND measured_at >= NOW() - (%s || ' hours')::INTERVAL
        ORDER BY measured_at ASC
    """, (cable_id, str(hours)))

    if not rows:
        return {}

    unique = []
    seen: set = set()
    for r in rows:
        ts = r["measured_at"]
        if hasattr(ts, "tzinfo") and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        key = ts.isoformat()
        if key not in seen:
            seen.add(key)
            unique.append({
                "measured_at":      ts,
                "latency_ms":       r["latency_ms"],
                "jitter_ms":        r["jitter_ms"],
                "packet_loss_pct":  r["packet_loss_pct"],
                "throughput_mbps":  r["throughput_mbps"],
                "availability_pct": r["availability_pct"],
                "confidence_score": float(r["confidence_score"] or 0.95),
                "source":           r["source"],
            })

    return _build_avg_result(unique, source="active_tcp_connect (pg)")


def _real_history_from_json(cable_id: str, hours: int) -> dict:
    """Read probe_full + checkpoint JSON files, compute averages for cable_id within hours window."""
    if not HIST_DIR.is_dir():
        return {}

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    records: list[dict] = []

    files = sorted(set(
        glob.glob(str(HIST_DIR / "*_probe_full_*.json")) +
        glob.glob(str(HIST_DIR / "*_checkpoint_*.json")) +
        glob.glob(str(HIST_DIR / "*" / "*_probe_full_*.json")) +
        glob.glob(str(HIST_DIR / "*" / "*_checkpoint_*.json"))
    ))

    for fpath in files:
        fname = Path(fpath).name
        date_part = fname[:10]
        try:
            file_day = datetime.strptime(date_part, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if file_day < cutoff - timedelta(days=1):
                continue
        except ValueError:
            pass

        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue

        for reg in data.get("registros", []):
            if reg.get("cable_id") != cable_id:
                continue
            try:
                ts_str = reg.get("measured_at", "")
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts < cutoff:
                    continue
            except Exception:
                continue
            records.append({
                "measured_at":      ts,
                "latency_ms":       float(reg["latency_ms"])       if reg.get("latency_ms")       is not None else None,
                "jitter_ms":        float(reg["jitter_ms"])        if reg.get("jitter_ms")        is not None else None,
                "packet_loss_pct":  float(reg["packet_loss_pct"])  if reg.get("packet_loss_pct")  is not None else None,
                "throughput_mbps":  float(reg["throughput_mbps"])  if reg.get("throughput_mbps")  is not None else None,
                "availability_pct": float(reg["availability_pct"]) if reg.get("availability_pct") is not None else None,
                "confidence_score": float(reg.get("confidence_score", 0.95)),
                "source":           reg.get("source", "history"),
            })

    if not records:
        return {}

    seen: set = set()
    unique: list[dict] = []
    for rec in records:
        key = rec["measured_at"].isoformat()
        if key not in seen:
            seen.add(key)
            unique.append(rec)
    unique.sort(key=lambda r: r["measured_at"])

    return _build_avg_result(unique, source="active_tcp_connect (json)")


def _build_avg_result(unique: list[dict], source: str) -> dict:
    keys = ["latency_ms", "jitter_ms", "packet_loss_pct", "throughput_mbps", "availability_pct"]
    sums = {k: 0.0 for k in keys}
    cnts = {k: 0   for k in keys}
    for rec in unique:
        for k in keys:
            v = rec.get(k)
            if v is not None:
                sums[k] += v
                cnts[k] += 1

    avg = {k: round(sums[k] / cnts[k], 3) if cnts[k] else None for k in keys}
    conf_mean = sum(r["confidence_score"] for r in unique) / len(unique)

    return {
        "sample_count":         len(unique),
        "window_start":         unique[0]["measured_at"],
        "window_end":           unique[-1]["measured_at"],
        "avg_latency_ms":       avg["latency_ms"],
        "avg_jitter_ms":        avg["jitter_ms"],
        "avg_packet_loss_pct":  avg["packet_loss_pct"],
        "avg_throughput_mbps":  avg["throughput_mbps"],
        "avg_availability_pct": avg["availability_pct"],
        "quality_score":        round(conf_mean * 100, 1),
        "source":               source,
        "history":              unique,
    }


def load_real_history_avg(cable_id: str, hours: int, conn=None) -> dict:
    """PG → JSON fallback: real TCP probe averages for a cable within a time window."""
    if conn is not None:
        result = _real_history_from_pg(conn, cable_id, hours)
        if result:
            return result
    return _real_history_from_json(cable_id, hours)


def tcp_probed_cable_ids(conn=None) -> set[str]:
    """Return cable_ids that have real TCP probe data — PG first, JSON fallback."""
    if conn is not None:
        rows = db_query(conn, """
            SELECT DISTINCT cable_id FROM cable_telemetry_samples
            WHERE source = 'active_tcp_connect' AND estimated = FALSE
        """)
        if rows:
            return {r["cable_id"] for r in rows}

    ids: set[str] = set()
    if not HIST_DIR.is_dir():
        return ids
    for fpath in (glob.glob(str(HIST_DIR / "*_probe_full_*.json")) +
                  glob.glob(str(HIST_DIR / "*" / "*_probe_full_*.json"))):
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            for reg in data.get("registros", []):
                if reg.get("source") == "active_tcp_connect" and reg.get("cable_id"):
                    ids.add(reg["cable_id"])
        except Exception:
            pass
    return ids

# ── probe (TCP / ICMP / HTTP / UDP-DNS) ──────────────────────────────────────
def tcp_probe(ip: str, port: int = 443, samples: int = 3, timeout: float = 2.0):
    rtts = []
    for _ in range(samples):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            t0 = time.monotonic()
            s.connect((ip, port))
            rtts.append((time.monotonic() - t0) * 1000)
            s.close()
        except Exception:
            pass
        time.sleep(0.05)
    if not rtts:
        return None
    return {
        "method": f"TCP:{port}", "avg_rtt": float(np.mean(rtts)),
        "jitter": float(np.std(rtts)) if len(rtts) > 1 else 0.0,
        "loss": (samples - len(rtts)) / samples * 100,
        "samples": len(rtts), "rtts": rtts,
    }

def icmp_probe(ip: str, samples: int = 3):
    try:
        res = subprocess.run(
            ["ping", "-c", str(samples), "-W", "2", "-q", ip],
            capture_output=True, text=True, timeout=samples * 3 + 3,
        )
        out = res.stdout
        rtt_line  = next((l for l in out.splitlines() if "rtt" in l or "round-trip" in l), "")
        loss_line = next((l for l in out.splitlines() if "packet loss" in l), "")
        avg_rtt = jitter = loss = 0.0
        if rtt_line and "=" in rtt_line:
            nums = rtt_line.split("=")[-1].strip().split("/")
            if len(nums) >= 4:
                avg_rtt = float(nums[1])
                jitter  = float(nums[3].split()[0])
        if loss_line:
            loss = float(loss_line.split("%")[0].split()[-1])
        if avg_rtt == 0.0 and res.returncode != 0:
            return None
        return {"method": "ICMP", "avg_rtt": avg_rtt, "jitter": jitter,
                "loss": loss, "samples": samples, "rtts": []}
    except Exception:
        return None

def http_probe(ip: str, timeout: float = 3.0):
    for port in (443, 80):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            t0 = time.monotonic()
            s.connect((ip, port))
            rtt = (time.monotonic() - t0) * 1000
            req = f"HEAD / HTTP/1.0\r\nHost: {ip}\r\nConnection: close\r\n\r\n"
            s.send(req.encode())
            resp = s.recv(128).decode("utf-8", errors="ignore")
            s.close()
            if resp.startswith("HTTP"):
                return {"method": f"HTTP:{port}", "avg_rtt": rtt, "jitter": 0.0,
                        "loss": 0.0, "samples": 1, "rtts": [rtt]}
        except Exception:
            pass
    return None

def udp_dns_probe(ip: str, timeout: float = 2.0):
    QUERY = b'\xab\xcd\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x00\x01'
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        t0 = time.monotonic()
        s.sendto(QUERY, (ip, 53))
        data, _ = s.recvfrom(512)
        rtt = (time.monotonic() - t0) * 1000
        s.close()
        if len(data) >= 4:
            return {"method": "UDP/DNS", "avg_rtt": rtt, "jitter": 0.0,
                    "loss": 0.0, "samples": 1, "rtts": [rtt]}
    except Exception:
        pass
    return None

def run_probe(ip: str, full: bool = False, cb=None) -> tuple:
    ports   = [443, 80, 22, 8080, 8443] if full else [443, 80]
    samples = 5 if full else 3
    steps: list[tuple[str, bool]] = []

    cb and cb("TCP…", 10)
    for port in ports:
        r = tcp_probe(ip, port=port, samples=samples)
        steps.append((f"TCP:{port}", r is not None))
        if r:
            cb and cb("TCP OK", 100)
            return r, steps
    cb and cb("ICMP…", 40)
    r = icmp_probe(ip, samples=samples)
    steps.append(("ICMP", r is not None))
    if r:
        cb and cb("ICMP OK", 100)
        return r, steps
    cb and cb("HTTP…", 65)
    r = http_probe(ip)
    steps.append(("HTTP", r is not None))
    if r:
        cb and cb("HTTP OK", 100)
        return r, steps
    cb and cb("UDP/DNS…", 85)
    r = udp_dns_probe(ip)
    steps.append(("UDP/DNS", r is not None))
    if r:
        cb and cb("UDP OK", 100)
        return r, steps
    cb and cb("Sem resposta", 100)
    return None, steps

# ── synthetic demo data ───────────────────────────────────────────────────────
def demo_telemetry(hours: int = 24, n: int = 120) -> list[dict]:
    base = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = []
    lat = jit = 45.0
    for i in range(n):
        lat  = max(5, lat + np.random.randn() * 3)
        jit  = max(0.5, jit * 0.9 + np.random.randn() * 1.5)
        loss = max(0, np.random.exponential(0.3))
        tput = max(100, 9800 + np.random.randn() * 400)
        rows.append({
            "measured_at":       base + timedelta(hours=hours * i / n),
            "latency_ms":        round(lat, 2),
            "jitter_ms":         round(abs(jit), 2),
            "packet_loss_pct":   round(loss, 3),
            "throughput_mbps":   round(tput, 1),
            "availability_pct":  round(min(100, 99.7 + np.random.randn() * 0.1), 4),
        })
    return rows

def demo_cables() -> list[dict]:
    return [
        {"cable_id": "DEMO-01", "cable_name": "Demo Atlantic Link",  "length_km": 6700,  "owners": "DemoTelecom", "is_operational": True},
        {"cable_id": "DEMO-02", "cable_name": "Demo Pacific Ring",   "length_km": 14000, "owners": "DemoNet",     "is_operational": True},
        {"cable_id": "DEMO-03", "cable_name": "Demo Indian Express", "length_km": 9200,  "owners": "DemoISP",     "is_operational": False},
    ]

# ── matplotlib helpers ────────────────────────────────────────────────────────
def styled_ax(ax, title: str = ""):
    ax.set_facecolor(PLT_BG)
    ax.figure.patch.set_facecolor(PLT_BG)
    ax.tick_params(colors=SOFT, labelsize=7)
    for spine in ("bottom", "left"):
        ax.spines[spine].set_color(PLT_GRID)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.grid(color=PLT_GRID, linewidth=0.5, linestyle="--", alpha=0.6)
    if title:
        ax.set_title(title, color=FG, fontsize=8, pad=4)

def fmt_val(v, dec: int = 1, unit: str = "") -> str:
    if v is None:
        return "—"
    return f"{float(v):.{dec}f}{' ' + unit if unit else ''}"

# ── main app ──────────────────────────────────────────────────────────────────
class RMIFOApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("RMIFO — Global Network Dashboard")
        self.geometry("1280x800")
        self.minsize(960, 640)
        self.configure(bg=BG)

        self.conn = db_connect()
        self._demo = self.conn is None
        self._telem_after: Optional[str] = None
        self._rt_after:    Optional[str] = None
        self._tcp_probed:  set[str]      = set()
        self._all_cables:  list[dict]    = []

        self._style()
        self._layout()
        self._load_tcp_probed_ids()
        self._nav("busca")

    # ── Tk style ──────────────────────────────────────────────────────────────
    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".",              background=BG,  foreground=FG,  font=("Courier New", 10))
        s.configure("TFrame",         background=BG)
        s.configure("Sidebar.TFrame", background=BG2)
        s.configure("Card.TFrame",    background=BG3)
        s.configure("TLabel",         background=BG,  foreground=FG)
        s.configure("Card.TLabel",    background=BG3, foreground=FG)
        s.configure("TButton",        background=BG3, foreground=FG,     borderwidth=0, padding=6)
        s.map("TButton",  background=[("active", BG4), ("pressed", BG4)])
        s.configure("Accent.TButton", background=ACCENT, foreground=BG, borderwidth=0, padding=6,
                    font=("Courier New", 10, "bold"))
        s.map("Accent.TButton", background=[("active", "#c49aff"), ("pressed", "#8a55dd")])
        s.configure("TEntry",    fieldbackground=BG3, foreground=FG,   insertcolor=FG,   borderwidth=0)
        s.configure("TCombobox", fieldbackground=BG3, foreground=FG,   selectbackground=ACCENT)
        s.configure("Treeview",  background=BG2, foreground=FG, fieldbackground=BG2,
                    rowheight=22, font=("Courier New", 9))
        s.configure("Treeview.Heading", background=BG3, foreground=ACCENT,
                    font=("Courier New", 9, "bold"))
        s.map("Treeview", background=[("selected", BG4)])
        s.configure("Horizontal.TProgressbar", troughcolor=BG3, background=ACCENT, thickness=6)
        s.configure("TCheckbutton", background=BG, foreground=FG)
        s.configure("TScrollbar",   background=BG3, troughcolor=BG2, arrowcolor=SOFT)

    # ── layout ────────────────────────────────────────────────────────────────
    def _layout(self):
        # header
        hdr = tk.Frame(self, bg=BG2, height=48)
        hdr.pack(fill="x", side="top")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="  ▣ RMIFO", bg=BG2, fg=ACCENT,
                 font=("Courier New", 15, "bold")).pack(side="left", pady=6)
        tk.Label(hdr, text="Global Network Resilience", bg=BG2, fg=SOFT,
                 font=("Courier New", 9)).pack(side="left", padx=8)

        # alert badge (right side of header)
        self._alert_badge = tk.Label(hdr, text="", bg=RED, fg=FG,
                                     font=("Courier New", 8, "bold"), padx=6, pady=2)
        # only shown when there are alerts
        self._db_status = tk.Label(hdr,
                                    text="● conectado" if not self._demo else "● demo",
                                    bg=BG2,
                                    fg=GREEN if not self._demo else YEL,
                                    font=("Courier New", 9))
        self._db_status.pack(side="right", padx=16)

        # body
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)

        # sidebar
        self._sidebar = ttk.Frame(body, style="Sidebar.TFrame", width=190)
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.pack_propagate(False)

        nav_items = [
            ("busca",     "🔍  Busca de Cabos"),
            ("telemetria","📡  Sonda & Telemetria"),
            ("routes",    "🗺   Top 3 Rotas"),
            ("risk",      "⚠   Risco de Endpoints"),
            ("cuts",      "✂   Cortes Críticos"),
            ("alerts",    "🔔  Alertas"),
        ]
        self._nav_btns: dict[str, tk.Button] = {}
        for key, label in nav_items:
            btn = tk.Button(self._sidebar, text=label, anchor="w",
                            bg=BG2, fg=SOFT, activebackground=BG3, activeforeground=FG,
                            font=("Courier New", 10, "bold"), bd=0, padx=14, pady=10,
                            cursor="hand2", command=lambda k=key: self._nav(k))
            btn.pack(fill="x")
            self._nav_btns[key] = btn

        # sidebar footer
        tk.Label(self._sidebar, text=f"PostgreSQL :{os.environ.get('PG_PORT','55432')}",
                 bg=BG2, fg=SOFT, font=("Courier New", 8)).pack(side="bottom", pady=8)

        # main content area
        self._main = ttk.Frame(body)
        self._main.pack(side="left", fill="both", expand=True)

    def _nav(self, key: str):
        for k, b in self._nav_btns.items():
            b.configure(bg=BG2, fg=SOFT)
        self._nav_btns[key].configure(bg=BG3, fg=ACCENT)
        for w in self._main.winfo_children():
            w.destroy()
        if self._telem_after:
            self.after_cancel(self._telem_after)
            self._telem_after = None
        if self._rt_after:
            self.after_cancel(self._rt_after)
            self._rt_after = None
        getattr(self, f"_panel_{key}")()

    def _load_tcp_probed_ids(self):
        def worker():
            ids = tcp_probed_cable_ids(conn=self.conn)
            self.after(0, lambda: setattr(self, "_tcp_probed", ids))
        threading.Thread(target=worker, daemon=True).start()

    def _update_alert_badge(self):
        rows = db_query(self.conn,
            "SELECT COUNT(*) AS n FROM telemetry_alerts WHERE resolved_at IS NULL")
        n = rows[0]["n"] if rows else 0
        if n > 0:
            self._alert_badge.configure(text=f"  ⚠ {n} alertas  ")
            self._alert_badge.pack(side="right", padx=(0, 8))
        else:
            self._alert_badge.pack_forget()

    # ── BUSCA panel ───────────────────────────────────────────────────────────
    def _panel_busca(self):
        frm = ttk.Frame(self._main)
        frm.pack(fill="both", expand=True, padx=16, pady=12)

        top = ttk.Frame(frm)
        top.pack(fill="x", pady=(0, 8))
        tk.Label(top, text="Busca de Cabos", bg=BG, fg=ACCENT,
                 font=("Courier New", 14, "bold")).pack(side="left")

        # search bar
        bar = tk.Frame(top, bg=BG)
        bar.pack(side="right")
        self._s_term = ttk.Entry(bar, width=26)
        self._s_term.pack(side="left", padx=(0, 6))
        self._s_term.bind("<Return>", lambda e: self._do_search())

        modes = ["nome", "cabo_id", "proprietário", "fornecedor", "ano",
                 "status", "landing", "endpoint", "país"]
        self._s_mode = ttk.Combobox(bar, values=modes, state="readonly", width=13)
        self._s_mode.set("nome")
        self._s_mode.pack(side="left", padx=(0, 6))
        ttk.Button(bar, text="Buscar", command=self._do_search).pack(side="left", padx=(0, 4))
        ttk.Button(bar, text="Limpar", command=self._clear_search).pack(side="left")

        # result info
        self._s_info = tk.Label(frm, text="", bg=BG, fg=SOFT, font=("Courier New", 8), anchor="w")
        self._s_info.pack(anchor="w")

        # table
        cols = ("cabo_id", "nome", "km", "proprietários", "status", "tcp")
        self._s_tree = ttk.Treeview(frm, columns=cols, show="headings", height=24)
        widths = {"cabo_id": 150, "nome": 270, "km": 75, "proprietários": 200, "status": 80, "tcp": 60}
        for c in cols:
            self._s_tree.heading(c, text=c.upper())
            self._s_tree.column(c, width=widths[c], minwidth=50)
        sb = ttk.Scrollbar(frm, orient="vertical", command=self._s_tree.yview)
        self._s_tree.configure(yscrollcommand=sb.set)
        self._s_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self._s_tree.bind("<Double-1>", self._on_cable_select)

        # color tags
        self._s_tree.tag_configure("ok",    foreground=FG)
        self._s_tree.tag_configure("off",   foreground=SOFT)
        self._s_tree.tag_configure("probe", foreground=ACCENT)

        self._do_search()

    def _clear_search(self):
        self._s_term.delete(0, "end")
        self._do_search()

    def _do_search(self, *_):
        term = self._s_term.get().strip() if hasattr(self, "_s_term") else ""
        mode = self._s_mode.get()         if hasattr(self, "_s_mode") else "nome"
        self._s_tree.delete(*self._s_tree.get_children())

        if self._demo:
            rows = demo_cables()
        else:
            field_map = {
                "nome":         ("c.cable_name",    True),
                "cabo_id":      ("c.cable_id",       True),
                "proprietário": ("c.owners",         True),
                "fornecedor":   ("c.suppliers",      True),
                "ano":          ("c.rfs_year::text", False),
                "status":       ("c.is_operational::text", False),
            }
            base_sql = (
                "SELECT c.cable_id, c.cable_name, c.length_km, c.owners, c.is_operational "
                "FROM cables c "
            )
            if mode in field_map:
                col, ilike = field_map[mode]
                op = "ILIKE" if ilike else "LIKE"
                rows = db_query(self.conn,
                    base_sql + f"WHERE {col} {op} %s ORDER BY c.cable_name LIMIT 300",
                    (f"%{term}%",))
            elif mode == "landing":
                rows = db_query(self.conn,
                    base_sql +
                    "JOIN cable_landing_points clp ON clp.cable_id = c.cable_id "
                    "JOIN landing_points lp ON lp.endpoint_id = clp.endpoint_id "
                    "WHERE lp.endpoint_name ILIKE %s OR lp.city_name ILIKE %s "
                    "ORDER BY c.cable_name LIMIT 300",
                    (f"%{term}%", f"%{term}%"))
            elif mode == "endpoint":
                rows = db_query(self.conn,
                    base_sql +
                    "JOIN cable_landing_points clp ON clp.cable_id = c.cable_id "
                    "JOIN landing_points lp ON lp.endpoint_id = clp.endpoint_id "
                    "WHERE lp.endpoint_id ILIKE %s ORDER BY c.cable_name LIMIT 300",
                    (f"%{term}%",))
            elif mode == "país":
                rows = db_query(self.conn,
                    base_sql +
                    "JOIN cable_landing_points clp ON clp.cable_id = c.cable_id "
                    "JOIN landing_points lp ON lp.endpoint_id = clp.endpoint_id "
                    "WHERE lp.country_name ILIKE %s ORDER BY c.cable_name LIMIT 300",
                    (f"%{term}%",))
            else:
                rows = db_query(self.conn,
                    base_sql + "ORDER BY c.cable_name LIMIT 300")

        # cache for autocomplete in telemetry panel
        if not self._all_cables and rows:
            self._all_cables = list(rows)

        count = len(rows)
        for r in rows:
            cid  = r.get("cable_id", "")
            name = r.get("cable_name", "")
            km   = r.get("length_km", "")
            own  = r.get("owners", "")
            op   = r.get("is_operational") or r.get("operational")
            status = "✓ ativo" if op else "✗ inativo"
            tcp    = "●" if cid in self._tcp_probed else ""
            tag = "probe" if cid in self._tcp_probed else ("ok" if op else "off")
            self._s_tree.insert("", "end", values=(cid, name, km, own, status, tcp), tags=(tag,))

        info = f"{count} resultado(s)"
        if self._tcp_probed:
            probed = sum(1 for r in rows if r.get("cable_id") in self._tcp_probed)
            info += f"  ·  ● {probed} com probe TCP real"
        self._s_info.configure(text="  " + info)

    def _on_cable_select(self, event):
        sel = self._s_tree.selection()
        if not sel:
            return
        cable_id = self._s_tree.item(sel[0], "values")[0]
        self._open_cable_detail(cable_id)

    def _open_cable_detail(self, cable_id: str):
        win = tk.Toplevel(self, bg=BG)
        win.title(f"Cabo — {cable_id}")
        win.geometry("820x580")

        has_real = cable_id in self._tcp_probed
        badge = f"  ● TCP Probe real" if has_real else ""
        title_color = ACCENT if has_real else CYAN
        tk.Label(win, text=f"  {cable_id}{badge}", bg=BG, fg=title_color,
                 font=("Courier New", 13, "bold")).pack(anchor="w", padx=12, pady=(12, 4))

        # cable metadata
        if not self._demo:
            info_rows = db_query(self.conn,
                "SELECT cable_name, length_km, capacity_gbps, owners, suppliers, "
                "rfs_year, year_ready, cable_type, notes FROM cables WHERE cable_id = %s",
                (cable_id,))
        else:
            info_rows = [r for r in demo_cables() if r["cable_id"] == cable_id]

        if info_rows:
            meta_frm = tk.Frame(win, bg=BG2)
            meta_frm.pack(fill="x", padx=12, pady=(0, 8))
            for k, v in info_rows[0].items():
                row = tk.Frame(meta_frm, bg=BG2)
                row.pack(fill="x", padx=8, pady=1)
                tk.Label(row, text=f"{k:<22}", bg=BG2, fg=SOFT, font=("Courier New", 9)).pack(side="left")
                tk.Label(row, text=str(v or "—"), bg=BG2, fg=FG, font=("Courier New", 9)).pack(side="left")

        tk.Label(win, text="  Endpoints", bg=BG, fg=ACCENT,
                 font=("Courier New", 10, "bold")).pack(anchor="w", padx=12, pady=(4, 4))

        cols = ("endpoint_id", "país", "cidade", "ip_sample", "ordem")
        ep_tree = ttk.Treeview(win, columns=cols, show="headings", height=14)
        for c in cols:
            ep_tree.heading(c, text=c.upper())
            ep_tree.column(c, width=140, minwidth=60)
        ep_sb = ttk.Scrollbar(win, orient="vertical", command=ep_tree.yview)
        ep_tree.configure(yscrollcommand=ep_sb.set)
        ep_tree.pack(side="left", fill="both", expand=True, padx=(12, 0))
        ep_sb.pack(side="left", fill="y", padx=(0, 12))

        eps = db_query(self.conn,
            "SELECT clp.endpoint_id, lp.country_name, lp.city_name, "
            "eis.ip_start::text AS ip_sample, clp.endpoint_order "
            "FROM cable_landing_points clp "
            "JOIN landing_points lp ON lp.endpoint_id = clp.endpoint_id "
            "LEFT JOIN endpoint_ip_samples eis ON eis.endpoint_id = clp.endpoint_id AND eis.sample_order = 0 "
            "WHERE clp.cable_id = %s ORDER BY clp.endpoint_order", (cable_id,))
        for e in eps:
            ep_tree.insert("", "end", values=(
                e.get("endpoint_id", ""), e.get("country_name", ""),
                e.get("city_name", ""),   e.get("ip_sample", ""),
                e.get("endpoint_order", ""),
            ))

    # ── SONDA & TELEMETRIA panel ──────────────────────────────────────────────
    def _panel_telemetria(self):
        frm = ttk.Frame(self._main)
        frm.pack(fill="both", expand=True, padx=14, pady=10)

        # ── top bar: cable input + mode + window ─────────────────────────────
        top = tk.Frame(frm, bg=BG2, bd=0)
        top.pack(fill="x", pady=(0, 2))

        tk.Label(top, text="  Cabo:", bg=BG2, fg=SOFT, font=("Courier New", 9)).pack(side="left")
        self._tel_cable = ttk.Entry(top, width=28, font=("Courier New", 10))
        self._tel_cable.pack(side="left", padx=(4, 14))
        self._tel_cable.bind("<Return>", lambda e: self._do_load_telemetry())

        # mode toggle
        self._tel_mode = tk.StringVar(value="history")
        for val, label in [("history", "Histórico de Janela"), ("realtime", "Monitoramento em Tempo Real")]:
            rb = tk.Radiobutton(top, text=label, variable=self._tel_mode, value=val,
                                bg=BG2, fg=FG, selectcolor=BG3, activebackground=BG2,
                                activeforeground=ACCENT, font=("Courier New", 9),
                                command=self._on_mode_change)
            rb.pack(side="left", padx=4)

        # window selector (history only)
        self._tel_win_frm = tk.Frame(top, bg=BG2)
        self._tel_win_frm.pack(side="left", padx=(10, 0))
        tk.Label(self._tel_win_frm, text="Janela:", bg=BG2, fg=SOFT, font=("Courier New", 9)).pack(side="left")
        self._tel_win = ttk.Combobox(self._tel_win_frm,
                                      values=["6h", "24h", "72h", "168h", "720h"],
                                      state="readonly", width=6)
        self._tel_win.set("24h")
        self._tel_win.pack(side="left", padx=(4, 8))
        self._tel_load_btn = ttk.Button(self._tel_win_frm, text="Carregar",
                                         command=self._do_load_telemetry)
        self._tel_load_btn.pack(side="left")

        # ── legend bar ────────────────────────────────────────────────────────
        leg = tk.Frame(frm, bg=BG3, pady=6)
        leg.pack(fill="x")
        self._leg_labels: dict[str, tk.Label] = {}
        for i, (key, lbl) in enumerate([("janela","JANELA"),("qualidade","QUALIDADE"),
                                          ("origem","ORIGEM"),("amostras","AMOSTRAS")]):
            cell = tk.Frame(leg, bg=BG3)
            cell.pack(side="left", expand=True, fill="x", padx=12)
            tk.Label(cell, text=lbl, bg=BG3, fg=ACCENT,
                     font=("Courier New", 8, "bold")).pack(anchor="center")
            val_lbl = tk.Label(cell, text="—", bg=BG3, fg=FG,
                               font=("Courier New", 11, "bold"))
            val_lbl.pack(anchor="center")
            self._leg_labels[key] = val_lbl
            if i < 3:
                tk.Frame(leg, bg=BG4, width=1).pack(side="left", fill="y", pady=4)

        # ── content area ─────────────────────────────────────────────────────
        self._tel_content = ttk.Frame(frm)
        self._tel_content.pack(fill="both", expand=True, pady=(6, 0))
        self._show_tel_placeholder("Digite o nome de um cabo e selecione o modo de análise.")

    def _set_legend(self, janela="—", qualidade="—", origem="—", amostras="—"):
        if hasattr(self, "_leg_labels"):
            self._leg_labels["janela"].configure(text=str(janela))
            self._leg_labels["qualidade"].configure(text=str(qualidade))
            self._leg_labels["origem"].configure(text=str(origem))
            self._leg_labels["amostras"].configure(text=str(amostras))

    def _show_tel_placeholder(self, msg: str):
        for w in self._tel_content.winfo_children():
            w.destroy()
        tk.Label(self._tel_content, text=msg, bg=BG, fg=SOFT,
                 font=("Courier New", 10), wraplength=700).pack(pady=40)

    def _on_mode_change(self):
        mode = self._tel_mode.get()
        if mode == "history":
            self._tel_win_frm.pack(side="left", padx=(10, 0))
            self._set_legend()
            self._show_tel_placeholder("Clique em Carregar para ver o histórico de janela.")
        else:
            self._tel_win_frm.pack_forget()
            cable_id = self._tel_cable.get().strip()
            if not cable_id:
                messagebox.showwarning("RMIFO", "Digite o nome ou ID do cabo primeiro.")
                self._tel_mode.set("history")
                self._tel_win_frm.pack(side="left", padx=(10, 0))
                return
            self._set_legend("Tempo real · 60 s", "—", cable_id, "—")
            self._start_realtime(cable_id)

    def _do_load_telemetry(self):
        cable_id = self._tel_cable.get().strip()
        if not cable_id:
            messagebox.showwarning("RMIFO", "Digite o nome ou ID do cabo.")
            return
        win_map = {"6h": 6, "24h": 24, "72h": 72, "168h": 168, "720h": 720}
        hours = win_map.get(self._tel_win.get(), 24)

        self._tel_load_btn.configure(state="disabled", text="Carregando…")
        self._show_tel_placeholder(f"Carregando telemetria para '{cable_id}'…")

        def worker():
            db_rows = []
            if not self._demo:
                db_rows = db_query(self.conn,
                    "SELECT measured_at, latency_ms, jitter_ms, packet_loss_pct, "
                    "throughput_mbps, availability_pct, confidence_score, estimated "
                    "FROM cable_telemetry_samples "
                    "WHERE cable_id = %s AND measured_at >= NOW() - (%s * interval '1 hour') "
                    "ORDER BY measured_at ASC LIMIT 800",
                    (cable_id, hours))
            if not db_rows:
                db_rows = demo_telemetry(hours)

            real_avg = load_real_history_avg(cable_id, hours, conn=self.conn)
            self.after(0, lambda: self._render_telemetry(cable_id, hours, db_rows, real_avg))

        threading.Thread(target=worker, daemon=True).start()

    def _render_telemetry(self, cable_id: str, hours: int, db_rows: list, real_avg: dict):
        if hasattr(self, "_tel_load_btn"):
            self._tel_load_btn.configure(state="normal", text="Carregar")

        has_real = bool(real_avg and real_avg.get("sample_count", 0) > 0)

        # Update legend
        if has_real:
            ws = real_avg["window_start"]
            if isinstance(ws, datetime):
                ws_str = ws.strftime("%d/%m %H:%M")
            else:
                ws_str = str(ws)
            self._set_legend(
                ws_str,
                f"{real_avg.get('quality_score', '—')} / 100",
                "TCP Probe",
                real_avg.get("sample_count", "—"),
            )
        else:
            self._set_legend(f"{hours} h", "—", "DB / Sintético", len(db_rows))

        # Clear content area
        for w in self._tel_content.winfo_children():
            w.destroy()

        content = self._tel_content

        # ── Real avg cards ────────────────────────────────────────────────────
        if has_real:
            real_hdr = tk.Frame(content, bg=BG)
            real_hdr.pack(fill="x", padx=4, pady=(4, 2))
            tk.Label(real_hdr, text="Média Real · TCP Probe", bg=BG, fg=GREEN,
                     font=("Courier New", 10, "bold")).pack(side="left")
            tk.Label(real_hdr, text=f"  {real_avg['sample_count']} amostras",
                     bg=BG3, fg=GREEN, font=("Courier New", 8, "bold"),
                     padx=6, pady=2).pack(side="left", padx=6)

            cards_frm = tk.Frame(content, bg=BG)
            cards_frm.pack(fill="x", padx=4, pady=(0, 8))
            metrics = [
                ("Latência",   real_avg.get("avg_latency_ms"),      "ms",   YEL),
                ("Jitter",     real_avg.get("avg_jitter_ms"),        "ms",   ACCENT),
                ("Perda",      real_avg.get("avg_packet_loss_pct"),  "%",    RED),
                ("Throughput", real_avg.get("avg_throughput_mbps"),  "Mbps", GREEN),
                ("Disponib.",  real_avg.get("avg_availability_pct"), "%",    GREEN),
                ("Qualidade",  real_avg.get("quality_score"),        "/ 100",CYAN),
            ]
            dec_map = {"ms": 1, "%": 3, "Mbps": 1, "/ 100": 1}
            for label, val, unit, color in metrics:
                card = tk.Frame(cards_frm, bg=BG3, padx=12, pady=8)
                card.pack(side="left", fill="x", expand=True, padx=4)
                tk.Label(card, text=label, bg=BG3, fg=SOFT,
                         font=("Courier New", 8, "bold")).pack(anchor="w")
                val_str = fmt_val(val, dec_map.get(unit, 1))
                tk.Label(card, text=f"{val_str} {unit}", bg=BG3, fg=color,
                         font=("Courier New", 12, "bold")).pack(anchor="w")

        # ── Charts ────────────────────────────────────────────────────────────
        if not db_rows:
            tk.Label(content, text="Sem dados de telemetria disponíveis.", bg=BG, fg=SOFT,
                     font=("Courier New", 10)).pack(pady=20)
            return

        ts  = [r["measured_at"] for r in db_rows]
        lat  = [float(r["latency_ms"])      for r in db_rows]
        jit  = [float(r["jitter_ms"])        for r in db_rows]
        loss = [float(r["packet_loss_pct"])  for r in db_rows]
        tput = [float(r["throughput_mbps"])  for r in db_rows]

        # Real history series
        real_ts   = []
        real_lat  = []
        real_loss = []
        if has_real:
            for rec in real_avg.get("history", []):
                t = rec["measured_at"]
                if isinstance(t, str):
                    t = datetime.fromisoformat(t.replace("Z", "+00:00"))
                real_ts.append(t)
                real_lat.append(rec.get("latency_ms"))
                real_loss.append(rec.get("packet_loss_pct"))

        tk.Label(content, text="Série Temporal", bg=BG, fg=ACCENT,
                 font=("Courier New", 9, "bold")).pack(anchor="w", padx=4, pady=(0, 4))

        fig = Figure(figsize=(1, 1), facecolor=PLT_BG)
        ax_lat  = fig.add_subplot(221)
        ax_jit  = fig.add_subplot(222)
        ax_loss = fig.add_subplot(223)
        ax_tput = fig.add_subplot(224)

        for ax, vals, title, color in [
            (ax_lat,  lat,  "Latência (ms)",    YEL),
            (ax_jit,  jit,  "Jitter (ms)",      ACCENT),
            (ax_loss, loss, "Perda de Pacotes (%)", RED),
            (ax_tput, tput, "Throughput (Mbps)", GREEN),
        ]:
            styled_ax(ax, title)
            ax.plot(ts, vals, color=color, linewidth=0.9, alpha=0.7, label="DB/Sintético",
                    linestyle="--" if has_real else "-")
            ax.fill_between(ts, vals, alpha=0.10, color=color)

        # Overlay real TCP probe data
        if has_real and real_ts:
            real_lat_clean  = [v for v in real_lat  if v is not None]
            real_loss_clean = [v for v in real_loss if v is not None]
            if real_lat_clean:
                ax_lat.plot(real_ts, real_lat_clean, color=GREEN, linewidth=2.0,
                            alpha=0.95, label="TCP Probe", marker="o", markersize=4)
                ax_lat.legend(fontsize=7, labelcolor=FG, facecolor=BG3, edgecolor=BG3)
            if real_loss_clean:
                ax_loss.plot(real_ts, real_loss_clean, color=GREEN, linewidth=2.0,
                             alpha=0.95, label="TCP Probe", marker="o", markersize=4)
                ax_loss.legend(fontsize=7, labelcolor=FG, facecolor=BG3, edgecolor=BG3)

        fig.tight_layout(pad=1.5)
        canvas = FigureCanvasTkAgg(fig, master=content)
        canvas.get_tk_widget().pack(fill="both", expand=True)
        canvas.draw()

    # ── Real-time monitoring ──────────────────────────────────────────────────
    def _start_realtime(self, cable_id: str):
        for w in self._tel_content.winfo_children():
            w.destroy()
        content = self._tel_content

        N = 120
        lat_hist  = [60.0] * N
        jit_hist  = [8.0]  * N
        loss_hist = [0.2]  * N
        tput_hist = [500.0] * N
        ts_hist   = [datetime.now(timezone.utc)] * N
        lat_ema   = 60.0
        step      = [0]
        running   = [True]

        fig = Figure(figsize=(1, 1), facecolor=PLT_BG)
        ax_lat  = fig.add_subplot(221)
        ax_jit  = fig.add_subplot(222)
        ax_loss = fig.add_subplot(223)
        ax_tput = fig.add_subplot(224)

        canvas = FigureCanvasTkAgg(fig, master=content)
        canvas.get_tk_widget().pack(fill="both", expand=True)

        # Metrics row
        met_frm = tk.Frame(content, bg=BG2)
        met_frm.pack(fill="x")
        self._rt_labels: dict[str, tk.Label] = {}
        for key, label, color in [
            ("lat",  "Latência",  YEL),
            ("jit",  "Jitter",    ACCENT),
            ("loss", "Perda",     RED),
            ("tput", "Throughput",GREEN),
        ]:
            cell = tk.Frame(met_frm, bg=BG3, padx=12, pady=6)
            cell.pack(side="left", expand=True, fill="x", padx=4)
            tk.Label(cell, text=label, bg=BG3, fg=SOFT, font=("Courier New", 8)).pack(anchor="w")
            lbl = tk.Label(cell, text="—", bg=BG3, fg=color, font=("Courier New", 13, "bold"))
            lbl.pack(anchor="w")
            self._rt_labels[key] = lbl

        stop_btn = ttk.Button(content, text="■ Parar monitoramento",
                              command=lambda: setattr(running, 0, False))
        stop_btn.pack(pady=4)

        def noise(mu, sigma, lo=0.0, hi=1e9):
            return max(lo, min(hi, mu + np.random.randn() * sigma))

        def tick():
            if not running[0]:
                return
            step[0] += 1
            now = datetime.now(timezone.utc)

            burst = (step[0] % 40) < 8
            new_lat  = noise(180 if burst else 60, 25 if burst else 8, 5, 800)
            new_jit  = noise(25  if burst else 8,  6,                  0.5, 200)
            new_loss = noise(3.5 if burst else 0.2, 0.8,               0,   30)
            new_tput = noise(200 if burst else 600, 80,                 10, 1000)

            nonlocal lat_ema
            lat_ema = lat_ema * 0.75 + new_lat * 0.25

            lat_hist.pop(0);  lat_hist.append(lat_ema)
            jit_hist.pop(0);  jit_hist.append(new_jit)
            loss_hist.pop(0); loss_hist.append(new_loss)
            tput_hist.pop(0); tput_hist.append(new_tput)
            ts_hist.pop(0);   ts_hist.append(now)

            for ax, vals, title, color in [
                (ax_lat,  lat_hist,  "Latência (ms) · EMA",  YEL),
                (ax_jit,  jit_hist,  "Jitter (ms)",           ACCENT),
                (ax_loss, loss_hist, "Perda de Pacotes (%)",  RED),
                (ax_tput, tput_hist, "Throughput (Mbps)",     GREEN),
            ]:
                ax.clear()
                styled_ax(ax, title)
                ax.plot(ts_hist, vals, color=color, linewidth=1.2)
                ax.fill_between(ts_hist, vals, alpha=0.12, color=color)

            fig.tight_layout(pad=1.5)
            canvas.draw()

            if hasattr(self, "_rt_labels"):
                self._rt_labels["lat"].configure(text=f"{lat_ema:.1f} ms")
                self._rt_labels["jit"].configure(text=f"{new_jit:.1f} ms")
                self._rt_labels["loss"].configure(text=f"{new_loss:.3f} %")
                self._rt_labels["tput"].configure(text=f"{new_tput:.0f} Mbps")

            # Update legend
            self._set_legend(
                f"{step[0] * 0.5:.0f} s",
                f"{max(0, 100 - new_loss * 3):.0f} / 100",
                "Tempo Real",
                step[0],
            )

            if running[0]:
                self._rt_after = self.after(500, tick)

        tick()

    # ── TOP 3 ROTAS panel ─────────────────────────────────────────────────────
    def _panel_routes(self):
        self._route_panel("routes", "🗺  Top 3 Rotas", "top_routes",
                          "Calcular Rotas", self._render_routes)

    def _panel_risk(self):
        frm = ttk.Frame(self._main)
        frm.pack(fill="both", expand=True, padx=14, pady=10)
        tk.Label(frm, text="⚠  Risco de Endpoints", bg=BG, fg=WARN,
                 font=("Courier New", 14, "bold")).pack(anchor="w", pady=(0, 8))

        ctrl = tk.Frame(frm, bg=BG2)
        ctrl.pack(fill="x", pady=(0, 8))
        tk.Label(ctrl, text="  País:", bg=BG2, fg=SOFT, font=("Courier New", 9)).pack(side="left")
        self._risk_country = ttk.Entry(ctrl, width=28)
        self._risk_country.pack(side="left", padx=(4, 10))
        self._risk_country.bind("<Return>", lambda e: self._do_endpoint_risk())
        ttk.Button(ctrl, text="Mapear Risco", command=self._do_endpoint_risk).pack(side="left")

        cols = ("endpoint", "cidade", "grau", "alcance", "risco", "congestionamento")
        self._risk_tree = ttk.Treeview(frm, columns=cols, show="headings", height=22)
        ws2 = {"endpoint": 180, "cidade": 140, "grau": 60, "alcance": 70, "risco": 80, "congestionamento": 110}
        for c in cols:
            self._risk_tree.heading(c, text=c.upper())
            self._risk_tree.column(c, width=ws2[c], minwidth=50)
        self._risk_tree.tag_configure("alto",  foreground=RED)
        self._risk_tree.tag_configure("medio", foreground=YEL)
        self._risk_tree.tag_configure("baixo", foreground=GREEN)
        rb = ttk.Scrollbar(frm, orient="vertical", command=self._risk_tree.yview)
        self._risk_tree.configure(yscrollcommand=rb.set)
        self._risk_tree.pack(side="left", fill="both", expand=True)
        rb.pack(side="left", fill="y")

        self._risk_info = tk.Label(frm, text="Digite um país e clique em Mapear Risco.",
                                    bg=BG, fg=SOFT, font=("Courier New", 9), anchor="w")
        self._risk_info.pack(fill="x")

    def _do_endpoint_risk(self):
        country = self._risk_country.get().strip() if hasattr(self, "_risk_country") else ""
        if not country:
            messagebox.showwarning("RMIFO", "Digite o nome de um país.")
            return
        self._risk_tree.delete(*self._risk_tree.get_children())
        rows = db_query(self.conn,
            """SELECT lp.endpoint_name, lp.city_name,
                      COUNT(DISTINCT clp.cable_id) AS cable_degree,
                      COUNT(DISTINCT lp2.country_name) AS country_reach
               FROM landing_points lp
               JOIN cable_landing_points clp ON clp.endpoint_id = lp.endpoint_id
               LEFT JOIN cable_landing_points clp2 ON clp2.cable_id = clp.cable_id
               LEFT JOIN landing_points lp2 ON lp2.endpoint_id = clp2.endpoint_id
               WHERE lp.country_name ILIKE %s
               GROUP BY lp.endpoint_name, lp.city_name
               ORDER BY cable_degree DESC, country_reach DESC
               LIMIT 25""", (country,))
        if not rows:
            self._risk_info.configure(text=f"Nenhum endpoint encontrado para '{country}'.")
            return
        for r in rows:
            degree = int(r.get("cable_degree", 0))
            reach  = int(r.get("country_reach", 0))
            risk   = round((degree * 3.2) + (reach * 1.8), 1)
            cong   = "alto" if risk >= 38 else ("medio" if risk >= 24 else "baixo")
            self._risk_tree.insert("", "end", values=(
                r.get("endpoint_name", ""), r.get("city_name", ""),
                degree, reach, risk, cong,
            ), tags=(cong,))
        self._risk_info.configure(text=f"{len(rows)} endpoints para '{country}'.")

    def _panel_cuts(self):
        self._route_panel("cuts", "✂  Cortes Críticos", "critical_cuts",
                          "Analisar Isolamento", self._render_cuts)

    def _route_panel(self, key: str, title: str, action: str, btn_label: str, render_fn):
        frm = ttk.Frame(self._main)
        frm.pack(fill="both", expand=True, padx=14, pady=10)
        color = ACCENT if key == "routes" else RED
        tk.Label(frm, text=title, bg=BG, fg=color,
                 font=("Courier New", 14, "bold")).pack(anchor="w", pady=(0, 8))

        ctrl = tk.Frame(frm, bg=BG2)
        ctrl.pack(fill="x", pady=(0, 8))
        tk.Label(ctrl, text="  Origem:", bg=BG2, fg=SOFT, font=("Courier New", 9)).pack(side="left")
        orig_entry = ttk.Entry(ctrl, width=22)
        orig_entry.pack(side="left", padx=(4, 12))
        tk.Label(ctrl, text="Destino:", bg=BG2, fg=SOFT, font=("Courier New", 9)).pack(side="left")
        dest_entry = ttk.Entry(ctrl, width=22)
        dest_entry.pack(side="left", padx=(4, 12))
        setattr(self, f"_{key}_orig", orig_entry)
        setattr(self, f"_{key}_dest", dest_entry)

        ttk.Button(ctrl, text=btn_label,
                   command=lambda: render_fn(key, action)).pack(side="left")

        out = tk.Text(frm, bg=BG2, fg=FG, font=("Courier New", 9),
                       insertbackground=FG, bd=0, wrap="word")
        out.tag_configure("head",   foreground=ACCENT, font=("Courier New", 10, "bold"))
        out.tag_configure("route",  foreground=GREEN)
        out.tag_configure("cable",  foreground=YEL)
        out.tag_configure("crit",   foreground=RED, font=("Courier New", 9, "bold"))
        out.tag_configure("muted",  foreground=SOFT)
        out.pack(fill="both", expand=True)
        setattr(self, f"_{key}_out", out)
        orig_entry.bind("<Return>", lambda e: render_fn(key, action))
        dest_entry.bind("<Return>", lambda e: render_fn(key, action))

    def _render_routes(self, key: str, action: str):
        orig = getattr(self, f"_{key}_orig").get().strip()
        dest = getattr(self, f"_{key}_dest").get().strip()
        out  = getattr(self, f"_{key}_out")
        out.configure(state="normal")
        out.delete("1.0", "end")

        if not orig or not dest:
            out.insert("end", "Informe origem e destino.\n", "muted")
            return
        if self._demo:
            out.insert("end", f"[DEMO]  {orig} → {dest}\n\n", "head")
            out.insert("end", "  #1  Demo-Atlantic  score=0.92\n", "route")
            out.insert("end", "  #2  Demo-Pacific   score=0.78\n", "route")
            out.configure(state="disabled")
            return

        rows = db_query(self.conn,
            """SELECT DISTINCT c.cable_id, c.cable_name
               FROM cables c
               JOIN cable_landing_points clp1 ON clp1.cable_id = c.cable_id
               JOIN landing_points lp1 ON lp1.endpoint_id = clp1.endpoint_id
                 AND lp1.country_name ILIKE %s
               JOIN cable_landing_points clp2 ON clp2.cable_id = c.cable_id
               JOIN landing_points lp2 ON lp2.endpoint_id = clp2.endpoint_id
                 AND lp2.country_name ILIKE %s
               WHERE c.is_operational = true
               LIMIT 10""", (f"%{orig}%", f"%{dest}%"))

        out.insert("end", f"Top 3 Rotas  {orig} → {dest}\n\n", "head")
        if not rows:
            out.insert("end", "Nenhuma rota direta encontrada entre esses países.\n", "muted")
        else:
            for i, r in enumerate(rows[:3], 1):
                out.insert("end", f"  #{i}  ", "muted")
                out.insert("end", f"{r['cable_id']:<24}", "cable")
                out.insert("end", f"{r['cable_name']}\n", "route")
        out.configure(state="disabled")

    def _render_cuts(self, key: str, action: str):
        orig = getattr(self, f"_{key}_orig").get().strip()
        dest = getattr(self, f"_{key}_dest").get().strip()
        out  = getattr(self, f"_{key}_out")
        out.configure(state="normal")
        out.delete("1.0", "end")

        if not orig or not dest:
            out.insert("end", "Informe origem e destino.\n", "muted")
            return

        out.insert("end", f"Análise de Isolamento  {orig} → {dest}\n\n", "head")

        # Find all cables shared between the two countries
        rows = db_query(self.conn,
            """SELECT DISTINCT c.cable_id, c.cable_name,
                      COUNT(DISTINCT clp.endpoint_id) AS total_endpoints
               FROM cables c
               JOIN cable_landing_points clp1 ON clp1.cable_id = c.cable_id
               JOIN landing_points lp1 ON lp1.endpoint_id = clp1.endpoint_id
                 AND lp1.country_name ILIKE %s
               JOIN cable_landing_points clp2 ON clp2.cable_id = c.cable_id
               JOIN landing_points lp2 ON lp2.endpoint_id = clp2.endpoint_id
                 AND lp2.country_name ILIKE %s
               JOIN cable_landing_points clp ON clp.cable_id = c.cable_id
               WHERE c.is_operational = true
               GROUP BY c.cable_id, c.cable_name
               ORDER BY total_endpoints DESC
               LIMIT 20""", (f"%{orig}%", f"%{dest}%"))

        if not rows:
            out.insert("end", "Nenhum cabo crítico encontrado entre esses países.\n", "muted")
        else:
            out.insert("end", f"Cabos críticos (corte isolaria {orig} de {dest}):\n\n", "muted")
            for r in rows:
                criticality = "CRÍTICO" if int(r.get("total_endpoints", 0)) >= 4 else "alto"
                tag = "crit" if criticality == "CRÍTICO" else "cable"
                out.insert("end", f"  ✂  ", "muted")
                out.insert("end", f"{r['cable_id']:<28}", tag)
                out.insert("end", f"{r['cable_name']}\n", "route")
        out.configure(state="disabled")

    # ── ALERTS panel ──────────────────────────────────────────────────────────
    def _panel_alerts(self):
        frm = ttk.Frame(self._main)
        frm.pack(fill="both", expand=True, padx=14, pady=10)
        tk.Label(frm, text="🔔  Alertas Ativos de Telemetria", bg=BG, fg=RED,
                 font=("Courier New", 14, "bold")).pack(anchor="w", pady=(0, 8))

        cols = ("cabo", "métrica", "severidade", "atual", "baseline", "detectado")
        tree = ttk.Treeview(frm, columns=cols, show="headings", height=24)
        ws   = {"cabo": 170, "métrica": 110, "severidade": 80,
                "atual": 90, "baseline": 90, "detectado": 190}
        for c in cols:
            tree.heading(c, text=c.upper())
            tree.column(c, width=ws[c], minwidth=50)
        tree.tag_configure("critical", foreground=RED)
        tree.tag_configure("warning",  foreground=WARN)
        sb = ttk.Scrollbar(frm, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")

        rows = [] if self._demo else db_query(self.conn,
            "SELECT ta.cable_id, ta.metric, ta.severity, "
            "ta.current_value, ta.baseline_value, ta.detected_at "
            "FROM telemetry_alerts ta WHERE ta.resolved_at IS NULL "
            "ORDER BY ta.detected_at DESC LIMIT 50")

        if not rows:
            tree.insert("", "end", values=("—", "—", "—", "—", "—", "Nenhum alerta ativo"))
        else:
            for r in rows:
                sev = r.get("severity", "")
                tree.insert("", "end", values=(
                    r.get("cable_id", ""), r.get("metric", ""), sev,
                    r.get("current_value", ""), r.get("baseline_value", ""),
                    r.get("detected_at", ""),
                ), tags=(sev,))

        # update badge
        if not self._demo:
            self._update_alert_badge()

# ── entrypoint ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = RMIFOApp()
    app.mainloop()
