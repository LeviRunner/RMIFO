exemplo de árvore de diretórios para o seu Global-Network-Resilience-Framework:

global-network-resilience/
├── data/                       # NÃO subir os 70GB aqui (use .gitignore)
│   ├── raw/                    # Onde os arquivos do CAIDA (.gz) ficarão localmente
│   │   ├── itdk-2024-08-links.gz
│   │   ├── itdk-2024-08-nodes.gz
│   │   └── itdk-2024-08-nodes.geo.gz
│   ├── processed/              # Parquet files (otimizados para Spark)
│   │   └── global_graph_topology.parquet/
│   └── external/               # Dados da TeleGeography e GeoIP
│       ├── submarine_cables.geojson
│       └── geoip_database.csv
├── config/                     # A "Mente" Modular do projeto
│   ├── main_config.yaml        # Definição de continentes e caminhos
│   └── simulation_scenarios.json
├── src/                        # Código fonte modularizado
│   ├── __init__.py
│   ├── data_ingestion.py       # Script Spark para ler os .gz
│   ├── graph_processor.py      # Lógica de Grafos (PySpark/GraphFrames)
│   ├── failure_simulator.py    # Algoritmos de "What-if"
│   └── utils.py                # Funções de suporte e logs
├── notebooks/                  # Apenas para prototipagem e EDA
│   └── 01_initial_data_exploration.ipynb
├── tests/                      # Testes unitários (Vital para mestrado)
│   └── test_graph_logic.py
├── results/                    # Outputs da simulação
│   ├── reports/                # Relatórios em PDF/Markdown
│   └── plots/                  # Mapas de calor e gráficos de latência
├── .gitignore                  # Para ignorar a pasta data/raw e arquivos pesados
├── requirements.txt            # Bibliotecas necessárias (pyspark, pandas, pyyaml)
├── LICENSE
└── README.md                   # Documentação mestre do projeto

Detalhe dos Arquivos Estratégicos (Exemplos de Conteúdo)
1. config/main_config.yaml
É aqui que você prova a modularidade. O usuário não mexe no código, apenas neste arquivo:
project_name: "Global Resilience Engine"
spark_settings:
  executor_memory: "8g"
    app_name: "NetworkSim"

    analysis_target:
      source_continent: "South America"
        target_continent: "Africa"
          metric: "avg_rtt_impact"

          simulation:
            remove_cable: "South Atlantic Inter Link (SAIL)"

            2. .gitignore (MUITO IMPORTANTE)
            Nunca suba os 70GB para o GitHub. Seu arquivo deve conter:
            data/raw/*
            data/processed/*
            *.gz
            .DS_Store
            __pycache__/
            .venv/

            3. src/data_ingestion.py (Snippet de exemplo)
            Como o Spark vai ler seus arquivos brutos de forma eficiente:
            def load_caida_data(spark, path):
                """Lê arquivos .gz nativamente no Spark"""
                    return spark.read.option("sep", "\t").csv(path)

                    Por que essa estrutura garante pontos na McGill?
                     * Separação de Preocupações: Você não mistura código de processamento (src) com exploração visual (notebooks).
                      * Reprodutibilidade: Com o requirements.txt e o main_config.yaml, qualquer pesquisador pode clonar seu repo e rodar a mesma simulação.
                       * Higiene de Dados: Usar um .gitignore correto mostra que você entende as limitações de versionamento de Big Data.
                       Gostaria que eu gerasse o código inicial do README.md (em inglês) para esse repositório, já com as seções acadêmicas que um orientador esperaria ver?
