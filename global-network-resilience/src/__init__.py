"""
RMIFO - Resilience of Intercontinental Fiber Optic Networks
Análise modularizada de resiliência de redes de fibra ótica intercontinentais.
"""

__version__ = "0.1.0"
__author__ = "Network Resilience Team"

# Importa componentes principais para acesso fácil
from .config import ConfigManager, get_config, SparkConfig, DataConfig, GraphConfig, SimulationConfig
from .data_ingestion import (
    DataIngestManager, CAIDADataLoader, SubmarineCablesLoader,
    GeoIPDataLoader, DataLoaderError
)
from .graph_processor import (
    GraphProcessor, TopologyGraphBuilder, GraphAnalyzer,
    CriticalityAnalyzer, GraphProcessingError
)
from .failure_simulator import (
    FailureSimulator, NodeFailureSimulator, EdgeFailureSimulator,
    ImpactAnalyzer, FailureMode, FailureScenario, FailureImpact, SimulationError
)
from .utils import (
    setup_logging, get_logger, Timer,
    validate_dataframe_columns, validate_path_exists,
    export_dataframe_to_csv, export_dataframe_to_parquet, export_results_to_json,
    print_dataframe_stats, print_query_plan,
    generate_report_header, format_metric
)

__all__ = [
    # Config
    'ConfigManager', 'get_config', 'SparkConfig', 'DataConfig',
    'GraphConfig', 'SimulationConfig',
    
    # Data Ingestion
    'DataIngestManager', 'CAIDADataLoader', 'SubmarineCablesLoader',
    'GeoIPDataLoader', 'DataLoaderError',
    
    # Graph Processing
    'GraphProcessor', 'TopologyGraphBuilder', 'GraphAnalyzer',
    'CriticalityAnalyzer', 'GraphProcessingError',
    
    # Failure Simulation
    'FailureSimulator', 'NodeFailureSimulator', 'EdgeFailureSimulator',
    'ImpactAnalyzer', 'FailureMode', 'FailureScenario', 'FailureImpact',
    'SimulationError',
    
    # Utils
    'setup_logging', 'get_logger', 'Timer',
    'validate_dataframe_columns', 'validate_path_exists',
    'export_dataframe_to_csv', 'export_dataframe_to_parquet',
    'export_results_to_json', 'print_dataframe_stats', 'print_query_plan',
    'generate_report_header', 'format_metric'
]
