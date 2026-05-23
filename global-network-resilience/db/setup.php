<?php
/**
 * setup.php — Aplica schema.sql e seed.sql via PDO (PHP nativo).
 * Chamado pelo start.sh no boot do Railway.
 * Usa a mesma lógica de conexão do api.php.
 */
declare(strict_types=1);

function log_msg(string $msg): void {
    echo '[RMIFO-setup] ' . $msg . PHP_EOL;
}

// ── Resolve credenciais (mesma lógica do api.php) ─────────────────────────────
$pgSsl = false;

$dbUrl = getenv('DATABASE_URL') ?: '';
if ($dbUrl !== '') {
    $p        = parse_url($dbUrl);
    $pgHost   = $p['host'] ?? 'localhost';
    $pgPort   = (int) ($p['port'] ?? 5432);
    $pgDb     = ltrim($p['path'] ?? '/railway', '/');
    $pgUser   = urldecode($p['user'] ?? 'postgres');
    $pgPass   = urldecode($p['pass'] ?? '');
    // Railway TCP proxy usa porta alta (ex: 46600) → requer SSL
    $pgSsl    = ($pgPort !== 5432 && $pgPort !== 55432 && $pgPort !== 5433);
    log_msg("DATABASE_URL detectada — host={$pgHost} port={$pgPort} db={$pgDb} ssl=" . ($pgSsl ? 'yes' : 'no'));
} else {
    $pgHost = getenv('PGHOST') ?: '';
    if ($pgHost !== '' && $pgHost[0] !== '/') {
        $pgPort = (int) (getenv('PGPORT') ?: 5432);
        $pgDb   = getenv('PGDATABASE') ?: getenv('POSTGRES_DB') ?: 'railway';
        $pgUser = getenv('PGUSER')     ?: getenv('POSTGRES_USER')     ?: 'postgres';
        $pgPass = getenv('PGPASSWORD') ?: getenv('POSTGRES_PASSWORD') ?: '';
        $pgSsl  = !str_ends_with($pgHost, '.railway.internal') && !str_ends_with($pgHost, '.local');
        log_msg("PGHOST detectado — host={$pgHost} port={$pgPort} db={$pgDb} ssl=" . ($pgSsl ? 'yes' : 'no'));
    } else {
        log_msg('Nenhuma variável de banco detectada — abortando setup.');
        exit(0); // Não é erro fatal: sobe o PHP server sem banco
    }
}

// ── Conecta com retry (aguarda o Postgres subir) ───────────────────────────────
$pdo = null;
$dsn = "pgsql:host={$pgHost};port={$pgPort};dbname={$pgDb}" . ($pgSsl ? ';sslmode=require' : '');

for ($i = 1; $i <= 20; $i++) {
    try {
        $pdo = new PDO($dsn, $pgUser, $pgPass, [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);
        log_msg("Banco disponível após {$i} tentativa(s).");
        break;
    } catch (PDOException $e) {
        log_msg("Tentativa {$i}/20 falhou: " . $e->getMessage());
        if ($i < 20) sleep(3);
    }
}

if (!$pdo) {
    log_msg('Não foi possível conectar ao banco após 60 s — continuando sem schema.');
    exit(0);
}

// ── Executa um arquivo SQL statement por statement ────────────────────────────
function run_sql_file(PDO $pdo, string $path): void {
    log_msg("Executando: {$path}");
    $sql = file_get_contents($path);
    if ($sql === false) {
        log_msg("Arquivo não encontrado: {$path}");
        return;
    }

    // Divide por ; mas respeita blocos $$ (funções/procedures)
    $statements = preg_split('/;\s*$/m', $sql);
    $ok = 0; $err = 0;
    foreach ($statements as $stmt) {
        $stmt = trim($stmt);
        if ($stmt === '' || str_starts_with($stmt, '--')) continue;
        try {
            $pdo->exec($stmt);
            $ok++;
        } catch (PDOException $e) {
            // Erros de "já existe" são normais em re-deploys
            $msg = $e->getMessage();
            if (str_contains($msg, 'already exists') || str_contains($msg, 'já existe')) {
                $ok++;
            } else {
                log_msg("  AVISO: " . trim($msg));
                $err++;
            }
        }
    }
    log_msg("  Concluído: {$ok} OK, {$err} avisos.");
}

$dir = dirname(__DIR__) . '/db';

// Schema (tabelas + views)
run_sql_file($pdo, $dir . '/schema.sql');

// Seed apenas se cables estiver vazia
try {
    $count = (int) $pdo->query('SELECT COUNT(*) FROM cables')->fetchColumn();
    if ($count === 0) {
        log_msg("Tabela cables vazia — aplicando seed...");
        run_sql_file($pdo, $dir . '/seed.sql');
    } else {
        log_msg("Tabela cables já tem {$count} registros — seed ignorado.");
    }
} catch (PDOException $e) {
    log_msg("Erro ao verificar cables: " . $e->getMessage());
}

log_msg("Setup concluído.");
