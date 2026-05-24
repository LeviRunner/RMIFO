#!/usr/bin/env python3
"""
RMIFO — Advanced Switch Reach Extension
=========================================
Quatro vetores novos que alcançam switches que os métodos anteriores
(SNMP, MTR, LLDP, ASN expansion) NÃO conseguem ver:

  VETOR A — SSH Banner Grab + TLS Cert
    Identifica vendor e hostname de switches sem precisar de credenciais.
    Porta 22 → banner "Cisco IOS / Junos / EOS".
    Porta 443/8443 → Subject CN do certificado TLS = hostname real.

  VETOR B — NETCONF / RESTCONF
    Para switches Juniper, Arista, Cisco IOS-XE e Nokia que expõem a
    porta 830 (NETCONF) ou 8080/443 (RESTCONF), extrai configuração
    e estado em XML/JSON estruturado, muito mais rico que SNMP.

  VETOR C — RouteViews MRT Dump (BGP global)
    Baixa dumps BGP do projeto RouteViews (arquivos MRT públicos),
    parseia todos os prefixos e seus AS paths, e cruza com os ASNs
    dos operadores de cabos para localizar roteadores de borda
    que nunca responderiam ICMP/SNMP externamente.

  VETOR D — MPLS Traceroute + IXP Peering LAN
    MPLS LSRs são invisíveis ao traceroute normal porque não decrementam
    TTL no payload interno. O MPLS traceroute usa UDP na porta 3503
    (ou TCP SYN na 443) para revelar Label Switch Routers ocultos.
    Complementado com varredura nas peering LANs dos IXPs (via PeeringDB)
    onde os switches de troca ficam.

  VETOR E — Shodan API + DNS PTR Fingerprinting
    Shodan já escaneou todo o espaço IPv4. Consultar switches por ASN
    retorna sysDescr, portas, banners — sem gerar tráfego.
    DNS PTR segue convenções como sw1.sfo.cable.net que revelam
    tipo de dispositivo (sw/rt/cr) e localização.

Uso:
  python rmifo_advanced_reach.py --mode banner  --cidr 203.0.113.0/24
  python rmifo_advanced_reach.py --mode netconf --target 192.0.2.1
  python rmifo_advanced_reach.py --mode mrt     --asn 3356
  python rmifo_advanced_reach.py --mode mpls    --dst 198.51.100.1
  python rmifo_advanced_reach.py --mode shodan  --asn 1273
  python rmifo_advanced_reach.py --mode full
"""

import os, sys, re, json, time, socket, ssl, logging
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
SHODAN_API_KEY = os.getenv("SHODAN_API_KEY", "")
NETCONF_USER   = os.getenv("NETCONF_USER",   "admin")
NETCONF_PASS   = os.getenv("NETCONF_PASS",   "")
MAX_WORKERS         = int(os.getenv("PROBE_WORKERS", "30"))
TIMEOUT             = int(os.getenv("PROBE_TIMEOUT", "5"))

# ── Cobertura calibrada por vetor ─────────────────────────────────────────────
MPLS_MAX_HOPS       = int(os.getenv("MPLS_MAX_HOPS",   "30"))  # Vetor D: hops TTL incremental
IXP_MAX_SCAN        = int(os.getenv("IXP_MAX_SCAN",    "20"))  # Vetor D: IXPs PeeringDB
NETCONF_MAX_IPS     = int(os.getenv("NETCONF_MAX",     "20"))  # Vetor B: máx IPs por ciclo
SHODAN_MAX_RESULTS  = int(os.getenv("SHODAN_MAX",      "50"))  # Vetor E: resultados Shodan
DNS_PTR_BATCH       = int(os.getenv("DNS_PTR_BATCH",   "50"))  # Vetor E: IPs por lote PTR

