#!/usr/bin/env python3
"""
Gera global-network-resilience/db/seed.sql completo a partir dos dados externos.

Fontes:
  data/external/submarine_cables.csv          → tabela cables
  data/external/submarine_cables_full.json    → cable_metadata, landing_points,
                                                 cable_landing_points
  data/external/dados_extra_sobre_ip.json     → endpoint_ip_samples

Uso:
  python3 scripts/generate_full_seed.py
  # Gera db/seed.sql (sobrescreve)
"""
import csv
import json
import re
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR    = PROJECT_DIR / "data" / "external"
OUT_FILE    = PROJECT_DIR / "db" / "seed.sql"


def esc(s: str | None) -> str:
    if s is None:
        return "NULL"
    return "'" + str(s).replace("'", "''") + "'"


def esc_bool(v) -> str:
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


def parse_length(raw: str) -> str:
    if not raw:
        return "NULL"
    digits = re.sub(r"[^\d]", "", raw)
    return digits if digits else "NULL"


def parse_year(raw: str) -> str:
    if not raw:
        return "NULL"
    m = re.search(r"\d{4}", str(raw))
    return m.group(0) if m else "NULL"


def parse_loc(loc_str: str):
    """'lat,lng' → (lat, lng) floats or (None, None)."""
    if not loc_str:
        return None, None
    parts = loc_str.split(",")
    if len(parts) != 2:
        return None, None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None, None


def country_code_from_name(name: str) -> str:
    """Best-effort 2-char country code from country name."""
    _map = {
        "united states": "US", "usa": "US", "u.s.": "US",
        "united kingdom": "GB", "uk": "GB",
        "france": "FR", "germany": "DE", "spain": "ES",
        "italy": "IT", "portugal": "PT", "netherlands": "NL",
        "belgium": "BE", "denmark": "DK", "norway": "NO",
        "sweden": "SE", "finland": "FI", "ireland": "IE",
        "greece": "GR", "turkey": "TR", "egypt": "EG",
        "south africa": "ZA", "nigeria": "NG", "kenya": "KE",
        "ghana": "GH", "angola": "AO", "mozambique": "MZ",
        "djibouti": "DJ", "somalia": "SO", "ethiopia": "ET",
        "tanzania": "TZ", "senegal": "SN", "cameroon": "CM",
        "gabon": "GA", "congo": "CG", "morocco": "MA",
        "algeria": "DZ", "tunisia": "TN", "libya": "LY",
        "brazil": "BR", "argentina": "AR", "chile": "CL",
        "colombia": "CO", "peru": "PE", "venezuela": "VE",
        "uruguay": "UY", "ecuador": "EC", "mexico": "MX",
        "canada": "CA", "bahamas": "BS", "cuba": "CU",
        "jamaica": "JM", "trinidad": "TT",
        "india": "IN", "pakistan": "PK", "bangladesh": "BD",
        "sri lanka": "LK", "myanmar": "MM", "thailand": "TH",
        "malaysia": "MY", "singapore": "SG", "indonesia": "ID",
        "philippines": "PH", "vietnam": "VN", "cambodia": "KH",
        "china": "CN", "taiwan": "TW", "hong kong": "HK",
        "japan": "JP", "south korea": "KR", "korea": "KR",
        "australia": "AU", "new zealand": "NZ", "fiji": "FJ",
        "guam": "GU", "palau": "PW", "micronesia": "FM",
        "papua new guinea": "PG", "solomon islands": "SB",
        "russia": "RU", "ukraine": "UA", "poland": "PL",
        "czech republic": "CZ", "austria": "AT", "switzerland": "CH",
        "israel": "IL", "saudi arabia": "SA", "uae": "AE",
        "united arab emirates": "AE", "qatar": "QA", "kuwait": "KW",
        "bahrain": "BH", "oman": "OM", "iran": "IR", "iraq": "IQ",
        "jordan": "JO", "lebanon": "LB", "cyprus": "CY",
        "malta": "MT", "iceland": "IS",
        "greenland": "GL", "faroe islands": "FO",
    }
    low = name.strip().lower()
    for k, v in _map.items():
        if k in low:
            return v
    return ""


