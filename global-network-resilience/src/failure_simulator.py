"""
Módulo Simulador de Falhas - RMIFO
Executa simulações de falhas de nós e arestas para análise de impacto.
"""

from pyspark.sql import SparkSession, DataFrame
from pyspark.sql.functions import col, not_, rand
from graphframes import GraphFrame
from typing import Dict, List, Optional, Tuple
import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class FailureMode(Enum):
    """Tipos de falhas a simular."""
    NODE = "node"  # Falha de nó (cidade/PoP)
    EDGE = "edge"  # Falha de aresta (cabo submarino)
    REGION = "region"  # Falha de região inteira
    CASCADE = "cascade"  # Falha em cascata


@dataclass
class FailureScenario:
    """Representa um cenário de falha."""
    scenario_id: str
    failure_mode: FailureMode
    failed_identifiers: List[any]  # IDs de nós ou arestas falhas
    description: str
    
    def __str__(self) -> str:
        return f"[{self.scenario_id}] {self.failure_mode.value}: {self.description}"


@dataclass
class FailureImpact:
    """Resultado do impacto de uma falha."""
    scenario: FailureScenario
    nodes_affected: int
    edges_affected: int
    connectivity_loss: float  # Percentual
    avg_latency_increase: float  # Percentual
    components_created: int  # Componentes desconectadas criadas
    critical_paths_broken: int


class SimulationError(Exception):
    """Exceção para erros em simulação."""
    pass


class NodeFailureSimulator:
    """Simulador de falhas de nós."""
    
    def __init__(self, spark: SparkSession):
        """
        Inicializa simulador de falhas de nós.
        
        Args:
            spark: SparkSession ativa
        """
        self.spark = spark
        self.logger = logging.getLogger(__name__)
    
    def simulate_node_failure(self, graph: GraphFrame, failed_node_id: any) -> GraphFrame:
        """
        Simula falha de um nó removendo-o do grafo.
        
        Args:
            graph: GraphFrame original
            failed_node_id: ID do nó que falhou
        
        Returns:
            GraphFrame com nó e arestas associadas removidas
        """
        try:
            # Remove o nó
            vertices = graph.vertices.filter(col("id") != failed_node_id)
            
            # Remove arestas conectadas a este nó
            edges = graph.edges.filter(
                (col("src") != failed_node_id) & (col("dst") != failed_node_id)
            )
            
            degraded_graph = GraphFrame(vertices, edges)
            
            self.logger.info(f"🔴 Nó {failed_node_id} falhou | Arestas removidas: {graph.edges.count() - edges.count()}")
            
            return degraded_graph
        
        except Exception as e:
            raise SimulationError(f"Erro ao simular falha de nó: {e}")
    
    def simulate_multiple_node_failures(self, graph: GraphFrame, failed_node_ids: List[any]) -> GraphFrame:
        """
        Simula falha de múltiplos nós.
        
        Args:
            graph: GraphFrame original
            failed_node_ids: Lista de IDs de nós falhos
        
        Returns:
            GraphFrame degradado
        """
        try:
            vertices = graph.vertices
            edges = graph.edges
            
            # Remove todos os nós falhos
            for node_id in failed_node_ids:
                vertices = vertices.filter(col("id") != node_id)
                edges = edges.filter(
                    (col("src") != node_id) & (col("dst") != node_id)
                )
            
            degraded_graph = GraphFrame(vertices, edges)
            
            self.logger.info(f"🔴 {len(failed_node_ids)} nós falharam | Novo grafo: "
                           f"{vertices.count()} nós, {edges.count()} arestas")
            
            return degraded_graph
        
        except Exception as e:
            raise SimulationError(f"Erro ao simular falhas múltiplas de nós: {e}")


