#!/usr/bin/env python3
"""CLI de demonstração RMIFO usando dados públicos no terminal.

Baixa dados OpenFlights (rotas e aeroportos), monta um grafo e calcula
métricas de resiliência sem necessidade de Spark em nuvem.
"""

from __future__ import annotations

import argparse
import csv
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
) -> Tuple[list, list]:
    """Carrega dados de rotas/aeroportos a partir de arquivos locais."""
    if not routes_path.exists():
        raise FileNotFoundError(f"Arquivo de rotas não encontrado: {routes_path}")
    if not airports_path.exists():
        raise FileNotFoundError(f"Arquivo de aeroportos não encontrado: {airports_path}")

    # Lê rotas com CSV simples
    routes_rows = []
    with routes_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        for idx, row in enumerate(reader):
            if idx >= max_routes:
                break
            if len(row) >= 6 and row[3] and row[5] and row[3] != "\\N" and row[5] != "\\N":
                routes_rows.append(row)

    # Lê aeroportos com CSV simples
    airports_rows = []
    with airports_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) >= 8 and row[0] and row[0] != "\\N":
                airports_rows.append(row)

    # Retorna tupla compatível com o resto da função
    class RelationWrapper:
        def __init__(self, rows):
            self._rows = rows
        def fetchall(self):
            return self._rows

    return RelationWrapper(routes_rows), RelationWrapper(airports_rows)


def ensure_public_cache(cache_dir: Path) -> Tuple[Path, Path]:
    """Garante arquivos públicos no cache local e retorna seus caminhos."""
    routes_path = download_file(PUBLIC_ROUTES_URL, cache_dir / "routes.dat")
    airports_path = download_file(PUBLIC_AIRPORTS_URL, cache_dir / "airports.dat")
    return routes_path, airports_path


def extract_routes_columns(all_routes_rows: List[any]) -> List[Tuple[str, str, str, str]]:
    """Extrai coluna de rotas (col 0, 3, 5, 8) de tuplas completas do DuckDB."""
    extracted = []
    for row in all_routes_rows:
        if len(row) > 8:
            extracted.append((str(row[0]) if row[0] else "", str(row[3]) if row[3] else "",str(row[5]) if row[5] else "", str(row[8]) if row[8] else ""))
        else:
            # Fallback para casos com menos colunas
            extracted.append((str(row[0]) if len(row) > 0 and row[0] else "",
                           str(row[3]) if len(row) > 3 and row[3] else "",
                           str(row[5]) if len(row) > 5 and row[5] else "",
                           str(row[8]) if len(row) > 8 and row[8] else ""))
    return extracted


