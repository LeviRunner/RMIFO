Resiliência da Malha Intercontinental de Fibra Ótica (RMIFO) 

Analysis of the Resilience of Intercontinental Fiber Optic Networks


> "Desenvolvimento de um motor de simulação modular em PySpark para identificar e quantificar pontos críticos de falha na infraestrutura física da Internet Global, cruzando topologia lógica (CAIDA) com mapeamento de cabos submarinos (TeleGeography)."
> 

Descrição Modularizada (Para o GitHub/Currículo)

Data Fusion: Pipeline escalável que integra o CAIDA ITDK (70GB+) com metadados geográficos da TeleGeography, criando um grafo unificado de camadas físicas e lógicas.

Modular Failure Simulator: Motor de simulação que permite "desligar" qualquer nó (cidade/PoP) ou aresta (cabo submarino) para observar a redistribuição do tráfego em tempo real.

Criticality Scoring: Algoritmo customizado que ranqueia conexões continentais com base no impacto sistêmico (aumento de latência/RTT) caso sejam interrompidas.

Global Scope: Capacidade de extrair insights de resiliência para qualquer par de continentes (ex: América Latina-Europa, Ásia-Oceania) através de parâmetros simples de configuração.

>"O framework foi desenhado para ser agnóstico à região. Ao fornecer coordenadas geográficas ou nomes de sistemas de cabos como entrada, o sistema processa bilhões de registros de traceroute para retornar um relatório de vulnerabilidade, identificando se a conectividade continental depende de um 'gargalo' único (Single Point of Failure)."
> 
    
    Tradução para o Inglês

    Project: Modular Global Network Resilience Engine

    * Objective: Quantify the systemic risk of transcontinental internet infrastructure.

    * Stack: PySpark, GraphFrames, Python (Pandas/Geopandas for visualization).

    * Key Achievement: Engineered a distributed data pipeline to process 70GB of raw topology data, enabling real-time simulation of "what-if" failure scenarios for global submarine fiber-optic links.

    * Methodology: Leveraged Betweenness Centrality and Shortest Path Algorithms to identify critical bottlenecks in the global internet backbone.