# RMIFO - Resilience of Intercontinental Fiber Optic Networks

## Descrição

Motor de simulação **modularizado e escalável** em **PySpark + GraphFrames** para análise de resiliência de redes de fibra ótica intercontinentais.

### Objetivo
Identificar e quantificar pontos críticos de falha na infraestrutura física da Internet Global, cruzando:
- **Topologia Lógica**: CAIDA ITDK (datasets de roteamento)
- **Infraestrutura Física**: Cabos submarinos (TeleGeography)
- **Geolocalização**: Mapeamento IP-Geografia

### Resultado
Score de criticidade para cada nó/aresta que mensura o impacto sistêmico se interrompidos (latência, throughput, disponibilidade).

---

## Estrutura Modularizada

```
src/
 config.py                 #  Gerenciamento centralizado de configurações
 data_ingestion.py        #  Carregamento de dados (CAIDA, cabos, GeoIP)
 graph_processor.py       #  Processamento de grafos com GraphFrames
 failure_simulator.py     #  Simulação de falhas de nós/arestas
 utils.py                 #  Funções auxiliares (logging, export, etc)
 __init__.py             #  Exports do pacote

main.py                      #  Orquestrador do pipeline completo
config/main_config.yaml      #  Configurações centralizadas
```

### Módulos & Responsabilidades

#### 1. **config.py** - Configuração Centralizada
Gerencia todas as configurações via dataclasses type-safe:
```python
from src import get_config

config = get_config("config/main_config.yaml")
config.spark_config.memory  # "4g"
config.graph_config.pagerank_iterations  # 20
config.analysis_config.criticality_threshold  # 0.7
```

#### 2. **data_ingestion.py** - Carregamento de Dados
Interface unificada para múltiplos formatos:
```python
from src import DataIngestManager

data_mgr = DataIngestManager(spark, config)

# Carrega dados reais ou de exemplo
datasets = data_mgr.load_all_data(use_samples=True)
topology_df = datasets['topology']
cables_df = datasets['cables']
geoip_df = datasets['geoip']
```

**Loaders Disponíveis**:
- `CAIDADataLoader`: Topologia CAIDA (parquet, CSV, JSON)
- `SubmarineCablesLoader`: Cabos submarinos (GeoJSON, CSV, Parquet)
- `GeoIPDataLoader`: Dados de geolocalização IP

#### 3. **graph_processor.py** - Processamento de Grafos
Análise escalável com GraphFrames:
```python
from src import GraphProcessor

processor = GraphProcessor(spark, config)

# Constrói grafo
graph = processor.create_network_graph(topology_df, cables_df)

# Análise completa
results = processor.analyze_network(graph)
# Retorna: basic_metrics, pagerank, components, critical_nodes
```

**Algoritmos**:
- PageRank (importância de nós)
- Componentes Conectadas (conectividade)
- Caminhos Mais Curtos
- Contagem de Triângulos
- Betweenness Centrality (aproximado)

#### 4. **failure_simulator.py** - Simulação de Falhas
Simula impacto de falhas em nós/arestas:
```python
from src import FailureSimulator

simulator = FailureSimulator(spark, config)
simulator.set_base_graph(graph)

# Simula falha de um nó
degraded_graph, impact = simulator.simulate_node_failure(node_id=42)

# Simula N falhas aleatórias
impacts = simulator.batch_simulate_random_failures(
    num_scenarios=100,
    failure_type="node"  # ou "edge"
)

# Impactos quantificados
impact.connectivity_loss      # % de nós desconectados
impact.avg_latency_increase   # % aumento de latência
impact.components_created     # Novas componentes desconectadas
```

#### 5. **utils.py** - Utilitários
Funções comuns:
```python
from src import setup_logging, Timer, export_dataframe_to_parquet

setup_logging("rmifo.log")

with Timer("Processamento"):
    # ... código ...

export_dataframe_to_parquet(df, "results/output")
```

---

## Quick Start

### 1. Instalação
```bash
# Clone o repositório
cd global-network-resilience

# Instale dependências (com Conda recomendado)
pip install -r requirements.txt
```

### 2. Configurar dados (opcional)
```bash
# Se usar dados REAIS (não inclusos):
# 1. Baixe CAIDA ITDK → data/raw/
# 2. Baixe cabos submarinos → data/external/
# 3. Altere "use_samples=False" em main.py
```

### 3. Executar Pipeline Completo
```bash
cd global-network-resilience
python main.py
```

