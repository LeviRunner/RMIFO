#!/usr/bin/env python3
"""
enrich_ip_data.py
-----------------
Busca informações extras sobre cada IP em endpoint_ip_samples via API
gratuita do ipinfo.io e salva em:
  data/external/dados_extra_sobre_ip.json
  data/external/dados_extra_sobre_ip.csv

Limite gratuito ipinfo.io: 50 000 req/mês.
O script deduplica por rede (/network) antes de consultar a API.
"""
from __future__ import annotations

import csv
import ipaddress
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import psycopg2

# ── configuração do banco ──────────────────────────────────────────────────
_SCRIPT_PARENT = Path(__file__).parent.parent   # global-network-resilience/
DB_HOST   = str(_SCRIPT_PARENT / ".local" / "run")
DB_PORT   = 55432
DB_NAME   = "global_network_resilience"
DB_USER   = "postgres"

# ── saída ──────────────────────────────────────────────────────────────────
SCRIPT_DIR  = Path(__file__).parent
DATA_DIR    = SCRIPT_DIR.parent / "data" / "external"
OUT_JSON    = DATA_DIR / "dados_extra_sobre_ip.json"
OUT_CSV     = DATA_DIR / "dados_extra_sobre_ip.csv"

# ── ipinfo.io ──────────────────────────────────────────────────────────────
IPINFO_BASE = "https://ipinfo.io/{ip}/json"
REQUEST_DELAY = 0.25          # segundos entre requisições (4 req/s)
TIMEOUT       = 10            # segundos por requisição


def int_to_ip(value: str) -> str:
    """Converte inteiro (string) para notação pontilhada."""
    return str(ipaddress.IPv4Address(int(value)))


def first_ip_of_network(network_str: str | None, ip_start: str | None) -> str | None:
    """Retorna o primeiro IP utilizável da rede (ou ip_start como fallback)."""
    if network_str:
        try:
            net = ipaddress.ip_network(network_str, strict=False)
            # Usa o network address (host da rede) como alvo de consulta
            return str(net.network_address)
        except ValueError:
            pass
    return ip_start


def query_ipinfo(ip: str) -> dict:
    url = IPINFO_BASE.format(ip=ip)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}", "queried_ip": ip}
    except Exception as exc:
        return {"error": str(exc), "queried_ip": ip}


def load_existing_results(path: Path) -> dict[str, dict]:
    """Carrega resultados já salvos para continuar de onde parou."""
    if not path.exists():
        return {}
    with path.open() as f:
        data = json.load(f)
    return {entry["queried_ip"]: entry for entry in data if "queried_ip" in entry}


def main() -> None:
    print("Conectando ao banco de dados...")
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, options="-c client_encoding=UTF8"
    )

    with conn.cursor() as cur:
        # Coleta todas as amostras de IP com info do endpoint
        cur.execute("""
            SELECT
                s.endpoint_id,
                s.sample_order,
                s.network::text,
                s.ip_start::text,
                s.ip_end::text,
                s.city        AS sample_city,
                s.region      AS sample_region,
                s.country_name AS sample_country,
                s.country_code,
                s.latitude    AS sample_lat,
                s.longitude   AS sample_lon,
                lp.endpoint_name AS lp_name,
                lp.city_name  AS lp_city,
                lp.country_name AS lp_country
            FROM endpoint_ip_samples s
            JOIN landing_points lp ON lp.endpoint_id = s.endpoint_id
            ORDER BY s.endpoint_id, s.sample_order
        """)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]

    conn.close()
    print(f"Amostras encontradas no banco: {len(rows)}")

    # Monta lista de registros, deduplicando por rede
    records: list[dict] = []
    seen_networks: set[str] = set()
    seen_ips: set[str] = set()

    for row in rows:
        rec = dict(zip(cols, row))
        ip = first_ip_of_network(rec.get("network"), rec.get("ip_start"))
        if not ip:
            continue
        key = rec.get("network") or ip
        if key in seen_networks:
            continue
        seen_networks.add(key)
        if ip in seen_ips:
            # rede diferente mas mesmo IP representativo – pula para não duplicar
            continue
        seen_ips.add(ip)
        rec["queried_ip"] = ip
        records.append(rec)

    print(f"IPs únicos a consultar: {len(records)}")

    # Carrega progresso anterior
    existing = load_existing_results(OUT_JSON)
    print(f"Já consultados anteriormente: {len(existing)}")

    results: list[dict] = list(existing.values())
    consulted = set(existing.keys())

    pending = [r for r in records if r["queried_ip"] not in consulted]
    print(f"Pendentes nesta execução: {len(pending)}")

    if not pending:
        print("Nada a fazer – todos os IPs já foram consultados.")
    else:
        for i, rec in enumerate(pending, 1):
            ip = rec["queried_ip"]
            ipinfo = query_ipinfo(ip)

            # Combina dados locais (do banco) com dados do ipinfo.io
            entry = {
                # identificação local
                "queried_ip":       ip,
                "network":          rec.get("network"),
                "ip_start":         rec.get("ip_start"),
                "ip_end":           rec.get("ip_end"),
                "lp_id":            rec.get("endpoint_id"),
                "lp_name":          rec.get("lp_name"),
                "lp_city":          rec.get("lp_city"),
                "lp_country":       rec.get("lp_country"),
                # dados locais de geolocalização (geoip_database.csv)
                "local_city":       rec.get("sample_city"),
                "local_region":     rec.get("sample_region"),
                "local_country":    rec.get("sample_country"),
                "local_country_code": rec.get("country_code"),
                "local_lat":        str(rec.get("sample_lat") or ""),
                "local_lon":        str(rec.get("sample_lon") or ""),
                # dados do ipinfo.io
                "ipinfo_ip":        ipinfo.get("ip"),
                "ipinfo_hostname":  ipinfo.get("hostname"),
                "ipinfo_org":       ipinfo.get("org"),        # "AS12345 NomeDaOrg"
                "ipinfo_asn":       ipinfo.get("org", "").split()[0] if ipinfo.get("org") else None,
                "ipinfo_org_name":  " ".join(ipinfo.get("org", "").split()[1:]) if ipinfo.get("org") else None,
                "ipinfo_city":      ipinfo.get("city"),
                "ipinfo_region":    ipinfo.get("region"),
                "ipinfo_country":   ipinfo.get("country"),
                "ipinfo_loc":       ipinfo.get("loc"),        # "lat,lon"
                "ipinfo_postal":    ipinfo.get("postal"),
                "ipinfo_timezone":  ipinfo.get("timezone"),
                "ipinfo_error":     ipinfo.get("error"),
            }

            results.append(entry)
            consulted.add(ip)

            if i % 50 == 0 or i == len(pending):
                # Salva checkpoint a cada 50 consultas
                _save_results(results, OUT_JSON, OUT_CSV)
                pct = i / len(pending) * 100
                print(f"  [{i}/{len(pending)}] {pct:.1f}% — último: {ip} | org: {entry.get('ipinfo_org')}")

            time.sleep(REQUEST_DELAY)

    # Salva resultado final
    _save_results(results, OUT_JSON, OUT_CSV)
    print(f"\nConcluído! {len(results)} registros salvos em:")
    print(f"  JSON → {OUT_JSON}")
    print(f"  CSV  → {OUT_CSV}")


def _save_results(results: list[dict], json_path: Path, csv_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)

    if not results:
        return

    fieldnames = list(results[0].keys())
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)


if __name__ == "__main__":
    main()
