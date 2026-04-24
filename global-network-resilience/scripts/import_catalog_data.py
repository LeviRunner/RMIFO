#!/usr/bin/env python3
from __future__ import annotations

import csv
import ipaddress
import json
import os
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

import psycopg2


COUNTRY_ALIASES = {
    "congo dem rep": "democratic republic of the congo",
    "congo rep": "republic of the congo",
    "cote divoire": "cote d'ivoire",
    "cote d ivoire": "cote d'ivoire",
    "korea rep": "south korea",
    "korea dem peoples rep": "north korea",
    "curacao": "curacao",
    "lao peoples democratic republic": "laos",
    "myanmar burma": "myanmar",
    "reunion": "reunion",
    "st kitts and nevis": "saint kitts and nevis",
    "st vincent and the grenadines": "saint vincent and the grenadines",
    "viet nam": "vietnam",
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
    normalized = normalize_text(value)
    return COUNTRY_ALIASES.get(normalized, normalized)


def clean_int(value: str | None) -> int | None:
    if not value:
        return None
    digits = re.sub(r"[^0-9]", "", value)
    return int(digits) if digits else None


def clean_bool(value: str | None) -> bool | None:
    if value is None or value == "":
        return None
    lowered = value.strip().lower()
    if lowered in {"true", "1", "yes"}:
        return True
    if lowered in {"false", "0", "no"}:
        return False
    return None


def int_to_ip(value: str | None) -> str | None:
    if value in (None, "", "-"):
        return None
    return str(ipaddress.IPv4Address(int(value)))


def summarize_network(start_ip: str | None, end_ip: str | None) -> str | None:
    if not start_ip or not end_ip:
        return None
    start_addr = ipaddress.ip_address(start_ip)
    end_addr = ipaddress.ip_address(end_ip)
    networks = list(ipaddress.summarize_address_range(start_addr, end_addr))
    if len(networks) == 1:
        return str(networks[0])
    return None


def load_cable_rows(csv_path: Path) -> list[tuple]:
    rows: list[tuple] = []
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append(
                (
                    row["cable_id"],
                    row["cable_name"],
                    row.get("owners") or None,
                    None,
                    clean_int(row.get("length_km")),
                    clean_int(row.get("rfs_year")),
                    clean_bool(row.get("is_operational")),
                    clean_int(row.get("landing_points")),
                    row.get("suppliers") or None,
                )
            )
    return rows


def collect_catalog(catalog_path: Path) -> tuple[list[tuple], dict[str, dict], list[tuple]]:
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    metadata_rows: list[tuple] = []
    endpoints: dict[str, dict] = {}
    links: list[tuple] = []

    for cable in payload.get("cables", []):
        cable_id = cable["id"]
        metadata_rows.append(
            (
                cable_id,
                cable.get("url"),
                cable.get("source_url"),
                cable.get("notes"),
                cable.get("is_planned"),
                cable.get("length"),
                cable.get("rfs"),
            )
        )

        for order, landing_point in enumerate(cable.get("landing_points", []), start=1):
            endpoint_id = landing_point["id"]
            endpoint_name = landing_point.get("name") or endpoint_id
            city_name = endpoint_name.split(",", 1)[0].strip()
            if endpoint_id not in endpoints:
                endpoints[endpoint_id] = {
                    "endpoint_id": endpoint_id,
                    "endpoint_name": endpoint_name,
                    "city_name": city_name,
                    "country_name": landing_point.get("country") or None,
                    "country_code": None,
                    "latitude": None,
                    "longitude": None,
                    "geo_source": "pending",
                    "samples": [],
                }

            links.append((cable_id, endpoint_id, order, landing_point.get("is_tbd")))

    return metadata_rows, endpoints, links


def enrich_endpoints_from_geoip(endpoints: dict[str, dict], geoip_path: Path, sample_limit: int = 5) -> None:
    city_country_index: dict[tuple[str, str], list[str]] = defaultdict(list)
    country_index: dict[str, list[str]] = defaultdict(list)
    country_fallbacks: dict[str, list[dict]] = defaultdict(list)

    for endpoint_id, endpoint in endpoints.items():
        city_key = normalize_text(endpoint["city_name"])
        country_key = normalize_country(endpoint["country_name"])
        if city_key and country_key:
            city_country_index[(city_key, country_key)].append(endpoint_id)
        if country_key:
            country_index[country_key].append(endpoint_id)

    target_countries = set(country_index.keys())

    with geoip_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            country_name = row.get("country") or ""
            if country_name in {"", "-"}:
                continue

            country_key = normalize_country(country_name)
            if country_key not in target_countries:
                continue

            city_name = row.get("city") or ""
            city_key = normalize_text(city_name)
            sample = {
                "network": summarize_network(int_to_ip(row.get("ip_range_start")), int_to_ip(row.get("ip_range_end"))),
                "ip_start": int_to_ip(row.get("ip_range_start")),
                "ip_end": int_to_ip(row.get("ip_range_end")),
                "country_name": country_name,
                "country_code": row.get("country_code") or None,
                "region": None if row.get("region") in {None, "", "-"} else row.get("region"),
                "city": None if city_name in {"", "-"} else city_name,
                "latitude": float(row["latitude"]) if row.get("latitude") not in {None, "", "-"} else None,
                "longitude": float(row["longitude"]) if row.get("longitude") not in {None, "", "-"} else None,
            }

            if len(country_fallbacks[country_key]) < sample_limit * 4:
                country_fallbacks[country_key].append(sample)

            if not city_key:
                continue

            for endpoint_id in city_country_index.get((city_key, country_key), []):
                endpoint = endpoints[endpoint_id]
                if endpoint["country_code"] is None:
                    endpoint["country_code"] = sample["country_code"]
                if endpoint["latitude"] is None and sample["latitude"] is not None:
                    endpoint["latitude"] = sample["latitude"]
                    endpoint["longitude"] = sample["longitude"]
                    endpoint["geo_source"] = "geoip-city-match"
                if len(endpoint["samples"]) < sample_limit:
                    endpoint["samples"].append(sample)

    for endpoint in endpoints.values():
        country_key = normalize_country(endpoint["country_name"])
        fallback_samples = country_fallbacks.get(country_key, [])

        if endpoint["country_code"] is None and fallback_samples:
            endpoint["country_code"] = fallback_samples[0]["country_code"]
        if endpoint["latitude"] is None and fallback_samples:
            endpoint["latitude"] = fallback_samples[0]["latitude"]
            endpoint["longitude"] = fallback_samples[0]["longitude"]
            endpoint["geo_source"] = "geoip-country-fallback"
        if not endpoint["samples"] and fallback_samples:
            endpoint["samples"] = fallback_samples[:sample_limit]

        if endpoint["geo_source"] == "pending":
            endpoint["geo_source"] = "unmatched"


def import_all(base_dir: Path) -> None:
    csv_path = base_dir / "data" / "external" / "submarine_cables.csv"
    catalog_path = base_dir / "data" / "external" / "submarine_cables_full.json"
    geoip_path = base_dir / "data" / "external" / "geoip_database.csv"

    cable_rows = load_cable_rows(csv_path)
    metadata_rows, endpoints, links = collect_catalog(catalog_path)
    enrich_endpoints_from_geoip(endpoints, geoip_path)

    conn = psycopg2.connect(
        host=os.getenv("PG_SOCKET", str(base_dir / ".local" / "run")),
        port=int(os.getenv("PG_PORT", "55432")),
        dbname=os.getenv("DB_NAME", "global_network_resilience"),
        user=os.getenv("PGUSER", "postgres"),
    )
    conn.autocommit = False

    with conn, conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO cables (
                cable_id, cable_name, owners, capacity_gbps, length_km,
                rfs_year, is_operational, landing_points_count, suppliers
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (cable_id) DO UPDATE SET
                cable_name = EXCLUDED.cable_name,
                owners = EXCLUDED.owners,
                capacity_gbps = EXCLUDED.capacity_gbps,
                length_km = EXCLUDED.length_km,
                rfs_year = EXCLUDED.rfs_year,
                is_operational = EXCLUDED.is_operational,
                landing_points_count = EXCLUDED.landing_points_count,
                suppliers = EXCLUDED.suppliers
            """,
            cable_rows,
        )

        cur.executemany(
            """
            INSERT INTO cable_metadata (
                cable_id, public_url, source_url, notes, is_planned, raw_length, raw_rfs
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (cable_id) DO UPDATE SET
                public_url = EXCLUDED.public_url,
                source_url = EXCLUDED.source_url,
                notes = EXCLUDED.notes,
                is_planned = EXCLUDED.is_planned,
                raw_length = EXCLUDED.raw_length,
                raw_rfs = EXCLUDED.raw_rfs
            """,
            metadata_rows,
        )

        cur.execute("TRUNCATE TABLE endpoint_ip_samples, cable_landing_points, landing_points RESTART IDENTITY CASCADE")

        cur.executemany(
            """
            INSERT INTO landing_points (
                endpoint_id, endpoint_name, city_name, country_name,
                country_code, latitude, longitude, geo_source
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    endpoint["endpoint_id"],
                    endpoint["endpoint_name"],
                    endpoint["city_name"],
                    endpoint["country_name"],
                    endpoint["country_code"],
                    endpoint["latitude"],
                    endpoint["longitude"],
                    endpoint["geo_source"],
                )
                for endpoint in endpoints.values()
            ],
        )

        cur.executemany(
            """
            INSERT INTO cable_landing_points (cable_id, endpoint_id, endpoint_order, is_tbd)
            VALUES (%s, %s, %s, %s)
            """,
            links,
        )

        endpoint_samples: list[tuple] = []
        for endpoint in endpoints.values():
            for sample_order, sample in enumerate(endpoint["samples"], start=1):
                endpoint_samples.append(
                    (
                        endpoint["endpoint_id"],
                        sample_order,
                        sample["network"],
                        sample["ip_start"],
                        sample["ip_end"],
                        sample["country_name"],
                        sample["country_code"],
                        sample["region"],
                        sample["city"],
                        sample["latitude"],
                        sample["longitude"],
                    )
                )

        if endpoint_samples:
            cur.executemany(
                """
                INSERT INTO endpoint_ip_samples (
                    endpoint_id, sample_order, network, ip_start, ip_end,
                    country_name, country_code, region, city, latitude, longitude
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                endpoint_samples,
            )

    print(f"Cabos importados/atualizados: {len(cable_rows)}")
    print(f"Metadados importados: {len(metadata_rows)}")
    print(f"Endpoints importados: {len(endpoints)}")
    print(f"Amostras de IP importadas: {sum(len(endpoint['samples']) for endpoint in endpoints.values())}")


if __name__ == "__main__":
    import_all(Path(__file__).resolve().parent.parent)