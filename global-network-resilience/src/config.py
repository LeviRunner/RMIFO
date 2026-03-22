"""
Módulo de Configuração - RMIFO
Gerencia todas as configurações do projeto de forma centralizada e extensível.
"""

import os
import yaml
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass, asdict


@dataclass
class SparkConfig:
    """Configurações do Spark."""
    app_name: str = "RMIFO-Network-Resilience"
    master: str = "local[*]"  # local[*], spark://..., yarn
    memory: str = "4g"
    cores: int = 4
    shuffle_partitions: int = 200
    
    def to_dict(self) -> Dict[str, str]:
        """Converte para dicionário para SparkSession."""
        return asdict(self)


@dataclass
class DataConfig:
    """Configurações de dados."""
    base_path: Path
    raw_dir: Path
    processed_dir: Path
    external_dir: Path
    
    # Arquivos esperados
    caida_topology_file: Optional[str] = "caida_topology.gz"
    submarine_cables_file: Optional[str] = "submarine_cables.geojson"
    geoip_database_file: Optional[str] = "geoip_database.csv"
    
    # Formatos
    caida_format: str = "parquet"  # parquet, csv, json
    cables_format: str = "geojson"
    
    def __post_init__(self):
        """Converte strings para Path."""
        if isinstance(self.base_path, str):
            self.base_path = Path(self.base_path)
        if isinstance(self.raw_dir, str):
            self.raw_dir = Path(self.raw_dir)
        if isinstance(self.processed_dir, str):
            self.processed_dir = Path(self.processed_dir)
        if isinstance(self.external_dir, str):
            self.external_dir = Path(self.external_dir)


@dataclass
class GraphConfig:
    """Configurações de processamento de grafos."""
    # Limites de dados
    max_vertices: Optional[int] = None  # None = sem limite
    max_edges: Optional[int] = None
    
    # Algoritmos
    enable_betweenness_centrality: bool = True
    enable_closeness_centrality: bool = True
    enable_pagerank: bool = True
    
    # Parâmetros de algoritmos
    pagerank_iterations: int = 20
    pagerank_reset_probability: float = 0.15
    
    # Cache
    cache_processed_graph: bool = True
    

@dataclass
class SimulationConfig:
    """Configurações de simulação."""
    # Tipos de falha
    failure_modes: list = None  # network, node, edge, regional
    
    # Parâmetros de simulação
    max_simulations: int = 100
    seed: int = 42
    
    # Métricas a calcular
    metrics_to_compute: list = None  # latency, throughput, availability
    
    def __post_init__(self):
        if self.failure_modes is None:
            self.failure_modes = ["node", "edge"]
        if self.metrics_to_compute is None:
            self.metrics_to_compute = ["latency", "throughput"]


@dataclass
class AnalysisConfig:
    """Configurações de análise."""
    # Limiares de criticidade
    criticality_threshold: float = 0.7
    
    # Continentes de interesse (deixar vazio = todos)
    target_regions: list = None
    
    # Análise de impacto
    include_cascading_failures: bool = True
    

class ConfigManager:
    """Gerenciador centralizado de configurações."""
    
    def __init__(self, config_file: Optional[str] = None):
        """
        Inicializa o gerenciador de configurações.
        
        Args:
            config_file: Caminho para arquivo YAML de configuração (opcional)
        """
        self.spark_config = SparkConfig()
        self.data_config = self._init_data_config()
        self.graph_config = GraphConfig()
        self.simulation_config = SimulationConfig()
        self.analysis_config = AnalysisConfig()
        
        # Carrega configurações de arquivo se fornecido
        if config_file and Path(config_file).exists():
            self.load_from_yaml(config_file)
    
    def _init_data_config(self) -> DataConfig:
        """Inicializa DataConfig com paths padrão do projeto."""
        base_path = Path(__file__).parent.parent / "data"
        return DataConfig(
            base_path=base_path,
            raw_dir=base_path / "raw",
            processed_dir=base_path / "processed",
            external_dir=base_path / "external"
        )
    
    def load_from_yaml(self, config_file: str) -> None:
        """
        Carrega configurações de arquivo YAML.
        
        Args:
            config_file: Caminho do arquivo YAML
        """
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config_dict = yaml.safe_load(f) or {}
            
            # Atualiza cada seção de configuração
            if 'spark' in config_dict:
                spark_params = config_dict['spark']
                self.spark_config = SparkConfig(**spark_params)
            
            if 'data' in config_dict:
                data_params = config_dict['data']
                self.data_config = DataConfig(**data_params)
            
            if 'graph' in config_dict:
                graph_params = config_dict['graph']
                self.graph_config = GraphConfig(**graph_params)
            
            if 'simulation' in config_dict:
                sim_params = config_dict['simulation']
                self.simulation_config = SimulationConfig(**sim_params)
            
            if 'analysis' in config_dict:
                analysis_params = config_dict['analysis']
                self.analysis_config = AnalysisConfig(**analysis_params)
        
        except Exception as e:
            print(f" Erro ao carregar config YAML: {e}. Usando defaults.")
    
    def to_dict(self) -> Dict[str, Any]:
        """Converte todas as configurações para dicionário."""
        return {
            'spark': asdict(self.spark_config),
            'data': asdict(self.data_config) if self.data_config else None,
            'graph': asdict(self.graph_config),
            'simulation': asdict(self.simulation_config),
            'analysis': asdict(self.analysis_config),
        }
    
    def save_to_yaml(self, output_file: str) -> None:
        """
        Salva configurações em arquivo YAML.
        
        Args:
            output_file: Caminho de saída
        """
        config_dict = self.to_dict()
        
        # Converte Path para string
        config_dict_serializable = self._make_serializable(config_dict)
        
        with open(output_file, 'w', encoding='utf-8') as f:
            yaml.dump(config_dict_serializable, f, default_flow_style=False)
    
    @staticmethod
    def _make_serializable(obj: Any) -> Any:
        """Converte objetos Path para strings recursivamente."""
        if isinstance(obj, dict):
            return {k: ConfigManager._make_serializable(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [ConfigManager._make_serializable(item) for item in obj]
        elif isinstance(obj, Path):
            return str(obj)
        return obj


# Instância global de configuração
_global_config: Optional[ConfigManager] = None


def get_config(config_file: Optional[str] = None) -> ConfigManager:
    """
    Obtém instância global de configuração (singleton).
    
    Args:
        config_file: Caminho para arquivo YAML (apenas na primeira chamada)
    
    Returns:
        ConfigManager: Instância global de configuração
    """
    global _global_config
    if _global_config is None:
        _global_config = ConfigManager(config_file)
    return _global_config


def reset_config() -> None:
    """Reseta a configuração global (útil para testes)."""
    global _global_config
    _global_config = None
