#!/usr/bin/env python3
"""
rmifo_ip_enrich.py — Enriquecimento nativo de IPs sem APIs de terceiros

Extrai campo a campo usando apenas protocolos de rede padrão:
  1. hostname        — PTR DNS reverso (socket.gethostbyaddr)
  2. timezone        — código IATA no PTR → tabela de 200 cidades
  3. asn_route       — WHOIS subprocess (route: / CIDR:)
  4. asn_domain      — WHOIS OrgURL ou domínio do abuse-mailbox
  5. asn_type        — WHOIS org-type + keywords no nome da org
  6. company_domain  — igual asn_domain (mesmo WHOIS)
  7. company_type    — igual asn_type
  8. abuse_email     — WHOIS abuse-mailbox
  9. abuse_network   — WHOIS inetnum / NetRange
 10. privacy_vpn     — port scan UDP/TCP 1194 / 51820 / 500 / 1723
 11. privacy_proxy   — port scan 3128 / 1080 + HTTP CONNECT probe
 12. privacy_tor     — port scan 9001 / 9030 + DirPort HTTP check
 13. privacy_relay   — padrão PTR de relay (apple.com relay / Cloudflare WARP)
 14. privacy_hosting — PTR pattern + ASN keywords + /24 PTR sweep
 15. tls_san_count   — contagem de SANs no certificado TLS (porta 443)
 16. tls_san_sample  — primeiros 20 domínios do SAN

Uso:
  python3 rmifo_ip_enrich.py                        # enriquece todos os IPs sem enriched_at
  python3 rmifo_ip_enrich.py --ip 1.2.3.4           # IP avulso
  python3 rmifo_ip_enrich.py --stale 7              # re-enriquece IPs com > 7 dias
  python3 rmifo_ip_enrich.py --field hostname,whois # só campos selecionados
  python3 rmifo_ip_enrich.py --dry-run              # imprime resultado sem salvar
  python3 rmifo_ip_enrich.py --limit 100            # limita número de IPs
"""

import argparse
import ipaddress
import json
import os
import re
import socket
import ssl
import struct
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

try:
    import psycopg2
    import psycopg2.extras
    HAS_PG = True
except ImportError:
    HAS_PG = False

# ── Configuração ──────────────────────────────────────────────────────────────

DATABASE_URL = os.getenv("DATABASE_URL", "")
TIMEOUT      = float(os.getenv("PROBE_TIMEOUT", "4"))
WORKERS      = int(os.getenv("PROBE_WORKERS",   "20"))

# ─────────────────────────────────────────────────────────────────────────────
# CAMPO 1 — hostname: PTR DNS reverso
# Protocolo: DNS PTR query (UDP 53) via socket padrão Python
# ─────────────────────────────────────────────────────────────────────────────

