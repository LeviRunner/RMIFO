#!/usr/bin/env python3
"""
RMIFO — Advanced Switch Reach Extension
=========================================
Vetores de descoberta de switches que os métodos anteriores
(SNMP, MTR, LLDP, ASN expansion) NÃO conseguem ver:

  VETOR A — SSH Banner Grab + TLS Cert + Telnet + BGP-179
    Identifica vendor e hostname de switches sem credenciais.
    Porta 22 → banner "Cisco IOS / Junos / EOS".
    Porta 443/8443 → Subject CN do certificado TLS = hostname real.
    Porta 23 → Telnet banner com prompt do sistema.
    Porta 179 → BGP OPEN message revela AS do roteador.
    HTTP header fingerprinting + JA3S TLS fingerprint.

  VETOR B — NETCONF / RESTCONF
    Para switches Juniper, Arista, Cisco IOS-XE e Nokia que expõem
    porta 830 (NETCONF) ou 8080/443 (RESTCONF).

  VETOR C — RouteViews + RIPE RIS MRT Dump (BGP global passivo)
    Baixa dumps BGP (RouteViews e RIPE RIS), parseia prefixos,
    AS paths e BGP Communities, cruza com ASNs de operadores.
    Complementado com Looking Glass live e IRR/WHOIS passivo.

  VETOR D — MPLS Traceroute + IXP Peering LAN
    MPLS traceroute revela LSRs ocultos. IXP peering LAN via PeeringDB.

  VETOR E — Shodan API + DNS PTR Fingerprinting
    Varredura passiva via Shodan + resolução reversa de IPs.

  VETOR F — gNMI / gRPC (OpenConfig Streaming Telemetry)
    Porta 57400/6030 — telemetria estruturada em tempo real.
    Requer: pip install pygnmi; credenciais GNMI_USER/GNMI_PASS.

  VETOR G — RIPE Atlas API
    Lê medições públicas existentes para IPs de landing points,
    cria traceroutes opcionalmente (requer RIPE_ATLAS_KEY).

Uso:
  python rmifo_advanced_reach.py --mode banner  --cidr 203.0.113.0/24
  python rmifo_advanced_reach.py --mode netconf --target 192.0.2.1
  python rmifo_advanced_reach.py --mode mrt     --asn 3356
  python rmifo_advanced_reach.py --mode ris     --asn 3356
  python rmifo_advanced_reach.py --mode mpls    --dst 198.51.100.1
  python rmifo_advanced_reach.py --mode shodan  --asn 1273
  python rmifo_advanced_reach.py --mode lg      --target 198.51.100.1
  python rmifo_advanced_reach.py --mode whois   --target 198.51.100.1
  python rmifo_advanced_reach.py --mode gnmi    --target 192.0.2.1
  python rmifo_advanced_reach.py --mode atlas   --target 192.0.2.1
  python rmifo_advanced_reach.py --mode full
"""

import os, sys, re, json, time, socket, ssl, struct, hashlib, logging
import ipaddress, argparse, subprocess
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import psycopg2, psycopg2.extras

try:
    from ncclient import manager as nc_manager
    NCCLIENT_OK = True
except ImportError:
    NCCLIENT_OK = False

try:
    import mrtparse
    MRTPARSE_OK = True
except ImportError:
    MRTPARSE_OK = False

try:
    from pygnmi.client import gNMIclient
    PYGNMI_OK = True
except ImportError:
    PYGNMI_OK = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("rmifo-reach")

DB_CONFIG = {
    "host":     os.getenv("PGHOST",     "localhost"),
    "port":     int(os.getenv("PGPORT", "55432")),
    "dbname":   os.getenv("PGDATABASE", "global_network_resilience"),
    "user":     os.getenv("PGUSER",     "postgres"),
    "password": os.getenv("PGPASSWORD", ""),
}
SHODAN_API_KEY  = os.getenv("SHODAN_API_KEY",  "")
NETCONF_USER    = os.getenv("NETCONF_USER",    "admin")
NETCONF_PASS    = os.getenv("NETCONF_PASS",    "")
GNMI_USER       = os.getenv("GNMI_USER",       "admin")
GNMI_PASS       = os.getenv("GNMI_PASS",       "")
RIPE_ATLAS_KEY  = os.getenv("RIPE_ATLAS_KEY",  "")

MAX_WORKERS         = int(os.getenv("PROBE_WORKERS", "30"))
TIMEOUT             = int(os.getenv("PROBE_TIMEOUT", "5"))

# ── Cobertura calibrada por vetor ─────────────────────────────────────────────
MPLS_MAX_HOPS       = int(os.getenv("MPLS_MAX_HOPS",       "30"))
IXP_MAX_SCAN        = int(os.getenv("IXP_MAX_SCAN",        "20"))
NETCONF_MAX_IPS     = int(os.getenv("NETCONF_MAX",         "20"))
SHODAN_MAX_RESULTS  = int(os.getenv("SHODAN_MAX",          "50"))
DNS_PTR_BATCH       = int(os.getenv("DNS_PTR_BATCH",       "50"))
GNMI_MAX_IPS        = int(os.getenv("GNMI_MAX",            "10"))
ATLAS_MAX_MEAS      = int(os.getenv("ATLAS_MAX_MEAS",      "50"))
LG_MAX_PEERS        = int(os.getenv("LG_MAX_PEERS",        "20"))
WHOIS_MAX_IPS       = int(os.getenv("WHOIS_MAX",           "50"))

# Fingerprints de vendor em banners SSH, sysDescr e Layer 1 óptico
VENDOR_PATTERNS = {
    "Cisco":    [r"cisco", r"IOS[\s\-]", r"Nexus", r"Catalyst"],
    "Juniper":  [r"juniper", r"Junos", r"JUNOS"],
    "Arista":   [r"arista", r"EOS"],
    "Huawei":   [r"huawei", r"VRP"],
    "Nokia":    [r"nokia", r"SR OS", r"TiMOS"],
    "Extreme":  [r"extreme", r"ExtremeXOS"],
    "MikroTik": [r"mikrotik", r"RouterOS"],
    "Cumulus":  [r"cumulus", r"Linux"],
    # Optical/DWDM vendors (Vetor A expansion)
    "Ciena":    [r"ciena", r"SAOS", r"WaveServer", r"6500\s"],
    "Infinera": [r"infinera", r"DTN[\-\s]", r"Cloud\s*Xpress"],
    "Coriant":  [r"coriant", r"Groove", r"hiT\s"],
    "Ribbon":   [r"ribbon", r"genband", r"GENBAND", r"PSX"],
}

# Filtros NETCONF (YANG models comuns)
NETCONF_FILTERS = {
    "interfaces": """
        <filter type="subtree">
          <interfaces xmlns="urn:ietf:params:xml:ns:yang:ietf-interfaces"/>
        </filter>""",
    "routing": """
        <filter type="subtree">
          <routing xmlns="urn:ietf:params:xml:ns:yang:ietf-routing"/>
        </filter>""",
    "bgp": """
        <filter type="subtree">
          <bgp xmlns="urn:ietf:params:xml:ns:yang:ietf-bgp"/>
        </filter>""",
    "lldp": """
        <filter type="subtree">
          <lldp xmlns="urn:ietf:params:xml:ns:yang:ietf-lldp"/>
        </filter>""",
}


# ── Banco de dados ────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(**DB_CONFIG)


