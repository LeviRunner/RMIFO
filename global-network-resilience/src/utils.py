"""
Módulo Utilitários - RMIFO
Funções auxiliares para logging, visualização e formatação.
"""

import logging
import os
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime
import json

# ============================================================================
# LOGGING
# ============================================================================

def setup_logging(log_file: Optional[str] = None, level=logging.INFO) -> None:
    """
    Configura sistema de logging para o projeto.
    
    Args:
        log_file: Caminho para arquivo de log (opcional)
        level: Nível de logging (DEBUG, INFO, WARNING, ERROR)
    """
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    # Handler para console
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_formatter = logging.Formatter(log_format)
    console_handler.setFormatter(console_formatter)
    
    # Handler para arquivo (se especificado)
    handlers = [console_handler]
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
        file_handler.setLevel(level)
        file_handler.setFormatter(console_formatter)
        handlers.append(file_handler)
    
    # Configura logger root
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # Remove handlers antigos
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Adiciona novos handlers
    for handler in handlers:
        root_logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """
    Obtém logger configurado.
    
    Args:
        name: Nome do logger (geralmente __name__)
    
    Returns:
        Logger configurado
    """
    return logging.getLogger(name)


# ============================================================================
# VALIDAÇÃO
# ============================================================================

def validate_dataframe_columns(df, required_columns: List[str]) -> bool:
    """
    Valida se DataFrame contém colunas obrigatórias.
    
    Args:
        df: PySpark DataFrame
        required_columns: Lista de colunas obrigatórias
    
    Returns:
        True se todas as colunas existem
    
    Raises:
        ValueError: Se faltam colunas
    """
    missing = set(required_columns) - set(df.columns)
    if missing:
        raise ValueError(f"Colunas obrigatórias faltando: {missing}")
    return True


def validate_path_exists(path: str, create: bool = False) -> bool:
    """
    Valida se caminho existe.
    
    Args:
        path: Caminho a validar
        create: Se True, cria diretório se não existir
    
    Returns:
        True se caminho existe ou foi criado
    
    Raises:
        FileNotFoundError: Se caminho não existe e create=False
    """
    path_obj = Path(path)
    
    if not path_obj.exists():
        if create and path_obj.suffix == '':  # É diretório
            path_obj.mkdir(parents=True, exist_ok=True)
            return True
        else:
            raise FileNotFoundError(f"Caminho não encontrado: {path}")
    
    return True


# ============================================================================
# FORMATAÇÃO E EXPORT
# ============================================================================

def export_dataframe_to_csv(df, output_path: str, coalesce: bool = True) -> None:
    """
    Exporta DataFrame Spark para CSV.
    
    Args:
        df: PySpark DataFrame
        output_path: Caminho de saída
        coalesce: Se True, combina partições antes de salvar
    """
    try:
        if coalesce:
            df = df.coalesce(1)
        
        df.write.csv(output_path, header=True, mode="overwrite")
        logger = get_logger(__name__)
        logger.info(f"✓ DataFrame exportado para {output_path}")
    
    except Exception as e:
        logger = get_logger(__name__)
        logger.error(f"❌ Erro ao exportar DataFrame: {e}")
        raise


def export_dataframe_to_parquet(df, output_path: str) -> None:
    """
    Exporta DataFrame Spark para Parquet.
    
    Args:
        df: PySpark DataFrame
        output_path: Caminho de saída
    """
    try:
        df.write.parquet(output_path, mode="overwrite")
        logger = get_logger(__name__)
        logger.info(f"✓ DataFrame exportado para {output_path}")
    
    except Exception as e:
        logger = get_logger(__name__)
        logger.error(f"❌ Erro ao exportar DataFrame: {e}")
        raise


def export_results_to_json(results: Dict[str, Any], output_path: str) -> None:
    """
    Exporta resultados para JSON.
    
    Args:
        results: Dicionário de resultados
        output_path: Caminho de saída
    """
    try:
        # Converte valores não-serializáveis
        results_serializable = _make_json_serializable(results)
        
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(results_serializable, f, indent=2, ensure_ascii=False)
        
        logger = get_logger(__name__)
        logger.info(f"✓ Resultados exportados para {output_path}")
    
    except Exception as e:
        logger = get_logger(__name__)
        logger.error(f"❌ Erro ao exportar JSON: {e}")
        raise