**Saída esperada:**
```
 Pipeline RMIFO inicializado
 SparkSession criado
 Topologia carregada: 100 nós (dados de exemplo)
 Cabos carregados: 50 cabos
 Grafo construído: 100 vértices, 50 arestas
 Análise de rede completa!
 10 simulações de nó completadas
 10 simulações de aresta completadas
 Resultados exportados em results/
```

---

## Uso Avançado

### Exemplo 1: Análise Customizada
```python
from src import get_config, DataIngestManager, GraphProcessor
from pyspark.sql import SparkSession

# Inicializar
config = get_config("config/main_config.yaml")
spark = SparkSession.builder.appName("Custom").master("local[4]").getOrCreate()

# Carregar dados
data_mgr = DataIngestManager(spark, config)
topology = data_mgr.get_topology(use_sample=True)
cables = data_mgr.get_cables(use_sample=True)

# Criar grafo
processor = GraphProcessor(spark, config)
graph = processor.create_network_graph(topology, cables)

# Análise manual
metrics = processor.analyzer.get_basic_metrics(graph)
pagerank_df = processor.analyzer.compute_pagerank(graph, max_iterations=30)
critical_nodes = processor.criticality.identify_critical_nodes(graph, threshold=0.75)

critical_nodes.show(10)
```

### Exemplo 2: Simular Cenários Específicos
```python
from src import FailureSimulator

simulator = FailureSimulator(spark, config)
simulator.set_base_graph(graph)

# Simular falha do nó ID 5
degraded, impact = simulator.simulate_node_failure(5)
print(f"Conectividade perdida: {impact.connectivity_loss:.2f}%")
print(f"Latência aumentou: {impact.avg_latency_increase:.2f}%")

# Simular falha de múltiplas arestas
edge_pairs = [(1, 2), (3, 4), (5, 6)]
degraded2 = simulator.edge_simulator.simulate_multiple_edge_failures(graph, edge_pairs)
```

### Exemplo 3: Exportar Resultados
```python
from src import export_dataframe_to_parquet, export_results_to_json

# Exportar DataFrames
export_dataframe_to_parquet(pagerank_df, "results/pagerank")

# Exportar resultados complexos
results_dict = {
    'metrics': {
        'num_vertices': graph.vertices.count(),
        'num_edges': graph.edges.count()
    },
    'critical_nodes': critical_nodes_list
}
export_results_to_json(results_dict, "results/analysis.json")
```

---

## Configuração (main_config.yaml)

Arquivo YAML centralizado com todos os parâmetros:

```yaml
spark:
  app_name: "RMIFO-Network-Resilience"
  master: "local[*]"
  memory: "4g"
  cores: 4

data:
  base_path: "data"
  caida_format: "parquet"  # ou "csv", "json"
  cables_format: "geojson"

graph:
  pagerank_iterations: 20
  pagerank_reset_probability: 0.15
  enable_betweenness_centrality: true

simulation:
  max_simulations: 100
  seed: 42

analysis:
  criticality_threshold: 0.7
```

**Customizações comuns:**
- Aumentar `spark.memory` → Para datasets maiores
- Aumentar `graph.pagerank_iterations` → Para precisão melhor
- Reducer `simulation.max_simulations` → Para testes rápidos

---

## Extensibilidade
A estrutura modularizada permite:

###  Adicionar novo carregador de dados
```python
# em src/data_ingestion.py
class MyDataLoader:
    def load_my_format(self, path: str) -> DataFrame:
        # Implementação
        return df
```

###  Novo algoritmo de grafo
```python
# em src/graph_processor.py
def compute_my_metric(self, graph: GraphFrame) -> DataFrame:
    # Implementação com GraphFrames
    return results_df
```

###  Novo tipo de falha
```python
# em src/failure_simulator.py
class RegionalFailureSimulator:
    def simulate_region_failure(self, region_code: str) -> GraphFrame:
        # Implementação
        return degraded_graph
```

---

## Performance & Otimização

### Para dados GRANDES (>1TB):
```yaml
spark:
  master: "spark://spark-master:7077"  # Cluster Spark
  cores: 32
  memory: "64g"
  shuffle_partitions: 500

graph:
  cache_processed_graph: true
```

### Para testes RÁPIDOS:
```yaml
simulation:
  max_simulations: 10

graph:
  max_vertices: 1000
  max_edges: 5000
```

### Monitorar performance:
```python
from src import print_query_plan

print_query_plan(topology_df)  # Ver plano de execução Spark
```

---

## Documentação API