def build_graph(routes_rows: List[Tuple[str, str, str, str]]) -> nx.Graph:
    """Constroi grafo não-direcionado a partir das rotas."""
    graph = nx.Graph()

    for row in routes_rows:
        # Tenta acessar com os índices do OpenFlights (3 e 5)
        src = None
        dst = None
        if len(row) > 5:
            src = row[3].strip() if row[3] else None
            dst = row[5].strip() if row[5] else None
        # Fallback para índices alternativos se necessário
        if not src or not dst:
            continue
        if src == "\\N" or dst == "\\N":
            continue
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
) -> Dict[str, float]:
    """Estima latência detalhada por rota assumindo propagação em fibra (200.000 km/s)."""
    airport_coords: Dict[str, Tuple[float, float]] = {}
    for row in airports_rows:
        airport_id = row[0].strip() if len(row) > 0 else None
        if not airport_id or airport_id == "\\N":
            continue
        try:
            lat = float(row[6].strip()) if len(row) > 6 and row[6] else None
            lon = float(row[7].strip()) if len(row) > 7 and row[7] else None
            if lat is not None and lon is not None:
                airport_coords[airport_id] = (lat, lon)
        except (ValueError, IndexError, AttributeError):
            continue

    joined: List[Tuple[str, str, float, float, float, float]] = []
    for row in routes_rows:
        src = row[3].strip() if len(row) > 3 else None
        dst = row[5].strip() if len(row) > 5 else None
        if not src or not dst or src == "\\N" or dst == "\\N":
            continue
        src_coords = airport_coords.get(src)
        dst_coords = airport_coords.get(dst)
        if src_coords is None or dst_coords is None:
            continue
        joined.append((src, dst, src_coords[0], src_coords[1], dst_coords[0], dst_coords[1]))

    if not joined:
        return {"avg_rtt_ms": 0.0, "avg_one_way_ms": 0.0, "avg_distance_km": 0.0, "min_rtt_ms": 0.0, "max_rtt_ms": 0.0}

    random.seed(42)
    sample = random.sample(joined, min(sample_size, len(joined)))

    # Velocidade na fibra: 200.000 km/s (2/3 velocidade luz no vácuo)
    distances = []
    one_way_latencies = []
    rtt_latencies = []
    
    for row in sample:
        distance = haversine_km(row[2], row[3], row[4], row[5])
        distances.append(distance)
        one_way_ms = (distance / 200000.0) * 1000.0
        rtt_ms = one_way_ms * 2.0
        one_way_latencies.append(one_way_ms)
        rtt_latencies.append(rtt_ms)

    avg_distance = sum(distances) / len(distances) if distances else 0.0
    avg_one_way = sum(one_way_latencies) / len(one_way_latencies) if one_way_latencies else 0.0
    avg_rtt = sum(rtt_latencies) / len(rtt_latencies) if rtt_latencies else 0.0
    min_rtt = min(rtt_latencies) if rtt_latencies else 0.0
    max_rtt = max(rtt_latencies) if rtt_latencies else 0.0

    return {
        "avg_rtt_ms": avg_rtt,
        "avg_one_way_ms": avg_one_way,
        "avg_distance_km": avg_distance,
        "min_rtt_ms": min_rtt,
        "max_rtt_ms": max_rtt,
    }


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
    latency_stats: Dict[str, float],
    top_nodes: List[Tuple[str, int]],
    cuts: List[Dict[str, float]],
) -> None:
    """Imprime relatório final no terminal com explicações de latência."""
    print("=" * 72)
    print("RMIFO TERMINAL DEMO | Dados Publicos OpenFlights")
    print("=" * 72)
    print(f"Nos: {summary['nodes']}")
    print(f"Arestas: {summary['edges']}")
    print(f"Densidade: {summary['density']:.6f}")
    print(f"Grau medio: {summary['avg_degree']:.2f}")
    print(f"Maior componente: {summary['largest_component_ratio'] * 100:.2f}%")
    
    print("\nLatencia de Propagacao (Fibra Optica @ 200.000 km/s):")
    print(f"  Distancia media entre rotas: {latency_stats['avg_distance_km']:.0f} km")
    print(f"  Latencia one-way (ida): {latency_stats['avg_one_way_ms']:.2f} ms")
    print(f"  Latencia RTT (ida+volta): {latency_stats['avg_rtt_ms']:.2f} ms")
    print(f"  Range RTT: {latency_stats['min_rtt_ms']:.2f} - {latency_stats['max_rtt_ms']:.2f} ms")

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
    latency_stats = estimate_route_latency_ms(routes_rows, airports_rows)
    top_nodes = sorted(graph.degree, key=lambda item: item[1], reverse=True)[:10]
    cuts_result = simulate_targeted_cuts(graph, cuts=cuts)

    return {
        "dataset_name": dataset_name,
        "paths": {
            "routes": str(routes_path),
            "airports": str(airports_path),
        },
        "summary": summary,
        "latency_stats": latency_stats,
        "top_nodes": [{"node": n, "degree": d} for n, d in top_nodes],
        "cuts": cuts_result,
    }


def print_comparison(base: Dict[str, object], other: Dict[str, object]) -> None:
    """Imprime comparação entre dois datasets com deltas principais."""
    base_summary = base["summary"]
    other_summary = other["summary"]
    base_latency = float(base["latency_stats"]["avg_rtt_ms"])
    other_latency = float(other["latency_stats"]["avg_rtt_ms"])

    delta_nodes = int(other_summary["nodes"] - base_summary["nodes"])
    delta_edges = int(other_summary["edges"] - base_summary["edges"])
    delta_lcc = (other_summary["largest_component_ratio"] - base_summary["largest_component_ratio"]) * 100.0
    delta_latency = other_latency - base_latency
    
    latency_direction = "mais lento" if delta_latency > 0 else "mais rapido" if delta_latency < 0 else "igual"

    print("\n" + "=" * 72)
    print(f"COMPARACAO: {base['dataset_name']} VS {other['dataset_name']}")
    print("=" * 72)
    print(f"Delta nos: {delta_nodes:+d}")
    print(f"Delta arestas: {delta_edges:+d}")
    print(f"Delta maior componente: {delta_lcc:+.2f} p.p.")
    print(f"Delta latencia media (RTT): {delta_latency:+.2f} ms ({latency_direction})")


def prompt_path(message: str, default: Path) -> Path:
    """Lê caminho interativo com valor default."""
    typed = input(f"{message} [{default}]: ").strip()
    return Path(typed) if typed else default


def load_continent_index(split_dir: Path) -> Dict[str, Dict[str, str]]:
    """Carrega índice de continentes (airports e routes) para seleção rápida."""
    airports_index_path = split_dir / "airports_index.csv"
    routes_from_index_path = split_dir / "routes_from_index.csv"

    continent_map: Dict[str, Dict[str, str]] = {}

    if airports_index_path.exists():
        with airports_index_path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                idx = row["index"]
                continent = row["continent"]
                file_name = row["file_name"]
                continent_map[idx] = {
                    "continent": continent,
                    "airports_file": str(split_dir / "airports" / file_name),
                    "routes_from_file": None,
                }

    if routes_from_index_path.exists():
        with routes_from_index_path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                idx = row["index"]
                file_name = row["file_name"]
                if idx in continent_map:
                    continent_map[idx]["routes_from_file"] = str(split_dir / "routes_from" / file_name)

    return continent_map


