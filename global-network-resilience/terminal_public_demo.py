#!/usr/bin/env python3
"""CLI de demonstração RMIFO usando dados públicos no terminal.

Baixa dados OpenFlights (rotas e aeroportos), monta um grafo e calcula
métricas de resiliência sem necessidade de Spark em nuvem.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import urllib.request
from pathlib import Path
from typing import Dict, List, Tuple

import duckdb
import networkx as nx

PUBLIC_ROUTES_URL = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/routes.dat"
PUBLIC_AIRPORTS_URL = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airports.dat"


def _escape_path_for_sql(path: Path) -> str:
    """Escapa caminho para uso seguro em SQL string literal."""
    return path.as_posix().replace("'", "''")


def download_file(url: str, destination: Path) -> Path:
    """Baixa um arquivo público para cache local, se necessário."""
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists() and destination.stat().st_size > 0:
        return destination

    with urllib.request.urlopen(url, timeout=60) as response:
        destination.write_bytes(response.read())

    return destination


def load_data_from_files(
    routes_path: Path,
    airports_path: Path,
    max_routes: int,
) -> Tuple[duckdb.DuckDBPyRelation, duckdb.DuckDBPyRelation]:
    """Carrega dados de rotas/aeroportos a partir de arquivos locais."""
    if not routes_path.exists():
        raise FileNotFoundError(f"Arquivo de rotas não encontrado: {routes_path}")
    if not airports_path.exists():
        raise FileNotFoundError(f"Arquivo de aeroportos não encontrado: {airports_path}")

    routes_sql_path = _escape_path_for_sql(routes_path)
    airports_sql_path = _escape_path_for_sql(airports_path)

    con = duckdb.connect(database=":memory:")

    routes = con.sql(
        f"""
        SELECT
            c0 AS airline,
            c3 AS source_airport_id,
            c5 AS destination_airport_id,
            c8 AS equipment
        FROM read_csv(
            '{routes_sql_path}',
            delim=',',
            quote='"',
            header=false,
            columns={{
                'c0': 'VARCHAR',
                'c1': 'VARCHAR',
                'c2': 'VARCHAR',
                'c3': 'VARCHAR',
                'c4': 'VARCHAR',
                'c5': 'VARCHAR',
                'c6': 'VARCHAR',
                'c7': 'VARCHAR',
                'c8': 'VARCHAR'
            }},
            ignore_errors=true
        )
        WHERE c3 IS NOT NULL
          AND c5 IS NOT NULL
          AND c3 != '\\N'
          AND c5 != '\\N'
        LIMIT {max_routes}
        """
    )

    airports = con.sql(
        f"""
        SELECT
            a0 AS airport_id,
            a1 AS airport_name,
            a2 AS city,
            a3 AS country,
            a4 AS iata,
            TRY_CAST(a6 AS DOUBLE) AS latitude,
            TRY_CAST(a7 AS DOUBLE) AS longitude
        FROM read_csv(
            '{airports_sql_path}',
            delim=',',
            quote='"',
            header=false,
            columns={{
                'a0': 'VARCHAR',
                'a1': 'VARCHAR',
                'a2': 'VARCHAR',
                'a3': 'VARCHAR',
                'a4': 'VARCHAR',
                'a5': 'VARCHAR',
                'a6': 'VARCHAR',
                'a7': 'VARCHAR',
                'a8': 'VARCHAR',
                'a9': 'VARCHAR',
                'a10': 'VARCHAR',
                'a11': 'VARCHAR',
                'a12': 'VARCHAR',
                'a13': 'VARCHAR'
            }},
            ignore_errors=true
        )
        WHERE a0 IS NOT NULL
          AND a0 != '\\N'
        """
    )

    return routes, airports


def ensure_public_cache(cache_dir: Path) -> Tuple[Path, Path]:
    """Garante arquivos públicos no cache local e retorna seus caminhos."""
    routes_path = download_file(PUBLIC_ROUTES_URL, cache_dir / "routes.dat")
    airports_path = download_file(PUBLIC_AIRPORTS_URL, cache_dir / "airports.dat")
    return routes_path, airports_path


def build_graph(routes_rows: List[Tuple[str, str, str, str]]) -> nx.Graph:
    """Constroi grafo não-direcionado a partir das rotas."""
    graph = nx.Graph()

    for row in routes_rows:
        src = row[1]
        dst = row[2]
        if src == dst:
            continue
        graph.add_edge(src, dst)

    return graph


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calcula distância geodésica aproximada em km."""
    r = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