class EdgeFailureSimulator:
    """Simulador de falhas de arestas."""
    
    def __init__(self, spark: SparkSession):
        """
        Inicializa simulador de falhas de arestas.
        
        Args:
            spark: SparkSession ativa
        """
        self.spark = spark
        self.logger = logging.getLogger(__name__)
    
    def simulate_edge_failure(self, graph: GraphFrame, source_node_id: any, 
                             dest_node_id: any) -> GraphFrame:
        """
        Simula falha de uma aresta (cabo submarino).
        
        Args:
            graph: GraphFrame original
            source_node_id: ID do nó fonte
            dest_node_id: ID do nó destino
        
        Returns:
            GraphFrame com aresta removida
        """
        try:
            # Remove a aresta específica (ambas as direções)
            edges = graph.edges.filter(
                ~(
                    ((col("src") == source_node_id) & (col("dst") == dest_node_id)) |
                    ((col("src") == dest_node_id) & (col("dst") == source_node_id))
                )
            )
            
            degraded_graph = GraphFrame(graph.vertices, edges)
            
            self.logger.info(f"🔗 Cabo {source_node_id}-{dest_node_id} foi cortado")
            
            return degraded_graph
        
        except Exception as e:
            raise SimulationError(f"Erro ao simular falha de aresta: {e}")
    
    def simulate_multiple_edge_failures(self, graph: GraphFrame, 
                                        edge_pairs: List[Tuple[any, any]]) -> GraphFrame:
        """
        Simula falha de múltiplas arestas.
        
        Args:
            graph: GraphFrame original
            edge_pairs: Lista de tuplas (source, dest)
        
        Returns:
            GraphFrame degradado
        """
        try:
            edges = graph.edges
            
            for src, dst in edge_pairs:
                edges = edges.filter(
                    ~(
                        ((col("src") == src) & (col("dst") == dst)) |
                        ((col("src") == dst) & (col("dst") == src))
                    )
                )
            
            degraded_graph = GraphFrame(graph.vertices, edges)
            
            self.logger.info(f"🔗 {len(edge_pairs)} cabos foram cortados | "
                           f"Arestas restantes: {edges.count()}")
            
            return degraded_graph
        
        except Exception as e:
            raise SimulationError(f"Erro ao simular falhas múltiplas de arestas: {e}")


class ImpactAnalyzer:
    """Analisador de impacto de falhas."""
    
    def __init__(self, spark: SparkSession):
        """
        Inicializa analisador de impacto.
        
        Args:
            spark: SparkSession ativa
        """
        self.spark = spark
        self.logger = logging.getLogger(__name__)
    
    def compute_impact(self, original_graph: GraphFrame, degraded_graph: GraphFrame,
                      scenario: FailureScenario) -> FailureImpact:
        """
        Calcula impacto de uma falha comparando grafos original e degradado.
        
        Args:
            original_graph: Grafo original intacto
            degraded_graph: Grafo após falha
            scenario: Cenário de falha
        
        Returns:
            FailureImpact com métricas de impacto
        """
        try:
            orig_vertices = original_graph.vertices.count()
            orig_edges = original_graph.edges.count()
            
            deg_vertices = degraded_graph.vertices.count()
            deg_edges = degraded_graph.edges.count()
            
            nodes_affected = orig_vertices - deg_vertices
            edges_affected = orig_edges - deg_edges
            connectivity_loss = (nodes_affected / orig_vertices) * 100 if orig_vertices > 0 else 0
            
            # Estima aumento de latência (simplicidade: proporcional a perda de arestas)
            avg_latency_increase = (edges_affected / orig_edges) * 15 if orig_edges > 0 else 0  # Max 15% increase
            
            # Componentes conectadas (aproximação de desconexão)
            try:
                components_df = degraded_graph.connectedComponents.run()
                components_created = components_df.vertices.select("component").distinct().count()
            except:
                components_created = 1
            
            impact = FailureImpact(
                scenario=scenario,
                nodes_affected=nodes_affected,
                edges_affected=edges_affected,
                connectivity_loss=connectivity_loss,
                avg_latency_increase=avg_latency_increase,
                components_created=components_created,
                critical_paths_broken=edges_affected  # Simplificado
            )
            
            return impact
        
        except Exception as e:
            raise SimulationError(f"Erro ao computar impacto: {e}")