def main():
    # ── Load source data ─────────────────────────────────────────────────────
    print("Loading CSV…", file=sys.stderr)
    with open(DATA_DIR / "submarine_cables.csv", encoding="utf-8") as f:
        csv_rows = {r["cable_id"]: r for r in csv.DictReader(f)}  # type: ignore[assignment]

    print("Loading full JSON…", file=sys.stderr)
    with open(DATA_DIR / "submarine_cables_full.json", encoding="utf-8") as f:
        full_data = json.load(f)["cables"]
    full_by_id = {c["id"]: c for c in full_data}

    print("Loading IP data…", file=sys.stderr)
    with open(DATA_DIR / "dados_extra_sobre_ip.json", encoding="utf-8") as f:
        ip_data = json.load(f)

    # Build IP lookup: lp_id → list of IP records (up to 5)
    ip_by_lp: dict[str, list] = {}
    for rec in ip_data:
        lid = rec.get("lp_id", "").strip()
        if not lid:
            continue
        ip_by_lp.setdefault(lid, [])
        if len(ip_by_lp[lid]) < 5:
            ip_by_lp[lid].append(rec)

    # ── Collect unique landing points across all cables ──────────────────────
    # lp_id → {id, name, city, country, is_tbd, lat, lng, cc}
    all_lps: dict[str, dict] = {}
    # cable → ordered list of lp_ids
    cable_lp_order: dict[str, list[tuple[str, bool]]] = {}

    for cable in full_data:
        cid = cable["id"]
        lps = cable.get("landing_points") or []
        order_list = []
        for i, lp in enumerate(lps):
            lid = lp.get("id", "").strip()
            if not lid:
                continue
            order_list.append((lid, bool(lp.get("is_tbd"))))

            if lid not in all_lps:
                # Derive city/country from name "City, Country"
                raw_name = lp.get("name", "") or ""
                parts = [p.strip() for p in raw_name.split(",", 1)]
                city    = parts[0] if parts else ""
                country = parts[1] if len(parts) > 1 else lp.get("country", "")

                # Try to get coords from IP data
                lat, lng = None, None
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
                    "id": lid,
                    "name": raw_name or lid,
                    "city": city,
                    "country": country or lp.get("country", ""),
                    "cc": cc[:2] if cc else "",
                    "lat": lat,
                    "lng": lng,
                    "geo_source": "ipinfo" if lat is not None else "unmatched",
                }

        cable_lp_order[cid] = order_list

    print(
        f"  Cables: {len(full_by_id)}  LPs: {len(all_lps)}  "
        f"LPs with coords: {sum(1 for lp in all_lps.values() if lp['lat'] is not None)}",
        file=sys.stderr,
    )

    # ── Write SQL ────────────────────────────────────────────────────────────
    lines = []
    W = lines.append

    W("-- ─────────────────────────────────────────────────────────────────────────────")
    W("-- RMIFO — Seed completo gerado automaticamente por generate_full_seed.py")
    W(f"-- Fonte: submarine_cables.csv + submarine_cables_full.json + dados_extra_sobre_ip.json")
    W(f"-- Cabos: {len(full_by_id)}  |  Landing points: {len(all_lps)}")
    W("-- ─────────────────────────────────────────────────────────────────────────────")
    W("")

    # ── Vista de compatibilidade ─────────────────────────────────────────────
    W("CREATE OR REPLACE VIEW endpoints AS")
    W("SELECT")
    W("    clp.cable_id,")
    W("    clp.endpoint_id,")
    W("    lp.endpoint_name          AS name,")
    W("    lp.country_name           AS country,")
    W("    lp.city_name              AS city,")
    W("    clp.endpoint_order,")
    W("    eis.ip_start::text        AS ip_sample")
    W("FROM cable_landing_points clp")
    W("JOIN  landing_points lp ON lp.endpoint_id = clp.endpoint_id")
    W("LEFT JOIN endpoint_ip_samples eis")
    W("       ON eis.endpoint_id = clp.endpoint_id AND eis.sample_order = 0;")
    W("")

    # ── cables ───────────────────────────────────────────────────────────────
    W("-- ── Cabos (692 registros) ───────────────────────────────────────────────────")
    W("INSERT INTO cables (cable_id, cable_name, owners, capacity_gbps, length_km,")
    W("                    rfs_year, is_operational, landing_points_count, suppliers)")
    W("VALUES")

    cable_rows_sql = []
    for cable in full_data:
        cid  = cable["id"]
        crow = csv_rows.get(cid, {})
        name = cable.get("name") or crow.get("cable_name") or cid
        owners    = cable.get("owners") or crow.get("owners") or ""
        suppliers = cable.get("suppliers") or crow.get("suppliers") or ""
        length    = parse_length(cable.get("length") or crow.get("length_km", ""))
        rfs_year  = parse_year(cable.get("rfs_year") or cable.get("rfs") or crow.get("rfs_year", ""))
        is_op     = esc_bool(
            False if cable.get("is_planned") else
            crow.get("is_operational", "True")
        )
        lp_count  = len(cable.get("landing_points") or [])
        # capacity not in source data — set NULL
        cable_rows_sql.append(
            f"  ({esc(cid)},{esc(name)},{esc(owners)},NULL,"
            f"{length},{rfs_year},{is_op},{lp_count},{esc(suppliers)})"
        )

    W(",\n".join(cable_rows_sql))
    W("ON CONFLICT (cable_id) DO UPDATE SET")
    W("  cable_name           = EXCLUDED.cable_name,")
    W("  owners               = EXCLUDED.owners,")
    W("  length_km            = EXCLUDED.length_km,")
    W("  rfs_year             = EXCLUDED.rfs_year,")
    W("  is_operational       = EXCLUDED.is_operational,")
    W("  landing_points_count = EXCLUDED.landing_points_count,")
    W("  suppliers            = EXCLUDED.suppliers;")
    W("")

    # ── cable_metadata ───────────────────────────────────────────────────────
    W("-- ── Metadados de cabos ──────────────────────────────────────────────────────")
    W("INSERT INTO cable_metadata (cable_id, public_url, source_url, notes, is_planned, raw_length, raw_rfs)")
    W("VALUES")
    meta_rows = []
    for cable in full_data:
        cid = cable["id"]
        meta_rows.append(
            f"  ({esc(cid)},{esc(cable.get('url'))},{esc(cable.get('source_url'))},"
            f"{esc(cable.get('notes'))},{esc_bool(cable.get('is_planned'))},"
            f"{esc(cable.get('length'))},{esc(str(cable.get('rfs') or cable.get('rfs_year') or ''))})"
        )
    W(",\n".join(meta_rows))
    W("ON CONFLICT (cable_id) DO UPDATE SET")
    W("  public_url  = EXCLUDED.public_url,")
    W("  source_url  = EXCLUDED.source_url,")
    W("  notes       = EXCLUDED.notes,")
    W("  is_planned  = EXCLUDED.is_planned,")
    W("  raw_length  = EXCLUDED.raw_length,")
    W("  raw_rfs     = EXCLUDED.raw_rfs;")
    W("")

    # ── landing_points ───────────────────────────────────────────────────────
    W(f"-- ── Landing points ({len(all_lps)} registros) ──────────────────────────────────────")
    W("INSERT INTO landing_points (endpoint_id, endpoint_name, city_name, country_name,")
    W("                            country_code, latitude, longitude, geo_source)")
    W("VALUES")
    lp_rows_sql = []
    for lp in all_lps.values():
        lat = str(lp["lat"]) if lp["lat"] is not None else "NULL"
        lng = str(lp["lng"]) if lp["lng"] is not None else "NULL"
        lp_rows_sql.append(
            f"  ({esc(lp['id'])},{esc(lp['name'])},{esc(lp['city'])},{esc(lp['country'])},"
            f"{esc(lp['cc'] or None)},{lat},{lng},{esc(lp['geo_source'])})"
        )
    W(",\n".join(lp_rows_sql))
    W("ON CONFLICT (endpoint_id) DO UPDATE SET")
    W("  endpoint_name = EXCLUDED.endpoint_name,")
    W("  city_name     = EXCLUDED.city_name,")
    W("  country_name  = EXCLUDED.country_name,")
    W("  country_code  = EXCLUDED.country_code,")
    W("  latitude      = COALESCE(EXCLUDED.latitude, landing_points.latitude),")
    W("  longitude     = COALESCE(EXCLUDED.longitude, landing_points.longitude),")
    W("  geo_source    = EXCLUDED.geo_source;")
    W("")

    # ── cable_landing_points ─────────────────────────────────────────────────
    total_clp = sum(len(v) for v in cable_lp_order.values())
    W(f"-- ── Cable–Landing-Point associations ({total_clp} registros) ─────────────────────")
    W("INSERT INTO cable_landing_points (cable_id, endpoint_id, endpoint_order, is_tbd)")
    W("VALUES")
    clp_rows = []
    for cid, lp_list in cable_lp_order.items():
        for order, (lid, is_tbd) in enumerate(lp_list):
            if lid not in all_lps:
                continue
            clp_rows.append(
                f"  ({esc(cid)},{esc(lid)},{order},{esc_bool(is_tbd)})"
            )
    W(",\n".join(clp_rows))
    W("ON CONFLICT (cable_id, endpoint_id) DO UPDATE SET")
    W("  endpoint_order = EXCLUDED.endpoint_order,")
    W("  is_tbd         = EXCLUDED.is_tbd;")
    W("")

    # ── endpoint_ip_samples ──────────────────────────────────────────────────
    total_ips = sum(min(len(v), 5) for v in ip_by_lp.values())
    W(f"-- ── IP samples por endpoint ({total_ips} registros) ───────────────────────────────")
    W("INSERT INTO endpoint_ip_samples")
    W("    (endpoint_id, sample_order, network, ip_start, ip_end,")
    W("     country_name, country_code, region, city, latitude, longitude)")
    W("VALUES")
    ip_rows_sql = []
    for lid, recs in ip_by_lp.items():
        if lid not in all_lps:
            continue
        for i, rec in enumerate(recs):
            lat, lng = parse_loc(rec.get("ipinfo_loc", ""))
            lat_s = str(lat) if lat is not None else "NULL"
            lng_s = str(lng) if lng is not None else "NULL"
            ip_rows_sql.append(
                f"  ({esc(lid)},{i},{esc(rec.get('network'))},{esc(rec.get('ip_start'))},"
                f"{esc(rec.get('ip_end'))},{esc(rec.get('ipinfo_country'))},{esc(rec.get('ipinfo_country'))},"
                f"{esc(rec.get('ipinfo_region'))},{esc(rec.get('ipinfo_city'))},{lat_s},{lng_s})"
            )
    W(",\n".join(ip_rows_sql))
    W("ON CONFLICT (endpoint_id, sample_order) DO NOTHING;")
    W("")

    # ── Summary notice ───────────────────────────────────────────────────────
    W("DO $$")
    W("DECLARE")
    W("  v_cables bigint; v_lps bigint; v_ips bigint;")
    W("BEGIN")
    W("  SELECT COUNT(*) INTO v_cables FROM cables;")
    W("  SELECT COUNT(*) INTO v_lps    FROM landing_points;")
    W("  SELECT COUNT(*) INTO v_ips    FROM endpoint_ip_samples;")
    W("  RAISE NOTICE '────────────────────────────────────────';")
    W("  RAISE NOTICE 'RMIFO seed aplicado com sucesso:';")
    W("  RAISE NOTICE '  cabos:          %', v_cables;")
    W("  RAISE NOTICE '  landing points: %', v_lps;")
    W("  RAISE NOTICE '  IP samples:     %', v_ips;")
    W("  RAISE NOTICE '────────────────────────────────────────';")
    W("END;")
    W("$$;")
    W("")

    sql = "\n".join(lines)
    OUT_FILE.write_text(sql, encoding="utf-8")
    print(f"Gerado: {OUT_FILE}  ({len(sql):,} bytes)", file=sys.stderr)


if __name__ == "__main__":
    main()