def ensure_advanced_tables(conn):
    ddl = """
    CREATE TABLE IF NOT EXISTS switch_banners (
        id           SERIAL PRIMARY KEY,
        probed_at    TIMESTAMPTZ DEFAULT NOW(),
        ip_address   INET NOT NULL,
        port         INTEGER,
        banner_type  TEXT,
        banner_raw   TEXT,
        vendor       TEXT,
        hostname     TEXT,
        tls_cn       TEXT,
        tls_san      TEXT[],
        tls_issuer   TEXT,
        tls_ja3s     TEXT,
        http_server  TEXT,
        http_headers JSONB,
        cable_id     VARCHAR(255)
    );
    CREATE INDEX IF NOT EXISTS idx_sb_ip ON switch_banners(ip_address);

    CREATE TABLE IF NOT EXISTS netconf_state (
        id           SERIAL PRIMARY KEY,
        probed_at    TIMESTAMPTZ DEFAULT NOW(),
        ip_address   INET NOT NULL,
        datastore    TEXT DEFAULT 'running',
        filter_name  TEXT,
        xml_data     TEXT,
        parsed_json  JSONB,
        vendor       TEXT,
        cable_id     VARCHAR(255)
    );

    CREATE TABLE IF NOT EXISTS mrt_bgp_peers (
        id           SERIAL PRIMARY KEY,
        imported_at  TIMESTAMPTZ DEFAULT NOW(),
        peer_ip      INET,
        peer_asn     TEXT,
        prefix       CIDR,
        as_path      TEXT,
        communities  TEXT[],
        origin       TEXT,
        next_hop     INET,
        cable_asn    TEXT,
        collector    TEXT,
        source       TEXT DEFAULT 'routeviews'
    );
    CREATE INDEX IF NOT EXISTS idx_mrt_peer ON mrt_bgp_peers(peer_ip);
    CREATE INDEX IF NOT EXISTS idx_mrt_asn  ON mrt_bgp_peers(peer_asn);

    CREATE TABLE IF NOT EXISTS mpls_lsr_map (
        id           SERIAL PRIMARY KEY,
        probed_at    TIMESTAMPTZ DEFAULT NOW(),
        dst_ip       INET,
        hop_number   SMALLINT,
        lsr_ip       INET,
        mpls_label   INTEGER,
        ttl_mode     TEXT,
        rtt_ms       NUMERIC(10,3),
        cable_id     VARCHAR(255)
    );

    CREATE TABLE IF NOT EXISTS ixp_peering_lan (
        id             SERIAL PRIMARY KEY,
        probed_at      TIMESTAMPTZ DEFAULT NOW(),
        ixp_name       TEXT,
        peering_prefix CIDR,
        peer_ip        INET,
        peer_asn       TEXT,
        peer_name      TEXT,
        open_ports     JSONB,
        snmp_descr     TEXT
    );

    CREATE TABLE IF NOT EXISTS gnmi_telemetry_samples (
        id           SERIAL PRIMARY KEY,
        collected_at TIMESTAMPTZ DEFAULT NOW(),
        ip_address   INET NOT NULL,
        gnmi_path    TEXT,
        update_json  JSONB,
        vendor       TEXT,
        cable_id     VARCHAR(255)
    );
    CREATE INDEX IF NOT EXISTS idx_gnmi_ip ON gnmi_telemetry_samples(ip_address);

    CREATE TABLE IF NOT EXISTS ripe_atlas_measurements (
        id              SERIAL PRIMARY KEY,
        fetched_at      TIMESTAMPTZ DEFAULT NOW(),
        measurement_id  BIGINT,
        target_ip       INET,
        msm_type        TEXT,
        status          TEXT,
        result_json     JSONB,
        cable_id        VARCHAR(255)
    );
    CREATE INDEX IF NOT EXISTS idx_atlas_target ON ripe_atlas_measurements(target_ip);

    CREATE TABLE IF NOT EXISTS looking_glass_routes (
        id           SERIAL PRIMARY KEY,
        queried_at   TIMESTAMPTZ DEFAULT NOW(),
        lg_name      TEXT,
        target_ip    INET,
        prefix       CIDR,
        as_path      TEXT,
        communities  TEXT[],
        next_hop     INET,
        cable_id     VARCHAR(255)
    );

    CREATE TABLE IF NOT EXISTS whois_irr_records (
        id           SERIAL PRIMARY KEY,
        queried_at   TIMESTAMPTZ DEFAULT NOW(),
        ip_address   INET,
        asn          TEXT,
        org          TEXT,
        network      CIDR,
        rir          TEXT,
        irr_route    TEXT,
        irr_origin   TEXT,
        raw_whois    TEXT,
        cable_id     VARCHAR(255)
    );
    """
    with conn.cursor() as cur:
        cur.execute(ddl)
    conn.commit()
    log.info("Tabelas avançadas criadas/verificadas.")


# ─────────────────────────────────────────────────────────────────────────────
# VETOR A — SSH BANNER GRAB + TLS CERT + TELNET + BGP-179 + HTTP HEADERS + JA3S
# ─────────────────────────────────────────────────────────────────────────────

def fingerprint_vendor(text: str) -> str:
    for vendor, patterns in VENDOR_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                return vendor
    return "unknown"


def grab_ssh_banner(ip: str, port: int = 22) -> dict:
    result = {"ip": ip, "port": port, "banner_type": "ssh",
              "banner_raw": "", "vendor": "unknown", "hostname": ""}
    try:
        with socket.create_connection((ip, port), timeout=TIMEOUT) as s:
            s.settimeout(TIMEOUT)
            banner = s.recv(256).decode("utf-8", errors="replace").strip()
            result["banner_raw"] = banner
            result["vendor"]     = fingerprint_vendor(banner)
            parts = banner.split()
            if len(parts) > 1:
                candidate = parts[-1]
                if "." in candidate and not candidate.startswith("SSH"):
                    result["hostname"] = candidate
    except Exception as e:
        log.debug(f"SSH banner {ip}:{port}: {e}")
    return result


def grab_tls_cert(ip: str, port: int = 443) -> dict:
    result = {"ip": ip, "port": port, "banner_type": "tls",
              "tls_cn": "", "tls_san": [], "tls_issuer": "",
              "hostname": "", "vendor": "unknown"}
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode    = ssl.CERT_NONE
        with socket.create_connection((ip, port), timeout=TIMEOUT) as raw:
            with ctx.wrap_socket(raw, server_hostname=ip) as s:
                cert = s.getpeercert()
                if cert:
                    subject = dict(x[0] for x in cert.get("subject", []))
                    issuer  = dict(x[0] for x in cert.get("issuer",  []))
                    sans    = [v for _, v in cert.get("subjectAltName", [])]
                    cn      = subject.get("commonName", "")
                    result["tls_cn"]     = cn
                    result["tls_san"]    = sans
                    result["tls_issuer"] = issuer.get("organizationName", "")
                    result["hostname"]   = cn or (sans[0] if sans else "")
                    result["vendor"]     = fingerprint_vendor(cn + " " + result["tls_issuer"])
    except Exception as e:
        log.debug(f"TLS cert {ip}:{port}: {e}")
    return result


def grab_telnet_banner(ip: str, port: int = 23) -> dict:
    """
    Conecta na porta Telnet/23 e captura o banner inicial.
    Switches mais antigos (Cisco IOS pré-SSH) ainda expõem Telnet.
    Retorna banner bruto com prompt do sistema operacional.
    """
    result = {"ip": ip, "port": port, "banner_type": "telnet",
              "banner_raw": "", "vendor": "unknown", "hostname": ""}
    try:
        with socket.create_connection((ip, port), timeout=TIMEOUT) as s:
            s.settimeout(TIMEOUT)
            # Telnet IAC negotiation: recebe opções e envia IAC WONT para tudo
            data = b""
            deadline = time.time() + TIMEOUT
            while time.time() < deadline:
                try:
                    chunk = s.recv(512)
                    if not chunk:
                        break
                    data += chunk
                    if len(data) > 1024:
                        break
                except socket.timeout:
                    break
            # Strip telnet IAC bytes (0xFF) for text extraction
            clean = re.sub(rb"\xff[\xfb-\xfe].", b"", data)
            banner = clean.decode("utf-8", errors="replace").strip()
            result["banner_raw"] = banner[:500]
            result["vendor"]     = fingerprint_vendor(banner)
            # Common hostname patterns in Telnet prompts: "hostname>" or "hostname#"
            m = re.search(r"([A-Za-z0-9][\w\-\.]{2,30})[>#]", banner)
            if m:
                result["hostname"] = m.group(1)
    except Exception as e:
        log.debug(f"Telnet banner {ip}:{port}: {e}")
    return result


def grab_bgp_open(ip: str, port: int = 179) -> dict:
    """
    Conecta na porta BGP/179 e captura o OPEN message do peer.
    O OPEN message revela: BGP version, AS number, Hold Time, Router-ID.
    Sem autenticação — apenas lê o que o roteador envia espontaneamente.
    """
    result = {"ip": ip, "port": port, "banner_type": "bgp",
              "banner_raw": "", "vendor": "unknown",
              "bgp_as": None, "bgp_router_id": "", "bgp_hold_time": None}
    try:
        with socket.create_connection((ip, port), timeout=TIMEOUT) as s:
            s.settimeout(TIMEOUT)
            data = s.recv(256)
            if len(data) < 19:
                return result
            result["banner_raw"] = data.hex()[:200]
            # BGP message header: 16 bytes marker + 2 bytes length + 1 byte type
            # Type 1 = OPEN: 1 byte version, 2 bytes AS, 2 bytes hold, 4 bytes router-id
            if len(data) >= 29 and data[18] == 1:
                bgp_as     = struct.unpack("!H", data[20:22])[0]
                hold_time  = struct.unpack("!H", data[22:24])[0]
                router_id  = socket.inet_ntoa(data[24:28])
                result["bgp_as"]        = bgp_as
                result["bgp_hold_time"] = hold_time
                result["bgp_router_id"] = router_id
                result["hostname"]      = router_id
    except Exception as e:
        log.debug(f"BGP OPEN {ip}:{port}: {e}")
    return result


