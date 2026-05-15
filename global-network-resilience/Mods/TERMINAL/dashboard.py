#!/usr/bin/env python3
"""
RMIFO Dashboard — Interface gráfica multiplataforma
Busca de cabos · Telemetria em tempo real · Sonda multi-protocolo

Dependências: pip install matplotlib numpy psycopg2-binary networkx
Execução:     python dashboard.py
Build:        pyinstaller --onefile --windowed dashboard.py
"""
from __future__ import annotations
import os, sys, threading, subprocess, socket, time, json, struct, math
from datetime import datetime, timedelta
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

# ── palette ───────────────────────────────────────────────────────────────────
BG    = "#0f1117"
BG2   = "#1a1d2e"
BG3   = "#252840"
FG    = "#dde0f0"
CYAN  = "#00d4ff"
GREEN = "#00e87a"
YEL   = "#ffcc44"
RED   = "#ff4455"
DIM   = "#55557a"
ACC   = "#4a9eff"
PLT_BG    = "#0f1117"
PLT_GRID  = "#252840"
PLT_CYAN  = "#00d4ff"
PLT_GREEN = "#00e87a"
PLT_YEL   = "#ffcc44"
PLT_RED   = "#ff4455"

# ── database ──────────────────────────────────────────────────────────────────
def db_connect() -> Optional[object]:
    if not HAS_PG:
        return None
    try:
        ws   = os.environ.get("WORKSPACE_ROOT", "/workspaces/RMIFO")
        host = os.environ.get("PG_SOCKET", os.environ.get("PGHOST", f"{ws}/.pgsql/run"))
        port = os.environ.get("PG_PORT",   os.environ.get("PGPORT", "5432"))
        db   = os.environ.get("PGDATABASE", "rmifo")
        user = os.environ.get("PGUSER",     "rmifo")
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

# ── synthetic demo data (no DB) ───────────────────────────────────────────────
def demo_telemetry(hours: int = 24, n: int = 120) -> list[dict]:
    base = datetime.utcnow() - timedelta(hours=hours)
    rows = []
    lat = jit = 45.0
    for i in range(n):
        lat  = max(5, lat + np.random.randn() * 3)
        jit  = max(0.5, jit * 0.9 + np.random.randn() * 1.5)
        loss = max(0, np.random.exponential(0.3))
        tput = max(100, 9800 + np.random.randn() * 400)
        rows.append({
            "measured_at": base + timedelta(hours=hours * i / n),
            "latency_ms":  round(lat, 2),
            "jitter_ms":   round(abs(jit), 2),
            "packet_loss_pct": round(loss, 3),
            "throughput_mbps": round(tput, 1),
            "availability_pct": round(min(100, 99.7 + np.random.randn() * 0.1), 4),
        })
    return rows

def demo_cables() -> list[dict]:
    return [
        {"cable_id": "DEMO-01", "cable_name": "Demo Atlantic Link",  "length_km": 6700,  "owners": "DemoTelecom", "operational": True},
        {"cable_id": "DEMO-02", "cable_name": "Demo Pacific Ring",   "length_km": 14000, "owners": "DemoNet",     "operational": True},
        {"cable_id": "DEMO-03", "cable_name": "Demo Indian Express", "length_km": 9200,  "owners": "DemoISP",     "operational": False},
    ]

# ── matplotlib helpers ────────────────────────────────────────────────────────
def styled_ax(ax, title=""):
    ax.set_facecolor(PLT_BG)
    ax.figure.patch.set_facecolor(PLT_BG)
    ax.tick_params(colors=DIM, labelsize=7)
    ax.spines["bottom"].set_color(PLT_GRID)
    ax.spines["left"].set_color(PLT_GRID)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(color=PLT_GRID, linewidth=0.5, linestyle="--", alpha=0.5)
    ax.set_title(title, color=FG, fontsize=8, pad=4)
    ax.title.set_color(FG)

