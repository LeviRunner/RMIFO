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
- `3`: compara dois datasets customizados (A vs B).

No final, o terminal imprime o comparativo (delta de nós, arestas, maior componente e latência).

## Pipeline Completo (Spark)

```bash
python main.py
```

Documentação detalhada: `README_USAGE.md`.