def grab_http_headers(ip: str, port: int = 80, use_tls: bool = False) -> dict:
    """
    Faz GET / e analisa headers de resposta HTTP.
    Server:, X-Powered-By:, WWW-Authenticate: revelam vendor e versão.
    Ordem dos headers (fingerprint) diferencia implementações.
    """
    result = {"ip": ip, "port": port, "banner_type": "http",
              "banner_raw": "", "vendor": "unknown",
              "http_server": "", "http_headers": {}}
    try:
        scheme  = "https" if use_tls else "http"
        url     = f"{scheme}://{ip}:{port}/"
        r = requests.get(url, timeout=TIMEOUT, verify=False,
                         allow_redirects=False,
                         headers={"User-Agent": "Mozilla/5.0"})
        # Capture ordered header names (fingerprint) and values
        header_order  = list(r.headers.keys())
        server_hdr    = r.headers.get("Server", "")
        auth_hdr      = r.headers.get("WWW-Authenticate", "")
        result["http_server"]  = server_hdr
        result["http_headers"] = {
            "order":            header_order[:20],
            "server":           server_hdr,
            "www_authenticate": auth_hdr,
            "x_powered_by":     r.headers.get("X-Powered-By", ""),
            "content_type":     r.headers.get("Content-Type", ""),
        }
        result["banner_raw"] = server_hdr or auth_hdr or str(r.status_code)
        result["vendor"]     = fingerprint_vendor(server_hdr + " " + auth_hdr)
    except Exception as e:
        log.debug(f"HTTP headers {ip}:{port}: {e}")
    return result


def compute_ja3s(ip: str, port: int = 443) -> dict:
    """
    Captura o ServerHello TLS via socket raw e extrai JA3S fingerprint.
    JA3S: MD5(TLSVersion,Cipher,Extensions) identifica implementações TLS
    de switches e roteadores sem precisar de credenciais.
    """
    result = {"ip": ip, "port": port, "banner_type": "ja3s",
              "tls_ja3s": "", "tls_version": "", "tls_cipher": "",
              "tls_extensions": [], "banner_raw": ""}

    # Minimal TLS 1.3 ClientHello to elicit a ServerHello
    client_hello = bytes.fromhex(
        "16030100c1"                          # TLS record: handshake, TLS 1.0 compat
        "0100"                                # ClientHello
        "00bd"                                # length
        "0303"                                # client version: TLS 1.2
        + os.urandom(32).hex()               # random (32 bytes)
        + "00"                                # session ID length = 0
        "001c"                                # cipher suites length = 28
        "c02bc02cc02fc030009cc09dc013c014"   # common cipher suites
        "002f0035000a"                        # more ciphers
        "0100"                                # compression: null
        "0072"                                # extensions length
        "0017000000ff0100010000000010"        # extension data (simplified)
        "00" * 100                            # padding to stable length
    )
    # Use a proper minimal hello instead — raw bytes approach
    try:
        with socket.create_connection((ip, port), timeout=TIMEOUT) as s:
            s.settimeout(TIMEOUT)
            # Send minimal TLS ClientHello (TLS 1.2 compatible)
            hello = _build_client_hello()
            s.sendall(hello)
            # Read TLS record header (5 bytes)
            header = _recv_exact(s, 5)
            if not header or header[0] != 0x16:
                return result
            rec_len = struct.unpack("!H", header[3:5])[0]
            body    = _recv_exact(s, rec_len)
            if not body or body[0] != 0x02:  # HandshakeType.server_hello
                return result

            # Parse ServerHello
            offset   = 4  # skip type(1) + length(3)
            ver      = struct.unpack("!H", body[offset:offset+2])[0]
            offset  += 2 + 32 + 1  # version + random + session_id_len
            sess_len = body[4 + 2 + 1]  # session_id_len byte
            offset   = 4 + 2 + 32 + 1 + sess_len
            if offset + 2 > len(body):
                return result
            cipher   = struct.unpack("!H", body[offset:offset+2])[0]
            offset  += 3  # cipher(2) + compression(1)
            exts     = []
            if offset + 2 <= len(body):
                ext_total = struct.unpack("!H", body[offset:offset+2])[0]
                offset   += 2
                end       = offset + ext_total
                while offset + 4 <= end and offset + 4 <= len(body):
                    ext_type = struct.unpack("!H", body[offset:offset+2])[0]
                    ext_len  = struct.unpack("!H", body[offset+2:offset+4])[0]
                    exts.append(ext_type)
                    offset += 4 + ext_len

            ver_str  = _tls_ver(ver)
            cipher_s = str(cipher)
            exts_s   = "-".join(str(e) for e in exts)
            ja3s_str = f"{ver_str},{cipher_s},{exts_s}"
            ja3s_md5 = hashlib.md5(ja3s_str.encode()).hexdigest()

            result["tls_version"]    = ver_str
            result["tls_cipher"]     = cipher_s
            result["tls_extensions"] = exts
            result["tls_ja3s"]       = ja3s_md5
            result["banner_raw"]     = ja3s_str
    except Exception as e:
        log.debug(f"JA3S {ip}:{port}: {e}")
    return result


def _tls_ver(code: int) -> str:
    return {0x0301: "769", 0x0302: "770", 0x0303: "771", 0x0304: "772"}.get(code, str(code))


def _recv_exact(s: socket.socket, n: int) -> bytes | None:
    buf = b""
    while len(buf) < n:
        try:
            chunk = s.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        except Exception:
            return None
    return buf


def _build_client_hello() -> bytes:
    """Builds a minimal TLS 1.2 ClientHello for ServerHello elicitation."""
    random_bytes = os.urandom(32)
    ciphers = struct.pack("!8H",
        0xC02B, 0xC02C, 0xC02F, 0xC030,
        0x009C, 0x009D, 0x0035, 0x002F)
    extensions = (
        b"\x00\x0d\x00\x14\x00\x12"   # signature_algorithms
        b"\x04\x03\x05\x03\x06\x03\x08\x07\x08\x08\x08\x09\x08\x0a\x08\x0b\x08\x04\x08\x05"
        b"\x00\x17\x00\x00"            # extended_master_secret
        b"\xff\x01\x00\x01\x00"        # renegotiation_info
    )
    hello_body = (
        b"\x03\x03"                    # client version TLS 1.2
        + random_bytes
        + b"\x00"                      # session_id length = 0
        + struct.pack("!H", len(ciphers)) + ciphers
        + b"\x01\x00"                  # compression: null only
        + struct.pack("!H", len(extensions)) + extensions
    )
    handshake = b"\x01" + struct.pack("!I", len(hello_body))[1:] + hello_body
    record    = b"\x16\x03\x01" + struct.pack("!H", len(handshake)) + handshake
    return record


def run_banner_sweep(conn, cidr: str, cable_id: str = None) -> list[dict]:
    """
    Varre um CIDR buscando banners SSH, TLS, Telnet, BGP-179 e HTTP headers.
    Adiciona JA3S fingerprint para hosts com TLS.
    """
    log.info(f"=== VETOR A: Banner sweep em {cidr} ===")
    net     = ipaddress.ip_network(cidr, strict=False)
    hosts   = list(net.hosts())
    results = []

    def probe_host(ip_obj):
        ip  = str(ip_obj)
        out = []
        probe_map = [
            (22,  grab_ssh_banner),
            (443, grab_tls_cert),
            (830, grab_ssh_banner),
            (8443, grab_tls_cert),
            (23,  grab_telnet_banner),
            (179, grab_bgp_open),
            (80,  lambda h, p: grab_http_headers(h, p, use_tls=False)),
        ]
        for port, fn in probe_map:
            r = fn(ip, port)
            if r.get("banner_raw") or r.get("tls_cn") or r.get("bgp_as"):
                r["cable_id"] = cable_id
                out.append(r)
        # JA3S on any host that answered TLS
        tls_answered = any(r["port"] in (443, 8443) and r.get("tls_cn") for r in out)
        if tls_answered:
            j = compute_ja3s(ip, 443)
            if j.get("tls_ja3s"):
                j["cable_id"] = cable_id
                out.append(j)
        return out

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(probe_host, h): h for h in hosts}
        for f in as_completed(futures):
            results.extend(f.result())

    if results and conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, """
                INSERT INTO switch_banners
                    (ip_address, port, banner_type, banner_raw,
                     vendor, hostname, tls_cn, tls_san, tls_issuer,
                     tls_ja3s, http_server, http_headers, cable_id)
                VALUES %s
            """, [(
                r["ip"], r["port"], r["banner_type"],
                r.get("banner_raw", ""), r.get("vendor", ""),
                r.get("hostname", ""), r.get("tls_cn", ""),
                r.get("tls_san", []), r.get("tls_issuer", ""),
                r.get("tls_ja3s", ""),
                r.get("http_server", ""),
                json.dumps(r.get("http_headers")) if r.get("http_headers") else None,
                r.get("cable_id"),
            ) for r in results])
        conn.commit()

    log.info(f"Banner sweep: {len(results)} fingerprints em {cidr}.")
    return results