def estimate_route_latency_ms(
    routes_rows: List[Tuple[str, str, str, str]],
    airports_rows: List[Tuple[str, str, str, str, str, float, float]],
    sample_size: int = 3000,
) -> float:
    """Estima latência média por rota assumindo propagação em fibra."""
    airport_coords = {
        row[0]: (row[5], row[6])
        for row in airports_rows
        if row[5] is not None and row[6] is not None
    }

    joined: List[Tuple[str, str, float, float, float, float]] = []
    for row in routes_rows:
        src = row[1]
        dst = row[2]
        src_coords = airport_coords.get(src)
        dst_coords = airport_coords.get(dst)
        if src_coords is None or dst_coords is None:
            continue
        joined.append((src, dst, src_coords[0], src_coords[1], dst_coords[0], dst_coords[1]))

    if not joined:
        return 0.0

    random.seed(42)
    sample = random.sample(joined, min(sample_size, len(joined)))

    # Velocidade aproximada na fibra: 200.000 km/s -> 5 ms por 1000 km (one-way)
    # RTT aproximado = ida e volta
    latencies = []
    for row in sample:
        distance = haversine_km(row[2], row[3], row[4], row[5])
        rtt_ms = (distance / 200000.0) * 1000.0 * 2.0
        latencies.append(rtt_ms)

    return sum(latencies) / len(latencies)


def summarize_graph(graph: nx.Graph) -> Dict[str, float]:
    """Calcula métricas principais de conectividade."""
    nodes = graph.number_of_nodes()
    edges = graph.number_of_edges()

    if nodes == 0:
        return {
            "nodes": 0,
            "edges": 0,
            "density": 0.0,
            "largest_component_ratio": 0.0,
            "avg_degree": 0.0,
        }

    largest_cc = max(nx.connected_components(graph), key=len)
    largest_ratio = len(largest_cc) / nodes
    avg_degree = (2 * edges) / nodes

    return {
        "nodes": nodes,
        "edges": edges,
        "density": nx.density(graph),
        "largest_component_ratio": largest_ratio,
        "avg_degree": avg_degree,
    }


def simulate_targeted_cuts(graph: nx.Graph, cuts: int) -> List[Dict[str, float]]:
    """Remove iterativamente nós mais centrais e mede impacto."""
    working_graph = graph.copy()
    baseline = summarize_graph(working_graph)
    results: List[Dict[str, float]] = []

    for step in range(1, cuts + 1):
        if working_graph.number_of_nodes() < 3:
            break

        centrality = nx.degree_centrality(working_graph)
        node_to_remove = max(centrality.items(), key=lambda item: item[1])[0]

        working_graph.remove_node(node_to_remove)
        current = summarize_graph(working_graph)

        loss = (baseline["largest_component_ratio"] - current["largest_component_ratio"]) * 100.0
        results.append(
            {
                "step": step,
                "removed_node": node_to_remove,
                "largest_component_ratio": current["largest_component_ratio"],
                "component_loss_pct": max(loss, 0.0),
                "remaining_nodes": current["nodes"],
                "remaining_edges": current["edges"],
            }
        )

    return results


def print_results(
    summary: Dict[str, float],
    avg_latency_ms: float,
    top_nodes: List[Tuple[str, int]],
    cuts: List[Dict[str, float]],
) -> None:
    """Imprime relatório final no terminal."""
    print("=" * 72)
    print("RMIFO TERMINAL DEMO | Dados Publicos OpenFlights")
    print("=" * 72)
    print(f"Nos: {summary['nodes']}")
    print(f"Arestas: {summary['edges']}")
    print(f"Densidade: {summary['density']:.6f}")
    print(f"Grau medio: {summary['avg_degree']:.2f}")
    print(f"Maior componente: {summary['largest_component_ratio'] * 100:.2f}%")
    print(f"Latencia media estimada (RTT): {avg_latency_ms:.2f} ms")

    print("\nTop 10 aeroportos por grau (proxy de criticidade):")
    for idx, (node, degree) in enumerate(top_nodes, start=1):
        print(f"{idx:>2}. airport_id={node:<6} grau={degree}")

    print("\nSimulacao de cortes direcionados (remoção de no critico):")
    if not cuts:
        print("Nenhum corte executado")
    for cut in cuts:
        print(
            f"passo={cut['step']} removido={cut['removed_node']} "
            f"maior_comp={cut['largest_component_ratio'] * 100:.2f}% "
            f"perda_comp={cut['component_loss_pct']:.2f}%"
        )