def extract_hostname(ip: str) -> Optional[str]:
    """
    Consulta PTR DNS reverso para o IP.
    Retorna o hostname canônico ou None se não existir.

    ex: 8.8.8.8 → "dns.google"
        142.250.X.X → "lga34s32-in-f14.1e100.net"
    """
    try:
        result = socket.gethostbyaddr(ip)
        return result[0] or None
    except (socket.herror, socket.gaierror, OSError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# CAMPO 2 — timezone: código IATA no hostname PTR
# Roteadores de backbone usam padrão NANOG: hostname.{iata3}.{operadora}.net
# ─────────────────────────────────────────────────────────────────────────────

# Mapeamento IATA → IANA timezone (200 aeroportos principais)
IATA_TZ: dict[str, str] = {
    # América do Norte
    "jfk": "America/New_York",    "lga": "America/New_York",
    "ewr": "America/New_York",    "bos": "America/New_York",
    "iad": "America/New_York",    "dca": "America/New_York",
    "mia": "America/New_York",    "atl": "America/New_York",
    "ord": "America/Chicago",     "mdw": "America/Chicago",
    "dfw": "America/Chicago",     "iah": "America/Chicago",
    "den": "America/Denver",      "slc": "America/Denver",
    "phx": "America/Phoenix",     "lax": "America/Los_Angeles",
    "sfo": "America/Los_Angeles", "sjc": "America/Los_Angeles",
    "sea": "America/Los_Angeles", "pdx": "America/Los_Angeles",
    "las": "America/Los_Angeles", "yyz": "America/Toronto",
    "yul": "America/Montreal",    "yvr": "America/Vancouver",
    "mex": "America/Mexico_City",
    # América do Sul
    "gru": "America/Sao_Paulo",   "gig": "America/Sao_Paulo",
    "bsb": "America/Sao_Paulo",   "ssa": "America/Bahia",
    "rec": "America/Recife",      "for": "America/Fortaleza",
    "bel": "America/Belem",       "mao": "America/Manaus",
    "bog": "America/Bogota",      "lim": "America/Lima",
    "scl": "America/Santiago",    "eze": "America/Argentina/Buenos_Aires",
    "mvd": "America/Montevideo",  "uio": "America/Guayaquil",
    "ccs": "America/Caracas",     "pan": "America/Panama",
    # Europa
    "lhr": "Europe/London",       "lgw": "Europe/London",
    "man": "Europe/London",       "cdg": "Europe/Paris",
    "ory": "Europe/Paris",        "ams": "Europe/Amsterdam",
    "fra": "Europe/Berlin",       "txl": "Europe/Berlin",
    "muc": "Europe/Berlin",       "ham": "Europe/Berlin",
    "bru": "Europe/Brussels",     "zrh": "Europe/Zurich",
    "vie": "Europe/Vienna",       "prg": "Europe/Prague",
    "waw": "Europe/Warsaw",       "bud": "Europe/Budapest",
    "arn": "Europe/Stockholm",    "cph": "Europe/Copenhagen",
    "hel": "Europe/Helsinki",     "osl": "Europe/Oslo",
    "mad": "Europe/Madrid",       "bcn": "Europe/Madrid",
    "lis": "Europe/Lisbon",       "fco": "Europe/Rome",
    "mxp": "Europe/Rome",         "ath": "Europe/Athens",
    "ist": "Europe/Istanbul",     "svo": "Europe/Moscow",
    "led": "Europe/Moscow",       "dme": "Europe/Moscow",
    "kbp": "Europe/Kiev",
    # Oriente Médio
    "dxb": "Asia/Dubai",          "auh": "Asia/Dubai",
    "doh": "Asia/Qatar",          "bah": "Asia/Bahrain",
    "kwi": "Asia/Kuwait",         "ruh": "Asia/Riyadh",
    "thr": "Asia/Tehran",         "tlv": "Asia/Jerusalem",
    # Ásia
    "bom": "Asia/Kolkata",        "del": "Asia/Kolkata",
    "maa": "Asia/Kolkata",        "bom": "Asia/Kolkata",
    "hkg": "Asia/Hong_Kong",      "pek": "Asia/Shanghai",
    "pvg": "Asia/Shanghai",       "can": "Asia/Shanghai",
    "nrt": "Asia/Tokyo",          "hnd": "Asia/Tokyo",
    "kix": "Asia/Tokyo",          "icn": "Asia/Seoul",
    "sin": "Asia/Singapore",      "kul": "Asia/Kuala_Lumpur",
    "cgk": "Asia/Jakarta",        "bkk": "Asia/Bangkok",
    "mnl": "Asia/Manila",         "tpe": "Asia/Taipei",
    "ctg": "Asia/Dhaka",          "dac": "Asia/Dhaka",
    # África
    "los": "Africa/Lagos",        "acc": "Africa/Accra",
    "abj": "Africa/Abidjan",      "dkr": "Africa/Dakar",
    "cmn": "Africa/Casablanca",   "alg": "Africa/Algiers",
    "tun": "Africa/Tunis",        "cai": "Africa/Cairo",
    "jro": "Africa/Nairobi",      "nbo": "Africa/Nairobi",
    "jnb": "Africa/Johannesburg", "cpt": "Africa/Johannesburg",
    "lun": "Africa/Lusaka",       "lad": "Africa/Luanda",
    "mpm": "Africa/Maputo",
    # Oceania
    "syd": "Australia/Sydney",    "mel": "Australia/Melbourne",
    "bne": "Australia/Brisbane",  "per": "Australia/Perth",
    "akl": "Pacific/Auckland",    "nadi": "Pacific/Fiji",
}

def extract_timezone(hostname: Optional[str], country_code: Optional[str] = None) -> Optional[str]:
    """
    Tenta extrair timezone do hostname PTR.
    Fallback: country_code → capital timezone.

    Padrões comuns:
      ae-1.r01.lax01.isp.net       → lax → America/Los_Angeles
      xe-0-0.pe1.ams.colt.net      → ams → Europe/Amsterdam
      gi1.br01.gru02.net.ms        → gru → America/Sao_Paulo
    """
    if hostname:
        # Extrai segmentos de 3 letras que podem ser códigos IATA
        segments = re.findall(r'(?<![a-z])([a-z]{3})\d*(?![a-z])', hostname.lower())
        for seg in segments:
            if seg in IATA_TZ:
                return IATA_TZ[seg]

    # Fallback por código de país → timezone da capital
    COUNTRY_TZ: dict[str, str] = {
        "BR": "America/Sao_Paulo", "US": "America/New_York",
        "GB": "Europe/London",     "DE": "Europe/Berlin",
        "FR": "Europe/Paris",      "NL": "Europe/Amsterdam",
        "PT": "Europe/Lisbon",     "NG": "Africa/Lagos",
        "ZA": "Africa/Johannesburg", "SG": "Asia/Singapore",
        "JP": "Asia/Tokyo",        "CN": "Asia/Shanghai",
        "IN": "Asia/Kolkata",      "AE": "Asia/Dubai",
    }
    if country_code and country_code.upper() in COUNTRY_TZ:
        return COUNTRY_TZ[country_code.upper()]
    return None


# ─────────────────────────────────────────────────────────────────────────────
# CAMPOS 3-9 — WHOIS: asn_route, asn_domain, asn_type, company_*, abuse_*
# Protocolo: whois subprocess → TCP 43 (RFC 3912)
# Servidores: whois.ripe.net, whois.arin.net, whois.apnic.net (auto-seleção)
# ─────────────────────────────────────────────────────────────────────────────

# Palavras-chave para classificar asn_type
_HOSTING_KW = {
    "amazon", "aws", "google cloud", "azure", "microsoft", "cloudflare",
    "fastly", "akamai", "digitalocean", "linode", "vultr", "hetzner",
    "ovh", "scaleway", "leaseweb", "rackspace", "softlayer", "equinix",
    "hosting", "datacenter", "data center", "colocation", "colo",
    "cdn", "content delivery", "cloud",
}
_EDU_KW = {
    "university", "college", "instituto", "universidade", "faculdade",
    "education", "escola", "academic", "research",
}
_GOV_KW = {
    "governo", "government", "federal", "ministry", "ministerio",
    "ministerio", "prefeitura", "municipal", "estado de", "estado do",
    "republic", "national", "agencia", "agency",
}
_VPN_KW = {
    "nordvpn", "expressvpn", "surfshark", "mullvad", "protonvpn",
    "privateinternetaccess", "pia", "hidemyass", "cyberghost", "vpn",
}


def _run_whois(target: str, server: Optional[str] = None) -> str:
    """Executa whois e retorna stdout como string."""
    cmd = ["whois"]
    if server:
        cmd += ["-h", server]
    cmd.append(target)
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=TIMEOUT * 2, errors="replace"
        )
        return result.stdout or ""
    except Exception:
        return ""