def bulk_dns_ptr_fingerprint(ips: list[str], conn=None) -> list[dict]:
    """
    PTR lookup em massa. Convenções de nomeação:
      sw1.pop-sao.telemar.net  → switch, PoP São Paulo
      cr2.ams.level3.net       → core router, Amsterdam
    """
    log.info(f"DNS PTR fingerprint: {len(ips)} IPs")
    pattern = re.compile(
        r'^(?P<type>sw|rt|cr|pe|br|gw|agg|dist|core)'
        r'[\d\-]*\.'
        r'(?P<pop>[a-z]{2,4})'
        r'[^.]*\.'
        r'(?P<domain>.+)$',
        re.IGNORECASE
    )
    type_map = {
        "sw": "switch", "rt": "router", "cr": "core-router",
        "pe": "provider-edge", "br": "border-router",
        "gw": "gateway", "agg": "aggregation",
        "dist": "distribution", "core": "core",
    }
    results = []

    def ptr_one(ip):
        try:
            hostname = socket.gethostbyaddr(ip)[0]
        except Exception:
            hostname = ""
        entry = {"ip": ip, "hostname": hostname, "device_type": "unknown",
                 "pop": "", "operator": ""}
        if hostname:
            m = pattern.match(hostname)
            if m:
                entry["device_type"] = type_map.get(m.group("type").lower(), "unknown")
                entry["pop"]         = m.group("pop").upper()
                entry["operator"]    = m.group("domain")
        return entry

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(ptr_one, ip): ip for ip in ips}
        for f in as_completed(futures):
            results.append(f.result())

    switches = [r for r in results if r["device_type"] != "unknown"]
    log.info(f"DNS PTR: {len(switches)} de {len(results)} IPs identificados como switches/roteadores.")
    return results


# ─────────────────────────────────────────────────────────────────────────────
# VETOR B — NETCONF / RESTCONF
# ─────────────────────────────────────────────────────────────────────────────

def netconf_get(ip: str, filter_name: str = "interfaces",
                user: str = NETCONF_USER, password: str = NETCONF_PASS) -> dict:
    if not NCCLIENT_OK:
        return {"error": "ncclient não instalado. pip install ncclient"}
    if not password:
        return {"error": "NETCONF_PASS não definido"}

    xml_filter = NETCONF_FILTERS.get(filter_name, NETCONF_FILTERS["interfaces"])
    result = {"ip": ip, "filter": filter_name, "xml": "", "parsed": {}}

    try:
        with nc_manager.connect(
            host           = ip,
            port           = 830,
            username       = user,
            password       = password,
            timeout        = TIMEOUT * 2,
            hostkey_verify = False,
            look_for_keys  = False,
        ) as m:
            caps   = [str(c) for c in m.server_capabilities]
            vendor = "unknown"
            for cap in caps:
                if "juniper" in cap.lower():  vendor = "Juniper"
                elif "cisco"  in cap.lower(): vendor = "Cisco"
                elif "arista" in cap.lower(): vendor = "Arista"
                elif "huawei" in cap.lower(): vendor = "Huawei"
                elif "nokia"  in cap.lower(): vendor = "Nokia"
            result["vendor"]       = vendor
            result["capabilities"] = caps

            cfg = m.get_config(source="running", filter=xml_filter)
            result["xml"] = cfg.data_xml
            iface_names   = re.findall(r"<name>([^<]+)</name>", cfg.data_xml)
            result["parsed"]["interface_names"] = list(set(iface_names))
    except Exception as e:
        result["error"] = str(e)
        log.debug(f"NETCONF {ip}: {e}")

    return result


def restconf_get(ip: str, path: str = "data/ietf-interfaces:interfaces",
                 user: str = NETCONF_USER, password: str = NETCONF_PASS) -> dict:
    result = {"ip": ip, "path": path, "data": {}}
    for port, scheme in [(8080, "http"), (443, "https"), (80, "http")]:
        url = f"{scheme}://{ip}:{port}/restconf/{path}"
        try:
            r = requests.get(url, auth=(user, password), timeout=TIMEOUT, verify=False,
                             headers={"Accept": "application/yang-data+json"})
            if r.status_code == 200:
                result["data"]   = r.json()
                result["port"]   = port
                result["vendor"] = fingerprint_vendor(
                    r.headers.get("Server", "") + json.dumps(result["data"])[:200]
                )
                break
        except Exception:
            continue
    return result


def run_netconf_collection(conn, targets: list[str], cable_id: str = None) -> list[dict]:
    log.info(f"=== VETOR B: NETCONF — {len(targets)} alvos ===")
    all_results = []

    for ip in targets:
        for fname in ["interfaces", "routing", "bgp", "lldp"]:
            r = netconf_get(ip, fname)
            r["cable_id"] = cable_id
            all_results.append(r)
            if "error" not in r and conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO netconf_state
                            (ip_address, filter_name, xml_data, parsed_json, vendor, cable_id)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (ip, fname, r.get("xml", ""),
                          json.dumps(r.get("parsed", {})),
                          r.get("vendor"), cable_id))
                conn.commit()

    log.info(f"NETCONF: {len(all_results)} coletas realizadas.")
    return all_results


# ─────────────────────────────────────────────────────────────────────────────
# VETOR C — ROUTEVIEWS + RIPE RIS MRT DUMP + LOOKING GLASS + IRR/WHOIS
# ─────────────────────────────────────────────────────────────────────────────

def download_latest_mrt(collector: str = "route-views2") -> str | None:
    """Baixa o dump MRT mais recente do RouteViews (~100 MB, a cada 2h)."""
    base_url = f"https://archive.routeviews.org/{collector}/bgpdata/"
    tmp_path = f"/tmp/rmifo_mrt_{collector}.bz2"

    try:
        month   = datetime.now(timezone.utc).strftime("%Y.%m")
        rib_url = f"{base_url}{month}/RIBS/"
        r       = requests.get(rib_url, timeout=30)
        files   = re.findall(r'href="(rib\.\d+\.\d+\.bz2)"', r.text)
        if not files:
            log.warning(f"MRT: nenhum RIB encontrado em {rib_url}")
            return None

        file_url = rib_url + sorted(files)[-1]
        log.info(f"MRT RouteViews: baixando {file_url} (~100 MB)…")
        with requests.get(file_url, stream=True, timeout=120) as resp:
            resp.raise_for_status()
            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    f.write(chunk)

        log.info(f"MRT RouteViews: concluído → {tmp_path}")
        return tmp_path
    except Exception as e:
        log.error(f"MRT RouteViews download: {e}")
        return None


def download_latest_ris(rrc: str = "rrc00") -> str | None:
    """
    Baixa o dump MRT mais recente do RIPE RIS (second source alternativo).
    URL base: https://data.ris.ripe.net/{rrc}/
    Coletores úteis: rrc00 (Amsterdam), rrc01 (London), rrc10 (Milan).
    """
    base_url = f"https://data.ris.ripe.net/{rrc}/"
    tmp_path = f"/tmp/rmifo_ris_{rrc}.gz"

    try:
        r     = requests.get(base_url, timeout=30)
        month = datetime.now(timezone.utc).strftime("%Y.%m")
        # Look for latest/ directory or current month
        dirs  = re.findall(r'href="(\d{4}\.\d{2}/)"', r.text)
        if month + "/" in dirs:
            target_dir = month + "/"
        elif dirs:
            target_dir = sorted(dirs)[-1]
        else:
            log.warning(f"RIS: nenhum diretório encontrado em {base_url}")
            return None

        rib_url = base_url + target_dir
        r2      = requests.get(rib_url, timeout=30)
        files   = re.findall(r'href="(bview\.\d+\.\d+\.gz)"', r2.text)
        if not files:
            log.warning(f"RIS: nenhum bview encontrado em {rib_url}")
            return None

        file_url = rib_url + sorted(files)[-1]
        log.info(f"RIS RIPE: baixando {file_url}…")
        with requests.get(file_url, stream=True, timeout=120) as resp:
            resp.raise_for_status()
            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    f.write(chunk)

        log.info(f"RIS RIPE: concluído → {tmp_path}")
        return tmp_path
    except Exception as e:
        log.error(f"RIS RIPE download ({rrc}): {e}")
        return None