# Fingerprints de vendor em banners SSH e sysDescr
VENDOR_PATTERNS = {
    "Cisco":    [r"cisco", r"IOS[\s\-]", r"Nexus", r"Catalyst"],
    "Juniper":  [r"juniper", r"Junos", r"JUNOS"],
    "Arista":   [r"arista", r"EOS"],
    "Huawei":   [r"huawei", r"VRP"],
    "Nokia":    [r"nokia", r"SR OS", r"TiMOS"],
    "Extreme":  [r"extreme", r"ExtremeXOS"],
    "MikroTik": [r"mikrotik", r"RouterOS"],
    "Cumulus":  [r"cumulus", r"Linux"],
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
        origin       TEXT,
        next_hop     INET,
        cable_asn    TEXT,
        collector    TEXT
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
    """
    with conn.cursor() as cur:
        cur.execute(ddl)
    conn.commit()
    log.info("Tabelas avançadas criadas/verificadas.")


# ─────────────────────────────────────────────────────────────────────────────
# VETOR A — SSH BANNER GRAB + TLS CERTIFICATE
# ─────────────────────────────────────────────────────────────────────────────

def fingerprint_vendor(text: str) -> str:
    for vendor, patterns in VENDOR_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                return vendor
    return "unknown"


def grab_ssh_banner(ip: str, port: int = 22) -> dict:
    """
    Conecta na porta SSH e lê o banner de identificação sem autenticar.
    Switches expõem vendor/versão no banner:
    "SSH-2.0-Cisco-1.25" ou "SSH-2.0-OpenSSH_for_Junos_7.4".
    """
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
    """
    Extrai o certificado TLS sem autenticar.
    Subject CN e SANs geralmente contêm o FQDN real do switch.
    """
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


def run_banner_sweep(conn, cidr: str, cable_id: str = None) -> list[dict]:
    """
    Varre um CIDR buscando banners SSH e TLS.
    Apenas IPs que respondem na porta são processados.
    """
    log.info(f"=== VETOR A: Banner sweep em {cidr} ===")
    net     = ipaddress.ip_network(cidr, strict=False)
    hosts   = list(net.hosts())
    results = []

    def probe_host(ip_obj):
        ip  = str(ip_obj)
        out = []
        for port, fn in [(22, grab_ssh_banner), (443, grab_tls_cert),
                         (830, grab_ssh_banner), (8443, grab_tls_cert)]:
            r = fn(ip, port)
            if r.get("banner_raw") or r.get("tls_cn"):
                r["cable_id"] = cable_id
                out.append(r)
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
                     vendor, hostname, tls_cn, tls_san, tls_issuer, cable_id)
                VALUES %s
            """, [(
                r["ip"], r["port"], r["banner_type"],
                r.get("banner_raw", ""), r.get("vendor", ""),
                r.get("hostname", ""), r.get("tls_cn", ""),
                r.get("tls_san", []), r.get("tls_issuer", ""),
                r.get("cable_id"),
            ) for r in results])
        conn.commit()

    log.info(f"Banner sweep: {len(results)} switches identificados em {cidr}.")
    return results


def bulk_dns_ptr_fingerprint(ips: list[str], conn=None) -> list[dict]:
    """
    PTR lookup em massa. Convenções de nomeação:
      sw1.pop-sao.telemar.net  → switch, PoP São Paulo
      cr2.ams.level3.net       → core router, Amsterdam
    Extrai: tipo (sw/cr/pe/br), localidade, operador.
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
    """
    Abre sessão NETCONF (SSH na porta 830) e executa <get-config>.
    Retorna XML bruto + resumo estruturado.
    Requer: pip install ncclient
    """
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
    """
    Consulta RESTCONF (HTTP/HTTPS na porta 8080 ou 443).
    Retorna JSON estruturado com o estado do dispositivo.
    """
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
    """Coleta NETCONF em paralelo em uma lista de switches."""
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
# VETOR C — ROUTEVIEWS MRT DUMP (BGP global passivo)
# ─────────────────────────────────────────────────────────────────────────────

def download_latest_mrt(collector: str = "route-views2") -> str | None:
    """
    Baixa o dump MRT mais recente do RouteViews (arquivos .bz2 ~100 MB, a cada 2h).
    Retorna caminho do arquivo local ou None em caso de erro.
    """
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
        log.info(f"MRT: baixando {file_url} (~100 MB)…")
        with requests.get(file_url, stream=True, timeout=120) as resp:
            resp.raise_for_status()
            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    f.write(chunk)

        log.info(f"MRT: download concluído → {tmp_path}")
        return tmp_path
    except Exception as e:
        log.error(f"MRT download: {e}")
        return None


def parse_mrt_for_asn(mrt_path: str, target_asns: set[str],
                      conn=None, collector: str = "route-views2",
                      max_entries: int = 500_000) -> list[dict]:
    """
    Parseia dump MRT procurando prefixos cujo AS path contém
    qualquer ASN dos operadores de cabos submarinos.
    Cada entrada revela: peer_ip (roteador de borda), peer_asn, prefix, path.
    Requer: pip install mrtparse
    """
    if not MRTPARSE_OK:
        log.warning("mrtparse não instalado. pip install mrtparse")
        return []

    log.info(f"=== VETOR C: MRT parse — ASNs alvo: {target_asns} ===")
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
                    for attr in rib.get("path_attributes", []):
                        if attr.get("type_code") == 2:
                            for seg in attr.get("value", []):
                                as_path.extend(str(a) for a in seg.get("value", []))

                    if target_asns.intersection(set(as_path)) or peer_asn in target_asns:
                        matches.append({
                            "peer_ip":   peer_ip,
                            "peer_asn":  peer_asn,
                            "prefix":    prefix,
                            "as_path":   " ".join(as_path),
                            "cable_asn": next((a for a in as_path if a in target_asns), peer_asn),
                            "collector": collector,
                        })
            except Exception:
                continue
    except Exception as e:
        log.error(f"MRT parse: {e}")
        return []

    log.info(f"MRT: {len(matches)} roteadores BGP encontrados nos ASNs alvo.")

    if matches and conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, """
                INSERT INTO mrt_bgp_peers
                    (peer_ip, peer_asn, prefix, as_path, cable_asn, collector)
                VALUES %s ON CONFLICT DO NOTHING
            """, [(
                m["peer_ip"] or None, m["peer_asn"], m["prefix"],
                m["as_path"], m["cable_asn"], m["collector"]
            ) for m in matches if m.get("peer_ip")])
        conn.commit()

    return matches


# ─────────────────────────────────────────────────────────────────────────────
# VETOR D — MPLS TRACEROUTE + IXP PEERING LAN
# ─────────────────────────────────────────────────────────────────────────────

def mpls_traceroute(dst: str, max_hops: int = MPLS_MAX_HOPS) -> list[dict]:
    """
    Traceroute MPLS-aware usando UDP na porta 3503 (LDP echo)
    ou TCP SYN na 443. Revela Label Switch Routers (LSRs) ocultos
    ao traceroute ICMP padrão.
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
    Consulta PeeringDB para obter prefixos de peering LAN dos IXPs.
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
# PIPELINE COMPLETO AVANÇADO
# ─────────────────────────────────────────────────────────────────────────────