class FailureSimulator:
    """Orquestrador principal de simulações de falha."""
    
    def __init__(self, spark: SparkSession, config):
        """
        Inicializa simulador de falhas.
        
        Args:
            spark: SparkSession ativa
            config: Objeto de configuração
        """
        self.spark = spark
        self.config = config
        self.logger = logging.getLogger(__name__)
        
        self.node_simulator = NodeFailureSimulator(spark)
        self.edge_simulator = EdgeFailureSimulator(spark)
        self.impact_analyzer = ImpactAnalyzer(spark)
        
        self.original_graph: Optional[GraphFrame] = None
    
    def set_base_graph(self, graph: GraphFrame) -> None:
        """Define grafo base para simulações."""
        self.original_graph = graph
        self.logger.info(f"📌 Grafo base definido: {graph.vertices.count()} nós")
    
    def simulate_node_failure(self, node_id: any) -> Tuple[GraphFrame, FailureImpact]:
        """Simula falha de um nó."""
        if self.original_graph is None:
            raise SimulationError("Grafo base não definido. Use set_base_graph()")
        
        scenario = FailureScenario(
            scenario_id=f"node_failure_{node_id}",
            failure_mode=FailureMode.NODE,
            failed_identifiers=[node_id],
            description=f"Falha do nó {node_id}"
        )
        
        degraded = self.node_simulator.simulate_node_failure(self.original_graph, node_id)
        impact = self.impact_analyzer.compute_impact(self.original_graph, degraded, scenario)
        
        return degraded, impact
    
    def simulate_edge_failure(self, src: any, dst: any) -> Tuple[GraphFrame, FailureImpact]:
        """Simula falha de uma aresta."""
        if self.original_graph is None:
            raise SimulationError("Grafo base não definido. Use set_base_graph()")
        
        scenario = FailureScenario(
            scenario_id=f"edge_failure_{src}_{dst}",
            failure_mode=FailureMode.EDGE,
            failed_identifiers=[(src, dst)],
            description=f"Falha do cabo {src}-{dst}"
        )
        
        degraded = self.edge_simulator.simulate_edge_failure(self.original_graph, src, dst)
        impact = self.impact_analyzer.compute_impact(self.original_graph, degraded, scenario)
        
        return degraded, impact
    
    def batch_simulate_random_failures(self, num_scenarios: int = 10,
                                      failure_type: str = "node") -> List[FailureImpact]:
        """
        Executa múltiplas simulações de falha aleatória.
        
        Args:
            num_scenarios: Número de cenários a simular
            failure_type: "node" ou "edge"
        
        Returns:
            Lista de FailureImpact
        """
        if self.original_graph is None:
            raise SimulationError("Grafo base não definido. Use set_base_graph()")
        
        impacts = []
        
        if failure_type == "node":
            vertices_list = self.original_graph.vertices.select("id").collect()
            node_ids = [row.id for row in vertices_list]
            
            import random
            random.seed(self.config.simulation_config.seed)
            
            for i in range(min(num_scenarios, len(node_ids))):
                failed_node = random.choice(node_ids)
                _, impact = self.simulate_node_failure(failed_node)
                impacts.append(impact)
        
        elif failure_type == "edge":
            edges_list = self.original_graph.edges.select("src", "dst").collect()
            
            import random
            random.seed(self.config.simulation_config.seed)
            
            for i in range(min(num_scenarios, len(edges_list))):
                edge = random.choice(edges_list)
                _, impact = self.simulate_edge_failure(edge.src, edge.dst)
                impacts.append(impact)
        
        self.logger.info(f"✓ {num_scenarios} simulações de {failure_type} completadas")
        
        return impacts
    
    def print_impact_summary(self, impacts: List[FailureImpact]) -> None:
        """Imprime resumo dos impactos."""
        self.logger.info(f"\n{'='*60}")
        self.logger.info(f"RESUMO DE IMPACTOS - {len(impacts)} cenários")
        self.logger.info(f"{'='*60}")
        
        for impact in impacts[:5]:  # Mostra top 5
            self.logger.info(f"\n{impact.scenario}")
            self.logger.info(f"  • Nós afetados: {impact.nodes_affected}")
            self.logger.info(f"  • Arestas afetadas: {impact.edges_affected}")
            self.logger.info(f"  • Perda de conectividade: {impact.connectivity_loss:.2f}%")
            self.logger.info(f"  • Aumento de latência: {impact.avg_latency_increase:.2f}%")
