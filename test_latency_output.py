#!/usr/bin/env python3
"""Script para testar a saída de latência melhorada do terminal."""

import sys
sys.path.insert(0, "/workspaces/RMIFO/global-network-resilience")

from pathlib import Path
from terminal_public_demo import (
    run_analysis,
    print_results,
)

# Caminhos dos arquivos públicos
data_dir = Path("/workspaces/RMIFO/global-network-resilience/data/public_cache")
public_routes = data_dir / "routes.dat"
public_airports = data_dir / "airports.dat"

print("Testando análise de latência com dados públicos...")
print("=" * 72)

result = run_analysis(
    dataset_name="public_cache_test",
    routes_path=public_routes,
    airports_path=public_airports,
    max_routes=10000,
    cuts=5,
)

print("\nSaída de latência melhorada:")
print_results(
    result["summary"],
    result["latency_stats"],
    [(n["node"], n["degree"]) for n in result["top_nodes"]],
    result["cuts"],
)

print("\n\nDetalhes da latência (estrutura retornada):")
print(f"  avg_rtt_ms: {result['latency_stats']['avg_rtt_ms']:.2f} ms")
print(f"  avg_one_way_ms: {result['latency_stats']['avg_one_way_ms']:.2f} ms")
print(f"  avg_distance_km: {result['latency_stats']['avg_distance_km']:.0f} km")
print(f"  min_rtt_ms: {result['latency_stats']['min_rtt_ms']:.2f} ms")
print(f"  max_rtt_ms: {result['latency_stats']['max_rtt_ms']:.2f} ms")
