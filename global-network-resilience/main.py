"""
Script Principal - RMIFO
Orquestra todo o pipeline de análise de resiliência de rede.
"""

from pyspark.sql import SparkSession
from pathlib import Path
import logging
from typing import Dict, Any

# Importa módulos do projeto
from src import (
    get_config, DataIngestManager, GraphProcessor, FailureSimulator,
    setup_logging, get_logger, Timer, generate_report_header,
    export_results_to_json, export_dataframe_to_parquet
)


class RMIFOPipeline:
    """Orquestrador principal do pipeline RMIFO."""
    
    def __init__(self, config_file: str = None, use_samples: bool = True):
        """
        Inicializa o pipeline.
        
        Args:
            config_file: Caminho para arquivo YAML de configuração
            use_samples: Se True, usa dados de exemplo ao invés de reais
        """
        self.logger = logging.getLogger(__name__)
        
        # Carrega configuração
        self.config = get_config(config_file)
        self.use_samples = use_samples
        
        # Inicializa Spark
        self.spark = self._initialize_spark()
        
        # Inicializa componentes
        self.data_manager = DataIngestManager(self.spark, self.config)
        self.graph_processor = GraphProcessor(self.spark, self.config)
        self.failure_simulator = FailureSimulator(self.spark, self.config)
        
        self.results = {}
        
        self.logger.info("✓ Pipeline RMIFO inicializado")
    
    def _initialize_spark(self) -> SparkSession:
        """Inicializa SparkSession com configurações."""
        spark_config = self.config.spark_config
        
        builder = SparkSession.builder \
            .appName(spark_config.app_name) \
            .master(spark_config.master) \
            .config("spark.driver.memory", spark_config.memory) \
            .config("spark.sql.shuffle.partitions", spark_config.shuffle_partitions)
        
        # Adiciona GraphFrames (se necessário)
        try:
            builder = builder.config("spark.jars.packages",
                                   "graphframes:graphframes:0.8.2-spark3.2-s_2.12")
        except:
            self.logger.warning("⚠️ GraphFrames pode não estar instalado")
        
        spark = builder.getOrCreate()
        self.logger.info(f"✓ SparkSession criado: {spark_config.app_name}")
        
        return spark
    
    def stage_1_load_data(self) -> Dict[str, Any]:
        """
        Etapa 1: Carrega dados de topologia, cabos e geolocalização.
        """
        self.logger.info(generate_report_header("ETAPA 1: CARREGAMENTO DE DADOS"))
        
        with Timer("Carregamento de dados"):
            data = self.data_manager.load_all_data(use_samples=self.use_samples)
        
        self.results['data'] = data
        
        self.logger.info(f"✓ Topologia: {data['topology'].count()} nós")
        self.logger.info(f"✓ Cabos: {data['cables'].count()} cabos")
        if data['geoip']:
            self.logger.info(f"✓ GeoIP: {data['geoip'].count()} registros")
        
        return data
    
    def stage_2_build_graph(self, topology_df, cables_df) -> Any:
        """
        Etapa 2: Constrói grafo de topologia usando GraphFrames.
        """
        self.logger.info(generate_report_header("ETAPA 2: CONSTRUÇÃO DO GRAFO"))
        
        with Timer("Construção do grafo"):
            graph = self.graph_processor.create_network_graph(topology_df, cables_df)
        
        self.results['graph'] = graph
        
        return graph
    
    def stage_3_analyze_network(self, graph) -> Dict[str, Any]:
        """
        Etapa 3: Analisa propriedades da rede e identifica nós críticos.
        """
        self.logger.info(generate_report_header("ETAPA 3: ANÁLISE DE REDE"))
        
        with Timer("Análise completa de rede"):
            analysis_results = self.graph_processor.analyze_network(graph)
        
        self.results['analysis'] = analysis_results
        
        # Log dos resultados
        metrics = analysis_results['basic_metrics']
        self.logger.info(f"\n📊 Métricas da Rede:")
        self.logger.info(f"  • Vértices: {metrics['num_vertices']}")
        self.logger.info(f"  • Arestas: {metrics['num_edges']}")
        self.logger.info(f"  • Grau médio: {metrics['avg_in_degree']:.2f}")
        self.logger.info(f"  • Densidade: {metrics['density']:.4f}")
        
        critical_nodes = analysis_results['critical_nodes'].collect()
        self.logger.info(f"\n🎯 Nós Críticos Identificados: {len(critical_nodes)}")
        for row in critical_nodes[:5]:  # Top 5
            self.logger.info(f"  • Nó {row.id}: PageRank = {row.pagerank:.4f}")
        
        return analysis_results
    
    def stage_4_simulate_failures(self, graph, num_scenarios: int = 10) -> Dict[str, Any]:
        """
        Etapa 4: Simula falhas e quantifica impactos.
        
        Args:
            graph: GraphFrame para simular falhas
            num_scenarios: Número de cenários a simular
        """
        self.logger.info(generate_report_header("ETAPA 4: SIMULAÇÃO DE FALHAS"))
        
        self.failure_simulator.set_base_graph(graph)
        
        # Simula falhas de nós
        with Timer(f"Simulação de {num_scenarios} falhas de nó"):
            node_impacts = self.failure_simulator.batch_simulate_random_failures(
                num_scenarios=num_scenarios,
                failure_type="node"
            )
        
        # Simula falhas de arestas
        with Timer(f"Simulação de {num_scenarios} falhas de aresta"):
            edge_impacts = self.failure_simulator.batch_simulate_random_failures(
                num_scenarios=num_scenarios,
                failure_type="edge"
            )
        
        self.results['simulation'] = {
            'node_impacts': node_impacts,
            'edge_impacts': edge_impacts
        }
        
        # Análise de impactos
        self.logger.info(f"\n📊 Análise de Impactos:")
        self.logger.info(f"  • Falhas de Nó ({len(node_impacts)} cenários)")
        
        if node_impacts:
            avg_connectivity_loss = sum(i.connectivity_loss for i in node_impacts) / len(node_impacts)
            avg_latency = sum(i.avg_latency_increase for i in node_impacts) / len(node_impacts)
            self.logger.info(f"    - Perda de conectividade média: {avg_connectivity_loss:.2f}%")
            self.logger.info(f"    - Aumento de latência médio: {avg_latency:.2f}%")
        
        self.logger.info(f"  • Falhas de Aresta ({len(edge_impacts)} cenários)")
        if edge_impacts:
            avg_connectivity_loss = sum(i.connectivity_loss for i in edge_impacts) / len(edge_impacts)
            avg_latency = sum(i.avg_latency_increase for i in edge_impacts) / len(edge_impacts)
            self.logger.info(f"    - Perda de conectividade média: {avg_connectivity_loss:.2f}%")
            self.logger.info(f"    - Aumento de latência médio: {avg_latency:.2f}%")
        
        return self.results['simulation']
    
    def stage_5_export_results(self, output_dir: str = "results") -> None:
        """
        Etapa 5: Exporta resultados em múltiplos formatos.
        
        Args:
            output_dir: Diretório para salvar resultados
        """
        self.logger.info(generate_report_header("ETAPA 5: EXPORTAÇÃO DE RESULTADOS"))
        
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        # Exporta DataFrames principais
        if 'data' in self.results:
            with Timer("Exportação de DataFrames"):
                data = self.results['data']
                if data['topology']:
                    export_dataframe_to_parquet(data['topology'],
                                              str(output_path / "topology"))
                if data['cables']:
                    export_dataframe_to_parquet(data['cables'],
                                              str(output_path / "cables"))
        
        # Exporta análise
        if 'analysis' in self.results and 'critical_nodes' in self.results['analysis']:
            with Timer("Exportação de nós críticos"):
                critical_nodes_df = self.results['analysis']['critical_nodes']
                export_dataframe_to_parquet(critical_nodes_df,
                                          str(output_path / "critical_nodes"))
        
        self.logger.info(f"✓ Resultados exportados em {output_path}")
    
    def run_full_pipeline(self, output_dir: str = "results", num_simulations: int = 10) -> None:
        """
        Executa o pipeline completo.
        
        Args:
            output_dir: Diretório para salvar resultados
            num_simulations: Número de simulações por tipo de falha
        """
        try:
            self.logger.info("="*70)
            self.logger.info("INICIANDO PIPELINE RMIFO")
            self.logger.info("="*70)
            
            # Etapa 1: Carrega dados
            data = self.stage_1_load_data()
            
            # Etapa 2: Construir grafo
            graph = self.stage_2_build_graph(data['topology'], data['cables'])
            
            # Etapa 3: Analisar rede
            analysis = self.stage_3_analyze_network(graph)
            
            # Etapa 4: Simular falhas
            simulation = self.stage_4_simulate_failures(graph, num_scenarios=num_simulations)
            
            # Etapa 5: Exportar resultados
            self.stage_5_export_results(output_dir)
            
            self.logger.info("\n" + "="*70)
            self.logger.info("✓ PIPELINE RMIFO COMPLETADO COM SUCESSO")
            self.logger.info("="*70)
        
        except Exception as e:
            self.logger.error(f"❌ ERRO NO PIPELINE: {e}")
            raise
        
        finally:
            self.spark.stop()


def main():
    """Função principal."""
    # Configurar logging
    setup_logging(log_file="rmifo.log")
    logger = get_logger(__name__)
    
    try:
        # Criar e executar pipeline
        # use_samples=True para testar com dados de exemplo
        # use_samples=False para usar dados reais (quando disponíveis)
        pipeline = RMIFOPipeline(
            config_file="config/main_config.yaml",
            use_samples=True  # Mude para False para dados reais
        )
        
        # Executar pipeline completo
        pipeline.run_full_pipeline(
            output_dir="results",
            num_simulations=10  # Número de falhas a simular
        )
    
    except Exception as e:
        logger.error(f"Erro fatal: {e}")
        raise


if __name__ == "__main__":
    main()
