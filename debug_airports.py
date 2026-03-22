#!/usr/bin/env python3
"""Script para debug de parsing do arquivo airports.dat"""

import csv
from pathlib import Path

airports_file = Path("/workspaces/RMIFO/global-network-resilience/data/public_cache/airports.dat")

with open(airports_file) as f:
    reader = csv.reader(f)
    for i, row in enumerate(reader):
        if i < 3:
            print(f"Row {i}: Total fields = {len(row)}")
            for j, field in enumerate(row):
                print(f"  [{j}] = {field[:50] if len(field) > 50 else field}")
        else:
            break
