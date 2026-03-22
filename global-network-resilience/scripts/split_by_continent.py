#!/usr/bin/env python3
"""Separa airports.dat e routes.dat por continente com arquivos enumerados.

Regras de continente:
- Usa o campo de timezone IANA (coluna 11 de airports.dat), ex.: Europe/Paris.
- Normaliza para: Africa, America, Asia, Europe, Oceania, Antarctica, Arctic, Unknown.

Saídas:
- airports/: arquivos por continente
- routes_from/: rotas por continente de origem
- routes_pair/: rotas por par origem->destino de continentes
- índices CSV + summary JSON com contagens
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Separa OpenFlights por continente")
    parser.add_argument(
        "--airports",
        default="data/public_cache/airports.dat",
        help="Caminho para airports.dat",
    )
    parser.add_argument(
        "--routes",
        default="data/public_cache/routes.dat",
        help="Caminho para routes.dat",
    )
    parser.add_argument(
        "--output-dir",
        default="data/public_cache/continent_split",
        help="Diretorio de saida para os arquivos separados",
    )
    return parser.parse_args()


def normalize_continent_from_tz(tz_value: str) -> str:
    """Normaliza continente a partir do prefixo do timezone IANA."""
    if not tz_value or tz_value == "\\N":
        return "Unknown"

    region = tz_value.split("/", 1)[0].strip()
    mapping = {
        "Africa": "Africa",
        "America": "America",
        "Asia": "Asia",
        "Europe": "Europe",
        "Pacific": "Oceania",
        "Australia": "Oceania",
        "Indian": "Oceania",
        "Atlantic": "Oceania",
        "Antarctica": "Antarctica",
        "Arctic": "Arctic",
    }
    return mapping.get(region, "Unknown")


def read_dat_rows(file_path: Path) -> List[List[str]]:
    rows: List[List[str]] = []
    with file_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            rows.append(row)
    return rows


def safe_slug(value: str) -> str:
    return value.lower().replace(" ", "_")


def write_dat_file(path: Path, rows: Iterable[List[str]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        for row in rows:
            writer.writerow(row)
            count += 1
    return count


def write_index_csv(path: Path, header: List[str], rows: List[List[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    airports_path = Path(args.airports)
    routes_path = Path(args.routes)
    output_dir = Path(args.output_dir)

    if not airports_path.exists():
        raise FileNotFoundError(f"airports.dat nao encontrado: {airports_path}")
    if not routes_path.exists():
        raise FileNotFoundError(f"routes.dat nao encontrado: {routes_path}")

    airports_rows = read_dat_rows(airports_path)
    routes_rows = read_dat_rows(routes_path)

    # airports.dat OpenFlights:
    # [0]=airport_id, [11]=timezone db (IANA)
    airport_to_continent: Dict[str, str] = {}
    airports_by_continent: Dict[str, List[List[str]]] = defaultdict(list)

    for row in airports_rows:
        if len(row) < 12:
            continue
        airport_id = row[0].strip()
        if not airport_id or airport_id == "\\N":
            continue

        tz_value = row[11].strip() if len(row) > 11 else ""
        continent = normalize_continent_from_tz(tz_value)
        airport_to_continent[airport_id] = continent
        airports_by_continent[continent].append(row)

    # routes.dat OpenFlights:
    # [3]=source_airport_id, [5]=destination_airport_id
    routes_from_continent: Dict[str, List[List[str]]] = defaultdict(list)
    routes_pair_continent: Dict[Tuple[str, str], List[List[str]]] = defaultdict(list)

    unresolved_routes = 0
    for row in routes_rows:
        if len(row) < 6:
            continue
        src_id = row[3].strip()
        dst_id = row[5].strip()

        if src_id in ("", "\\N") or dst_id in ("", "\\N"):
            unresolved_routes += 1
            continue

        src_cont = airport_to_continent.get(src_id, "Unknown")
        dst_cont = airport_to_continent.get(dst_id, "Unknown")

        routes_from_continent[src_cont].append(row)
        routes_pair_continent[(src_cont, dst_cont)].append(row)

    airports_dir = output_dir / "airports"
    routes_from_dir = output_dir / "routes_from"
    routes_pair_dir = output_dir / "routes_pair"

    airports_index_rows: List[List[str]] = []
    routes_from_index_rows: List[List[str]] = []
    routes_pair_index_rows: List[List[str]] = []

    # Enumeração consistente por ordem alfabética de continente
    for idx, continent in enumerate(sorted(airports_by_continent.keys()), start=1):
        file_name = f"{idx:02d}_{safe_slug(continent)}_airports.dat"
        file_path = airports_dir / file_name
        count = write_dat_file(file_path, airports_by_continent[continent])
        airports_index_rows.append([str(idx), continent, file_name, str(count)])

    for idx, continent in enumerate(sorted(routes_from_continent.keys()), start=1):
        file_name = f"{idx:02d}_{safe_slug(continent)}_routes_from.dat"
        file_path = routes_from_dir / file_name
        count = write_dat_file(file_path, routes_from_continent[continent])
        routes_from_index_rows.append([str(idx), continent, file_name, str(count)])

    sorted_pairs = sorted(routes_pair_continent.keys(), key=lambda x: (x[0], x[1]))
    for idx, (src_cont, dst_cont) in enumerate(sorted_pairs, start=1):
        file_name = (
            f"{idx:02d}_{safe_slug(src_cont)}_to_{safe_slug(dst_cont)}_routes.dat"
        )
        file_path = routes_pair_dir / file_name
        count = write_dat_file(file_path, routes_pair_continent[(src_cont, dst_cont)])
        routes_pair_index_rows.append([str(idx), src_cont, dst_cont, file_name, str(count)])

    write_index_csv(
        output_dir / "airports_index.csv",
        ["index", "continent", "file_name", "airports_count"],
        airports_index_rows,
    )
    write_index_csv(
        output_dir / "routes_from_index.csv",
        ["index", "source_continent", "file_name", "routes_count"],
        routes_from_index_rows,
    )
    write_index_csv(
        output_dir / "routes_pair_index.csv",
        ["index", "source_continent", "dest_continent", "file_name", "routes_count"],
        routes_pair_index_rows,
    )

    summary = {
        "input": {
            "airports": str(airports_path),
            "routes": str(routes_path),
        },
        "output_dir": str(output_dir),
        "totals": {
            "airports_rows": len(airports_rows),
            "routes_rows": len(routes_rows),
            "airports_mapped": sum(len(v) for v in airports_by_continent.values()),
            "routes_resolved": sum(len(v) for v in routes_from_continent.values()),
            "routes_unresolved": unresolved_routes,
        },
        "airports_by_continent": dict(Counter({k: len(v) for k, v in airports_by_continent.items()})),
        "routes_from_continent": dict(Counter({k: len(v) for k, v in routes_from_continent.items()})),
        "routes_by_pair_count": len(routes_pair_continent),
    }

    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("Separacao por continente concluida.")
    print(f"Output: {output_dir}")
    print(f"airports_index.csv: {len(airports_index_rows)} arquivos")
    print(f"routes_from_index.csv: {len(routes_from_index_rows)} arquivos")
    print(f"routes_pair_index.csv: {len(routes_pair_index_rows)} arquivos")
    print(f"summary.json: {summary_path}")


if __name__ == "__main__":
    main()