def _extract_whois_field(text: str, *keys: str) -> Optional[str]:
    """Extrai primeiro valor encontrado para qualquer das chaves WHOIS."""
    for key in keys:
        m = re.search(
            rf'^{re.escape(key)}\s*:\s*(.+)$',
            text, re.MULTILINE | re.IGNORECASE
        )
        if m:
            val = m.group(1).strip()
            if val and val not in ("-", "—", "N/A", "PRIVATE"):
                return val
    return None


def _classify_asn_type(org_name: str, org_type: str) -> str:
    """Classifica o tipo do ASN/empresa baseado em org-type WHOIS e keywords."""
    name_lower = (org_name or "").lower()
    ot_lower   = (org_type or "").lower()

    # RIPE org-type explícito
    if "lir" in ot_lower:
        # LIR pode ser ISP ou hosting — refinar com keywords
        if any(k in name_lower for k in _HOSTING_KW):
            return "hosting"
        return "isp"

    # Keywords de hosting têm prioridade (CDN/cloud mascaram como ISP)
    if any(k in name_lower for k in _HOSTING_KW):
        return "hosting"
    if any(k in name_lower for k in _VPN_KW):
        return "vpn"
    if any(k in name_lower for k in _EDU_KW):
        return "edu"
    if any(k in name_lower for k in _GOV_KW):
        return "gov"

    # org-type OTHER sem keywords → business
    if "other" in ot_lower or "assigned" in ot_lower:
        return "business"

    # Fallback: tem "telecom", "tel", "comunicações" → ISP
    if any(k in name_lower for k in {"telecom", "telco", "comunicac", "internet provider"}):
        return "isp"

    return "business"


