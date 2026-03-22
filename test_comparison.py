#!/usr/bin/env python3
"""Script para testar comparação de continentes com latência melhorada."""

import sys
sys.path.insert(0, "/workspaces/RMIFO/global-network-resilience")

from pathlib import Path
from terminal_public_demo import (
    run_analysis,
    print_results,
    print_comparison,
)

# Caminhos dos arquivos de continentes
data_dir = Path("/workspaces/RMIFO/global-network-resilience/data/public_cache/continent_split")

# Testar rotas dentro de continentes
test_pairs = [
    ("01", "Africa", "africa", "01"),
    ("16", "Asia", "asia", "05"),
]

print("Testando comparação de continentes com latência melhorada...")
print("=" * 72)

results = {}
for code, name, slug, airports_code in test_pairs:
    routes_file = data_dir / "routes_pair" / f"{code}_{slug}_to_{slug}_routes.dat"
    airports_file = data_dir / "airports" / f"{airports_code}_{slug}_airports.dat"
    
    if not routes_file.exists() or not airports_file.exists():
        print(f"Arquivos não encontrados para {name}")
        print(f"Procurando em routes_pair: {routes_file.exists()}")
        print(f"Procurando em airports: {airports_file.exists()}")
        continue
    
    print(f"\nAnalisando {name}...")
    result = run_analysis(
        dataset_name=name,
        routes_path=routes_file,
        airports_path=airports_file,
        max_routes=5000,
        cuts=3,
    )
    results[name] = result
    print_results(
        result["summary"],
        result["latency_stats"],
        [(n["node"], n["degree"]) for n in result["top_nodes"]],
        result["cuts"],
    )

# Comparação
if len(results) == 2:
    names = sorted(results.keys())
    base = results[names[0]]
    other = results[names[1]]
    print("\n" + "=" * 72)
    print_comparison(base, other)
