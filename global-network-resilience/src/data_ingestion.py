"""
Módulo de Ingestão de Dados - RMIFO
Responsável por carregar dados de topologia CAIDA, cabos submarinos e geoIP.
"""

import pandas as pd
import geopandas as gpd
from pathlib import Path
from typing import Tuple, Optional, Dict, Any
from pyspark.sql import SparkSession, DataFrame
from pyspark.sql.types import StructType, StructField, StringType, DoubleType, IntegerType
import logging

logger = logging.getLogger(__name__)


class DataLoaderError(Exception):
    """Exceção customizada para erros de carregamento de dados."""
    pass


class CAIDADataLoader:
    """Carregador para dados de topologia CAIDA."""
    
    def __init__(self, spark: SparkSession, data_path: Path):
        """
        Inicializa o carregador CAIDA.
        
        Args:
            spark: SparkSession ativa
            data_path: Caminho para arquivo de topologia
        """
        self.spark = spark
        self.data_path = Path(data_path)
        self.logger = logging.getLogger(__name__)
    
    def load_topology(self, format: str = "parquet") -> DataFrame:
        """
        Carrega topologia CAIDA em DataFrame Spark.
        
        Args:
            format: Formato do arquivo (parquet, csv, json)
        
        Returns:
            DataFrame com topologia: (node_id, node_name, latitude, longitude, country, asn)
        
        Raises:
            DataLoaderError: Se arquivo não existir ou formato inválido
        """
        if not self.data_path.exists():
            raise DataLoaderError(f"Arquivo CAIDA não encontrado: {self.data_path}")
        
        try:
            if format == "parquet":
                df = self.spark.read.parquet(str(self.data_path))
            elif format == "csv":
                df = self.spark.read.csv(str(self.data_path), header=True, inferSchema=True)
            elif format == "json":
                df = self.spark.read.json(str(self.data_path))
            else:
                raise DataLoaderError(f"Formato não suportado: {format}")
            
            self.logger.info(f"[OK] Topologia CAIDA carregada: {df.count()} nós")
            return df
        
        except Exception as e:
            raise DataLoaderError(f"Erro ao carregar topologia CAIDA: {e}")
    
    def create_sample_topology(self, num_nodes: int = 100) -> DataFrame:
        """
        Cria topologia de exemplo para testes.
        
        Args:
            num_nodes: Número de nós para gerar
        
        Returns:
            DataFrame com topologia simulada
        """
        import random
        
        nodes_data = [
            (i, f"node_{i}", random.uniform(-90, 90), random.uniform(-180, 180), 
             random.choice(["BR", "US", "EU", "CN", "JP"]), random.randint(1000, 65000))
            for i in range(num_nodes)
        ]
        
        schema = StructType([
            StructField("node_id", IntegerType(), False),
            StructField("node_name", StringType(), True),
            StructField("latitude", DoubleType(), True),
            StructField("longitude", DoubleType(), True),
            StructField("country", StringType(), True),
            StructField("asn", IntegerType(), True)
        ])
        
        df = self.spark.createDataFrame(nodes_data, schema=schema)
        self.logger.info(f"[OK] Topologia de exemplo criada: {num_nodes} nós")
        return df


class SubmarineCablesLoader:
    """Carregador para dados de cabos submarinos."""
    
    def __init__(self, spark: SparkSession, data_path: Path):
        """
        Inicializa o carregador de cabos submarinos.
        
        Args:
            spark: SparkSession ativa
            data_path: Caminho para arquivo GeoJSON
        """
        self.spark = spark
        self.data_path = Path(data_path)
        self.logger = logging.getLogger(__name__)
    
    def load_cables(self, format: str = "geojson") -> DataFrame:
        """
        Carrega dados de cabos submarinos.
        
        Args:
            format: Formato do arquivo (geojson, csv, parquet)
        
        Returns:
            DataFrame com cabos: (cable_id, cable_name, source, destination, capacity, length_km)
        
        Raises:
            DataLoaderError: Se arquivo não existir ou erro ao carregar
        """
        if not self.data_path.exists():
            raise DataLoaderError(f"Arquivo de cabos não encontrado: {self.data_path}")
        
        try:
            if format == "geojson":
                gdf = gpd.read_file(str(self.data_path))
                # Converte GeoDataFrame para Spark DataFrame
                df_pandas = gdf.drop(columns='geometry', errors='ignore')
                df = self.spark.createDataFrame(df_pandas)
            elif format == "csv":
                df = self.spark.read.csv(str(self.data_path), header=True, inferSchema=True)
            elif format == "parquet":
                df = self.spark.read.parquet(str(self.data_path))
            else:
                raise DataLoaderError(f"Formato não suportado: {format}")
            
            self.logger.info(f"[OK] Cabos submarinos carregados: {df.count()} cabos")
            return df
        
        except Exception as e:
            raise DataLoaderError(f"Erro ao carregar cabos submarinos: {e}")
    
    def create_sample_cables(self, num_cables: int = 50) -> DataFrame:
        """
        Cria cabos de exemplo para testes.
        
        Args:
            num_cables: Número de cabos para gerar
        
        Returns:
            DataFrame com cabos simulados
        """
        import random
        
        cables_data = [
            (i, f"cable_{i}", random.randint(0, 99), random.randint(0, 99),
             random.randint(10, 400), random.randint(100, 20000))
            for i in range(num_cables)
        ]
        
        schema = StructType([
            StructField("cable_id", IntegerType(), False),
            StructField("cable_name", StringType(), True),
            StructField("source_node_id", IntegerType(), True),
            StructField("destination_node_id", IntegerType(), True),
            StructField("capacity_gbps", IntegerType(), True),
            StructField("length_km", IntegerType(), True)
        ])
        
        df = self.spark.createDataFrame(cables_data, schema=schema)
        self.logger.info(f"[OK] Cabos de exemplo criados: {num_cables} cabos")
        return df


