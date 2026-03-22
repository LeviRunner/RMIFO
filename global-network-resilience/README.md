# RMIFO - Global Network Resilience

Projeto para análise de resiliência de redes intercontinentais com foco em falhas de conectividade.

## Execução Rápida no Terminal (dados públicos)

```bash
pip install -r requirements.txt
chmod +x terminal_public_demo.py
./terminal_public_demo.py --max-routes 30000 --cuts 5
```

Saídas:
- relatório com métricas no terminal;
- arquivo JSON em `results/reports/terminal_public_demo_summary.json`.

## Modo Interativo de Comparação

Use o menu interativo para comparar datasets `routes.dat` e `airports.dat`:

```bash
./terminal_public_demo.py --interactive --max-routes 30000 --cuts 5
```

Menu disponível:
- `1`: roda apenas o dataset padrão em `data/public_cache`;
- `2`: compara `data/public_cache` vs outro dataset informado por caminho;
- `3`: compara dois datasets customizados (A vs B);
- `4`: compara dois continentes por número (ex: 02=America vs 05=Asia);
- `5`: analisa um único continente por número (ex: 01=Africa).

No final, o terminal imprime o comparativo (delta de nós, arestas, maior componente e latência) e salva em JSON.

## Separação por Continente (airports/routes)

Para facilitar comparação de dados e medições de resiliência, execute:

```bash
python scripts/split_by_continent.py
```

Isso gera arquivos enumerados em `data/public_cache/continent_split`:
- `airports/01_africa_airports.dat`, `airports/02_america_airports.dat`, ...
- `routes_from/01_africa_routes_from.dat`, `routes_from/02_america_routes_from.dat`, ...
- `routes_pair/01_africa_to_africa_routes.dat`, ...

Índices para comparação:
- `data/public_cache/continent_split/airports_index.csv`
- `data/public_cache/continent_split/routes_from_index.csv`
- `data/public_cache/continent_split/routes_pair_index.csv`
- `data/public_cache/continent_split/summary.json`

## Pipeline Completo (Spark)

```bash
python main.py
```

Documentação detalhada: `README_USAGE.md`.