def run_analysis(
    dataset_name: str,
    routes_path: Path,
    airports_path: Path,
    max_routes: int,
    cuts: int,
) -> Dict[str, object]:
    """Executa análise completa para um dataset e retorna payload serializável."""
    routes, airports = load_data_from_files(routes_path, airports_path, max_routes=max_routes)
    routes_rows = routes.fetchall()
    airports_rows = airports.fetchall()

    graph = build_graph(routes_rows)
    summary = summarize_graph(graph)
    avg_latency_ms = estimate_route_latency_ms(routes_rows, airports_rows)
    top_nodes = sorted(graph.degree, key=lambda item: item[1], reverse=True)[:10]
    cuts_result = simulate_targeted_cuts(graph, cuts=cuts)

    return {
        "dataset_name": dataset_name,
        "paths": {
            "routes": str(routes_path),
            "airports": str(airports_path),
        },
        "summary": summary,
        "avg_latency_ms": avg_latency_ms,
        "top_nodes": [{"node": n, "degree": d} for n, d in top_nodes],
        "cuts": cuts_result,
    }


def print_comparison(base: Dict[str, object], other: Dict[str, object]) -> None:
    """Imprime comparação entre dois datasets com deltas principais."""
    base_summary = base["summary"]
    other_summary = other["summary"]
    base_latency = float(base["avg_latency_ms"])
    other_latency = float(other["avg_latency_ms"])

    delta_nodes = int(other_summary["nodes"] - base_summary["nodes"])
    delta_edges = int(other_summary["edges"] - base_summary["edges"])
    delta_lcc = (other_summary["largest_component_ratio"] - base_summary["largest_component_ratio"]) * 100.0
    delta_latency = other_latency - base_latency

    print("\n" + "=" * 72)
    print(f"COMPARACAO: {base['dataset_name']} VS {other['dataset_name']}")
    print("=" * 72)
    print(f"Delta nos: {delta_nodes:+d}")
    print(f"Delta arestas: {delta_edges:+d}")
    print(f"Delta maior componente: {delta_lcc:+.2f} p.p.")
    print(f"Delta latencia media (RTT): {delta_latency:+.2f} ms")


def prompt_path(message: str, default: Path) -> Path:
    """Lê caminho interativo com valor default."""
    typed = input(f"{message} [{default}]: ").strip()
    return Path(typed) if typed else default