def run_full_advanced(conn):
    """
    Pipeline avançado completo integrando todos os vetores.
    Usa dados já existentes no banco como seeds
    (IPs de landing points, ASNs de switches conhecidos).
    """
    log.info("━" * 50)
    log.info("  RMIFO — Advanced Reach: Full Pipeline")
    log.info("━" * 50)

    with conn.cursor() as cur:
        # IPs dos landing points via endpoint_ip_samples → cable_landing_points
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

        # CIDRs /24 dos landing points como alvo para banner sweep
        cidrs = list(set(
            str(ipaddress.ip_network(f"{ip}/24", strict=False))
            for ip in landing_ips if ip
        ))

        # ASNs dos comutadores já descobertos via MTR
        cur.execute("""
            SELECT DISTINCT as_number::text
            FROM   hop_switch_geolocations
            WHERE  as_number IS NOT NULL
            LIMIT  20
        """)
        asns = {r[0] for r in cur.fetchall()}

    log.info(f"Seeds: {len(landing_ips)} IPs, {len(cidrs)} CIDRs, {len(asns)} ASNs.")

    # Vetor A: Banner sweep nos CIDRs dos landing points
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

    # Vetor B: NETCONF nos switches com porta 830 aberta (preserva cable_id)
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

    # Vetor C: MRT dump do RouteViews
    if asns:
        mrt_path = download_latest_mrt("route-views2")
        if mrt_path:
            parse_mrt_for_asn(mrt_path, asns, conn=conn)

    # Vetor D: MPLS traceroute para os principais destinos (inclui cable_id)
    ip_to_cable = dict(zip(landing_ips, cable_ids))
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

    # IXP Peering LAN
    scan_ixp_peering_lan(conn, max_ixps=10)

    # Vetor E: Shodan para cada ASN
    for asn in list(asns)[:5]:
        shodan_search_asn(asn, max_results=100)

    # Sumário
    with conn.cursor() as cur:
        totals = {}
        for tbl in ["switch_banners", "netconf_state", "mrt_bgp_peers",
                    "mpls_lsr_map", "ixp_peering_lan"]:
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
    ap = argparse.ArgumentParser(description="RMIFO Advanced Switch Reach")
    ap.add_argument("--mode", choices=["banner", "netconf", "mrt", "mpls",
                                        "shodan", "ixp", "dns", "full"],
                    default="full")
    ap.add_argument("--cidr",   help="CIDR para banner sweep (ex: 203.0.113.0/24)")
    ap.add_argument("--target", help="IP alvo para NETCONF/MPLS")
    ap.add_argument("--asn",    help="ASN para MRT/Shodan (ex: 3356)")
    ap.add_argument("--dst",    help="IP destino para MPLS traceroute")
    ap.add_argument("--ips",    nargs="*", default=[], help="IPs para DNS PTR")
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
                results = parse_mrt_for_asn(path, {args.asn}, conn=conn)
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
