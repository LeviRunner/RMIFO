"""
Módulo de Processamento de Grafos - RMIFO
Responsável por construir e analisar grafos de topologia usando GraphFrames.
"""

from pyspark.sql import SparkSession, DataFrame
from pyspark.sql.functions import col, count, sum as spark_sum, avg
from graphframes import GraphFrame
from typing import Dict, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class GraphProcessingError(Exception):
    """Exceção customizada para erros em processamento de grafos."""
    pass


class TopologyGraphBuilder:
    """Construtor de grafos de topologia a partir de nós e arestas."""
    
    def __init__(self, spark: SparkSession):
        """
        Inicializa o construtor de grafos.
        
        Args:
            spark: SparkSession ativa
        """
        self.spark = spark
        self.logger = logging.getLogger(__name__)
    
    def build_graph(self, vertices_df: DataFrame, edges_df: DataFrame) -> GraphFrame:
        """
        Constrói um grafo GraphFrame a partir de vértices e arestas.
        
        Args:
            vertices_df: DataFrame com vértices (deve ter coluna 'id')
            edges_df: DataFrame com arestas (deve ter colunas 'src', 'dst')
        
        Returns:
            GraphFrame com a topologia
        
        Raises:
            GraphProcessingError: Se estrutura de dados inválida
        """
        try:
            # Valida estrutura
            if 'id' not in vertices_df.columns:
                if 'node_id' in vertices_df.columns:
                    vertices_df = vertices_df.withColumnRenamed('node_id', 'id')
                else:
                    raise GraphProcessingError("vertices_df deve conter coluna 'id' ou 'node_id'")
            
            if 'src' not in edges_df.columns or 'dst' not in edges_df.columns:
                # Tenta mapear source/destination para src/dst
                if 'source_node_id' in edges_df.columns and 'destination_node_id' in edges_df.columns:
                    edges_df = edges_df.withColumnRenamed('source_node_id', 'src') \
                                      .withColumnRenamed('destination_node_id', 'dst')
                else:
                    raise GraphProcessingError("edges_df deve conter colunas 'src' e 'dst'")
            
            # Cria grafo
            graph = GraphFrame(vertices_df, edges_df)
            
            v_count = graph.vertices.count()
            e_count = graph.edges.count()
            
            self.logger.info(f"✓ Grafo construído com sucesso: {v_count} vértices, {e_count} arestas")
            
            return graph
        
        except Exception as e:
            raise GraphProcessingError(f"Erro ao construir grafo: {e}")


class GraphAnalyzer:
    """Analisador de propriedades e métricas de grafos."""
    
    def __init__(self, spark: SparkSession):
        """
        Inicializa o analisador de grafos.
        
        Args:
            spark: SparkSession ativa
        """
        self.spark = spark
        self.logger = logging.getLogger(__name__)
    
    def get_basic_metrics(self, graph: GraphFrame) -> Dict[str, any]:
        """
        Calcula métricas básicas do grafo.
        
        Args:
            graph: GraphFrame para analisar
        
        Returns:
            Dicionário com métricas: num_vertices, num_edges, avg_degree, etc.
        """
        try:
            num_vertices = graph.vertices.count()
            num_edges = graph.edges.count()
            
            # Grau médio
            degree_stats = graph.inDegrees.select(avg(col("inDegree"))).collect()[0][0]
            out_degree_stats = graph.outDegrees.select(avg(col("outDegree"))).collect()[0][0]
            
            metrics = {
                'num_vertices': num_vertices,
                'num_edges': num_edges,
                'avg_in_degree': float(degree_stats) if degree_stats else 0,
                'avg_out_degree': float(out_degree_stats) if out_degree_stats else 0,
                'density': (2 * num_edges) / (num_vertices * (num_vertices - 1)) if num_vertices > 1 else 0
            }
            
            self.logger.info(f"📊 Métricas básicas: {num_vertices} vértices, {num_edges} arestas")
            return metrics
        
        except Exception as e:
            raise GraphProcessingError(f"Erro ao calcular métricas: {e}")
    
    def compute_pagerank(self, graph: GraphFrame, max_iterations: int = 20,
                        reset_probability: float = 0.15) -> DataFrame:
        """
        Computa PageRank para identificar nós mais importantes.
        
        Args:
            graph: GraphFrame
            max_iterations: Número de iterações
            reset_probability: Probabilidade de reset aleatório
        
        Returns:
            DataFrame com colunas (id, pagerank)
        """
        try:
            results = graph.pageRank.maxIter(max_iterations).resetProbability(reset_probability).run()
            pagerank_df = results.vertices.select("id", "pagerank")
            
            self.logger.info(f"✓ PageRank computado ({max_iterations} iterações)")
            return pagerank_df
        
        except Exception as e:
            raise GraphProcessingError(f"Erro ao computar PageRank: {e}")
    
    def compute_connected_components(self, graph: GraphFrame) -> DataFrame:
        """
        Identifica componentes conectadas do grafo.
        
        Args:
            graph: GraphFrame
        
        Returns:
            DataFrame com colunas (id, component)
        """
        try:
            results = graph.connectedComponents.run()
            components_df = results.vertices.select("id", "component")
            
            num_components = components_df.select("component").distinct().count()
            self.logger.info(f"✓ {num_components} componentes conectadas encontradas")
            
            return components_df
        
        except Exception as e:
            raise GraphProcessingError(f"Erro ao computar componentes conectadas: {e}")
    
    def find_shortest_paths(self, graph: GraphFrame, landmarks: list) -> DataFrame:
        """
        Encontra caminhos mais curtos de landmarks para todos os nós.
        
        Args:
            graph: GraphFrame
            landmarks: Lista de IDs de nós landmark
        
        Returns:
            DataFrame com caminhos mais curtos
        """
        try:
            results = graph.shortestPaths.landmarks(landmarks).run()
            
            self.logger.info(f"✓ Caminhos mais curtos computados para {len(landmarks)} landmarks")
            return results.vertices
        
        except Exception as e:
            raise GraphProcessingError(f"Erro ao computar caminhos mais curtos: {e}")
    
    def compute_triangle_counts(self, graph: GraphFrame) -> DataFrame:
        """
        Computa contagem de triângulos (clustering).
        
        Args:
            graph: GraphFrame
        
        Returns:
            DataFrame com (id, count)
        """
        try:
            results = graph.triangleCount.run()
            triangle_df = results.vertices.select("id", "count")
            
            self.logger.info(f"✓ Contagem de triângulos computada")
            return triangle_df
        
        except Exception as e:
            raise GraphProcessingError(f"Erro ao computar triângulos: {e}")