def _make_json_serializable(obj: Any) -> Any:
    """Converte objetos para formato JSON-serializável."""
    if isinstance(obj, dict):
        return {k: _make_json_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_make_json_serializable(item) for item in obj]
    elif isinstance(obj, (Path, type)):
        return str(obj)
    elif hasattr(obj, '__dict__'):
        return str(obj)
    elif isinstance(obj, (int, float, str, bool, type(None))):
        return obj
    else:
        return str(obj)


# ============================================================================
# ESTATÍSTICAS E ANÁLISE
# ============================================================================

def print_dataframe_stats(df, name: str = "DataFrame") -> None:
    """
    Imprime estatísticas de um DataFrame.
    
    Args:
        df: PySpark DataFrame
        name: Nome para exibição
    """
    logger = get_logger(__name__)
    
    logger.info(f"\n{'='*60}")
    logger.info(f"ESTATÍSTICAS: {name}")
    logger.info(f"{'='*60}")
    logger.info(f"Linhas: {df.count()}")
    logger.info(f"Colunas: {len(df.columns)}")
    logger.info(f"Tipos: {dict(df.dtypes)}")
    logger.info("")


def print_query_plan(df) -> None:
    """Imprime plano de execução de DataFrame."""
    logger = get_logger(__name__)
    logger.info(f"\n{df.explain()}")


# ============================================================================
# GEOLOCALIZAÇÃO
# ============================================================================

def get_continent_from_country(country_code: str) -> str:
    """
    Mapeia código de país para continente.
    
    Args:
        country_code: Código ISO de país (ex: BR, US, DE)
    
    Returns:
        Nome do continente
    """
    country_to_continent = {
        # América do Sul
        'BR': 'South America', 'AR': 'South America', 'CL': 'South America',
        'CO': 'South America', 'PE': 'South America', 'VE': 'South America',
        # América do Norte
        'US': 'North America', 'CA': 'North America', 'MX': 'North America',
        # Europa
        'DE': 'Europe', 'FR': 'Europe', 'GB': 'Europe', 'IT': 'Europe',
        'ES': 'Europe', 'PT': 'Europe', 'NL': 'Europe', 'BE': 'Europe',
        'SE': 'Europe', 'NO': 'Europe', 'RU': 'Europe',
        # Ásia
        'CN': 'Asia', 'JP': 'Asia', 'IN': 'Asia', 'SG': 'Asia',
        'KR': 'Asia', 'HK': 'Asia', 'TH': 'Asia', 'ID': 'Asia',
        # Oceania
        'AU': 'Oceania', 'NZ': 'Oceania',
        # África
        'ZA': 'Africa', 'EG': 'Africa', 'NG': 'Africa',
    }
    
    return country_to_continent.get(country_code.upper(), 'Unknown')


def great_circle_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calcula distância entre dois pontos geográficos em km.
    
    Args:
        lat1, lon1: Coordenadas do ponto 1
        lat2, lon2: Coordenadas do ponto 2
    
    Returns:
        Distância em quilômetros
    """
    import math
    
    R = 6371  # Raio da terra em km
    
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    
    a = math.sin(delta_lat / 2) ** 2 + \
        math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
    
    c = 2 * math.asin(math.sqrt(a))
    
    return R * c


# ============================================================================
# RELATÓRIOS
# ============================================================================

def generate_report_header(title: str) -> str:
    """Gera cabeçalho de relatório."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    header = f"""
{'='*70}
{title:^70}
{'='*70}
Gerado em: {timestamp}
{'='*70}
"""
    return header


def format_metric(value: float, unit: str = "", decimals: int = 2) -> str:
    """Formata métrica para exibição."""
    if isinstance(value, float):
        return f"{value:.{decimals}f}{unit}"
    return f"{value}{unit}"


# ============================================================================
# TIMING E PERFORMANCE
# ============================================================================

class Timer:
    """Context manager para medir tempo de execução."""
    
    def __init__(self, name: str = "Operação"):
        self.name = name
        self.start_time = None
        self.logger = get_logger(__name__)
    
    def __enter__(self):
        self.start_time = datetime.now()
        return self
    
    def __exit__(self, *args):
        elapsed = (datetime.now() - self.start_time).total_seconds()
        self.logger.info(f"⏱️  {self.name}: {elapsed:.2f}s")


# ============================================================================
# INICIALIZAÇÃO GLOBAL
# ============================================================================

# Configura logging por padrão
setup_logging(level=logging.INFO)