def parse_mrt_for_asn(mrt_path: str, target_asns: set[str],
                      conn=None, collector: str = "route-views2",
                      source: str = "routeviews",
                      max_entries: int = 500_000) -> list[dict]:
    """
    Parseia dump MRT procurando prefixos cujo AS path contém
    qualquer ASN dos operadores de cabos. Extrai BGP Communities.
    Suporta arquivos RouteViews (.bz2) e RIPE RIS (.gz).
    """
    if not MRTPARSE_OK:
        log.warning("mrtparse não instalado. pip install mrtparse")
        return []

    log.info(f"=== VETOR C: MRT parse [{source}] — ASNs alvo: {target_asns} ===")
    matches = []
    count   = 0

    try:
        for entry in mrtparse.Reader(mrt_path):
            count += 1
            if count > max_entries:
                break
            if count % 100_000 == 0:
                log.info(f"  MRT: {count} entradas, {len(matches)} matches")

            try:
                mrt     = entry.data
                prefix  = mrt.get("prefix", "") + "/" + str(mrt.get("prefix_length", 0))
                entries = mrt.get("rib_entries", [])

                for rib in entries:
                    peer_ip  = rib.get("peer_ip", "")
                    peer_asn = str(rib.get("peer_as", ""))
                    as_path  = []
                    communities = []

                    for attr in rib.get("path_attributes", []):
                        type_code = attr.get("type_code")
                        # AS_PATH (type 2)
                        if type_code == 2:
                            for seg in attr.get("value", []):
                                as_path.extend(str(a) for a in seg.get("value", []))
                        # COMMUNITY (type 8): list of "ASN:value" strings
                        elif type_code == 8:
                            for comm in attr.get("value", []):
                                if isinstance(comm, dict):
                                    asn_part = comm.get("asn", "")
                                    val_part = comm.get("value", "")
                                    communities.append(f"{asn_part}:{val_part}")
                                elif isinstance(comm, str):
                                    communities.append(comm)
                        # LARGE_COMMUNITY (type 32)
                        elif type_code == 32:
                            for lc in attr.get("value", []):
                                if isinstance(lc, dict):
                                    communities.append(
                                        f"{lc.get('global_admin','')}:"
                                        f"{lc.get('local_data_part_1','')}:"
                                        f"{lc.get('local_data_part_2','')}"
                                    )

                    if target_asns.intersection(set(as_path)) or peer_asn in target_asns:
                        matches.append({
                            "peer_ip":     peer_ip,
                            "peer_asn":    peer_asn,
                            "prefix":      prefix,
                            "as_path":     " ".join(as_path),
                            "communities": communities,
                            "cable_asn":   next((a for a in as_path if a in target_asns), peer_asn),
                            "collector":   collector,
                            "source":      source,
                        })
            except Exception:
                continue
    except Exception as e:
        log.error(f"MRT parse: {e}")
        return []

    log.info(f"MRT [{source}]: {len(matches)} roteadores BGP encontrados.")

    if matches and conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, """
                INSERT INTO mrt_bgp_peers
                    (peer_ip, peer_asn, prefix, as_path, communities,
                     cable_asn, collector, source)
                VALUES %s ON CONFLICT DO NOTHING
            """, [(
                m["peer_ip"] or None, m["peer_asn"], m["prefix"],
                m["as_path"], m.get("communities", []),
                m["cable_asn"], m["collector"], m["source"]
            ) for m in matches if m.get("peer_ip")])
        conn.commit()

    return matches


# Looking Glass public endpoints (JSON / plain-text APIs)
LOOKING_GLASS_ENDPOINTS = [
    {
        "name": "Hurricane Electric",
        "url":  "https://lg.he.net/cgi-bin/bgp_route.cgi",
        "params_fn": lambda ip: {"addr": ip, "type": "bgp"},
        "parse_fn": "he",
    },
    {
        "name": "RIPE Stat",
        "url":  "https://stat.ripe.net/data/looking-glass/data.json",
        "params_fn": lambda ip: {"resource": ip},
        "parse_fn": "ripestat",
    },
]


def looking_glass_query(target_ip: str, conn=None,
                        cable_id: str = None) -> list[dict]:
    """
    Consulta Looking Glasses públicos para obter rotas BGP live.
    HE LG e RIPE Stat não requerem autenticação.
    """
    log.info(f"Looking Glass query: {target_ip}")
    results = []

    for lg in LOOKING_GLASS_ENDPOINTS:
        try:
            params = lg["params_fn"](target_ip)
            r      = requests.get(lg["url"], params=params, timeout=15)
            routes = []

            if lg["parse_fn"] == "ripestat":
                data = r.json().get("data", {})
                for rtr in data.get("rrcs", []):
                    for peer in rtr.get("peers", []):
                        routes.append({
                            "lg_name":   lg["name"],
                            "target_ip": target_ip,
                            "prefix":    peer.get("prefix", ""),
                            "as_path":   " ".join(str(a) for a in peer.get("asn_origin", [])),
                            "communities": peer.get("community", []),
                            "next_hop":  peer.get("next_hop", ""),
                            "cable_id":  cable_id,
                        })
            elif lg["parse_fn"] == "he":
                # HE returns HTML/text — extract AS path lines
                text = r.text
                for line in text.splitlines():
                    m = re.search(r"AS path:\s*([\d ]+)", line)
                    if m:
                        routes.append({
                            "lg_name":   lg["name"],
                            "target_ip": target_ip,
                            "prefix":    target_ip + "/24",
                            "as_path":   m.group(1).strip(),
                            "communities": [],
                            "next_hop":  "",
                            "cable_id":  cable_id,
                        })

            results.extend(routes[:LG_MAX_PEERS])
        except Exception as e:
            log.debug(f"LG {lg['name']} {target_ip}: {e}")

    if results and conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, """
                INSERT INTO looking_glass_routes
                    (lg_name, target_ip, prefix, as_path, communities, next_hop, cable_id)
                VALUES %s
            """, [(
                r["lg_name"],
                r["target_ip"] or None,
                r["prefix"] or None,
                r["as_path"],
                r.get("communities", []),
                r["next_hop"] or None,
                r["cable_id"],
            ) for r in results])
        conn.commit()

    log.info(f"Looking Glass: {len(results)} rotas para {target_ip}.")
    return results


def whois_lookup(ip: str, conn=None, cable_id: str = None) -> dict:
    """
    IRR/WHOIS passivo via subprocess whois.
    RIPE, ARIN, APNIC respondem sem credenciais.
    Extrai: ASN, org, network CIDR, RIR, route/origin IRR.
    """
    result = {"ip": ip, "asn": "", "org": "", "network": "",
              "rir": "", "irr_route": "", "irr_origin": "", "raw": ""}
    try:
        proc = subprocess.run(
            ["whois", "-H", ip],
            capture_output=True, text=True, timeout=15
        )
        raw = proc.stdout
        result["raw"] = raw[:2000]

        def field(pattern):
            m = re.search(pattern, raw, re.IGNORECASE | re.MULTILINE)
            return m.group(1).strip() if m else ""

        result["asn"]        = field(r"^(?:origin|OriginAS|ASNumber):\s*AS?(\d+)")
        result["org"]        = field(r"^(?:OrgName|org-name|descr):\s*(.+)")
        result["network"]    = field(r"^(?:CIDR|inetnum|NetRange|route):\s*(.+)")
        result["rir"]        = field(r"^(?:source):\s*(.+)")
        result["irr_route"]  = field(r"^route:\s*(.+)")
        result["irr_origin"] = field(r"^origin:\s*AS(\d+)")
    except Exception as e:
        log.debug(f"WHOIS {ip}: {e}")

    if result.get("asn") and conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO whois_irr_records
                    (ip_address, asn, org, network, rir, irr_route, irr_origin, raw_whois, cable_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (ip, result["asn"], result["org"],
                  result["network"] or None, result["rir"],
                  result["irr_route"], result["irr_origin"],
                  result["raw"][:2000], cable_id))
        conn.commit()

    return result