class GeoIPDataLoader:
    """Carregador para dados de geolocalização IP."""
    
    def __init__(self, spark: SparkSession, data_path: Path):
        """
        Inicializa o carregador GeoIP.
        
        Args:
            spark: SparkSession ativa
            data_path: Caminho para arquivo CSV
        """
        self.spark = spark
        self.data_path = Path(data_path)
        self.logger = logging.getLogger(__name__)
    
    def load_geoip(self) -> DataFrame:
        """
        Carrega dados de mapeamento IP-Geolocalização.
        
        Returns:
            DataFrame com GeoIP: (ip_range_start, ip_range_end, latitude, longitude, country)
        
        Raises:
            DataLoaderError: Se arquivo não existir
        """
        if not self.data_path.exists():
            raise DataLoaderError(f"Arquivo GeoIP não encontrado: {self.data_path}")
        
        try:
            df = self.spark.read.csv(str(self.data_path), header=True, inferSchema=True)
            self.logger.info(f"[OK] Dados GeoIP carregados: {df.count()} registros")
            return df
        
        except Exception as e:
            raise DataLoaderError(f"Erro ao carregar dados GeoIP: {e}")


class DataIngestManager:
    """Gerenciador centralizado de ingestão de dados."""
    
    def __init__(self, spark: SparkSession, config):
        """
        Inicializa o gerenciador de ingestão.
        
        Args:
            spark: SparkSession ativa
            config: Objeto de configuração (ConfigManager)
        """
        self.spark = spark
        self.config = config
        self.logger = logging.getLogger(__name__)
        
        self.caida_loader = CAIDADataLoader(spark, config.data_config.raw_dir / "topology")
        self.cables_loader = SubmarineCablesLoader(spark, config.data_config.external_dir / config.data_config.submarine_cables_file)
        self.geoip_loader = GeoIPDataLoader(spark, config.data_config.external_dir / config.data_config.geoip_database_file)
        
        self._cached_topology: Optional[DataFrame] = None
        self._cached_cables: Optional[DataFrame] = None
        self._cached_geoip: Optional[DataFrame] = None
    
    def load_all_data(self, use_samples: bool = False) -> Dict[str, DataFrame]:
        """
        Carrega todos os datasets necessários.
        
        Args:
            use_samples: Se True, usa dados de exemplo ao invés de arquivos reais
        
        Returns:
            Dicionário com 'topology', 'cables', 'geoip'
        """
        try:
            if use_samples:
                self.logger.info("[INFO] Carregando dados de EXEMPLO...")
                topology_df = self.caida_loader.create_sample_topology(100)
                cables_df = self.cables_loader.create_sample_cables(50)
                geoip_df = None
            else:
                self.logger.info("[INFO] Carregando dados REAIS...")
                topology_df = self.caida_loader.load_topology(self.config.data_config.caida_format)
                cables_df = self.cables_loader.load_cables(self.config.data_config.cables_format)
                try:
                    geoip_df = self.geoip_loader.load_geoip()
                except DataLoaderError:
                    self.logger.warning("[AVISO] Dados GeoIP não disponíveis, continuando sem...")
                    geoip_df = None
            
            # Cache
            self._cached_topology = topology_df
            self._cached_cables = cables_df
            self._cached_geoip = geoip_df
            
            return {
                'topology': topology_df,
                'cables': cables_df,
                'geoip': geoip_df
            }
        
        except Exception as e:
            self.logger.error(f"[ERRO] Erro ao carregar dados: {e}")
            raise
    
    def get_topology(self, use_sample: bool = False) -> DataFrame:
        """Obtém dados de topologia (com cache)."""
        if self._cached_topology is None:
            if use_sample:
                self._cached_topology = self.caida_loader.create_sample_topology()
            else:
                self._cached_topology = self.caida_loader.load_topology(self.config.data_config.caida_format)
        return self._cached_topology
    
    def get_cables(self, use_sample: bool = False) -> DataFrame:
        """Obtém dados de cabos (com cache)."""
        if self._cached_cables is None:
            if use_sample:
                self._cached_cables = self.cables_loader.create_sample_cables()
            else:
                self._cached_cables = self.cables_loader.load_cables(self.config.data_config.cables_format)
        return self._cached_cables
    
    def get_geoip(self) -> Optional[DataFrame]:
        """Obtém dados GeoIP (com cache)."""
        if self._cached_geoip is None:
            try:
                self._cached_geoip = self.geoip_loader.load_geoip()
            except DataLoaderError:
                self.logger.warning("Dados GeoIP não disponíveis")
        return self._cached_geoip
    
    def clear_cache(self) -> None:
        """Limpa cache de dados carregados."""
        self._cached_topology = None
        self._cached_cables = None
        self._cached_geoip = None
        self.logger.info("Cache de dados limpo")