# ── main app ──────────────────────────────────────────────────────────────────
class RMIFOApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("RMIFO — Global Network Dashboard")
        self.geometry("1200x780")
        self.minsize(900, 600)
        self.configure(bg=BG)

        self.conn = db_connect()
        self._demo = self.conn is None
        self._telem_after: Optional[str] = None

        self._style()
        self._layout()
        self._nav("search")

    # ── Tk style ─────────────────────────────────────────────────────────────
    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".",           background=BG,  foreground=FG, font=("Courier New", 10))
        s.configure("TFrame",      background=BG)
        s.configure("Sidebar.TFrame", background=BG2)
        s.configure("Card.TFrame", background=BG3)
        s.configure("TLabel",      background=BG,  foreground=FG)
        s.configure("Card.TLabel", background=BG3, foreground=FG)
        s.configure("TButton",     background=BG3, foreground=FG, borderwidth=0, padding=6)
        s.map("TButton", background=[("active", BG3), ("pressed", ACC)])
        s.configure("Nav.TButton", background=BG2, foreground=DIM, borderwidth=0, padding=10,
                    font=("Courier New", 10, "bold"))
        s.map("Nav.TButton",
              background=[("active", BG3), ("pressed", BG3)],
              foreground=[("active", FG),  ("pressed", CYAN)])
        s.configure("TEntry",    fieldbackground=BG3, foreground=FG, insertcolor=FG, borderwidth=0)
        s.configure("TCombobox", fieldbackground=BG3, foreground=FG, selectbackground=ACC)
        s.configure("Treeview",  background=BG2, foreground=FG, fieldbackground=BG2,
                    rowheight=22, font=("Courier New", 9))
        s.configure("Treeview.Heading", background=BG3, foreground=CYAN,
                    font=("Courier New", 9, "bold"))
        s.map("Treeview", background=[("selected", ACC)])
        s.configure("Horizontal.TProgressbar", troughcolor=BG3, background=CYAN, thickness=6)
        s.configure("TCheckbutton", background=BG, foreground=FG)
        s.configure("TScrollbar",   background=BG3, troughcolor=BG2, arrowcolor=DIM)

    # ── layout ────────────────────────────────────────────────────────────────
    def _layout(self):
        # header
        hdr = tk.Frame(self, bg=BG2, height=48)
        hdr.pack(fill="x", side="top")
        tk.Label(hdr, text="  ▣ RMIFO", bg=BG2, fg=CYAN,
                 font=("Courier New", 14, "bold")).pack(side="left", pady=6)
        self._status_lbl = tk.Label(hdr, text="● conectado" if not self._demo else "● demo",
                                    bg=BG2, fg=GREEN if not self._demo else YEL,
                                    font=("Courier New", 9))
        self._status_lbl.pack(side="right", padx=16)

        # body
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)

        # sidebar
        self._sidebar = ttk.Frame(body, style="Sidebar.TFrame", width=170)
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.pack_propagate(False)

        nav_items = [
            ("search",    "🔍 Busca"),
            ("telemetry", "📈 Telemetria"),
            ("probe",     "📡 Sonda"),
            ("routes",    "🗺  Rotas"),
            ("alerts",    "🔔 Alertas"),
        ]
        self._nav_btns: dict[str, tk.Button] = {}
        for key, label in nav_items:
            btn = tk.Button(self._sidebar, text=label, anchor="w",
                            bg=BG2, fg=DIM, activebackground=BG3, activeforeground=FG,
                            font=("Courier New", 10, "bold"), bd=0, padx=12, pady=10,
                            cursor="hand2", command=lambda k=key: self._nav(k))
            btn.pack(fill="x")
            self._nav_btns[key] = btn

        # main area
        self._main = ttk.Frame(body)
        self._main.pack(side="left", fill="both", expand=True)
        self._current_panel: Optional[str] = None

    def _nav(self, key: str):
        for k, b in self._nav_btns.items():
            b.configure(bg=BG2, fg=DIM)
        self._nav_btns[key].configure(bg=BG3, fg=CYAN)
        for w in self._main.winfo_children():
            w.destroy()
        if self._telem_after:
            self.after_cancel(self._telem_after)
            self._telem_after = None
        self._current_panel = key
        getattr(self, f"_panel_{key}")()

    # ── SEARCH panel ──────────────────────────────────────────────────────────
    def _panel_search(self):
        frm = ttk.Frame(self._main)
        frm.pack(fill="both", expand=True, padx=16, pady=12)

        # top bar
        top = ttk.Frame(frm)
        top.pack(fill="x", pady=(0, 8))
        tk.Label(top, text="Buscar Cabos", bg=BG, fg=CYAN,
                 font=("Courier New", 13, "bold")).pack(side="left")

        bar = ttk.Frame(top)
        bar.pack(side="right")
        self._s_term = ttk.Entry(bar, width=28)
        self._s_term.pack(side="left", padx=(0, 6))
        self._s_term.bind("<Return>", lambda e: self._do_search())

        self._s_field = ttk.Combobox(bar, values=["nome", "país", "proprietário", "ano", "cabo_id"],
                                     state="readonly", width=14)
        self._s_field.set("nome")
        self._s_field.pack(side="left", padx=(0, 6))
        ttk.Button(bar, text="Buscar", command=self._do_search).pack(side="left")

        # table
        cols = ("cable_id", "nome", "km", "proprietários", "status")
        self._s_tree = ttk.Treeview(frm, columns=cols, show="headings", height=20)
        widths = {"cable_id": 140, "nome": 280, "km": 80, "proprietários": 220, "status": 90}
        for c in cols:
            self._s_tree.heading(c, text=c)
            self._s_tree.column(c, width=widths[c], minwidth=60)
        sb = ttk.Scrollbar(frm, orient="vertical", command=self._s_tree.yview)
        self._s_tree.configure(yscrollcommand=sb.set)
        self._s_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self._s_tree.bind("<Double-1>", self._on_cable_select)

        self._s_info = tk.Label(frm, text="", bg=BG, fg=DIM,
                                font=("Courier New", 8), anchor="w")
        # load all cables on open
        self._do_search()

    def _do_search(self, *_):
        term  = (self._s_term.get() if hasattr(self, "_s_term") else "").strip()
        field = (self._s_field.get() if hasattr(self, "_s_field") else "nome")
        self._s_tree.delete(*self._s_tree.get_children())

        if self._demo:
            rows = demo_cables()
        elif field == "nome":
            rows = db_query(self.conn,
                "SELECT cable_id, cable_name, length_km, owners, operational "
                "FROM cables WHERE cable_name ILIKE %s ORDER BY cable_name LIMIT 200",
                (f"%{term}%",))
        elif field == "país":
            rows = db_query(self.conn,
                "SELECT DISTINCT c.cable_id, c.cable_name, c.length_km, c.owners, c.operational "
                "FROM cables c JOIN endpoints e ON e.cable_id = c.cable_id "
                "WHERE e.country ILIKE %s ORDER BY c.cable_name LIMIT 200", (f"%{term}%",))
        elif field == "proprietário":
            rows = db_query(self.conn,
                "SELECT cable_id, cable_name, length_km, owners, operational "
                "FROM cables WHERE owners ILIKE %s ORDER BY cable_name LIMIT 200", (f"%{term}%",))
        elif field == "ano":
            rows = db_query(self.conn,
                "SELECT cable_id, cable_name, length_km, owners, operational "
                "FROM cables WHERE year_ready::text LIKE %s ORDER BY cable_name LIMIT 200",
                (f"%{term}%",))
        else:
            rows = db_query(self.conn,
                "SELECT cable_id, cable_name, length_km, owners, operational "
                "FROM cables WHERE cable_id ILIKE %s ORDER BY cable_name LIMIT 200",
                (f"%{term}%",))

        for r in rows:
            cid    = r.get("cable_id", "")
            name   = r.get("cable_name", "")
            km     = r.get("length_km", "")
            owners = r.get("owners", "")
            op     = "✓ ativo" if r.get("operational") else "✗ inativo"
            tag    = "ok" if r.get("operational") else "off"
            self._s_tree.insert("", "end", values=(cid, name, km, owners, op), tags=(tag,))
        self._s_tree.tag_configure("ok",  foreground=FG)
        self._s_tree.tag_configure("off", foreground=DIM)

    def _on_cable_select(self, event):
        sel = self._s_tree.selection()
        if not sel:
            return
        vals = self._s_tree.item(sel[0], "values")
        cable_id = vals[0]
        self._open_cable_detail(cable_id)

    def _open_cable_detail(self, cable_id: str):
        win = tk.Toplevel(self, bg=BG)
        win.title(f"Cabo — {cable_id}")
        win.geometry("760x520")

        tk.Label(win, text=f"  {cable_id}", bg=BG, fg=CYAN,
                 font=("Courier New", 13, "bold")).pack(anchor="w", padx=12, pady=(12, 4))

        if self._demo:
            info_rows = [r for r in demo_cables() if r["cable_id"] == cable_id]
        else:
            info_rows = db_query(self.conn,
                "SELECT cable_name, length_km, capacity_gbps, owners, rfs_year, year_ready, "
                "cable_type, notes FROM cables WHERE cable_id = %s", (cable_id,))
        if info_rows:
            r = info_rows[0]
            for k, v in r.items():
                row = tk.Frame(win, bg=BG2)
                row.pack(fill="x", padx=12, pady=1)
                tk.Label(row, text=f"{k:<20}", bg=BG2, fg=DIM,
                         font=("Courier New", 9)).pack(side="left")
                tk.Label(row, text=str(v), bg=BG2, fg=FG,
                         font=("Courier New", 9)).pack(side="left")

        tk.Label(win, text="  Endpoints", bg=BG, fg=CYAN,
                 font=("Courier New", 10, "bold")).pack(anchor="w", padx=12, pady=(10, 4))

        cols = ("endpoint_id", "country", "city", "ip_sample", "order")
        ep_tree = ttk.Treeview(win, columns=cols, show="headings", height=10)
        for c in cols:
            ep_tree.heading(c, text=c)
            ep_tree.column(c, width=120, minwidth=60)
        ep_tree.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        eps = db_query(self.conn,
            "SELECT endpoint_id, country, city, ip_sample, endpoint_order "
            "FROM endpoints WHERE cable_id = %s ORDER BY endpoint_order", (cable_id,))
        for e in eps:
            ep_tree.insert("", "end", values=(
                e.get("endpoint_id", ""), e.get("country", ""),
                e.get("city", ""), e.get("ip_sample", ""), e.get("endpoint_order", ""),
            ))

    # ── TELEMETRY panel ───────────────────────────────────────────────────────
    def _panel_telemetry(self):
        frm = ttk.Frame(self._main)
        frm.pack(fill="both", expand=True, padx=12, pady=8)

        top = ttk.Frame(frm)
        top.pack(fill="x", pady=(0, 8))
        tk.Label(top, text="Telemetria em Tempo Real", bg=BG, fg=CYAN,
                 font=("Courier New", 13, "bold")).pack(side="left")

        ctrl = ttk.Frame(top)
        ctrl.pack(side="right")
        tk.Label(ctrl, text="cable_id:", bg=BG, fg=DIM).pack(side="left")
        self._t_cable = ttk.Entry(ctrl, width=20)
        self._t_cable.pack(side="left", padx=(4, 8))
        self._t_cable.insert(0, "DEMO-01" if self._demo else "")

        self._t_win = ttk.Combobox(ctrl, values=["6h", "24h", "72h", "168h", "720h"],
                                   state="readonly", width=6)
        self._t_win.set("24h")
        self._t_win.pack(side="left", padx=(0, 8))
        self._t_auto = tk.BooleanVar(value=True)
        ttk.Checkbutton(ctrl, text="Auto", variable=self._t_auto,
                        command=self._telem_auto_toggle).pack(side="left", padx=(0, 8))
        ttk.Button(ctrl, text="Atualizar", command=self._load_telemetry).pack(side="left")

        # 5 charts in a 3-col / 2-row grid
        fig = Figure(figsize=(1, 1), facecolor=PLT_BG)
        self._t_fig = fig
        ax_lat,  ax_jit  = fig.add_subplot(231), fig.add_subplot(232)
        ax_loss, ax_tput = fig.add_subplot(233), fig.add_subplot(234)
        ax_avail          = fig.add_subplot(235)
        self._t_axes = [ax_lat, ax_jit, ax_loss, ax_tput, ax_avail]
        fig.tight_layout(pad=2.0)

        canvas = FigureCanvasTkAgg(fig, master=frm)
        canvas.get_tk_widget().pack(fill="both", expand=True)
        self._t_canvas = canvas

        # stats bar
        self._t_stats = tk.Label(frm, text="", bg=BG2, fg=DIM,
                                  font=("Courier New", 8), anchor="w")
        self._t_stats.pack(fill="x")

        self._load_telemetry()

    def _load_telemetry(self):
        cable_id = self._t_cable.get().strip() if hasattr(self, "_t_cable") else ""
        win_map = {"6h": 6, "24h": 24, "72h": 72, "168h": 168, "720h": 720}
        hours   = win_map.get(self._t_win.get() if hasattr(self, "_t_win") else "24h", 24)

        def fetch():
            if self._demo or not cable_id:
                return demo_telemetry(hours)
            rows = db_query(self.conn,
                "SELECT measured_at, latency_ms, jitter_ms, packet_loss_pct, "
                "throughput_mbps, availability_pct "
                "FROM cable_telemetry_samples "
                "WHERE cable_id = %s AND measured_at >= NOW() - (%s * interval '1 hour') "
                "ORDER BY measured_at ASC LIMIT 600",
                (cable_id, hours))
            return rows if rows else demo_telemetry(hours)

        def done(rows):
            if not rows or not hasattr(self, "_t_axes"):
                return
            ts    = [r["measured_at"] for r in rows]
            lat   = [float(r["latency_ms"])       for r in rows]
            jit   = [float(r["jitter_ms"])         for r in rows]
            loss  = [float(r["packet_loss_pct"])   for r in rows]
            tput  = [float(r["throughput_mbps"])   for r in rows]
            avail = [float(r["availability_pct"])  for r in rows]

            colors = [PLT_CYAN, PLT_YEL, PLT_RED, PLT_GREEN, "#aa88ff"]
            data   = [(lat, "Latência (ms)"), (jit, "Jitter (ms)"),
                      (loss, "Perda (%)"), (tput, "Throughput (Mbps)"),
                      (avail, "Disponib. (%)")]
            for ax, (vals, title), color in zip(self._t_axes, data, colors):
                ax.clear()
                styled_ax(ax, title)
                ax.plot(ts, vals, color=color, linewidth=0.9, alpha=0.85)
                ax.fill_between(ts, vals, alpha=0.12, color=color)
                if len(ts) > 1:
                    fmt = mdates.AutoDateFormatter(mdates.AutoDateLocator())
                    ax.xaxis.set_major_formatter(fmt)
                    self._t_fig.autofmt_xdate(rotation=25, ha="right")

            self._t_fig.tight_layout(pad=1.5)
            self._t_canvas.draw()

            last = rows[-1]
            self._t_stats.configure(
                text=(f"  Último registro: {last['measured_at']}  |  "
                      f"lat={float(last['latency_ms']):.2f}ms  "
                      f"jitter={float(last['jitter_ms']):.2f}ms  "
                      f"perda={float(last['packet_loss_pct']):.3f}%  "
                      f"throughput={float(last['throughput_mbps']):.0f}Mbps"))

        def worker():
            rows = fetch()
            self.after(0, lambda: done(rows))

        threading.Thread(target=worker, daemon=True).start()
        if getattr(self, "_t_auto", None) and self._t_auto.get():
            self._telem_after = self.after(30_000, self._load_telemetry)

    def _telem_auto_toggle(self):
        if self._telem_after:
            self.after_cancel(self._telem_after)
            self._telem_after = None
        if hasattr(self, "_t_auto") and self._t_auto.get():
            self._telem_after = self.after(30_000, self._load_telemetry)

    # ── PROBE panel ───────────────────────────────────────────────────────────
    def _panel_probe(self):
        frm = ttk.Frame(self._main)
        frm.pack(fill="both", expand=True, padx=12, pady=8)

        tk.Label(frm, text="Sonda de Rede  TCP → ICMP → HTTP → UDP/DNS", bg=BG, fg=CYAN,
                 font=("Courier New", 13, "bold")).pack(anchor="w", pady=(0, 8))

        # config bar
        cfg = tk.Frame(frm, bg=BG2)
        cfg.pack(fill="x", pady=(0, 8))
        tk.Label(cfg, text="  IP/Host:", bg=BG2, fg=DIM).pack(side="left")
        self._p_ip = ttk.Entry(cfg, width=22)
        self._p_ip.pack(side="left", padx=(4, 12))
        self._p_full = tk.BooleanVar()
        ttk.Checkbutton(cfg, text="Sonda completa", variable=self._p_full).pack(side="left")
        self._p_btn = ttk.Button(cfg, text="▶ Iniciar Sonda", command=self._run_probe_ui)
        self._p_btn.pack(side="right", padx=8)

        # progress bar
        self._p_prog_var = tk.DoubleVar()
        self._p_prog     = ttk.Progressbar(frm, variable=self._p_prog_var, maximum=100,
                                            style="Horizontal.TProgressbar")
        self._p_prog.pack(fill="x")
        self._p_status = tk.Label(frm, text="Aguardando…", bg=BG, fg=DIM,
                                   font=("Courier New", 9))
        self._p_status.pack(anchor="w")

        # protocol badges frame
        self._p_badges_frm = tk.Frame(frm, bg=BG)
        self._p_badges_frm.pack(fill="x", pady=6)

        # result charts (RTT distribution + loss gauge)
        self._p_fig = Figure(figsize=(1, 1), facecolor=PLT_BG)
        ax_rtt  = self._p_fig.add_subplot(121)
        ax_loss = self._p_fig.add_subplot(122)
        self._p_ax_rtt  = ax_rtt
        self._p_ax_loss = ax_loss
        styled_ax(ax_rtt,  "RTT por amostra (ms)")
        styled_ax(ax_loss, "Distribuição por protocolo")
        self._p_fig.tight_layout(pad=2.0)
        p_canvas = FigureCanvasTkAgg(self._p_fig, master=frm)
        p_canvas.get_tk_widget().pack(fill="both", expand=True)
        self._p_canvas = p_canvas

        # metrics row
        self._p_metrics = tk.Label(frm, text="", bg=BG2, fg=FG,
                                    font=("Courier New", 9), anchor="w")
        self._p_metrics.pack(fill="x")

    def _run_probe_ui(self):
        ip = self._p_ip.get().strip()
        if not ip:
            messagebox.showwarning("RMIFO", "Informe um IP ou hostname.")
            return
        self._p_btn.configure(state="disabled")
        self._p_prog_var.set(0)
        for w in self._p_badges_frm.winfo_children():
            w.destroy()

        full = self._p_full.get() if hasattr(self, "_p_full") else False

        def cb(msg: str, pct: int):
            self.after(0, lambda: [
                self._p_status.configure(text=msg),
                self._p_prog_var.set(pct),
            ])

        def worker():
            result, steps = run_probe(ip, full=full, cb=cb)
            self.after(0, lambda: self._probe_done(result, steps, ip))

        threading.Thread(target=worker, daemon=True).start()

    def _probe_done(self, result, steps, ip: str):
        self._p_btn.configure(state="normal")
        self._p_prog_var.set(100)

        # badges
        for proto, ok in steps:
            color = GREEN if ok else DIM
            tk.Label(self._p_badges_frm, text=f" {proto} ", bg=color if ok else BG3,
                     fg=BG if ok else DIM, font=("Courier New", 8, "bold"),
                     bd=0, padx=4, pady=2).pack(side="left", padx=2)

        if result is None:
            self._p_status.configure(text="✗ Sem resposta em nenhum protocolo", fg=RED)
            self._p_metrics.configure(text="")
            return

        self._p_status.configure(
            text=f"✓ {result['method']}  —  {ip}", fg=GREEN)
        self._p_metrics.configure(
            text=(f"  RTT médio: {result['avg_rtt']:.1f} ms   "
                  f"Jitter: {result['jitter']:.1f} ms   "
                  f"Perda: {result['loss']:.1f}%   "
                  f"Amostras: {result['samples']}"))

        # charts
        self._p_ax_rtt.clear()
        styled_ax(self._p_ax_rtt, "RTT por amostra (ms)")
        rtts = result.get("rtts") or [result["avg_rtt"]]
        self._p_ax_rtt.bar(range(len(rtts)), rtts, color=PLT_CYAN, alpha=0.8, width=0.6)
        self._p_ax_rtt.axhline(result["avg_rtt"], color=PLT_YEL, linewidth=1, linestyle="--",
                                label=f"avg={result['avg_rtt']:.1f}ms")
        self._p_ax_rtt.legend(fontsize=7, labelcolor=FG, facecolor=BG3, edgecolor=BG3)

        self._p_ax_loss.clear()
        styled_ax(self._p_ax_loss, "Status protocolos")
        labels = [s[0] for s in steps]
        vals   = [100 if s[1] else 0 for s in steps]
        colors = [PLT_GREEN if v else PLT_RED for v in vals]
        self._p_ax_loss.barh(labels, vals, color=colors, alpha=0.8)
        self._p_ax_loss.set_xlim(0, 110)
        self._p_ax_loss.set_xlabel("resposta (%)", color=DIM, fontsize=7)

        self._p_fig.tight_layout(pad=2.0)
        self._p_canvas.draw()

    # ── ROUTES panel ──────────────────────────────────────────────────────────
    def _panel_routes(self):
        frm = ttk.Frame(self._main)
        frm.pack(fill="both", expand=True, padx=12, pady=8)
        tk.Label(frm, text="Análise de Rotas", bg=BG, fg=CYAN,
                 font=("Courier New", 13, "bold")).pack(anchor="w", pady=(0, 8))

        top = ttk.Frame(frm)
        top.pack(fill="x", pady=(0, 6))
        tk.Label(top, text="Origem:", bg=BG, fg=DIM).pack(side="left")
        self._r_orig = ttk.Entry(top, width=22)
        self._r_orig.pack(side="left", padx=(4, 10))
        tk.Label(top, text="Destino:", bg=BG, fg=DIM).pack(side="left")
        self._r_dest = ttk.Entry(top, width=22)
        self._r_dest.pack(side="left", padx=(4, 10))
        ttk.Button(top, text="Buscar Rotas", command=self._find_routes).pack(side="left")
        ttk.Button(top, text="Grafo de Cabos", command=self._show_graph).pack(side="left", padx=8)

        self._r_out = tk.Text(frm, bg=BG2, fg=FG, font=("Courier New", 9),
                               insertbackground=FG, bd=0, height=24)
        self._r_out.pack(fill="both", expand=True)

    def _find_routes(self):
        orig = self._r_orig.get().strip()
        dest = self._r_dest.get().strip()
        if not orig or not dest:
            messagebox.showwarning("RMIFO", "Informe origem e destino.")
            return
        self._r_out.delete("1.0", "end")
        if self._demo:
            self._r_out.insert("end",
                f"[DEMO] Rota {orig} → {dest}\n\n"
                f"  #1  {orig} — DemoAtlantic — {dest}   score=0.92\n"
                f"  #2  {orig} — DemoPacific  — {dest}   score=0.78\n\n"
                "Sem conexão ao banco de dados. Instale psycopg2 e configure PGDATABASE.\n")
            return
        rows = db_query(self.conn,
            "SELECT DISTINCT c.cable_id, c.cable_name, "
            "e1.country AS orig_country, e2.country AS dest_country "
            "FROM cables c "
            "JOIN endpoints e1 ON e1.cable_id = c.cable_id AND e1.country ILIKE %s "
            "JOIN endpoints e2 ON e2.cable_id = c.cable_id AND e2.country ILIKE %s "
            "LIMIT 20", (f"%{orig}%", f"%{dest}%"))
        if not rows:
            self._r_out.insert("end", "Nenhuma rota direta encontrada.\n")
            return
        for i, r in enumerate(rows, 1):
            self._r_out.insert("end",
                f"  #{i:<3} {r['cable_id']:<18} {r['cable_name']}\n"
                f"       {r['orig_country']} ──▶ {r['dest_country']}\n\n")

    def _show_graph(self):
        if not HAS_NX:
            messagebox.showinfo("RMIFO",
                "NetworkX não instalado.\npip install networkx")
            return
        win = tk.Toplevel(self, bg=BG)
        win.title("Grafo de Cabos")
        win.geometry("900x620")
        fig = Figure(figsize=(9, 6), facecolor=PLT_BG)
        ax  = fig.add_subplot(111)
        styled_ax(ax, "Países × Cabos (top 300 conexões)")
        canvas = FigureCanvasTkAgg(fig, master=win)
        canvas.get_tk_widget().pack(fill="both", expand=True)

        def build():
            G = nx.Graph()
            if self._demo:
                edges = [("Brasil", "EUA"), ("EUA", "Europa"), ("Europa", "Ásia"),
                         ("Ásia", "África"), ("África", "Brasil")]
            else:
                rows = db_query(self.conn,
                    "SELECT DISTINCT e1.country AS a, e2.country AS b "
                    "FROM endpoints e1 JOIN endpoints e2 "
                    "ON e1.cable_id = e2.cable_id AND e1.country < e2.country "
                    "LIMIT 300")
                edges = [(r["a"], r["b"]) for r in rows]
            G.add_edges_from(edges)
            pos = nx.spring_layout(G, seed=42, k=2.5 / math.sqrt(max(1, len(G))))
            degrees = dict(G.degree())
            node_s  = [min(800, 80 + degrees[n] * 40) for n in G.nodes()]
            nx.draw_networkx_edges(G, pos, ax=ax, alpha=0.25, edge_color=PLT_GRID, width=0.7)
            nx.draw_networkx_nodes(G, pos, ax=ax, node_size=node_s,
                                   node_color=PLT_CYAN, alpha=0.75)
            labels = {n: n for n in G.nodes() if degrees[n] >= 2}
            nx.draw_networkx_labels(G, pos, labels=labels, ax=ax,
                                    font_size=6, font_color=FG)
            ax.axis("off")
            fig.tight_layout()
            self.after(0, canvas.draw)

        threading.Thread(target=build, daemon=True).start()

    # ── ALERTS panel ──────────────────────────────────────────────────────────
    def _panel_alerts(self):
        frm = ttk.Frame(self._main)
        frm.pack(fill="both", expand=True, padx=12, pady=8)
        tk.Label(frm, text="Alertas Ativos de Telemetria", bg=BG, fg=RED,
                 font=("Courier New", 13, "bold")).pack(anchor="w", pady=(0, 8))

        cols = ("cable_id", "métrica", "severidade", "atual", "base", "detectado")
        tree = ttk.Treeview(frm, columns=cols, show="headings", height=22)
        ws   = {"cable_id": 160, "métrica": 120, "severidade": 90,
                "atual": 90, "base": 90, "detectado": 180}
        for c in cols:
            tree.heading(c, text=c)
            tree.column(c, width=ws[c], minwidth=60)
        tree.tag_configure("critical", foreground=RED)
        tree.tag_configure("warning",  foreground=YEL)
        sb = ttk.Scrollbar(frm, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")

        rows = [] if self._demo else db_query(self.conn,
            "SELECT ta.cable_id, ta.metric, ta.severity, "
            "ta.current_value, ta.baseline_value, ta.detected_at "
            "FROM telemetry_alerts ta "
            "WHERE ta.resolved_at IS NULL ORDER BY ta.detected_at DESC LIMIT 50")
        if not rows:
            tree.insert("", "end", values=("—", "—", "—", "—", "—",
                                            "Nenhum alerta ativo"))
        else:
            for r in rows:
                sev = r.get("severity", "")
                tree.insert("", "end", values=(
                    r.get("cable_id", ""), r.get("metric", ""), sev,
                    r.get("current_value", ""), r.get("baseline_value", ""),
                    r.get("detected_at", ""),
                ), tags=(sev,))

# ── entrypoint ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = RMIFOApp()
    app.mainloop()