def _extract_domain_from_text(text: str, email_or_url: Optional[str]) -> Optional[str]:
    """Extrai domínio de um OrgURL ou endereço de email WHOIS."""
    if not email_or_url:
        return None
    # URL: http://www.google.com → google.com
    m = re.search(r'https?://(?:www\.)?([a-z0-9][\w\-\.]+\.[a-z]{2,})', email_or_url, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    # Email: abuse@amazon.com → amazon.com
    m = re.search(r'@([\w\-\.]+\.[a-z]{2,})', email_or_url, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    return None


def run_whois(ip: str) -> dict:
    """
    Executa whois para o IP e extrai todos os campos relevantes.
    Tenta o servidor padrão primeiro; fallback manual para ARIN/RIPE se necessário.
    """
    result: dict = {
        "asn_route":     None,
        "asn_domain":    None,
        "asn_type":      None,
        "company_domain": None,
        "company_type":  None,
        "abuse_email":   None,
        "abuse_network": None,
    }

    raw = _run_whois(ip)
    if not raw or len(raw) < 50:
        return result

    # Alguns servidores redirecionam — seguir se necessário
    refer = re.search(r'^refer:\s*(\S+)', raw, re.MULTILINE | re.IGNORECASE)
    if refer:
        raw2 = _run_whois(ip, refer.group(1))
        if raw2 and len(raw2) > len(raw):
            raw = raw2

    # ── asn_route: prefixo BGP ────────────────────────────────────────────────
    route = _extract_whois_field(raw, "route", "route6", "CIDR", "inet6num", "inetnum")
    if route:
        # Normalizar: "8.8.8.0/24" ou "8.8.8.0 - 8.8.8.255" → CIDR
        m = re.search(r'(\d{1,3}(?:\.\d{1,3}){3}/\d{1,2})', route)
        if m:
            result["asn_route"] = m.group(1)
        else:
            # inetnum range → converter para CIDR aproximado
            m2 = re.search(r'(\d+\.\d+\.\d+\.\d+)\s*-\s*(\d+\.\d+\.\d+\.\d+)', route)
            if m2:
                try:
                    nets = list(ipaddress.summarize_address_range(
                        ipaddress.IPv4Address(m2.group(1)),
                        ipaddress.IPv4Address(m2.group(2))
                    ))
                    result["asn_route"] = str(nets[0]) if nets else None
                except Exception:
                    pass

    # ── org-name e org-type ───────────────────────────────────────────────────
    org_name = _extract_whois_field(raw, "org-name", "OrgName", "descr", "owner")
    org_type = _extract_whois_field(raw, "org-type", "OrgType")

    # ── asn_type e company_type ───────────────────────────────────────────────
    if org_name:
        t = _classify_asn_type(org_name, org_type or "")
        result["asn_type"]     = t
        result["company_type"] = t

    # ── abuse_email ───────────────────────────────────────────────────────────
    abuse_email = _extract_whois_field(raw, "abuse-mailbox", "OrgAbuseEmail",
                                       "e-mail", "abuse", "tech-c")
    # Filtrar handles RIPE (ex: "AB123-RIPE") — queremos apenas emails
    if abuse_email and "@" in abuse_email:
        result["abuse_email"] = abuse_email.lower()
    elif abuse_email and re.match(r'^[A-Z0-9]+-[A-Z]+$', abuse_email.strip()):
        # É um handle RIPE — fazer whois do handle para pegar email real
        hdl_raw = _run_whois(abuse_email.strip())
        hdl_email = _extract_whois_field(hdl_raw, "e-mail", "email")
        if hdl_email and "@" in hdl_email:
            result["abuse_email"] = hdl_email.lower()

    # ── asn_domain e company_domain ───────────────────────────────────────────
    org_url   = _extract_whois_field(raw, "OrgURL", "org-url", "url", "website")
    ref_email = result["abuse_email"] or _extract_whois_field(raw, "tech-c", "e-mail")
    domain    = _extract_domain_from_text(raw, org_url) or _extract_domain_from_text(raw, ref_email)
    result["asn_domain"]     = domain
    result["company_domain"] = domain

    # ── abuse_network ─────────────────────────────────────────────────────────
    inet = _extract_whois_field(raw, "inetnum", "NetRange", "network")
    if inet:
        result["abuse_network"] = inet[:50]

    return result


# ─────────────────────────────────────────────────────────────────────────────
# CAMPOS 10-12 — privacy_vpn, privacy_proxy, privacy_tor: port scan
# Protocolo: TCP connect / UDP datagram em portas de protocolo específico
# ─────────────────────────────────────────────────────────────────────────────

VPN_PORTS_TCP   = [1194, 1723, 8080]
VPN_PORTS_UDP   = [1194, 51820, 500, 4500, 1701]
PROXY_PORTS_TCP = [3128, 1080, 8080, 8888, 3000, 8118]
TOR_PORTS_TCP   = [9001, 9030, 9050, 9150]


def _tcp_open(ip: str, port: int, timeout: float = 1.5) -> bool:
    """Testa se uma porta TCP está aberta."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((ip, port))
        sock.close()
        return result == 0
    except Exception:
        return False


def _udp_responds(ip: str, port: int, payload: bytes, timeout: float = 2.0) -> bool:
    """Envia payload UDP e verifica se recebe qualquer resposta."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.sendto(payload, (ip, port))
        sock.recv(256)
        sock.close()
        return True
    except socket.timeout:
        return False
    except Exception:
        return False


def _wireguard_handshake(ip: str, port: int = 51820, timeout: float = 2.0) -> bool:
    """
    Envia initiation message WireGuard (tipo 1, 148 bytes).
    Se receber resposta de 92 bytes (handshake response) → WireGuard ativo.
    """
    # WireGuard handshake initiation: type=1 + reserved(3) + sender(4) + ephemeral(32)
    # + encrypted_static(48) + encrypted_timestamp(28) + mac1(16) + mac2(16) = 148 bytes
    payload = struct.pack("<I", 1) + b"\x00" * 144
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.sendto(payload, (ip, port))
        data = sock.recv(256)
        sock.close()
        # Response type = 2
        return len(data) >= 4 and struct.unpack("<I", data[:4])[0] == 2
    except Exception:
        return False


def _ike_probe(ip: str, port: int = 500, timeout: float = 2.0) -> bool:
    """
    Envia IKEv2 SA_INIT (RFC 7296).
    IKE responde a qualquer probe válido com INFORMATIONAL ou SA_INIT.
    """
    # IKEv2 SA_INIT: HDR(Initiator SPI + 0 + ExchangeType=34 + Flags=8 + MsgID=0 + Length)
    spi_i  = os.urandom(8)
    spi_r  = b"\x00" * 8
    # Minimal SA payload (just header, no proposals → responder rejects but responds)
    header = spi_i + spi_r + b"\x21\x20\x08\x00\x00\x00\x00\x00\x00\x00\x00\x1c"
    return _udp_responds(ip, port, header, timeout)


def check_privacy_vpn(ip: str) -> bool:
    """
    Detecta VPN por port scan de protocolos VPN conhecidos.
    Usa TCP para OpenVPN/PPTP e UDP para WireGuard/IKE.
    """
    # TCP: OpenVPN (1194), PPTP (1723)
    for port in VPN_PORTS_TCP:
        if _tcp_open(ip, port, timeout=1.5):
            return True

    # UDP WireGuard
    if _wireguard_handshake(ip):
        return True

    # UDP IKE/IPsec
    if _ike_probe(ip):
        return True

    return False


def _http_connect_probe(ip: str, port: int, timeout: float = TIMEOUT) -> bool:
    """
    Testa se a porta aceita HTTP CONNECT (proxy aberto/semi-aberto).
    Proxy aberto retorna "200 Connection established".
    Proxy autenticado retorna "407 Proxy Authentication Required".
    Ambos confirmam que é um proxy.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip, port))
        req = b"CONNECT probe.rmifo.net:443 HTTP/1.1\r\nHost: probe.rmifo.net\r\n\r\n"
        sock.sendall(req)
        resp = sock.recv(256).decode("utf-8", errors="replace")
        sock.close()
        return "200" in resp or "407" in resp or "CONNECT" in resp.upper()
    except Exception:
        return False


def check_privacy_proxy(ip: str) -> bool:
    """
    Detecta proxy por port scan + HTTP CONNECT probe.
    Porta aberta + resposta a CONNECT → proxy confirmado.
    """
    for port in PROXY_PORTS_TCP:
        if _tcp_open(ip, port, timeout=1.5):
            if _http_connect_probe(ip, port):
                return True
    return False


def _tor_dirport_check(ip: str, port: int = 9030, timeout: float = TIMEOUT) -> bool:
    """
    Verifica DirPort Tor: GET / retorna documento de consenso com "Tor directory".
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip, port))
        sock.sendall(b"GET /tor/status-vote/current/consensus HTTP/1.0\r\n\r\n")
        data = sock.recv(512).decode("utf-8", errors="replace")
        sock.close()
        return "network-status" in data.lower() or "tor directory" in data.lower()
    except Exception:
        return False