def run_interactive_menu(args: argparse.Namespace) -> None:
    """Executa menu interativo para rodar análise simples ou comparativa."""
    cache_dir = Path(args.cache_dir)
    public_routes, public_airports = ensure_public_cache(cache_dir)

    print("=" * 72)
    print("RMIFO TERMINAL INTERATIVO")
    print("=" * 72)
    print("1) Rodar dataset publico (public_cache)")
    print("2) Comparar public_cache VS outro dataset")
    print("3) Comparar dois datasets customizados")
    print("0) Sair")

    choice = input("Escolha uma opcao: ").strip()
    if choice == "0":
        print("Encerrado.")
        return

    if choice == "1":
        result = run_analysis(
            dataset_name="public_cache",
            routes_path=public_routes,
            airports_path=public_airports,
            max_routes=args.max_routes,
            cuts=args.cuts,
        )
        print_results(result["summary"], result["avg_latency_ms"], [(n["node"], n["degree"]) for n in result["top_nodes"]], result["cuts"])
        payload = {
            "mode": "single",
            "result": result,
            "params": {
                "max_routes": args.max_routes,
                "cuts": args.cuts,
            },
        }
    elif choice == "2":
        other_routes = prompt_path("Digite o caminho de routes.dat do dataset B", public_routes)
        other_airports = prompt_path("Digite o caminho de airports.dat do dataset B", public_airports)

        base = run_analysis("public_cache", public_routes, public_airports, args.max_routes, args.cuts)
        other = run_analysis("dataset_B", other_routes, other_airports, args.max_routes, args.cuts)

        print("\n[Base] public_cache")
        print_results(base["summary"], base["avg_latency_ms"], [(n["node"], n["degree"]) for n in base["top_nodes"]], base["cuts"])
        print("\n[Comparado] dataset_B")
        print_results(other["summary"], other["avg_latency_ms"], [(n["node"], n["degree"]) for n in other["top_nodes"]], other["cuts"])
        print_comparison(base, other)

        payload = {
            "mode": "comparison",
            "base": base,
            "other": other,
            "params": {
                "max_routes": args.max_routes,
                "cuts": args.cuts,
            },
        }
    elif choice == "3":
        a_routes = prompt_path("Dataset A - caminho routes.dat", public_routes)
        a_airports = prompt_path("Dataset A - caminho airports.dat", public_airports)
        b_routes = prompt_path("Dataset B - caminho routes.dat", public_routes)
        b_airports = prompt_path("Dataset B - caminho airports.dat", public_airports)

        dataset_a = run_analysis("dataset_A", a_routes, a_airports, args.max_routes, args.cuts)
        dataset_b = run_analysis("dataset_B", b_routes, b_airports, args.max_routes, args.cuts)

        print("\n[Dataset A]")
        print_results(dataset_a["summary"], dataset_a["avg_latency_ms"], [(n["node"], n["degree"]) for n in dataset_a["top_nodes"]], dataset_a["cuts"])
        print("\n[Dataset B]")
        print_results(dataset_b["summary"], dataset_b["avg_latency_ms"], [(n["node"], n["degree"]) for n in dataset_b["top_nodes"]], dataset_b["cuts"])
        print_comparison(dataset_a, dataset_b)

        payload = {
            "mode": "comparison",
            "base": dataset_a,
            "other": dataset_b,
            "params": {
                "max_routes": args.max_routes,
                "cuts": args.cuts,
            },
        }
    else:
        print("Opcao invalida.")
        return

    output_path = Path(args.output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nResumo exportado para: {output_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Demo terminal RMIFO com dados publicos (OpenFlights)."
    )
    parser.add_argument(
        "--max-routes",
        type=int,
        default=30000,
        help="Limite de rotas para acelerar a execução local.",
    )
    parser.add_argument(
        "--cuts",
        type=int,
        default=5,
        help="Quantidade de cortes direcionados para simulação de falha.",
    )
    parser.add_argument(
        "--cache-dir",
        default="data/public_cache",
        help="Diretorio para cache de downloads publicos.",
    )
    parser.add_argument(
        "--output-json",
        default="results/reports/terminal_public_demo_summary.json",
        help="Arquivo JSON de saida com resumo da execução.",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Abre menu interativo para execução e comparação de datasets.",
    )
    parser.add_argument(
        "--routes-file",
        default="",
        help="Caminho local para routes.dat (modo não-interativo).",
    )
    parser.add_argument(
        "--airports-file",
        default="",
        help="Caminho local para airports.dat (modo não-interativo).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cache_dir = Path(args.cache_dir)

    if args.interactive:
        run_interactive_menu(args)
        return

    public_routes, public_airports = ensure_public_cache(cache_dir)

    routes_path = Path(args.routes_file) if args.routes_file else public_routes
    airports_path = Path(args.airports_file) if args.airports_file else public_airports

    run = run_analysis(
        dataset_name="single_run",
        routes_path=routes_path,
        airports_path=airports_path,
        max_routes=args.max_routes,
        cuts=args.cuts,
    )

    top_nodes_tuples = [(n["node"], n["degree"]) for n in run["top_nodes"]]
    print_results(run["summary"], run["avg_latency_ms"], top_nodes_tuples, run["cuts"])

    output_payload = {
        "mode": "single",
        "result": run,
        "params": {
            "max_routes": args.max_routes,
            "cuts": args.cuts,
            "cache_dir": str(cache_dir),
        },
    }

    output_path = Path(args.output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output_payload, indent=2), encoding="utf-8")

    print(f"\nResumo exportado para: {output_path}")


if __name__ == "__main__":
    main()
