#!/usr/bin/env python3
"""
Exporta dados completos de cabos submarinos para um único arquivo JSON.

Fonte:
- https://www.submarinecablemap.com/api/v3/search.json
- https://www.submarinecablemap.com/api/v3/cable/<slug>.json
- https://www.submarinecablemap.com/api/v3/cable/cable-geo.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

BASE_URL = "https://www.submarinecablemap.com"
SEARCH_ENDPOINT = "/api/v3/search.json"
CABLE_DETAIL_TEMPLATE = "/api/v3/cable/{slug}.json"
CABLE_GEO_ENDPOINT = "/api/v3/cable/cable-geo.json"
DEFAULT_OUTPUT = Path("data/external/submarine_cables_full.json")


def fetch_json(url: str, timeout: int = 30) -> Any:
    """Faz GET em uma URL e retorna JSON parseado."""
    req = Request(
        url,
        headers={
            "User-Agent": "RMIFO-cable-exporter/1.0",
            "Accept": "application/json",
        },
    )
    with urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_slug_from_url(path_or_url: str) -> str:
    """Extrai slug do campo URL retornado por /search.json."""
    parsed = urlparse(path_or_url)
    path = parsed.path if parsed.scheme else path_or_url
    cleaned = path.strip("/")
    parts = cleaned.split("/")
    return parts[-1] if parts else ""


def is_cable_url(path_or_url: str) -> bool:
    """Valida se URL de busca representa uma página de cabo submarino."""
    parsed = urlparse(path_or_url)
    path = parsed.path if parsed.scheme else path_or_url
    return path.strip("/").startswith("submarine-cable/")


def build_geo_index(geo_data: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Indexa features por cable id para facilitar merge posterior."""
    index: Dict[str, List[Dict[str, Any]]] = {}

    for feature in geo_data.get("features", []):
        props = feature.get("properties", {})
        cable_id = props.get("id")
        if not cable_id:
            continue

        index.setdefault(cable_id, []).append(
            {
                "properties": props,
                "geometry": feature.get("geometry"),
            }
        )

    return index


def fetch_all_cables(limit: int | None = None, pause_seconds: float = 0.05) -> Dict[str, Any]:
    """Coleta dados de todos os cabos e combina detalhes + geometria."""
    search_url = f"{BASE_URL}{SEARCH_ENDPOINT}"
    geo_url = f"{BASE_URL}{CABLE_GEO_ENDPOINT}"

    search_items = fetch_json(search_url)
    geo_data = fetch_json(geo_url)
    geo_index = build_geo_index(geo_data)

    slugs: List[str] = []
    for item in search_items:
        raw_url = item.get("url", "")
        if not is_cable_url(raw_url):
            continue
        slug = parse_slug_from_url(raw_url)
        if slug:
            slugs.append(slug)

    # Remove duplicados preservando ordem.
    seen = set()
    unique_slugs = []
    for slug in slugs:
        if slug not in seen:
            seen.add(slug)
            unique_slugs.append(slug)

    if limit is not None:
        unique_slugs = unique_slugs[:limit]

    total = len(unique_slugs)
    cables: List[Dict[str, Any]] = []
    failures: List[Dict[str, str]] = []

    for i, slug in enumerate(unique_slugs, start=1):
        detail_url = f"{BASE_URL}{CABLE_DETAIL_TEMPLATE.format(slug=slug)}"
        try:
            detail = fetch_json(detail_url)
            cable_id = detail.get("id", slug)

            detail["geo_features"] = geo_index.get(cable_id, [])
            detail["source_url"] = detail_url

            cables.append(detail)
            print(f"[{i}/{total}] OK: {slug}")
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            failures.append({"slug": slug, "error": str(exc)})
            print(f"[{i}/{total}] FALHA: {slug} -> {exc}", file=sys.stderr)

        # Pausa curta para reduzir chance de rate-limit.
        if pause_seconds > 0:
            time.sleep(pause_seconds)

    return {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_base_url": BASE_URL,
            "total_cables_requested": total,
            "total_cables_exported": len(cables),
            "total_failures": len(failures),
            "search_endpoint": f"{BASE_URL}{SEARCH_ENDPOINT}",
            "geo_endpoint": f"{BASE_URL}{CABLE_GEO_ENDPOINT}",
        },
        "cables": cables,
        "failures": failures,
    }


def save_output(payload: Dict[str, Any], output_file: Path) -> None:
    """Salva payload final em JSON formatado."""
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Baixa e exporta todos os dados de cabos submarinos em um único JSON.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Arquivo de saída (padrão: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limita a quantidade de cabos (útil para teste rápido).",
    )
    parser.add_argument(
        "--pause-seconds",
        type=float,
        default=0.05,
        help="Pausa entre requisições para evitar bloqueio (padrão: 0.05).",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    try:
        payload = fetch_all_cables(limit=args.limit, pause_seconds=args.pause_seconds)
        save_output(payload, args.output)
    except Exception as exc:  # pragma: no cover
        print(f"Erro ao exportar dados de cabos: {exc}", file=sys.stderr)
        return 1

    meta = payload["metadata"]
    print("\nExportação concluída.")
    print(f"Arquivo: {args.output}")
    print(f"Cabos exportados: {meta['total_cables_exported']}")
    print(f"Falhas: {meta['total_failures']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