def check_privacy_tor(ip: str) -> bool:
    """
    Detecta relay Tor: porta 9001 (ORPort) aberta OU DirPort 9030 com consenso.
    """
    if _tcp_open(ip, 9001, timeout=2.0):
        return True
    if _tcp_open(ip, 9030, timeout=1.5) and _tor_dirport_check(ip, 9030):
        return True
    if _tcp_open(ip, 9050, timeout=1.5):
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# CAMPO 13 — privacy_relay: Apple iCloud Private Relay / Cloudflare WARP
# Detectado por padrão no PTR hostname (sem port scan necessário)
# ─────────────────────────────────────────────────────────────────────────────

_RELAY_PTR_PATTERNS = [
    re.compile(r'apple\.com', re.IGNORECASE),
    re.compile(r'privaterelay', re.IGNORECASE),
    re.compile(r'icloud.*relay', re.IGNORECASE),
    re.compile(r'warp\.cloudflare\.com', re.IGNORECASE),
    re.compile(r'relay\d+\.\w+', re.IGNORECASE),
]

def check_privacy_relay(hostname: Optional[str], asn_name: Optional[str] = None) -> bool:
    """Detecta relay por padrão PTR ou nome do ASN."""
    for src in [hostname, asn_name]:
        if not src:
            continue
        for pat in _RELAY_PTR_PATTERNS:
            if pat.search(src):
                return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# CAMPO 14 — privacy_hosting: PTR pattern + ASN keywords + /24 sweep