def print_continent_options(continent_map: Dict[str, Dict[str, str]]) -> None:
    """Imprime opções de continentes disponíveis."""
    print("\nContinentes disponíveis:")
    for idx in sorted(continent_map.keys(), key=lambda x: int(x)):
        record = continent_map[idx]
        print(f"  [{idx}] {record['continent']}")


def select_continent(continent_map: Dict[str, Dict[str, str]], prompt_msg: str) -> Dict[str, str] | None:
    """Seleciona continente por número (aceita com ou sem leading zero)."""
    typed = input(f"{prompt_msg}: ").strip()
    
    # Tenta encontrar por índice exato ou por índice com leading zero removido
    if typed in continent_map:
        return continent_map[typed]
    
    # Se não encontrou, tenta remover leading zeros ou adicionar
    typed_normalized = str(int(typed)) if typed.isdigit() else typed
    for key in continent_map:
        if str(int(key)) == typed_normalized:
            return continent_map[key]
    
    print("Opcao invalida.")
    return None



def run_interactive_menu(args: argparse.Namespace) -> None:
    """Executa menu interativo para rodar análise simples ou comparativa."""
    cache_dir = Path(args.cache_dir)
    public_routes, public_airports = ensure_public_cache(cache_dir)
    split_dir = cache_dir / "continent_split"
    continent_map = {}
    if split_dir.exists():
        continent_map = load_continent_index(split_dir)

    print("=" * 72)
    print("RMIFO TERMINAL INTERATIVO")
    print("=" * 72)
    print("1) Rodar dataset publico (public_cache)")
    print("2) Comparar public_cache VS outro dataset")
    print("3) Comparar dois datasets customizados")
    if continent_map:
        print("4) Comparar continentes por numero (ex: 02 vs 05)")
        print("5) Rodar analise de um continente por numero")
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
        print_results(result["summary"], result["latency_stats"], [(n["node"], n["degree"]) for n in result["top_nodes"]], result["cuts"])
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
        print_results(base["summary"], base["latency_stats"], [(n["node"], n["degree"]) for n in base["top_nodes"]], base["cuts"])
        print("\n[Comparado] dataset_B")
        print_results(other["summary"], other["latency_stats"], [(n["node"], n["degree"]) for n in other["top_nodes"]], other["cuts"])
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
        print_results(dataset_a["summary"], dataset_a["latency_stats"], [(n["node"], n["degree"]) for n in dataset_a["top_nodes"]], dataset_a["cuts"])
        print("\n[Dataset B]")
        print_results(dataset_b["summary"], dataset_b["latency_stats"], [(n["node"], n["degree"]) for n in dataset_b["top_nodes"]], dataset_b["cuts"])
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
    elif choice == "4" and continent_map:
        print_continent_options(continent_map)
        cont_a = select_continent(continent_map, "Continente A (numero)")
        if not cont_a:
            return
        cont_b = select_continent(continent_map, "Continente B (numero)")
        if not cont_b:
            return

        name_a = cont_a["continent"]
        name_b = cont_b["continent"]
        airports_file_a = Path(cont_a["airports_file"])
        routes_file_a = Path(cont_a["routes_from_file"]) if cont_a["routes_from_file"] else None
        airports_file_b = Path(cont_b["airports_file"])
        routes_file_b = Path(cont_b["routes_from_file"]) if cont_b["routes_from_file"] else None

        if routes_file_a is None or routes_file_b is None:
            print("Erro: arquivos de rotas nao encontrados para os continentes selecionados.")
            return

        dataset_a = run_analysis(name_a, routes_file_a, airports_file_a, args.max_routes, args.cuts)
        dataset_b = run_analysis(name_b, routes_file_b, airports_file_b, args.max_routes, args.cuts)

        print(f"\n[{name_a}]")
        print_results(dataset_a["summary"], dataset_a["latency_stats"], [(n["node"], n["degree"]) for n in dataset_a["top_nodes"]], dataset_a["cuts"])
        print(f"\n[{name_b}]")
        print_results(dataset_b["summary"], dataset_b["latency_stats"], [(n["node"], n["degree"]) for n in dataset_b["top_nodes"]], dataset_b["cuts"])
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
    elif choice == "5" and continent_map:
        print_continent_options(continent_map)
        selected = select_continent(continent_map, "Continente (numero)")
        if not selected:
            return

        name = selected["continent"]
        airports_file = Path(selected["airports_file"])
        routes_file = Path(selected["routes_from_file"]) if selected["routes_from_file"] else None

        if routes_file is None:
            print("Erro: arquivo de rotas nao encontrado para este continente.")
            return

        result = run_analysis(name, routes_file, airports_file, args.max_routes, args.cuts)
        print_results(result["summary"], result["latency_stats"], [(n["node"], n["degree"]) for n in result["top_nodes"]], result["cuts"])
        payload = {
            "mode": "single",
            "result": result,
            "params": {"max_routes": args.max_routes, "cuts": args.cuts},
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
    print_results(run["summary"], run["latency_stats"], top_nodes_tuples, run["cuts"])

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