class CriticalityAnalyzer:
    """Analisador de criticidade de nós e arestas."""
    
    def __init__(self, spark: SparkSession):
        """
        Inicializa o analisador de criticidade.
        
        Args:
            spark: SparkSession ativa
        """
        self.spark = spark
        self.logger = logging.getLogger(__name__)
        self.analyzer = GraphAnalyzer(spark)
    
    def identify_critical_nodes(self, graph: GraphFrame, threshold: float = 0.7) -> DataFrame:
        """
        Identifica nós críticos baseado em PageRank.
        
        Args:
            graph: GraphFrame
            threshold: Limiar percentual para considerar crítico
        
        Returns:
            DataFrame com nós críticos e suas pontuações
        """
        try:
            pagerank_df = self.analyzer.compute_pagerank(graph)
            
            # Obtém threshold percentual
            percentile_value = pagerank_df.approxQuantile("pagerank", [threshold], 0.01)[0]
            
            critical_nodes = pagerank_df.filter(col("pagerank") >= percentile_value)
            
            self.logger.info(f"🎯 {critical_nodes.count()} nós críticos identificados (threshold={threshold})")
            
            return critical_nodes.orderBy("pagerank", ascending=False)
        
        except Exception as e:
            raise GraphProcessingError(f"Erro ao identificar nós críticos: {e}")
    
    def compute_node_betweenness(self, graph: GraphFrame) -> DataFrame:
        """
        Computa betweenness centrality para identificar gargalos.
        
        Args:
            graph: GraphFrame
        
        Returns:
            DataFrame com (id, betweenness_approximation)
        """
        try:
            # GraphFrames não tem betweenness nativo, então aproximamos com PageRank
            # Em produção, seria necessário algoritmo customizado
            pagerank_df = self.analyzer.compute_pagerank(graph)
            
            self.logger.info("✓ Betweenness (aproximado via PageRank) calculado")
            return pagerank_df
        
        except Exception as e:
            raise GraphProcessingError(f"Erro ao computar betweenness: {e}")


class GraphProcessor:
    """Orquestrador principal de processamento de grafos."""
    
    def __init__(self, spark: SparkSession, config):
        """
        Inicializa o processador de grafos.
        
        Args:
            spark: SparkSession ativa
            config: Objeto de configuração
        """
        self.spark = spark
        self.config = config
        self.logger = logging.getLogger(__name__)
        
        self.builder = TopologyGraphBuilder(spark)
        self.analyzer = GraphAnalyzer(spark)
        self.criticality = CriticalityAnalyzer(spark)
        
        self._current_graph: Optional[GraphFrame] = None
    
    def create_network_graph(self, vertices_df: DataFrame, edges_df: DataFrame) -> GraphFrame:
        """
        Cria grafo de rede a partir de dados.
        
        Args:
            vertices_df: DataFrame com nós
            edges_df: DataFrame com arestas
        
        Returns:
            GraphFrame construído
        """
        self._current_graph = self.builder.build_graph(vertices_df, edges_df)
        return self._current_graph
    
    def analyze_network(self, graph: GraphFrame = None) -> Dict[str, any]:
        """
        Executa análise completa de rede.
        
        Args:
            graph: GraphFrame (usa self._current_graph se None)
        
        Returns:
            Dicionário com resultados de análise
        """
        if graph is None:
            graph = self._current_graph
        
        if graph is None:
            raise GraphProcessingError("Nenhum grafo carregado. Crie um com create_network_graph()")
        
        try:
            results = {}
            
            # Métricas básicas
            results['basic_metrics'] = self.analyzer.get_basic_metrics(graph)
            
            # PageRank
            results['pagerank'] = self.analyzer.compute_pagerank(graph,
                                                                  max_iterations=self.config.graph_config.pagerank_iterations)
            
            # Componentes conectadas
            results['components'] = self.analyzer.compute_connected_components(graph)
            
            # Nós críticos
            results['critical_nodes'] = self.criticality.identify_critical_nodes(
                graph, 
                threshold=self.config.analysis_config.criticality_threshold
            )
            
            self.logger.info("✓ Análise de rede completa!")
            return results
        
        except Exception as e:
            self.logger.error(f"❌ Erro na análise de rede: {e}")
            raise
