#!/usr/bin/env python3
"""
Baixa e normaliza dados GeoIP da IP2Location LITE para CSV usado no RMIFO.

Fluxos suportados:
1) URL pública direta (padrão): DB1 (country-level)
2) Download autenticado por token + código de pacote

Saída padrão:
- data/external/geoip_database.csv
"""

from __future__ import annotations

import argparse
import csv
import io
import tempfile
import zipfile
from pathlib import Path
from typing import List, Optional, Sequence
from urllib.parse import urlencode
from urllib.request import Request, urlopen

PUBLIC_DB1_ZIP_URL = "https://download.ip2location.com/lite/IP2LOCATION-LITE-DB1.CSV.ZIP"
AUTH_DOWNLOAD_BASE_URL = "https://www.ip2location.com/download"
DEFAULT_OUTPUT = Path("data/external/geoip_database.csv")


def download_bytes(url: str, timeout: int = 60) -> bytes:
    """Faz download de bytes via HTTP GET."""
    req = Request(
        url,
        headers={
            "User-Agent": "RMIFO-geoip-exporter/1.0",
            "Accept": "application/octet-stream,*/*",
        },
    )
    with urlopen(req, timeout=timeout) as response:
        return response.read()


def build_auth_url(download_token: str, download_code: str) -> str:
    """Monta URL autenticada para download no endpoint oficial da IP2Location."""
    query = urlencode({"token": download_token, "file": download_code})
    return f"{AUTH_DOWNLOAD_BASE_URL}?{query}"


def find_csv_in_zip(blob: bytes) -> tuple[str, bytes]:
    """Extrai o primeiro CSV encontrado dentro de um arquivo ZIP em memória."""
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=True) as tmp:
        tmp.write(blob)
        tmp.flush()

        with zipfile.ZipFile(tmp.name, "r") as zf:
            csv_candidates = [
                name for name in zf.namelist() if name.lower().endswith(".csv")
            ]
            if not csv_candidates:
                raise ValueError("Nenhum CSV encontrado no ZIP baixado.")

            csv_name = csv_candidates[0]
            return csv_name, zf.read(csv_name)


def clean_value(value: Optional[str]) -> str:
    """Remove espaços e aspas de campos vindos do CSV fonte."""
    if value is None:
        return ""
    return value.strip().strip('"')


def parse_float(value: str) -> Optional[float]:
    """Converte string numérica para float; retorna None para vazio/inválido."""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def normalize_row(columns: Sequence[str]) -> Optional[List[str]]:
    """
    Normaliza linhas de diferentes DBs da IP2Location.

    Mapeamento assumido:
    - DB1:  ip_from, ip_to, country_code, country_name
    - DB5+: ip_from, ip_to, country_code, country_name, region, city, lat, lon, ...
    """
    if len(columns) < 4:
        return None

    ip_from = clean_value(columns[0])
    ip_to = clean_value(columns[1])
    country_code = clean_value(columns[2])
    country_name = clean_value(columns[3])
    region = clean_value(columns[4]) if len(columns) > 4 else ""
    city = clean_value(columns[5]) if len(columns) > 5 else ""

    # Nos DBs com geodados, latitude/longitude aparecem a partir dessas posições.
    latitude = parse_float(clean_value(columns[6])) if len(columns) > 6 else None
    longitude = parse_float(clean_value(columns[7])) if len(columns) > 7 else None

    if not ip_from or not ip_to:
        return None

    return [
        ip_from,
        ip_to,
        f"{latitude:.6f}" if latitude is not None else "",
        f"{longitude:.6f}" if longitude is not None else "",
        country_name,
        country_code,
        region,
        city,
    ]


def normalize_ip2location_csv(csv_bytes: bytes, output_file: Path) -> tuple[int, int]:
    """Converte CSV IP2Location para o schema padrão do RMIFO."""
    output_file.parent.mkdir(parents=True, exist_ok=True)

    decoded = csv_bytes.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(decoded))

    total = 0
    written = 0

    with output_file.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "ip_range_start",
                "ip_range_end",
                "latitude",
                "longitude",
                "country",
                "country_code",
                "region",
                "city",
            ]
        )

        for row in reader:
            total += 1
            normalized = normalize_row(row)
            if normalized is None:
                continue
            writer.writerow(normalized)
            written += 1

    return total, written


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Baixa e converte GeoIP IP2Location LITE para data/external/geoip_database.csv"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Arquivo CSV de saída (padrão: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--source-url",
        type=str,
        default=PUBLIC_DB1_ZIP_URL,
        help="URL de ZIP para download direto (padrão: DB1 público).",
    )
    parser.add_argument(
        "--download-token",
        type=str,
        default=None,
        help="Token de download da conta IP2Location (opcional).",
    )
    parser.add_argument(
        "--download-code",
        type=str,
        default=None,
        help="Código/arquivo de download informado na conta IP2Location (opcional).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Timeout em segundos para download (padrão: 60).",
    )
    return parser


def resolve_download_url(args: argparse.Namespace) -> str:
    """Resolve URL final de download conforme o modo selecionado."""
    if args.download_token or args.download_code:
        if not args.download_token or not args.download_code:
            raise ValueError(
                "Para download autenticado, informe ambos: --download-token e --download-code."
            )
        return build_auth_url(args.download_token, args.download_code)

    return args.source_url


def main() -> int:
    args = build_parser().parse_args()

    try:
        url = resolve_download_url(args)
        print(f"Baixando: {url}")
        blob = download_bytes(url, timeout=args.timeout)

        csv_name, csv_bytes = find_csv_in_zip(blob)
        print(f"CSV encontrado no ZIP: {csv_name}")

        total_rows, written_rows = normalize_ip2location_csv(csv_bytes, args.output)

        print("\nConversão concluída.")
        print(f"Arquivo de saída: {args.output}")
        print(f"Linhas lidas: {total_rows}")
        print(f"Linhas gravadas: {written_rows}")
        return 0
    except Exception as exc:  # pragma: no cover
        print(f"Erro na exportação GeoIP: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