### ConfigManager
```python
from src import ConfigManager, get_config

# Opção 1: Com arquivo YAML
config = ConfigManager("config/main_config.yaml")

# Opção 2: Singleton global
config = get_config()

# Acesso aos configs
config.spark_config.cores
config.data_config.raw_dir
config.graph_config.pagerank_iterations
```

### DataIngestManager
```python
from src import DataIngestManager

mgr = DataIngestManager(spark, config)

# Carregar tudo
all_data = mgr.load_all_data(use_samples=False)

# Carregar parcialmente
topology = mgr.get_topology(use_sample=True)
cables = mgr.get_cables(use_sample=False)
geoip = mgr.get_geoip()

# Limpar cache
mgr.clear_cache()
```

### GraphProcessor
```python
from src import GraphProcessor

proc = GraphProcessor(spark, config)
graph = proc.create_network_graph(vertices, edges)
results = proc.analyze_network(graph)

# Acessar componentes
proc.analyzer.get_basic_metrics(graph)
proc.criticality.identify_critical_nodes(graph, threshold=0.7)
```

### FailureSimulator
```python
from src import FailureSimulator

sim = FailureSimulator(spark, config)
sim.set_base_graph(graph)

# Simular
degraded, impact = sim.simulate_node_failure(node_id)
impacts = sim.batch_simulate_random_failures(num_scenarios=100)
```

---

## Troubleshooting

###  "Nenhum módulo nomeado 'graphframes'"
```bash
# Instale via conda (recomendado)
conda install -c conda-forge graphframes
```

###  "SparkSession não inicializado"
```python
# Certifique-se de chamar antes de usar qualquer módulo
from pyspark.sql import SparkSession
spark = SparkSession.builder.appName("App").getOrCreate()
```

###  "Arquivo de dados não encontrado"
```python
# Use dados de exemplo primeiro
mgr.load_all_data(use_samples=True)

# Depois coloque/vincule dados reais em data/raw/ e data/external/
```

###  "Memory/Performance lento"
```yaml
# Reduza em config/main_config.yaml:
graph:
  max_vertices: 5000  # Limita dados
simulation:
  max_simulations: 5  # Menos cenários
```

---

## Dataset Esperado

### Topologia CAIDA (data/raw/)
Formato: Parquet, CSV ou JSON
```
node_id | node_name | latitude | longitude | country | asn
--------|-----------|----------|-----------|---------|----
0       | node_0    | -23.55   | -46.63    | BR      | 1234
1       | node_1    | 40.71    | -74.01    | US      | 5678
```

### Cabos Submarinos (data/external/)
Formato: GeoJSON ou CSV
```
cable_id | cable_name | source_node_id | destination_node_id | capacity_gbps | length_km
---------|------------|---------:|-------------|-----------|-------
0        | SAC        | 0       | 5           | 200       | 15000
1        | TAT        | 1       | 15          | 400       | 8000
```

### GeoIP (data/external/)
Formato: CSV
```
ip_start    | ip_end      | latitude | longitude | country
------------|-------------|----------|-----------|--------
1.0.0.0     | 1.0.0.255   | -25.27   | 133.78    | AU
```

---

## Roadmap

- [ ] Suporte a falhas em cascata (com probabilidade)
- [ ] Simulação Monte Carlo de resiliência
- [ ] Dashboard via Streamlit/Dash
- [ ] Exportar para Gephi/Cytoscape visualization
- [ ] ML: Prever falhas críticas por padrão
- [ ] API REST para queries em tempo real
- [ ] Integração com Kafka para streams

---

## Contribuições & Customizações

O código foi estruturado para **máxima extensibilidade**:

1. **Cada módulo é independente** → Pode ser usado isoladamente
2. **Interfaces claras** → Fácil adicionar novos loaders/algoritmos
3. **Type hints** → IDE-friendly e type-safe
4. **Logging centralizado** → Debug facilitado

Para adicionar funcionalidades:
1. Crie classe em módulo apropriado
2. Herde de classe base (se aplicável)
3. Siga padrão de logging/error handling
4. Atualize `src/__init__.py` com exports

---

## Licença & Autoria

**Projeto**: RMIFO - Resilience of Intercontinental Fiber Optic Networks
**Modificado**: 2024
**Framework**: Apache Spark + GraphFrames

---

## Contato & Suporte

Para dúvidas ou issues:
1. Verifique troubleshooting acima
2. Revise config/main_config.yaml
3. Ative DEBUG logging em utils.setup_logging()

---

**Última atualização**: 2024
**Versão**: 0.1.0