# ─────────────────────────────────────────────────────────────────────────────
# VETOR D — MPLS TRACEROUTE + IXP PEERING LAN
# ─────────────────────────────────────────────────────────────────────────────

def mpls_traceroute(dst: str, max_hops: int = MPLS_MAX_HOPS) -> list[dict]:
    """
    Traceroute MPLS-aware usando UDP/3503, TCP SYN/443, ICMP.
    Revela Label Switch Routers (LSRs) ocultos ao traceroute normal.
    """
    lsrs = []
    log.info(f"MPLS traceroute → {dst}")

    for method, extra in [
        ("udp",  ["-p", "3503"]),
        ("tcp",  ["-T", "-p", "443"]),
        ("icmp", ["-I"]),
    ]:
        try:
            cmd = ["traceroute", "-n", "-m", str(max_hops), "-w", "2",
                   "-M", method] + extra + [dst]
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=90)

            for i, line in enumerate(out.stdout.splitlines()[1:], 1):
                parts = line.split()
                if len(parts) >= 2 and parts[1] != "*":
                    try:
                        hop_ip = parts[1]
                        ipaddress.ip_address(hop_ip)
                        rtt    = float(parts[2]) if len(parts) > 2 else 0.0
                        label  = None
                        m      = re.search(r"Label\s+(\d+)", line)
                        if m:
                            label = int(m.group(1))
                        lsrs.append({
                            "hop_number": i,
                            "lsr_ip":     hop_ip,
                            "mpls_label": label,
                            "ttl_mode":   method,
                            "rtt_ms":     rtt,
                        })
                    except Exception:
                        continue
            if lsrs:
                break
        except Exception as e:
            log.debug(f"MPLS traceroute {method}: {e}")

    return lsrs


def scan_ixp_peering_lan(conn=None, max_ixps: int = IXP_MAX_SCAN) -> list[dict]:
    """
    Consulta PeeringDB para prefixos de peering LAN dos IXPs.
    Esses prefixos contêm switches de troca raramente visíveis externamente.
    """
    log.info(f"=== VETOR D: IXP Peering LAN scan (top {max_ixps} IXPs) ===")
    found = []

    try:
        r = requests.get("https://www.peeringdb.com/api/ixpfx",
                         params={"status": "ok"}, timeout=30,
                         headers={"Accept": "application/json"})
        ixp_prefixes = r.json().get("data", [])[:max_ixps * 2]
        log.info(f"PeeringDB: {len(ixp_prefixes)} prefixos obtidos.")
    except Exception as e:
        log.error(f"PeeringDB: {e}")
        return []

    for pfx_entry in ixp_prefixes[:max_ixps]:
        prefix_str = pfx_entry.get("prefix", "")
        ixp_id     = pfx_entry.get("ixlan_id")

        try:
            net   = ipaddress.ip_network(prefix_str, strict=False)
            hosts = list(net.hosts())
            if len(hosts) > 512:
                continue
        except Exception:
            continue

        ixp_name = f"IXP-{ixp_id}"
        try:
            r2 = requests.get(f"https://www.peeringdb.com/api/ixlan/{ixp_id}",
                               timeout=10, headers={"Accept": "application/json"})
            ixp_name = r2.json().get("data", [{}])[0].get("name", ixp_name)
        except Exception:
            pass

        log.info(f"Scanning {ixp_name} ({prefix_str}) — {len(hosts)} hosts")

        def scan_ixp_host(ip_obj):
            ip    = str(ip_obj)
            ports = []
            for port in [22, 80, 161, 443, 830]:
                try:
                    with socket.create_connection((ip, port), timeout=1):
                        ports.append(port)
                except Exception:
                    pass
            if not ports:
                return None

            asn_info = {}
            try:
                r = requests.get("https://stat.ripe.net/data/prefix-overview/data.json",
                                 params={"resource": ip}, timeout=5)
                asns = r.json().get("data", {}).get("asns", [])
                if asns:
                    asn_info = {"asn": str(asns[0]["asn"]),
                                "name": asns[0].get("holder", "")}
            except Exception:
                pass

            return {
                "ixp_name":       ixp_name,
                "peering_prefix": prefix_str,
                "peer_ip":        ip,
                "peer_asn":       asn_info.get("asn", ""),
                "peer_name":      asn_info.get("name", ""),
                "open_ports":     ports,
            }

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(scan_ixp_host, h): h for h in hosts}
            for f in as_completed(futures):
                result = f.result()
                if result:
                    found.append(result)

    if found and conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, """
                INSERT INTO ixp_peering_lan
                    (ixp_name, peering_prefix, peer_ip, peer_asn, peer_name, open_ports)
                VALUES %s
            """, [(e["ixp_name"], e["peering_prefix"], e["peer_ip"],
                   e["peer_asn"], e["peer_name"], json.dumps(e["open_ports"]))
                  for e in found])
        conn.commit()

    log.info(f"IXP Peering LAN: {len(found)} switches/roteadores encontrados.")
    return found


# ─────────────────────────────────────────────────────────────────────────────
# VETOR E — SHODAN API (varredura passiva)
# ─────────────────────────────────────────────────────────────────────────────

def shodan_search_asn(asn: str, max_results: int = 200) -> list[dict]:
    """
    Consulta o Shodan para dispositivos já escaneados num ASN.
    Retorna sysDescr SNMP, banners e portas abertas sem gerar tráfego.
    Requer: SHODAN_API_KEY definido.
    """
    if not SHODAN_API_KEY:
        log.warning("Shodan: SHODAN_API_KEY não definido.")
        return []

    log.info(f"Shodan: buscando AS{asn}…")
    results = []
    page    = 1

    while len(results) < max_results:
        try:
            r = requests.get(
                "https://api.shodan.io/shodan/host/search",
                params={"key": SHODAN_API_KEY,
                        "query": f"asn:AS{asn} port:161,830,22",
                        "page": page},
                timeout=15,
            )
            items = r.json().get("matches", [])
            if not items:
                break

            for item in items:
                results.append({
                    "ip":        item.get("ip_str", ""),
                    "port":      item.get("port"),
                    "vendor":    fingerprint_vendor(item.get("data", "")),
                    "banner":    item.get("data", "")[:300],
                    "org":       item.get("org", ""),
                    "isp":       item.get("isp", ""),
                    "hostnames": item.get("hostnames", []),
                    "country":   item.get("location", {}).get("country_code", ""),
                    "asn":       item.get("asn", ""),
                })
            page += 1
            time.sleep(1)  # Shodan free: 1 req/s
        except Exception as e:
            log.error(f"Shodan: {e}")
            break

    log.info(f"Shodan: {len(results)} dispositivos em AS{asn}.")
    return results


# ─────────────────────────────────────────────────────────────────────────────
# VETOR F — gNMI / gRPC (OpenConfig Streaming Telemetry)
# ─────────────────────────────────────────────────────────────────────────────

# OpenConfig paths comuns para equipamentos de cabo submarino
GNMI_PATHS = [
    "/interfaces/interface/state",
    "/network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor/state",
    "/platform/components/component/state",
    "/system/state",
]


def gnmi_get(ip: str, paths: list[str] = None,
             user: str = GNMI_USER, password: str = GNMI_PASS,
             port: int = 57400) -> dict:
    """
    Conecta via gNMI (gRPC, porta 57400 ou 6030) e faz Get request.
    Retorna telemetria estruturada OpenConfig dos paths solicitados.
    Requer: pip install pygnmi; credenciais GNMI_USER/GNMI_PASS.
    Alternativa de porta: 6030 (Arista), 57400 (Cisco/Nokia padrão).
    """
    if not PYGNMI_OK:
        return {"error": "pygnmi não instalado. pip install pygnmi"}
    if not password:
        return {"error": "GNMI_PASS não definido"}

    paths   = paths or GNMI_PATHS[:2]
    result  = {"ip": ip, "port": port, "paths": paths,
               "updates": [], "vendor": "unknown"}

    for try_port in [port, 6030]:
        try:
            with gNMIclient(
                target=(ip, try_port),
                username=user,
                password=password,
                insecure=True,
                timeout=TIMEOUT * 3,
            ) as gc:
                cap = gc.capabilities()
                for model in cap.get("supported_models", []):
                    vendor_hint = model.get("organization", "")
                    result["vendor"] = fingerprint_vendor(vendor_hint) or result["vendor"]

                get_result = gc.get(path=paths, encoding="JSON_IETF")
                for notif in get_result.get("notification", []):
                    for upd in notif.get("update", []):
                        result["updates"].append({
                            "path": upd.get("path", ""),
                            "val":  upd.get("val", {}),
                        })

            result["port"] = try_port
            break
        except Exception as e:
            log.debug(f"gNMI {ip}:{try_port}: {e}")
            continue

    return result


