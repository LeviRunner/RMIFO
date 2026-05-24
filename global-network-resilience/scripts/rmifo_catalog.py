#!/usr/bin/env python3
"""
RMIFO — Gerenciamento do Catálogo de Cabos

Subcomandos:
  seed     Gera db/seed.sql a partir dos arquivos externos (data/external/)
  import   Importa diretamente o catálogo no PostgreSQL via geoip + JSON
  history  Importa arquivos JSON de histórico de sondas → cable_telemetry_samples

Uso:
  python rmifo_catalog.py seed
  python rmifo_catalog.py import
  python rmifo_catalog.py history [--hours 48] [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import ipaddress
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("[rmifo_catalog] psycopg2 não instalado — execute: pip install psycopg2-binary")
    sys.exit(1)

BASE_DIR  = Path(__file__).resolve().parent.parent
DATA_DIR  = BASE_DIR / "data" / "external"
HIST_DIR  = BASE_DIR / "data" / "local" / "historico"


# ─────────────────────────────────────────────────────────────────────────────
# Conexão
# ─────────────────────────────────────────────────────────────────────────────

def db_connect(base_dir: Path | None = None) -> psycopg2.extensions.connection:
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        return psycopg2.connect(db_url)
    bd = base_dir or BASE_DIR
    ws = os.getenv("WORKSPACE_ROOT", str(bd))
    return psycopg2.connect(
        host=os.getenv("PG_SOCKET", os.getenv("PGHOST", f"{ws}/.local/run")),
        port=int(os.getenv("PG_PORT", os.getenv("PGPORT", "55432"))),
        dbname=os.getenv("DB_NAME", os.getenv("PGDATABASE", "global_network_resilience")),
        user=os.getenv("PGUSER", "postgres"),
        connect_timeout=10,
    )


# ─────────────────────────────────────────────────────────────────────────────
# ── Utilitários compartilhados ────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

COUNTRY_ALIASES = {
    "congo dem rep":                    "democratic republic of the congo",
    "congo rep":                        "republic of the congo",
    "cote divoire":                     "cote d'ivoire",
    "cote d ivoire":                    "cote d'ivoire",
    "korea rep":                        "south korea",
    "korea dem peoples rep":            "north korea",
    "lao peoples democratic republic":  "laos",
    "myanmar burma":                    "myanmar",
    "viet nam":                         "vietnam",
    "st kitts and nevis":               "saint kitts and nevis",
    "st vincent and the grenadines":    "saint vincent and the grenadines",
}

COUNTRY_CODE_MAP = {
    "united states": "US", "usa": "US", "united kingdom": "GB", "uk": "GB",
    "france": "FR", "germany": "DE", "spain": "ES", "italy": "IT",
    "portugal": "PT", "netherlands": "NL", "belgium": "BE", "denmark": "DK",
    "norway": "NO", "sweden": "SE", "finland": "FI", "ireland": "IE",
    "greece": "GR", "turkey": "TR", "egypt": "EG", "south africa": "ZA",
    "nigeria": "NG", "kenya": "KE", "ghana": "GH", "angola": "AO",
    "mozambique": "MZ", "djibouti": "DJ", "somalia": "SO", "ethiopia": "ET",
    "tanzania": "TZ", "senegal": "SN", "cameroon": "CM", "gabon": "GA",
    "congo": "CG", "morocco": "MA", "algeria": "DZ", "tunisia": "TN",
    "libya": "LY", "brazil": "BR", "argentina": "AR", "chile": "CL",
    "colombia": "CO", "peru": "PE", "venezuela": "VE", "uruguay": "UY",
    "ecuador": "EC", "mexico": "MX", "canada": "CA", "bahamas": "BS",
    "cuba": "CU", "jamaica": "JM", "trinidad": "TT", "india": "IN",
    "pakistan": "PK", "bangladesh": "BD", "sri lanka": "LK", "myanmar": "MM",
    "thailand": "TH", "malaysia": "MY", "singapore": "SG", "indonesia": "ID",
    "philippines": "PH", "vietnam": "VN", "cambodia": "KH", "china": "CN",
    "taiwan": "TW", "hong kong": "HK", "japan": "JP", "south korea": "KR",
    "australia": "AU", "new zealand": "NZ", "fiji": "FJ", "guam": "GU",
    "palau": "PW", "micronesia": "FM", "papua new guinea": "PG",
    "solomon islands": "SB", "russia": "RU", "ukraine": "UA", "poland": "PL",
    "czech republic": "CZ", "austria": "AT", "switzerland": "CH",
    "israel": "IL", "saudi arabia": "SA", "uae": "AE",
    "united arab emirates": "AE", "qatar": "QA", "kuwait": "KW",
    "bahrain": "BH", "oman": "OM", "iran": "IR", "iraq": "IQ",
    "jordan": "JO", "lebanon": "LB", "cyprus": "CY", "malta": "MT",
    "iceland": "IS", "greenland": "GL",
}


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def normalize_country(value: str | None) -> str:
    return COUNTRY_ALIASES.get(normalize_text(value), normalize_text(value))


def country_code_from_name(name: str) -> str:
    low = name.strip().lower()
    for k, v in COUNTRY_CODE_MAP.items():
        if k in low:
            return v
    return ""


def clean_int(value: str | None) -> int | None:
    if not value:
        return None
    digits = re.sub(r"[^0-9]", "", value)
    return int(digits) if digits else None


def clean_bool(value: str | None) -> bool | None:
    if value is None or value == "":
        return None
    low = value.strip().lower()
    if low in {"true", "1", "yes"}:
        return True
    if low in {"false", "0", "no"}:
        return False
    return None


def int_to_ip(value: str | None) -> str | None:
    if value in (None, "", "-"):
        return None
    return str(ipaddress.IPv4Address(int(value)))


def summarize_network(start_ip: str | None, end_ip: str | None) -> str | None:
    if not start_ip or not end_ip:
        return None
    nets = list(ipaddress.summarize_address_range(
        ipaddress.ip_address(start_ip), ipaddress.ip_address(end_ip)))
    return str(nets[0]) if len(nets) == 1 else None


def parse_length(raw: str) -> str:
    digits = re.sub(r"[^\d]", "", raw) if raw else ""
    return digits if digits else "NULL"


def parse_year(raw: str) -> str:
    if not raw:
        return "NULL"
    m = re.search(r"\d{4}", str(raw))
    return m.group(0) if m else "NULL"


def parse_loc(loc_str: str):
    if not loc_str:
        return None, None
    parts = loc_str.split(",")
    if len(parts) != 2:
        return None, None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None, None


def sql_esc(s) -> str:
    if s is None:
        return "NULL"
    return "'" + str(s).replace("'", "''") + "'"


def sql_bool(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    s = str(v).strip().lower()
    if s in ("true", "1", "yes", "t"):
        return "TRUE"
    if s in ("false", "0", "no", "f"):
        return "FALSE"
    return "NULL"


# ─────────────────────────────────────────────────────────────────────────────
# ── SUBCOMANDO: seed ──────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

def cmd_seed(_args: argparse.Namespace) -> None:
    out_file = BASE_DIR / "db" / "seed.sql"

    print("Carregando CSV…", file=sys.stderr)
    with open(DATA_DIR / "submarine_cables.csv", encoding="utf-8") as f:
        csv_rows = {r["cable_id"]: r for r in csv.DictReader(f)}

    print("Carregando JSON completo…", file=sys.stderr)
    with open(DATA_DIR / "submarine_cables_full.json", encoding="utf-8") as f:
        full_data = json.load(f)["cables"]
    full_by_id = {c["id"]: c for c in full_data}

    print("Carregando dados de IP…", file=sys.stderr)
    with open(DATA_DIR / "dados_extra_sobre_ip.json", encoding="utf-8") as f:
        ip_data = json.load(f)

    ip_by_lp: dict[str, list] = {}
    for rec in ip_data:
        lid = rec.get("lp_id", "").strip()
        if not lid:
            continue
        ip_by_lp.setdefault(lid, [])
        if len(ip_by_lp[lid]) < 5:
            ip_by_lp[lid].append(rec)

    all_lps: dict[str, dict] = {}
    cable_lp_order: dict[str, list] = {}

    for cable in full_data:
        cid = cable["id"]
        order_list = []
        for lp in (cable.get("landing_points") or []):
            lid = lp.get("id", "").strip()
            if not lid:
                continue
            order_list.append((lid, bool(lp.get("is_tbd"))))
            if lid not in all_lps:
                raw_name = lp.get("name", "") or ""
                parts    = [p.strip() for p in raw_name.split(",", 1)]
                city     = parts[0] if parts else ""
                country  = parts[1] if len(parts) > 1 else lp.get("country", "")
                lat = lng = None
                cc = ""
                if lid in ip_by_lp:
                    for ip_rec in ip_by_lp[lid]:
                        loc = ip_rec.get("ipinfo_loc", "")
                        if loc:
                            lat, lng = parse_loc(loc)
                            cc = ip_rec.get("ipinfo_country", "")
                            break
                if not cc:
                    cc = country_code_from_name(country)
                all_lps[lid] = {
                    "id": lid, "name": raw_name or lid, "city": city,
                    "country": country or lp.get("country", ""),
                    "cc": cc[:2] if cc else "",
                    "lat": lat, "lng": lng,
                    "geo_source": "ipinfo" if lat is not None else "unmatched",
                }
        cable_lp_order[cid] = order_list

    print(f"  Cabos: {len(full_by_id)}  LPs: {len(all_lps)}  "
          f"com coords: {sum(1 for lp in all_lps.values() if lp['lat'] is not None)}",
          file=sys.stderr)

    lines: list[str] = []
    W = lines.append

    W("-- RMIFO seed gerado por rmifo_catalog.py")
    W(f"-- Cabos: {len(full_by_id)}  |  Landing points: {len(all_lps)}")
    W("")
    W("CREATE OR REPLACE VIEW endpoints AS")
    W("SELECT clp.cable_id, clp.endpoint_id, lp.endpoint_name AS name,")
    W("       lp.country_name AS country, lp.city_name AS city,")
    W("       clp.endpoint_order, eis.ip_start::text AS ip_sample")
    W("FROM cable_landing_points clp")
    W("JOIN  landing_points lp ON lp.endpoint_id = clp.endpoint_id")
    W("LEFT JOIN endpoint_ip_samples eis ON eis.endpoint_id = clp.endpoint_id AND eis.sample_order = 0;")
    W("")

    W("INSERT INTO cables (cable_id, cable_name, owners, capacity_gbps, length_km,")
    W("                    rfs_year, is_operational, landing_points_count, suppliers)")
    W("VALUES")
    cable_rows_sql = []
    for cable in full_data:
        cid  = cable["id"]
        crow = csv_rows.get(cid, {})
        name = cable.get("name") or crow.get("cable_name") or cid
        cable_rows_sql.append(
            f"  ({sql_esc(cid)},{sql_esc(name)},{sql_esc(cable.get('owners') or crow.get('owners') or '')},"
            f"NULL,{parse_length(cable.get('length') or crow.get('length_km', ''))},"
            f"{parse_year(cable.get('rfs_year') or cable.get('rfs') or crow.get('rfs_year', ''))},"
            f"{sql_bool(False if cable.get('is_planned') else crow.get('is_operational', 'True'))},"
            f"{len(cable.get('landing_points') or [])},"
            f"{sql_esc(cable.get('suppliers') or crow.get('suppliers') or '')})"
        )
    W(",\n".join(cable_rows_sql))
    W("ON CONFLICT (cable_id) DO UPDATE SET")
    W("  cable_name=EXCLUDED.cable_name, owners=EXCLUDED.owners,")
    W("  length_km=EXCLUDED.length_km, rfs_year=EXCLUDED.rfs_year,")
    W("  is_operational=EXCLUDED.is_operational,")
    W("  landing_points_count=EXCLUDED.landing_points_count, suppliers=EXCLUDED.suppliers;")
    W("")

    W("INSERT INTO cable_metadata (cable_id, public_url, source_url, notes, is_planned, raw_length, raw_rfs)")
    W("VALUES")
    W(",\n".join(
        f"  ({sql_esc(c['id'])},{sql_esc(c.get('url'))},{sql_esc(c.get('source_url'))},"
        f"{sql_esc(c.get('notes'))},{sql_bool(c.get('is_planned'))},"
        f"{sql_esc(c.get('length'))},{sql_esc(str(c.get('rfs') or c.get('rfs_year') or ''))})"
        for c in full_data
    ))
    W("ON CONFLICT (cable_id) DO UPDATE SET")
    W("  public_url=EXCLUDED.public_url, source_url=EXCLUDED.source_url,")
    W("  notes=EXCLUDED.notes, is_planned=EXCLUDED.is_planned,")
    W("  raw_length=EXCLUDED.raw_length, raw_rfs=EXCLUDED.raw_rfs;")
    W("")

    W(f"INSERT INTO landing_points (endpoint_id, endpoint_name, city_name, country_name,")
    W("                             country_code, latitude, longitude, geo_source)")
    W("VALUES")
    W(",\n".join(
        f"  ({sql_esc(lp['id'])},{sql_esc(lp['name'])},{sql_esc(lp['city'])},{sql_esc(lp['country'])},"
        f"{sql_esc(lp['cc'] or None)},"
        f"{lp['lat'] if lp['lat'] is not None else 'NULL'},"
        f"{lp['lng'] if lp['lng'] is not None else 'NULL'},"
        f"{sql_esc(lp['geo_source'])})"
        for lp in all_lps.values()
    ))
    W("ON CONFLICT (endpoint_id) DO UPDATE SET")
    W("  endpoint_name=EXCLUDED.endpoint_name, city_name=EXCLUDED.city_name,")
    W("  country_name=EXCLUDED.country_name, country_code=EXCLUDED.country_code,")
    W("  latitude=COALESCE(EXCLUDED.latitude, landing_points.latitude),")
    W("  longitude=COALESCE(EXCLUDED.longitude, landing_points.longitude),")
    W("  geo_source=EXCLUDED.geo_source;")
    W("")

    clp_rows = []
    for cid, lp_list in cable_lp_order.items():
        for order, (lid, is_tbd) in enumerate(lp_list):
            if lid in all_lps:
                clp_rows.append(f"  ({sql_esc(cid)},{sql_esc(lid)},{order},{sql_bool(is_tbd)})")
    W(f"INSERT INTO cable_landing_points (cable_id, endpoint_id, endpoint_order, is_tbd)")
    W("VALUES")
    W(",\n".join(clp_rows))
    W("ON CONFLICT (cable_id, endpoint_id) DO UPDATE SET")
    W("  endpoint_order=EXCLUDED.endpoint_order, is_tbd=EXCLUDED.is_tbd;")
    W("")

    ip_rows_sql = []
    for lid, recs in ip_by_lp.items():
        if lid not in all_lps:
            continue
        for i, rec in enumerate(recs):
            lat, lng = parse_loc(rec.get("ipinfo_loc", ""))
            ip_rows_sql.append(
                f"  ({sql_esc(lid)},{i},{sql_esc(rec.get('network'))},{sql_esc(rec.get('ip_start'))},"
                f"{sql_esc(rec.get('ip_end'))},{sql_esc(rec.get('ipinfo_country'))},"
                f"{sql_esc(rec.get('ipinfo_country'))},{sql_esc(rec.get('ipinfo_region'))},"
                f"{sql_esc(rec.get('ipinfo_city'))},"
                f"{lat if lat is not None else 'NULL'},{lng if lng is not None else 'NULL'})"
            )
    W("INSERT INTO endpoint_ip_samples")
    W("    (endpoint_id, sample_order, network, ip_start, ip_end,")
    W("     country_name, country_code, region, city, latitude, longitude)")
    W("VALUES")
    W(",\n".join(ip_rows_sql))
    W("ON CONFLICT (endpoint_id, sample_order) DO NOTHING;")

    sql = "\n".join(lines)
    out_file.write_text(sql, encoding="utf-8")
    print(f"Gerado: {out_file}  ({len(sql):,} bytes)", file=sys.stderr)


# ─────────────────────────────────────────────────────────────────────────────
# ── SUBCOMANDO: import ────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

def enrich_endpoints_from_geoip(endpoints: dict[str, dict], geoip_path: Path,
                                  sample_limit: int = 5) -> None:
    city_country_index: dict[tuple, list] = defaultdict(list)
    country_index:      dict[str, list]   = defaultdict(list)
    country_fallbacks:  dict[str, list]   = defaultdict(list)

    for eid, ep in endpoints.items():
        ck = normalize_text(ep["city_name"])
        co = normalize_country(ep["country_name"])
        if ck and co:
            city_country_index[(ck, co)].append(eid)
        if co:
            country_index[co].append(eid)

    target_countries = set(country_index.keys())

    with geoip_path.open("r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            country_name = row.get("country") or ""
            if country_name in {"", "-"}:
                continue
            co = normalize_country(country_name)
            if co not in target_countries:
                continue
            city_name = row.get("city") or ""
            ck = normalize_text(city_name)
            sample = {
                "network":      summarize_network(int_to_ip(row.get("ip_range_start")), int_to_ip(row.get("ip_range_end"))),
                "ip_start":     int_to_ip(row.get("ip_range_start")),
                "ip_end":       int_to_ip(row.get("ip_range_end")),
                "country_name": country_name,
                "country_code": row.get("country_code") or None,
                "region":       None if row.get("region") in {None, "", "-"} else row.get("region"),
                "city":         None if city_name in {"", "-"} else city_name,
                "latitude":     float(row["latitude"])  if row.get("latitude")  not in {None, "", "-"} else None,
                "longitude":    float(row["longitude"]) if row.get("longitude") not in {None, "", "-"} else None,
            }
            if len(country_fallbacks[co]) < sample_limit * 4:
                country_fallbacks[co].append(sample)
            if not ck:
                continue
            for eid in city_country_index.get((ck, co), []):
                ep = endpoints[eid]
                if ep["country_code"] is None:
                    ep["country_code"] = sample["country_code"]
                if ep["latitude"] is None and sample["latitude"] is not None:
                    ep["latitude"]   = sample["latitude"]
                    ep["longitude"]  = sample["longitude"]
                    ep["geo_source"] = "geoip-city-match"
                if len(ep["samples"]) < sample_limit:
                    ep["samples"].append(sample)

    for ep in endpoints.values():
        co = normalize_country(ep["country_name"])
        fb = country_fallbacks.get(co, [])
        if ep["country_code"] is None and fb:
            ep["country_code"] = fb[0]["country_code"]
        if ep["latitude"] is None and fb:
            ep["latitude"]   = fb[0]["latitude"]
            ep["longitude"]  = fb[0]["longitude"]
            ep["geo_source"] = "geoip-country-fallback"
        if not ep["samples"] and fb:
            ep["samples"] = fb[:sample_limit]
        if ep["geo_source"] == "pending":
            ep["geo_source"] = "unmatched"


def cmd_import(_args: argparse.Namespace) -> None:
    csv_path     = DATA_DIR / "submarine_cables.csv"
    catalog_path = DATA_DIR / "submarine_cables_full.json"
    geoip_path   = DATA_DIR / "geoip_database.csv"

    # Load cables
    cable_rows: list[tuple] = []
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            cable_rows.append((
                row["cable_id"], row["cable_name"],
                row.get("owners") or None, None,
                clean_int(row.get("length_km")), clean_int(row.get("rfs_year")),
                clean_bool(row.get("is_operational")),
                clean_int(row.get("landing_points")),
                row.get("suppliers") or None,
            ))

    # Load catalog
    payload      = json.loads(catalog_path.read_text(encoding="utf-8"))
    metadata_rows: list[tuple] = []
    endpoints:     dict[str, dict] = {}
    links:         list[tuple] = []

    for cable in payload.get("cables", []):
        cid = cable["id"]
        metadata_rows.append((
            cid, cable.get("url"), cable.get("source_url"),
            cable.get("notes"), cable.get("is_planned"),
            cable.get("length"), cable.get("rfs"),
        ))
        for order, lp in enumerate(cable.get("landing_points", []), start=1):
            eid  = lp["id"]
            ename = lp.get("name") or eid
            city  = ename.split(",", 1)[0].strip()
            if eid not in endpoints:
                endpoints[eid] = {
                    "endpoint_id": eid, "endpoint_name": ename, "city_name": city,
                    "country_name": lp.get("country") or None,
                    "country_code": None, "latitude": None, "longitude": None,
                    "geo_source": "pending", "samples": [],
                }
            links.append((cid, eid, order, lp.get("is_tbd")))

    enrich_endpoints_from_geoip(endpoints, geoip_path)

    conn = db_connect()
    conn.autocommit = False

    with conn, conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO cables (cable_id, cable_name, owners, capacity_gbps, length_km,
                                rfs_year, is_operational, landing_points_count, suppliers)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (cable_id) DO UPDATE SET
                cable_name=EXCLUDED.cable_name, owners=EXCLUDED.owners,
                capacity_gbps=EXCLUDED.capacity_gbps, length_km=EXCLUDED.length_km,
                rfs_year=EXCLUDED.rfs_year, is_operational=EXCLUDED.is_operational,
                landing_points_count=EXCLUDED.landing_points_count, suppliers=EXCLUDED.suppliers
        """, cable_rows)

        cur.executemany("""
            INSERT INTO cable_metadata
                (cable_id, public_url, source_url, notes, is_planned, raw_length, raw_rfs)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (cable_id) DO UPDATE SET
                public_url=EXCLUDED.public_url, source_url=EXCLUDED.source_url,
                notes=EXCLUDED.notes, is_planned=EXCLUDED.is_planned,
                raw_length=EXCLUDED.raw_length, raw_rfs=EXCLUDED.raw_rfs
        """, metadata_rows)

        cur.execute("TRUNCATE TABLE endpoint_ip_samples, cable_landing_points, landing_points RESTART IDENTITY CASCADE")

        cur.executemany("""
            INSERT INTO landing_points
                (endpoint_id, endpoint_name, city_name, country_name,
                 country_code, latitude, longitude, geo_source)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """, [(ep["endpoint_id"], ep["endpoint_name"], ep["city_name"], ep["country_name"],
               ep["country_code"], ep["latitude"], ep["longitude"], ep["geo_source"])
              for ep in endpoints.values()])

        cur.executemany("""
            INSERT INTO cable_landing_points (cable_id, endpoint_id, endpoint_order, is_tbd)
            VALUES (%s,%s,%s,%s)
        """, links)

        ep_samples = [
            (ep["endpoint_id"], i + 1, s["network"], s["ip_start"], s["ip_end"],
             s["country_name"], s["country_code"], s["region"], s["city"],
             s["latitude"], s["longitude"])
            for ep in endpoints.values()
            for i, s in enumerate(ep["samples"])
        ]
        if ep_samples:
            cur.executemany("""
                INSERT INTO endpoint_ip_samples
                    (endpoint_id, sample_order, network, ip_start, ip_end,
                     country_name, country_code, region, city, latitude, longitude)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, ep_samples)

    print(f"Cabos importados/atualizados : {len(cable_rows)}")
    print(f"Metadados importados         : {len(metadata_rows)}")
    print(f"Endpoints importados         : {len(endpoints)}")
    print(f"Amostras de IP importadas    : {len(ep_samples)}")


# ─────────────────────────────────────────────────────────────────────────────
# ── SUBCOMANDO: history ───────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

HISTORY_UPSERT_SQL = """
INSERT INTO cable_telemetry_samples (
    cable_id, measured_at, latency_ms, jitter_ms, packet_loss_pct, throughput_mbps,
    packets_per_second, availability_pct, route_error_count,
    source, method, confidence_score, estimated, telemetry_model_version, metadata
) VALUES (
    %(cable_id)s, %(measured_at)s,
    %(latency_ms)s, %(jitter_ms)s, %(packet_loss_pct)s, %(throughput_mbps)s,
    %(packets_per_second)s, %(availability_pct)s, %(route_error_count)s,
    %(source)s, %(method)s, %(confidence_score)s, %(estimated)s,
    %(telemetry_model_version)s, %(metadata)s
)
ON CONFLICT (cable_id, measured_at) DO NOTHING
"""


def _parse_ts(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _record_to_row(reg: dict) -> dict | None:
    ts = _parse_ts(reg.get("measured_at", ""))
    if ts is None or not reg.get("cable_id"):
        return None
    meta = reg.get("metadata") or {}
    meta["hops"]        = reg.get("hops", [])
    meta["open_ports"]  = reg.get("open_ports", [])
    meta["probe_agent"] = reg.get("probe_agent", "")
    return {
        "cable_id":                reg["cable_id"],
        "measured_at":             ts,
        "latency_ms":              reg.get("latency_ms"),
        "jitter_ms":               reg.get("jitter_ms"),
        "packet_loss_pct":         reg.get("packet_loss_pct"),
        "throughput_mbps":         reg.get("throughput_mbps"),
        "packets_per_second":      reg.get("packets_per_second"),
        "availability_pct":        reg.get("availability_pct"),
        "route_error_count":       reg.get("route_error_count", 0),
        "source":                  reg.get("source", "synthetic"),
        "method":                  reg.get("method", "heuristic"),
        "confidence_score":        reg.get("confidence_score", 0.55),
        "estimated":               bool(reg.get("estimated", True)),
        "telemetry_model_version": reg.get("telemetry_model_version", "1.0.0"),
        "metadata":                json.dumps(meta, default=str),
    }


def cmd_history(args: argparse.Namespace) -> None:
    if not HIST_DIR.is_dir():
        print(f"[history] Diretório não encontrado: {HIST_DIR}")
        sys.exit(1)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.hours) if args.hours else None
    files: list[Path] = []
    for pat in ("*_probe_full_*.json", "*_checkpoint_*.json"):
        files.extend(HIST_DIR.glob(pat))
        files.extend(HIST_DIR.glob(f"*/{pat}"))
    files = sorted(set(files))

    if cutoff is not None:
        def _in_window(f: Path) -> bool:
            try:
                day = datetime.strptime(f.name[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                return day >= cutoff - timedelta(days=1)
            except ValueError:
                return True
        files = [f for f in files if _in_window(f)]

    if not files:
        print("[history] Nenhum arquivo encontrado.")
        return

    print(f"[history] {len(files)} arquivo(s)" + (f"  (janela: últimas {args.hours}h)" if args.hours else ""))

    conn   = db_connect() if not args.dry_run else None
    total  = 0
    errors = 0
    seen:  set[tuple] = set()

    for fpath in files:
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  [ERRO] {fpath.name}: {exc}")
            errors += 1
            continue

        rows = []
        for reg in data.get("registros", []):
            row = _record_to_row(reg)
            if row is None:
                continue
            if cutoff and row["measured_at"] < cutoff:
                continue
            key = (row["cable_id"], row["measured_at"].isoformat())
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)

        if not rows:
            continue

        if args.dry_run:
            print(f"  [dry-run] {fpath.name}: {len(rows)} registros")
            total += len(rows)
            continue

        try:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(cur, HISTORY_UPSERT_SQL, rows, page_size=500)
            conn.commit()
            total += len(rows)
            print(f"  {fpath.name}: {len(rows)} registros inseridos/ignorados")
        except Exception as exc:
            conn.rollback()
            print(f"  [ERRO] {fpath.name}: {exc}")
            errors += 1

    if conn:
        conn.close()
    print(f"\n[history] Total: {total} registros  |  Erros: {errors}")


# ─────────────────────────────────────────────────────────────────────────────
# ── CLI ───────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="RMIFO — Gerenciamento do Catálogo de Cabos",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="subcmd", required=True)

    sub.add_parser("seed",   help="Gera db/seed.sql a partir dos arquivos externos")
    sub.add_parser("import", help="Importa catálogo diretamente no PostgreSQL")

    p_hist = sub.add_parser("history", help="Importa JSON de sondas históricas → cable_telemetry_samples")
    p_hist.add_argument("--hours",   type=int,  default=None,
                        help="Importar apenas últimas N horas (padrão: tudo)")
    p_hist.add_argument("--dry-run", action="store_true",
                        help="Simula sem gravar no banco")

    args = parser.parse_args()

    if   args.subcmd == "seed":    cmd_seed(args)
    elif args.subcmd == "import":  cmd_import(args)
    elif args.subcmd == "history": cmd_history(args)


if __name__ == "__main__":
    main()