# ─────────────────────────────────────────────────────────────────────────────

_HOSTING_PTR_RE = re.compile(
    r'ec2-|\.compute\.|\.amazonaws\.com|\.googleusercontent\.com'
    r'|\.cloudflare\.com|\.fastly\.net|\.akamaiedge\.net|\.akadns\.net'
    r'|\.msedge\.net|\.azurewebsites\.net|\.digitalocean\.com'
    r'|\.linode\.com|\.vultr\.com|\.hetzner\.cloud|\.ovh\.net'
    r'|static\.\d+\.\d+\.\d+\.\d+|server\d+\.|vps\d+\.',
    re.IGNORECASE
)

def _ptr_sweep_24(ip: str, sample: int = 20) -> int:
    """
    Faz PTR reverso em `sample` IPs aleatórios do mesmo /24.
    Retorna quantos têm hostnames distintos (alta diversidade = hosting).
    """
    try:
        base = ".".join(ip.split(".")[:3])
        import random
        offsets = random.sample(range(2, 254), min(sample, 50))
        hostnames: set[str] = set()
        with ThreadPoolExecutor(max_workers=10) as ex:
            futs = {ex.submit(extract_hostname, f"{base}.{o}"): o for o in offsets}
            for fut in as_completed(futs):
                h = fut.result()
                if h:
                    # Remove o sufixo numérico para comparar padrão base
                    base_h = re.sub(r'\d+', '', h)
                    hostnames.add(base_h)
        return len(hostnames)
    except Exception:
        return 0


def check_privacy_hosting(ip: str, hostname: Optional[str],
                           asn_name: Optional[str], asn_type: Optional[str]) -> bool:
    """
    Detecta data center / CDN / hosting por 3 sinais:
      a) Padrão PTR (amazonaws, cloudflare, etc.)
      b) ASN já classificado como hosting
      c) /24 com alta diversidade de PTRs (> 15 hostnames distintos = shared)
    """
    # a) PTR pattern
    if hostname and _HOSTING_PTR_RE.search(hostname):
        return True

    # b) ASN type
    if asn_type == "hosting":
        return True

    # c) /24 sweep — rápido (só 20 amostras)
    diversity = _ptr_sweep_24(ip)
    if diversity >= 15:
        return True

    return False


# ─────────────────────────────────────────────────────────────────────────────
# CAMPOS 15-16 — tls_san_count, tls_san_sample: SANs do certificado TLS
# Protocolo: TLS handshake (TCP 443) via ssl.SSLContext + getpeercert()
# ─────────────────────────────────────────────────────────────────────────────