def run_gnmi_collection(conn, targets: list[str],
                        cable_id: str = None) -> list[dict]:
    """Coleta telemetria gNMI em paralelo para uma lista de IPs."""
    log.info(f"=== VETOR F: gNMI — {len(targets)} alvos ===")
    all_results = []

    def collect_one(ip):
        r = gnmi_get(ip)
        r["cable_id"] = cable_id
        return r

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, GNMI_MAX_IPS)) as pool:
        futures = {pool.submit(collect_one, ip): ip for ip in targets}
        for f in as_completed(futures):
            r = f.result()
            all_results.append(r)
            if r.get("updates") and conn:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_values(cur, """
                        INSERT INTO gnmi_telemetry_samples
                            (ip_address, gnmi_path, update_json, vendor, cable_id)
                        VALUES %s
                    """, [(
                        r["ip"],
                        ",".join(r["paths"]),
                        json.dumps(r["updates"]),
                        r.get("vendor"),
                        cable_id,
                    )])
                conn.commit()

    log.info(f"gNMI: {len(all_results)} coletas realizadas.")
    return all_results


# ─────────────────────────────────────────────────────────────────────────────
# VETOR G — RIPE Atlas API
# ─────────────────────────────────────────────────────────────────────────────

ATLAS_API_BASE = "https://atlas.ripe.net/api/v2"


def ripe_atlas_search_measurements(target_ip: str,
                                   max_results: int = ATLAS_MAX_MEAS) -> list[dict]:
    """
    Consulta medições públicas existentes no RIPE Atlas para um IP alvo.
    Retorna traceroutes, pings e DNS queries já realizados por probes globais.
    Não requer autenticação para leitura de medições públicas.
    """
    log.info(f"RIPE Atlas: buscando medições para {target_ip}…")
    results = []

    try:
        r = requests.get(
            f"{ATLAS_API_BASE}/measurements/",
            params={
                "target":    target_ip,
                "status":    "4,5",  # stopped + ongoing
                "type":      "traceroute,ping",
                "page_size": min(max_results, 100),
            },
            timeout=15,
        )
        data = r.json()
        for msm in data.get("results", []):
            results.append({
                "measurement_id": msm.get("id"),
                "target_ip":      target_ip,
                "msm_type":       msm.get("type", ""),
                "status":         msm.get("status", {}).get("name", ""),
                "result_count":   msm.get("result_rt", 0),
                "start_time":     msm.get("start_time"),
                "stop_time":      msm.get("stop_time"),
                "description":    msm.get("description", ""),
            })
    except Exception as e:
        log.error(f"RIPE Atlas search {target_ip}: {e}")
        return []

    log.info(f"RIPE Atlas: {len(results)} medições para {target_ip}.")
    return results


def ripe_atlas_get_results(measurement_id: int,
                            max_probes: int = 50) -> list[dict]:
    """
    Baixa os resultados mais recentes de uma medição RIPE Atlas.
    Retorna RTTs e hop IPs de traceroutes públicos.
    """
    results = []
    try:
        r = requests.get(
            f"{ATLAS_API_BASE}/measurements/{measurement_id}/results/",
            params={"most_recent": 1, "page_size": max_probes},
            timeout=20,
        )
        results = r.json() if isinstance(r.json(), list) else r.json().get("results", [])
    except Exception as e:
        log.debug(f"RIPE Atlas results {measurement_id}: {e}")
    return results


