# Worker de sonda RMIFO

O worker em `services/sonda/sonda.js` executa uma coleta e encerra o processo, permitindo agendamento por cron. Ele carrega cabos do PostgreSQL ou de um JSON, usa TCP, HTTP e MTR quando disponíveis e pode produzir estimativas sintéticas. A gravação no banco depende da conexão e das operações individuais; saída zero não garante que todas as amostras tenham sido persistidas.

## Railway

1. Crie um serviço do repositório com Root Directory `services/sonda`.
2. Configure `DATABASE_URL` com a conexão PostgreSQL para carregar cabos e persistir resultados.
3. Configure o cron no serviço, por exemplo `0 6 * * *` (06:00 UTC). O processo termina após a execução.
4. Confira os logs e os registros no banco após cada execução. Sem banco, a sonda pode executar com `--cables-file`, mas não persiste no PostgreSQL; sem cabos, encerra com aviso e código zero.

## Execução e opções

```bash
cd services/sonda
npm install
DATABASE_URL='postgresql://usuario:senha@host:5432/banco' node sonda.js --limit 10
node sonda.js --cables-file /caminho/cabos.json --limit 10
node sonda.js --full-scan --mtr-cycles 15 --timeout 8
```

| Opção | Comportamento |
|---|---|
| `--limit N` | Máximo de cabos; padrão 999. |
| `--full-scan` | Habilita varredura ampliada; desligada por padrão. |
| `--mtr-cycles N` | Ciclos MTR; padrão 15. |
| `--timeout N` | Timeout de sondagem em segundos; padrão 8. |
| `--cables-file ARQUIVO` | Carrega um array JSON; se vazio ou ilegível e houver banco, tenta carregar do banco. |
| `DATABASE_URL` | Conexão PostgreSQL; se ausente ou indisponível, executa sem persistência. |
| `BASE_DIR` | Raiz dos arquivos locais em `data/local/historico/<data>`; padrão é o diretório pai do worker. |
| `LOG_FILE` | Valor registrado no status da execução. |

As opções de limite e varredura são argumentos da linha de comando; `PROBE_LIMIT` e `FULL_SCAN` não são lidos por este worker. A conexão PostgreSQL usa TLS com `rejectUnauthorized: false`; avalie a confiança da rede e dos certificados antes de implantá-la. O arquivo de histórico é local ao ambiente do worker e não substitui o banco.

Leia também [Back-end: arquitetura e operação](../../docs/BACKEND.md).