def extract_tls_sans(ip: str, port: int = 443,
                     timeout: float = TIMEOUT) -> tuple[int, list[str]]:
    """
    Conecta via TLS e extrai SANs (Subject Alternative Names) do certificado.
    Retorna (total_count, primeiros_20_dominios).

    Certificado com > 50 SANs → CDN / shared hosting.
    Certificado com 1-3 SANs → servidor dedicado.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE

    try:
        raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw.settimeout(timeout)
        raw.connect((ip, port))
        tls = ctx.wrap_socket(raw, server_hostname=ip)
        cert = tls.getpeercert()
        tls.close()
        raw.close()

        sans = [v for t, v in cert.get("subjectAltName", []) if t == "DNS"]
        return len(sans), sans[:20]
    except Exception:
        return 0, []


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline completo por IP
# ─────────────────────────────────────────────────────────────────────────────

def enrich_ip(ip: str, existing: Optional[dict] = None,
              fields: Optional[set] = None) -> dict:
    """
    Executa todos os extratores para um IP e retorna dict com campos enriquecidos.
    `existing` — dados já no banco (para evitar re-processar campos já preenchidos)
    `fields`   — set de grupos a processar: {"hostname","whois","privacy","tls"}
    """
    fields  = fields or {"hostname", "whois", "privacy", "tls"}
    ex      = existing or {}
    result: dict = {"hop_ip": ip}

    # ── 1. hostname + timezone ────────────────────────────────────────────────
    if "hostname" in fields:
        hostname = ex.get("hostname") or extract_hostname(ip)
        result["hostname"] = hostname
        result["timezone"] = ex.get("timezone") or extract_timezone(
            hostname, ex.get("country_code")
        )
    else:
        hostname = ex.get("hostname")

    # ── 2. WHOIS (route, domain, type, abuse) ────────────────────────────────
    if "whois" in fields:
        w = run_whois(ip)
        result.update(w)
        # Se não classificou ainda, tentar pelo as_name existente
        if not result.get("asn_type") and ex.get("as_name"):
            t = _classify_asn_type(ex["as_name"], "")
            result["asn_type"]     = t
            result["company_type"] = t
    else:
        result["asn_type"] = ex.get("asn_type")

    asn_type = result.get("asn_type") or ex.get("asn_type")
    asn_name = result.get("asn_domain") or ex.get("as_name") or ""

    # ── 3. Privacy flags ──────────────────────────────────────────────────────
    if "privacy" in fields:
        result["privacy_relay"]   = check_privacy_relay(hostname, asn_name)
        result["privacy_vpn"]     = check_privacy_vpn(ip)
        result["privacy_proxy"]   = check_privacy_proxy(ip)
        result["privacy_tor"]     = check_privacy_tor(ip)
        result["privacy_hosting"] = check_privacy_hosting(ip, hostname, asn_name, asn_type)

    # ── 4. TLS SANs ───────────────────────────────────────────────────────────
    if "tls" in fields:
        san_count, san_sample = extract_tls_sans(ip)
        result["tls_san_count"]  = san_count if san_count > 0 else None
        result["tls_san_sample"] = san_sample if san_sample else None

        # TLS com muitos SANs reforça classificação hosting
        if san_count > 50 and "privacy" in fields:
            result["privacy_hosting"] = True

    result["enriched_at"] = datetime.now(timezone.utc).isoformat()
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Persistência no banco
# ─────────────────────────────────────────────────────────────────────────────

def save_enrichment(conn, data: dict) -> bool:
    """Atualiza hop_switch_geolocations com os campos enriquecidos."""
    cols   = [k for k in data if k != "hop_ip" and data[k] is not None]
    if not cols:
        return False
    sets   = ", ".join(f"{c} = %({c})s" for c in cols)
    sql    = f"UPDATE hop_switch_geolocations SET {sets} WHERE hop_ip = %(hop_ip)s"
    try:
        with conn.cursor() as cur:
            cur.execute(sql, data)
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        print(f"  [DB ERR] {data['hop_ip']}: {e}", file=sys.stderr)
        return False


def load_pending_ips(conn, stale_days: Optional[int], limit: int) -> list[dict]:
    """Carrega IPs do banco que precisam de enriquecimento."""
    if stale_days is not None:
        where = f"enriched_at IS NULL OR enriched_at < NOW() - INTERVAL '{stale_days} days'"
    else:
        where = "enriched_at IS NULL"

    sql = f"""
        SELECT hop_ip::text, city, region, country, country_code,
               as_number, as_name, hostname, timezone, asn_type
        FROM hop_switch_geolocations
        WHERE {where}
        ORDER BY queried_at DESC
        LIMIT %s
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, (limit,))
        return [dict(r) for r in cur.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _print_result(r: dict):
    """Imprime resultado formatado no terminal."""
    ip = r.get("hop_ip", "?")
    flags = []
    if r.get("privacy_vpn"):     flags.append("VPN")
    if r.get("privacy_proxy"):   flags.append("PROXY")
    if r.get("privacy_tor"):     flags.append("TOR")
    if r.get("privacy_relay"):   flags.append("RELAY")
    if r.get("privacy_hosting"): flags.append("HOSTING")

    print(f"\n{'─'*60}")
    print(f"  IP       : {ip}")
    print(f"  hostname : {r.get('hostname') or '—'}")
    print(f"  timezone : {r.get('timezone') or '—'}")
    print(f"  asn_route: {r.get('asn_route') or '—'}")
    print(f"  asn_type : {r.get('asn_type') or '—'}")
    print(f"  asn_dom  : {r.get('asn_domain') or '—'}")
    print(f"  abuse_em : {r.get('abuse_email') or '—'}")
    print(f"  abuse_net: {r.get('abuse_network') or '—'}")
    print(f"  tls_sans : {r.get('tls_san_count') or 0} domínios")
    if r.get("tls_san_sample"):
        print(f"  san[0:3] : {r['tls_san_sample'][:3]}")
    print(f"  privacy  : {', '.join(flags) if flags else 'none'}")


def main():
    ap = argparse.ArgumentParser(description="RMIFO — Enriquecimento nativo de IPs")
    ap.add_argument("--ip",      help="IP avulso a enriquecer")
    ap.add_argument("--stale",   type=int, metavar="DAYS",
                    help="Re-enriquece IPs com enriched_at mais antigo que DAYS dias")
    ap.add_argument("--limit",   type=int, default=500,
                    help="Máximo de IPs a processar (padrão: 500)")
    ap.add_argument("--field",   default="hostname,whois,privacy,tls",
                    help="Grupos de campos: hostname,whois,privacy,tls")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--dry-run", action="store_true",
                    help="Imprime resultado sem gravar no banco")
    ap.add_argument("--json",    action="store_true", help="Saída JSON")
    args = ap.parse_args()

    fields  = set(f.strip() for f in args.field.split(","))
    dry_run = args.dry_run
    results = []

    # ── IP avulso ─────────────────────────────────────────────────────────────
    if args.ip:
        r = enrich_ip(args.ip, fields=fields)
        results.append(r)
        if args.json:
            print(json.dumps(r, indent=2, default=str))
        else:
            _print_result(r)
        if not dry_run and HAS_PG and DATABASE_URL:
            conn = psycopg2.connect(DATABASE_URL)
            # Inserir linha se não existir
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO hop_switch_geolocations (hop_ip) VALUES (%s) ON CONFLICT DO NOTHING",
                    (args.ip,)
                )
            conn.commit()
            save_enrichment(conn, r)
            conn.close()
            print("\n[✓] Salvo no banco.")
        return

    # ── Batch do banco ────────────────────────────────────────────────────────
    if not HAS_PG or not DATABASE_URL:
        print("[!] psycopg2 / DATABASE_URL não disponíveis. Use --ip para modo avulso.")
        return

    conn = psycopg2.connect(DATABASE_URL)
    ips  = load_pending_ips(conn, args.stale, args.limit)

    if not ips:
        print("[✓] Nenhum IP pendente de enriquecimento.")
        conn.close()
        return

    print(f"[→] Enriquecendo {len(ips)} IPs | campos: {fields} | workers: {args.workers}")
    ok = err = 0

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {
            ex.submit(enrich_ip, row["hop_ip"], row, fields): row["hop_ip"]
            for row in ips
        }
        for fut in as_completed(futs):
            ip = futs[fut]
            try:
                r = fut.result()
                results.append(r)
                if dry_run:
                    _print_result(r)
                else:
                    if save_enrichment(conn, r):
                        ok += 1
                    else:
                        err += 1
                    # Progresso a cada 10
                    done = ok + err
                    if done % 10 == 0:
                        flags = []
                        if r.get("privacy_hosting"): flags.append("HOSTING")
                        if r.get("privacy_vpn"):     flags.append("VPN")
                        if r.get("privacy_tor"):     flags.append("TOR")
                        tag = f"[{','.join(flags)}]" if flags else ""
                        print(f"  [{done}/{len(ips)}] {ip} → {r.get('asn_type','?')} {tag}")
            except Exception as e:
                err += 1
                print(f"  [ERR] {ip}: {e}", file=sys.stderr)

    conn.close()

    if not dry_run:
        print(f"\n[✓] Concluído: {ok} atualizados | {err} erros")

    if args.json:
        print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    import os
    main()