def ripe_atlas_create_traceroute(target_ip: str) -> dict:
    """
    Cria uma nova medição traceroute no RIPE Atlas (requer RIPE_ATLAS_KEY).
    Usa 10 probes globais distribuídos para traçar rota até o IP alvo.
    """
    if not RIPE_ATLAS_KEY:
        return {"error": "RIPE_ATLAS_KEY não definido"}

    payload = {
        "definitions": [{
            "target":      target_ip,
            "description": f"RMIFO traceroute to {target_ip}",
            "type":        "traceroute",
            "af":          4,
            "protocol":    "ICMP",
            "resolve_on_probe": False,
        }],
        "probes": [{
            "type":      "area",
            "value":     "WW",
            "requested": 10,
        }],
        "is_oneoff": True,
        "bill_to": "levi.runner01@gmail.com",
    }

    try:
        r = requests.post(
            f"{ATLAS_API_BASE}/measurements/",
            json=payload,
            headers={"Authorization": f"Key {RIPE_ATLAS_KEY}"},
            timeout=15,
        )
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def run_ripe_atlas(conn, ips: list[str],
                   cable_id: str = None,
                   fetch_results: bool = True) -> list[dict]:
    """
    Pipeline RIPE Atlas: busca medições existentes + baixa resultados.
    Cria novas medições se RIPE_ATLAS_KEY disponível e não há dados recentes.
    """
    log.info(f"=== VETOR G: RIPE Atlas — {len(ips)} IPs ===")
    all_records = []

    for ip in ips[:20]:
        msms = ripe_atlas_search_measurements(ip)

        if not msms and RIPE_ATLAS_KEY:
            log.info(f"RIPE Atlas: criando traceroute para {ip}…")
            new_msm = ripe_atlas_create_traceroute(ip)
            if "measurements" in new_msm:
                msm_id = new_msm["measurements"][0]
                msms.append({
                    "measurement_id": msm_id,
                    "target_ip":      ip,
                    "msm_type":       "traceroute",
                    "status":         "ongoing",
                })

        for msm in msms:
            rec = {**msm, "cable_id": cable_id, "result_json": []}
            if fetch_results and msm.get("measurement_id"):
                rec["result_json"] = ripe_atlas_get_results(msm["measurement_id"])
            all_records.append(rec)

            if conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO ripe_atlas_measurements
                            (measurement_id, target_ip, msm_type, status, result_json, cable_id)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                    """, (
                        msm.get("measurement_id"),
                        ip,
                        msm.get("msm_type", ""),
                        msm.get("status", ""),
                        json.dumps(rec["result_json"]),
                        cable_id,
                    ))
                conn.commit()

    log.info(f"RIPE Atlas: {len(all_records)} registros coletados.")
    return all_records


# ─────────────────────────────────────────────────────────────────────────────
# PIPELINE COMPLETO AVANÇADO
# ─────────────────────────────────────────────────────────────────────────────

def run_full_advanced(conn):
    """
    Pipeline avançado completo integrando todos os vetores (A-G).
    Usa dados já existentes no banco como seeds.
    """
    log.info("━" * 60)
    log.info("  RMIFO — Advanced Reach: Full Pipeline (Vetores A-G)")
    log.info("━" * 60)

    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT eis.ip_start::text, clp.cable_id
            FROM   endpoint_ip_samples eis
            JOIN   cable_landing_points clp USING (endpoint_id)
            WHERE  eis.ip_start IS NOT NULL
            LIMIT  50
        """)
        ep_rows    = cur.fetchall()
        landing_ips = [r[0] for r in ep_rows]
        cable_ids   = [r[1] for r in ep_rows]

        cidrs = list(set(
            str(ipaddress.ip_network(f"{ip}/24", strict=False))
            for ip in landing_ips if ip
        ))

        cur.execute("""
            SELECT DISTINCT as_number::text
            FROM   hop_switch_geolocations
            WHERE  as_number IS NOT NULL
            LIMIT  20
        """)
        asns = {r[0] for r in cur.fetchall()}

    log.info(f"Seeds: {len(landing_ips)} IPs, {len(cidrs)} CIDRs, {len(asns)} ASNs.")

    # Vetor A: Banner sweep (SSH + TLS + Telnet + BGP-179 + HTTP + JA3S)
    for i, cidr in enumerate(cidrs[:5]):
        cid = cable_ids[i] if i < len(cable_ids) else None
        run_banner_sweep(conn, cidr, cable_id=cid)

    # DNS PTR em todos os IPs conhecidos
    all_ips = landing_ips.copy()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT peer_ip::text FROM mrt_bgp_peers
            WHERE peer_ip IS NOT NULL LIMIT 200
        """)
        all_ips += [r[0] for r in cur.fetchall()]
    bulk_dns_ptr_fingerprint(list(set(all_ips)))

    # Vetor B: NETCONF nos switches com porta 830 aberta
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (ip_address::text) ip_address::text, cable_id
            FROM switch_banners
            WHERE port = 830
            ORDER BY ip_address, cable_id NULLS LAST
            LIMIT 20
        """)
        netconf_map = {r[0]: r[1] for r in cur.fetchall()}
    for ip, cid in netconf_map.items():
        run_netconf_collection(conn, [ip], cable_id=cid)

    # Vetor C: MRT RouteViews + RIPE RIS + Looking Glass + WHOIS
    if asns:
        # RouteViews
        mrt_path = download_latest_mrt("route-views2")
        if mrt_path:
            parse_mrt_for_asn(mrt_path, asns, conn=conn, source="routeviews")
        # RIPE RIS (segundo source)
        ris_path = download_latest_ris("rrc00")
        if ris_path:
            parse_mrt_for_asn(ris_path, asns, conn=conn,
                              collector="rrc00", source="ripe_ris")

    # Looking Glass para os principais landing IPs
    for ip in landing_ips[:5]:
        looking_glass_query(ip, conn=conn)

    # IRR/WHOIS passivo
    ip_to_cable = dict(zip(landing_ips, cable_ids))
    for ip in landing_ips[:WHOIS_MAX_IPS]:
        whois_lookup(ip, conn=conn, cable_id=ip_to_cable.get(ip))

    # Vetor D: MPLS traceroute
    for dst_ip in landing_ips[:10]:
        cid = ip_to_cable.get(dst_ip)
        lsrs = mpls_traceroute(dst_ip)
        if lsrs and conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(cur, """
                    INSERT INTO mpls_lsr_map
                        (dst_ip, hop_number, lsr_ip, mpls_label, ttl_mode, rtt_ms, cable_id)
                    VALUES %s
                """, [(dst_ip, l["hop_number"], l["lsr_ip"],
                       l.get("mpls_label"), l["ttl_mode"], l["rtt_ms"], cid)
                      for l in lsrs])
            conn.commit()

    scan_ixp_peering_lan(conn, max_ixps=10)

    # Vetor E: Shodan
    for asn in list(asns)[:5]:
        shodan_search_asn(asn, max_results=100)

    # Vetor F: gNMI nos switches com porta 57400 aberta
    gnmi_candidates = []
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ip_address::text
            FROM switch_banners
            WHERE port = 22 AND vendor IN ('Cisco','Nokia','Arista','Juniper')
            LIMIT 10
        """)
        gnmi_candidates = [r[0] for r in cur.fetchall()]
    if gnmi_candidates:
        run_gnmi_collection(conn, gnmi_candidates[:GNMI_MAX_IPS])

    # Vetor G: RIPE Atlas
    run_ripe_atlas(conn, landing_ips[:20], fetch_results=True)

    # Sumário
    tables = [
        "switch_banners", "netconf_state", "mrt_bgp_peers", "mpls_lsr_map",
        "ixp_peering_lan", "gnmi_telemetry_samples", "ripe_atlas_measurements",
        "looking_glass_routes", "whois_irr_records",
    ]
    with conn.cursor() as cur:
        totals = {}
        for tbl in tables:
            cur.execute(f"SELECT COUNT(*) FROM {tbl}")
            totals[tbl] = cur.fetchone()[0]

    log.info("─── Sumário avançado ───")
    for k, v in totals.items():
        log.info(f"  {k}: {v}")
    return totals


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="RMIFO Advanced Switch Reach (Vetores A-G)")
    ap.add_argument("--mode", choices=[
        "banner", "netconf", "mrt", "ris", "mpls",
        "shodan", "ixp", "dns", "lg", "whois",
        "gnmi", "atlas", "full",
    ], default="full")
    ap.add_argument("--cidr",   help="CIDR para banner sweep (ex: 203.0.113.0/24)")
    ap.add_argument("--target", help="IP alvo para NETCONF/MPLS/gNMI/Atlas/WHOIS/LG")
    ap.add_argument("--asn",    help="ASN para MRT/Shodan (ex: 3356)")
    ap.add_argument("--dst",    help="IP destino para MPLS traceroute")
    ap.add_argument("--ips",    nargs="*", default=[], help="IPs para DNS PTR")
    ap.add_argument("--rrc",    default="rrc00", help="RIPE RIS coletor (ex: rrc00, rrc01)")
    ap.add_argument("--no-db",  action="store_true")
    args = ap.parse_args()

    conn = None
    if not args.no_db:
        try:
            conn = get_conn()
            ensure_advanced_tables(conn)
        except Exception as e:
            log.error(f"BD: {e}")
            sys.exit(1)

    try:
        if args.mode == "banner":
            cidr    = args.cidr or "203.0.113.0/24"
            results = run_banner_sweep(conn, cidr)
            print(json.dumps(results[:5], indent=2, ensure_ascii=False))

        elif args.mode == "netconf":
            if not args.target:
                log.error("--target obrigatório para netconf"); sys.exit(1)
            r = netconf_get(args.target)
            print(json.dumps({k: v for k, v in r.items() if k != "xml"}, indent=2))

        elif args.mode == "mrt":
            if not args.asn:
                log.error("--asn obrigatório para mrt"); sys.exit(1)
            path = download_latest_mrt()
            if path:
                results = parse_mrt_for_asn(path, {args.asn}, conn=conn, source="routeviews")
                print(json.dumps(results[:10], indent=2, ensure_ascii=False))

        elif args.mode == "ris":
            if not args.asn:
                log.error("--asn obrigatório para ris"); sys.exit(1)
            path = download_latest_ris(args.rrc)
            if path:
                results = parse_mrt_for_asn(path, {args.asn}, conn=conn,
                                            collector=args.rrc, source="ripe_ris")
                print(json.dumps(results[:10], indent=2, ensure_ascii=False))

        elif args.mode == "mpls":
            dst = args.dst or args.target
            if not dst:
                log.error("--dst obrigatório para mpls"); sys.exit(1)
            lsrs = mpls_traceroute(dst)
            print(json.dumps(lsrs, indent=2))

        elif args.mode == "shodan":
            if not args.asn:
                log.error("--asn obrigatório para shodan"); sys.exit(1)
            results = shodan_search_asn(args.asn)
            print(json.dumps(results[:10], indent=2, ensure_ascii=False))

        elif args.mode == "ixp":
            found = scan_ixp_peering_lan(conn)
            print(json.dumps(found[:5], indent=2, ensure_ascii=False))

        elif args.mode == "dns":
            ips = args.ips
            if not ips and conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT DISTINCT eis.ip_start::text
                        FROM endpoint_ip_samples eis
                        WHERE eis.ip_start IS NOT NULL LIMIT 100
                    """)
                    ips = [r[0] for r in cur.fetchall()]
            results  = bulk_dns_ptr_fingerprint(ips)
            switches = [r for r in results if r["device_type"] != "unknown"]
            print(json.dumps(switches, indent=2, ensure_ascii=False))

        elif args.mode == "lg":
            if not args.target:
                log.error("--target obrigatório para lg"); sys.exit(1)
            results = looking_glass_query(args.target, conn=conn)
            print(json.dumps(results, indent=2, ensure_ascii=False))

        elif args.mode == "whois":
            target = args.target or (args.ips[0] if args.ips else None)
            if not target:
                log.error("--target obrigatório para whois"); sys.exit(1)
            result = whois_lookup(target, conn=conn)
            print(json.dumps({k: v for k, v in result.items() if k != "raw"}, indent=2))

        elif args.mode == "gnmi":
            if not args.target:
                log.error("--target obrigatório para gnmi"); sys.exit(1)
            result = gnmi_get(args.target)
            print(json.dumps({k: v for k, v in result.items() if k != "updates"},
                             indent=2, ensure_ascii=False))
            if result.get("updates"):
                print(f"  [{len(result['updates'])} updates recebidos]")

        elif args.mode == "atlas":
            target = args.target
            if not target:
                log.error("--target obrigatório para atlas"); sys.exit(1)
            msms = ripe_atlas_search_measurements(target)
            if conn:
                run_ripe_atlas(conn, [target])
            print(json.dumps(msms[:5], indent=2, ensure_ascii=False))

        elif args.mode == "full":
            if not conn:
                log.error("Modo full requer banco."); sys.exit(1)
            totals = run_full_advanced(conn)
            print(json.dumps(totals, indent=2, ensure_ascii=False))

    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    main()
