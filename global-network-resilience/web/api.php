<?php
declare(strict_types=1);

// ── Conexão PostgreSQL ────────────────────────────────────────────────────────
// Prioridade:
//   1. DATABASE_URL  (Railway / Heroku / Render — formato postgresql://user:pass@host:port/db)
//   2. PGHOST + PGPORT + PGDATABASE + PGUSER + PGPASSWORD  (Railway variables explícitas)
//   3. PG_SOCKET + PG_PORT  (setup local com socket Unix)
// workspaceRoot: used for local data/local/ paths (history, logs)
// On Railway these paths won't exist — that's fine, it falls back to DB.
$workspaceRoot = getenv('WORKSPACE_ROOT') ?: '/tmp';

// dataExternal: where static external files live (geojson, csv…)
// Primary: inside the project dir (works on Railway rootDirectory + locally)
// Fallback: repo root / data/external (legacy local layout)
$_projectDir  = dirname(__DIR__); // = global-network-resilience/
$dataExternal = is_dir($_projectDir . '/data/external')
    ? $_projectDir . '/data/external'
    : (is_dir($_projectDir . '/../data/external')
        ? realpath($_projectDir . '/../data/external')
        : $workspaceRoot . '/data/external');

$pgSsl = false;

// Lê variável de ambiente de todas as fontes possíveis
function _env(string $key, string $default = ''): string {
    $v = getenv($key);
    if ($v !== false && $v !== '') return $v;
    if (isset($_ENV[$key])    && $_ENV[$key]    !== '') return $_ENV[$key];
    if (isset($_SERVER[$key]) && $_SERVER[$key] !== '') return (string)$_SERVER[$key];
    return $default;
}

// DATABASE_URL > DATABASE_PUBLIC_URL > PGHOST vars > local socket
$_dbUrl = _env('DATABASE_URL');
if ($_dbUrl === '') $_dbUrl = _env('DATABASE_PUBLIC_URL');
if ($_dbUrl !== '') {
    $_p       = parse_url($_dbUrl);
    $pgSocket = $_p['host'] ?? 'localhost';
    $pgPort   = (int) ($_p['port'] ?? 5432);
    $pgDbname = ltrim($_p['path'] ?? '/railway', '/');
    $pgUser   = urldecode($_p['user'] ?? 'postgres');
    $pgPass   = urldecode($_p['pass'] ?? '');
    $pgSsl    = ($pgPort !== 5432 && $pgPort !== 55432);
} else {
    $_pghost = _env('PGHOST');
    if ($_pghost !== '' && $_pghost[0] !== '/') {
        $pgSocket = $_pghost;
        $pgPort   = (int) (_env('PGPORT', '5432'));
        $pgDbname = _env('PGDATABASE') ?: _env('POSTGRES_DB', 'railway');
        $pgUser   = _env('PGUSER')     ?: _env('POSTGRES_USER', 'postgres');
        $pgPass   = _env('PGPASSWORD') ?: _env('POSTGRES_PASSWORD', '');
        $pgSsl    = !str_ends_with($pgSocket, '.railway.internal') && !str_ends_with($pgSocket, '.local');
    } else {
        $pgSocket = _env('PG_SOCKET') ?: ($workspaceRoot . '/.local/run');
        $pgPort   = (int) (_env('PG_PORT', '55432'));
        $pgDbname = 'global_network_resilience';
        $pgUser   = 'postgres';
        $pgPass   = '';
        $pgSsl    = false;
    }
}
// ─────────────────────────────────────────────────────────────────────────────

// Evita warnings/notices em HTML antes do JSON da API.
ini_set('display_errors', '0');
set_error_handler(static function (int $severity, string $message, string $file, int $line): bool {
    if (!(error_reporting() & $severity)) {
        return false;
    }
    throw new ErrorException($message, 0, $severity, $file, $line);
});

// ── Rate limiting ─────────────────────────────────────────────────────────────
// Janela de 1 minuto por IP (hash SHA-256). Limites:
//   - probe: 5 req/min  (evita uso como port scanner)
//   - demais: 60 req/min (uso normal da API)
function checkRateLimit(PDO $pdo, string $action): void
{
    $rawIp  = $_SERVER['HTTP_X_FORWARDED_FOR'] ?? $_SERVER['REMOTE_ADDR'] ?? '0.0.0.0';
    $ip     = trim(explode(',', $rawIp)[0]);
    // daily_start: 1 req/10min; probe: 5 req/min; demais: 60 req/min
    $limit  = match($action) {
        'daily_start' => 1,
        'probe'       => 5,
        default       => 60,
    };
    $windowMins = ($action === 'daily_start') ? 10 : 1;
    $ipHash = hash('sha256', $ip . $action); // hash separado por ação
    $window = gmdate('Y-m-d H:') . str_pad((string)((int)(gmdate('i') / $windowMins) * $windowMins), 2, '0', STR_PAD_LEFT) . ':00+00';

    try {
        $pdo->prepare(
            "INSERT INTO api_rate_limit (ip_hash, window_start, req_count)
             VALUES (:h, :w::timestamptz, 1)
             ON CONFLICT (ip_hash, window_start) DO UPDATE
             SET req_count = api_rate_limit.req_count + 1"
        )->execute([':h' => $ipHash, ':w' => $window]);

        $stmt = $pdo->prepare(
            "SELECT req_count FROM api_rate_limit
             WHERE ip_hash = :h AND window_start = :w::timestamptz"
        );
        $stmt->execute([':h' => $ipHash, ':w' => $window]);
        $count = (int) ($stmt->fetchColumn() ?: 0);

        if ($count > $limit) {
            http_response_code(429);
            header('Content-Type: application/json; charset=utf-8');
            header('Retry-After: 60');
            echo json_encode(['error' => 'Muitas requisições. Aguarde 1 minuto.']);
            exit;
        }

        // Limpeza periódica de janelas antigas (1 em 20 chances)
        if (random_int(1, 20) === 1) {
            $pdo->prepare(
                "DELETE FROM api_rate_limit WHERE window_start < NOW() - INTERVAL '10 minutes'"
            )->execute();
        }
    } catch (\Throwable) {
        // Tabela ainda não criada (primeiro deploy) — ignora silenciosamente
    }
}

// ── Validação de IP para probe ────────────────────────────────────────────────
// Restringe sondas a IPs registrados em endpoint_ip_samples.
// Impede uso do servidor como scanner de IPs arbitrários.
function assertProbeIpAllowed(PDO $pdo, string $ip): void
{
    if ($ip === '') {
        return; // IP vazio → probe usará fallback sintético, sem risco
    }
    if (!filter_var($ip, FILTER_VALIDATE_IP)) {
        throw new InvalidArgumentException('Formato de IP inválido.');
    }
    $stmt = $pdo->prepare(
        "SELECT 1 FROM endpoint_ip_samples WHERE ip_start::text = :ip LIMIT 1"
    );
    $stmt->execute([':ip' => $ip]);
    if (!$stmt->fetchColumn()) {
        throw new InvalidArgumentException('IP não pertence a nenhum endpoint cadastrado.');
    }
}

function getDb(string $host, int $port, string $dbname, string $user, string $pass = '', bool $ssl = false): PDO
{
    $dsn = "pgsql:host={$host};port={$port};dbname={$dbname}";
    if ($ssl) {
        $dsn .= ';sslmode=require';
    }
    return new PDO($dsn, $user, $pass, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        PDO::ATTR_EMULATE_PREPARES => false,
    ]);
}

function cableColumns(string $alias = 'c'): string
{
    return implode(",\n                ", [
        "{$alias}.cable_id",
        "{$alias}.cable_name",
        "{$alias}.owners",
        "{$alias}.capacity_gbps",
        "{$alias}.length_km",
        "{$alias}.rfs_year",
        "{$alias}.is_operational",
        "{$alias}.landing_points_count",
        "{$alias}.suppliers",
    ]);
}

function securityHeaders(): void
{
    header('X-Content-Type-Options: nosniff');
    header('X-Frame-Options: DENY');
    header('Referrer-Policy: strict-origin-when-cross-origin');
    header('Permissions-Policy: geolocation=(), microphone=(), camera=()');
    header("Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline' https://fonts.googleapis.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://fonts.gstatic.com; font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'");
}

function jsonResponse(array $payload, int $status = 200): never
{
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    securityHeaders();
    echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

function normalizeBool(mixed $value): ?bool
{
    if ($value === null) {
        return null;
    }
    if (is_bool($value)) {
        return $value;
    }
    $text = strtolower(trim((string) $value));
    if (in_array($text, ['t', 'true', '1', 'yes'], true)) {
        return true;
    }
    if (in_array($text, ['f', 'false', '0', 'no'], true)) {
        return false;
    }
    return null;
}

function fetchCableDetails(PDO $pdo, string $cableId): array
{
    $detailsStmt = $pdo->prepare(
        'SELECT '
        . cableColumns('c')
        . ', cm.public_url, cm.source_url, cm.notes, cm.is_planned, cm.raw_length, cm.raw_rfs
           FROM cables c
           LEFT JOIN cable_metadata cm ON cm.cable_id = c.cable_id
           WHERE c.cable_id = :id'
    );
    $detailsStmt->execute([':id' => $cableId]);
    $cable = $detailsStmt->fetch();

    if (!$cable) {
        throw new RuntimeException('Cabo não encontrado.');
    }

    $cable['is_operational'] = normalizeBool($cable['is_operational']);
    $cable['is_planned'] = normalizeBool($cable['is_planned']);

    $endpointStmt = $pdo->prepare(
        "SELECT
            lp.endpoint_id,
            lp.endpoint_name,
            lp.city_name,
            lp.country_name,
            lp.country_code,
            lp.latitude,
            lp.longitude,
            lp.geo_source,
            clp.endpoint_order,
            clp.is_tbd,
            COALESCE(
                json_agg(
                    json_build_object(
                        'sample_order', eis.sample_order,
                        'network', eis.network,
                        'ip_start', eis.ip_start,
                        'ip_end', eis.ip_end,
                        'country_name', eis.country_name,
                        'country_code', eis.country_code,
                        'region', eis.region,
                        'city', eis.city,
                        'latitude', eis.latitude,
                        'longitude', eis.longitude
                    ) ORDER BY eis.sample_order
                ) FILTER (WHERE eis.sample_order IS NOT NULL),
                '[]'::json
            ) AS ip_samples
         FROM cable_landing_points clp
         JOIN landing_points lp ON lp.endpoint_id = clp.endpoint_id
         LEFT JOIN endpoint_ip_samples eis ON eis.endpoint_id = lp.endpoint_id
         WHERE clp.cable_id = :id
         GROUP BY
            lp.endpoint_id,
            lp.endpoint_name,
            lp.city_name,
            lp.country_name,
            lp.country_code,
            lp.latitude,
            lp.longitude,
            lp.geo_source,
            clp.endpoint_order,
            clp.is_tbd
         ORDER BY clp.endpoint_order, lp.endpoint_name"
    );
    $endpointStmt->execute([':id' => $cableId]);
    $endpoints = $endpointStmt->fetchAll();

    foreach ($endpoints as &$endpoint) {
        $endpoint['is_tbd'] = normalizeBool($endpoint['is_tbd']);
        $endpoint['ip_samples'] = json_decode((string) $endpoint['ip_samples'], true, 512, JSON_THROW_ON_ERROR);
    }
    unset($endpoint);

    return [
        'cable' => $cable,
        'endpoints' => $endpoints,
    ];
}

function listCountries(PDO $pdo): array
{
    $stmt = $pdo->query(
        "SELECT DISTINCT country_name
         FROM landing_points
         WHERE country_name IS NOT NULL AND country_name <> ''
         ORDER BY country_name"
    );
    return array_map(static fn(array $row): string => (string) $row['country_name'], $stmt->fetchAll());
}

function buildCountryGraph(PDO $pdo): array
{
    $stmt = $pdo->query(
        "SELECT
            clp.cable_id,
            c.cable_name,
            c.length_km,
            clp.endpoint_order,
            lp.country_name
         FROM cable_landing_points clp
         JOIN landing_points lp ON lp.endpoint_id = clp.endpoint_id
         JOIN cables c ON c.cable_id = clp.cable_id
         WHERE lp.country_name IS NOT NULL AND lp.country_name <> ''
         ORDER BY clp.cable_id, clp.endpoint_order, lp.country_name"
    );

    $cableCountries = [];
    $cableMeta = [];

    foreach ($stmt->fetchAll() as $row) {
        $cableId = (string) ($row['cable_id'] ?? '');
        $country = (string) ($row['country_name'] ?? '');
        if ($cableId === '' || $country === '') {
            continue;
        }
        $order = (int) ($row['endpoint_order'] ?? 0);
        $cableCountries[$cableId][] = [
            'order' => $order,
            'country' => $country,
        ];
        $cableMeta[$cableId] = [
            'name' => (string) ($row['cable_name'] ?? $cableId),
            'length_km' => (int) ($row['length_km'] ?? 0),
        ];
    }

    $graph = [];
    foreach ($cableCountries as $cableId => $orderedStops) {
        usort($orderedStops, static fn(array $a, array $b): int => $a['order'] <=> $b['order']);

        $countries = [];
        foreach ($orderedStops as $stop) {
            $country = (string) ($stop['country'] ?? '');
            if ($country === '') {
                continue;
            }
            if ($countries === [] || $countries[count($countries) - 1] !== $country) {
                $countries[] = $country;
            }
        }

        $countryCount = count($countries);
        if ($countryCount < 2) {
            continue;
        }

        $length = max(1, (int) ($cableMeta[$cableId]['length_km'] ?? 0));
        $weight = max(20.0, $length / max(1, $countryCount - 1));

        for ($i = 0; $i < $countryCount - 1; $i++) {
            $a = $countries[$i];
            $b = $countries[$i + 1];
            if ($a === $b) {
                continue;
            }

            $edge = [
                'to' => $b,
                'cable_id' => $cableId,
                'cable_name' => (string) ($cableMeta[$cableId]['name'] ?? $cableId),
                'weight' => $weight,
                'length_km' => $length,
            ];
            $graph[$a][] = $edge;
            $edge['to'] = $a;
            $graph[$b][] = $edge;
        }
    }

    return $graph;
}

function findTopRoutes(array $graph, string $countryA, string $countryB, int $limit = 3, ?string $blockedCableId = null): array
{
    if ($countryA === $countryB) {
        return [];
    }

    $queue = new SplPriorityQueue();
    $queue->setExtractFlags(SplPriorityQueue::EXTR_BOTH);
    $queue->insert([
        'country' => $countryA,
        'countries' => [$countryA],
        'cables' => [],
        'score' => 0.0,
        'distance_km' => 0,
    ], 0.0);

    $routes = [];
    $seenSignatures = [];
    $iterations = 0;
    $maxIterations = 20000;
    $maxDepth = 6;

    while (!$queue->isEmpty() && count($routes) < $limit && $iterations < $maxIterations) {
        $iterations++;
        $item = $queue->extract()['data'];
        $current = (string) $item['country'];

        if ($current === $countryB && count($item['cables']) > 0) {
            $signature = implode('|', array_map(static fn(array $c): string => $c['cable_id'], $item['cables']));
            if (!isset($seenSignatures[$signature])) {
                $seenSignatures[$signature] = true;
                $routes[] = [
                    'score' => round((float) $item['score'], 2),
                    'distance_km' => (int) $item['distance_km'],
                    'hops' => count($item['cables']),
                    'countries' => $item['countries'],
                    'cables' => $item['cables'],
                ];
            }
            continue;
        }

        if (count($item['countries']) > $maxDepth) {
            continue;
        }

        foreach (($graph[$current] ?? []) as $edge) {
            if ($blockedCableId !== null && $edge['cable_id'] === $blockedCableId) {
                continue;
            }

            $next = (string) $edge['to'];
            if (in_array($next, $item['countries'], true)) {
                continue;
            }

            $newCountries = $item['countries'];
            $newCountries[] = $next;
            $newCables = $item['cables'];
            $newCables[] = [
                'cable_id' => (string) $edge['cable_id'],
                'cable_name' => (string) $edge['cable_name'],
                'from' => $current,
                'to' => $next,
            ];
            $newScore = (float) $item['score'] + (float) $edge['weight'];
            $newDistance = (int) $item['distance_km'] + (int) $edge['length_km'];

            $queue->insert([
                'country' => $next,
                'countries' => $newCountries,
                'cables' => $newCables,
                'score' => $newScore,
                'distance_km' => $newDistance,
            ], -$newScore);
        }
    }

    usort($routes, static fn(array $a, array $b): int => $a['score'] <=> $b['score']);
    return array_slice($routes, 0, $limit);
}

function hasCountryPath(array $graph, string $countryA, string $countryB, ?string $blockedCableId = null, int $maxDepth = 8): bool
{
    if ($countryA === $countryB) {
        return true;
    }

    $queue = new SplQueue();
    $queue->enqueue([$countryA, 0]);
    $visited = [$countryA => true];

    while (!$queue->isEmpty()) {
        [$current, $depth] = $queue->dequeue();
        if ($depth >= $maxDepth) {
            continue;
        }

        foreach (($graph[$current] ?? []) as $edge) {
            if ($blockedCableId !== null && $edge['cable_id'] === $blockedCableId) {
                continue;
            }
            $next = (string) $edge['to'];
            if ($next === $countryB) {
                return true;
            }
            if (isset($visited[$next])) {
                continue;
            }
            $visited[$next] = true;
            $queue->enqueue([$next, $depth + 1]);
        }
    }

    return false;
}

function cableTelemetry(array $details): array
{
    $cable = $details['cable'];
    $lengthKm = max(100, (int) ($cable['length_km'] ?? 1000));
    $capacityGbps = max(1.0, (float) ($cable['capacity_gbps'] ?? max(2.0, 12000 / $lengthKm)));
    $endpointCount = max(2, (int) ($cable['landing_points_count'] ?? 2));

    $latencyMs = round(max(12.0, ($lengthKm / 200.0) * 1.35), 2);
    $trafficMbps = round($capacityGbps * 1000.0 * (0.45 + min(0.4, $endpointCount / 100.0)), 2);
    $packetsPerSecond = (int) round(($trafficMbps * 1000000.0) / (8.0 * 900.0));
    $connectedMachines = (int) round(($trafficMbps * 1000000.0) / 2500000.0);

    return [
        'latency_ms' => $latencyMs,
        'traffic_mbps' => $trafficMbps,
        'packets_per_second' => $packetsPerSecond,
        'connected_machines_estimate' => $connectedMachines,
        'formula_notes' => [
            'latency' => 'estimativa por comprimento do cabo',
            'traffic' => 'capacidade efetiva aproximada por ocupacao media',
            'machines' => 'estimativa de 2.5 Mbps por maquina ativa',
        ],
    ];
}

function fetchActiveAlerts(PDO $pdo): array
{
    $tableStmt = $pdo->query("SELECT to_regclass('public.telemetry_alerts') AS table_name");
    if (empty($tableStmt->fetch()['table_name'])) {
        return [];
    }

    $stmt = $pdo->prepare(
        "SELECT
            ta.alert_id,
            ta.cable_id,
            c.cable_name,
            ta.metric,
            ta.severity,
            ta.current_value,
            ta.baseline_value,
            ta.detected_at,
            ta.description
         FROM telemetry_alerts ta
         JOIN cables c ON c.cable_id = ta.cable_id
         WHERE ta.resolved_at IS NULL
         ORDER BY ta.detected_at DESC
         LIMIT 20"
    );
    $stmt->execute();
    return $stmt->fetchAll();
}

// ---------------------------------------------------------------------------
// Helpers: cada função tenta um único protocolo e retorna null se falhar.
// ---------------------------------------------------------------------------

function makeMicroTs(float $t): string
{
    $usec = (string) (int) (($t - (int) $t) * 1000000);
    return gmdate('Y-m-d\TH:i:s', (int) $t)
        . '.' . str_pad($usec, 6, '0', STR_PAD_LEFT)
        . '+00:00';
}

function calcJitter(array $rtts): ?float
{
    if (count($rtts) < 2) {
        return null;
    }
    $mean     = array_sum($rtts) / count($rtts);
    $variance = array_sum(array_map(fn(float $r): float => ($r - $mean) ** 2, $rtts)) / (count($rtts) - 1);
    return round(sqrt($variance), 2);
}

/**
 * Protocolo 1 — TCP SYN/ACK connect.
 * Tenta cada porta até achar uma aberta, depois coleta N amostras adicionais.
 */
function tryTcpConnect(string $ip, array $ports, int $targetSamples, float $timeout = 2.5): ?array
{
    $rtts          = [];
    $connectedPort = null;
    $sentAt        = null;
    $samples       = 0;

    foreach ($ports as $port) {
        $tStart  = microtime(true);
        $conn    = @fsockopen($ip, $port, $errno, $errstr, $timeout);
        $elapsed = round((microtime(true) - $tStart) * 1000, 2);
        if ($conn) {
            fclose($conn);
            $connectedPort = $port;
            $sentAt        = makeMicroTs($tStart);
            $rtts[]        = $elapsed;
            $samples++;
            break;
        }
    }

    if ($connectedPort === null) {
        return null;
    }

    for ($i = 0; $i < $targetSamples - 1; $i++) {
        $tStart  = microtime(true);
        $conn    = @fsockopen($ip, $connectedPort, $errno, $errstr, $timeout);
        $elapsed = round((microtime(true) - $tStart) * 1000, 2);
        if ($conn) {
            fclose($conn);
            $rtts[] = $elapsed;
            $samples++;
        }
    }

    return [
        'method'      => "tcp-connect-port{$connectedPort}",
        'avg_rtt_ms'  => round(array_sum($rtts) / count($rtts), 2),
        'jitter_ms'   => calcJitter($rtts),
        'packet_loss' => round((1 - ($samples / $targetSamples)) * 100, 1),
        'samples'     => $samples,
        'total'       => $targetSamples,
        'sent_at'     => $sentAt,
        'received_at' => makeMicroTs(microtime(true)),
        'extra'       => ['port' => $connectedPort],
    ];
}

/**
 * Protocolo 2 — ICMP Echo Request (ping).
 * Opera na camada 3; passa em firewalls que bloqueiam TCP.
 * Requer que o binário ping esteja disponível e com setuid (padrão em Linux).
 */
function tryIcmpPing(string $ip, int $count = 4): ?array
{
    if (!function_exists('exec')) {
        return null;
    }

    $sentAt  = makeMicroTs(microtime(true));
    $escaped = escapeshellarg($ip);
    $output  = [];
    $retval  = 0;
    exec("ping -c {$count} -W 2 -q {$escaped} 2>/dev/null", $output, $retval);
    $receivedAt = makeMicroTs(microtime(true));

    $summary = implode("\n", $output);

    preg_match('/(\d+(?:\.\d+)?)% packet loss/', $summary, $lossM);
    $loss = isset($lossM[1]) ? (float) $lossM[1] : 100.0;

    // rtt min/avg/max/mdev — mdev é o desvio médio (≈ jitter)
    preg_match('/rtt min\/avg\/max\/mdev = [\d.]+\/([\d.]+)\/[\d.]+\/([\d.]+) ms/', $summary, $rttM);
    if (empty($rttM[1])) {
        return null;
    }

    preg_match('/(\d+) received/', $summary, $rcvM);
    $received = isset($rcvM[1]) ? (int) $rcvM[1] : 0;
    if ($received === 0) {
        return null;
    }

    return [
        'method'      => 'icmp-echo',
        'avg_rtt_ms'  => round((float) $rttM[1], 2),
        'jitter_ms'   => round((float) $rttM[2], 2),
        'packet_loss' => $loss,
        'samples'     => $received,
        'total'       => $count,
        'sent_at'     => $sentAt,
        'received_at' => $receivedAt,
        'extra'       => [],
    ];
}

/**
 * Protocolo 3 — HTTP HEAD request.
 * Usa handshake HTTP/HTTPS; muitos firewalls permitem tráfego Web mesmo
 * quando ICMP está bloqueado. Requer a extensão curl.
 */
function tryHttpHead(string $ip, int $timeoutSec = 3): ?array
{
    if (!function_exists('curl_init')) {
        return null;
    }

    $sentAt = makeMicroTs(microtime(true));

    foreach (['https', 'http'] as $scheme) {
        $ch = curl_init("{$scheme}://{$ip}/");
        curl_setopt_array($ch, [
            CURLOPT_NOBODY         => true,
            CURLOPT_TIMEOUT        => $timeoutSec,
            CURLOPT_CONNECTTIMEOUT => $timeoutSec,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_FOLLOWLOCATION => false,
            CURLOPT_SSL_VERIFYPEER => false,
            CURLOPT_SSL_VERIFYHOST => false,
            CURLOPT_USERAGENT      => 'RMIFO-NetworkProbe/1.2',
        ]);
        curl_exec($ch);
        $httpCode   = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
        $connectMs  = round((float) curl_getinfo($ch, CURLINFO_CONNECT_TIME) * 1000, 2);
        curl_close($ch);

        if ($httpCode > 0) {
            return [
                'method'      => "http-head-{$scheme}",
                'avg_rtt_ms'  => $connectMs,
                'jitter_ms'   => null,
                'packet_loss' => 0.0,
                'samples'     => 1,
                'total'       => 1,
                'sent_at'     => $sentAt,
                'received_at' => makeMicroTs(microtime(true)),
                'extra'       => ['http_code' => $httpCode, 'scheme' => $scheme],
            ];
        }
    }

    return null;
}

// ---------------------------------------------------------------------------
// Orquestrador principal: TCP → ICMP → HTTP → sintético.
// ---------------------------------------------------------------------------

function runNetworkProbe(string $targetIp, string $cableId, string $endpointId, PDO $pdo, bool $fullScan = false): array
{
    $startAll      = microtime(true);
    $probePorts    = $fullScan ? [443, 80, 22, 8080, 8443] : [443, 80, 22];
    $targetSamples = $fullScan ? 5 : 4;
    $probeSteps    = [];      // protocolos tentados, em ordem
    $hit           = null;   // primeiro resultado bem-sucedido

    if (filter_var($targetIp, FILTER_VALIDATE_IP)) {
        $probeSteps[] = 'tcp';
        $hit          = tryTcpConnect($targetIp, $probePorts, $targetSamples);

        if ($hit === null) {
            $probeSteps[] = 'icmp';
            $hit          = tryIcmpPing($targetIp, $targetSamples);
        }

        if ($hit === null) {
            $probeSteps[] = 'http';
            $hit          = tryHttpHead($targetIp);
        }
    }

    if ($hit !== null) {
        $reachable = true;
        $avgRtt    = $hit['avg_rtt_ms'];
        $jitterMs  = $hit['jitter_ms'];
        $packetLoss = $hit['packet_loss'];
        $samples   = $hit['samples'];
        $method    = $hit['method'];
        $sentAt    = $hit['sent_at'];
        $receivedAt = $hit['received_at'];

        $note = match(true) {
            str_starts_with($method, 'tcp-connect') =>
                "TCP connect em {$targetIp} (porta " . ($hit['extra']['port'] ?? '?') . "). {$samples}/{$hit['total']} amostras.",
            $method === 'icmp-echo' =>
                "ICMP echo para {$targetIp}. {$samples}/{$hit['total']} pacotes recebidos.",
            str_starts_with($method, 'http-head') =>
                "HTTP HEAD em {$targetIp} via " . strtoupper($hit['extra']['scheme'] ?? 'http')
                . " — código " . ($hit['extra']['http_code'] ?? '?') . '.',
            default => "Probe bem-sucedido para {$targetIp}.",
        };
        if ($fullScan && str_starts_with($method, 'tcp-connect')) {
            $note .= ' Sonda completa — portas testadas: ' . implode(', ', $probePorts) . '.';
        }
    } else {
        // Fallback sintético baseado no comprimento do cabo
        $cableStmt = $pdo->prepare('SELECT length_km FROM cables WHERE cable_id = :id');
        $cableStmt->execute([':id' => $cableId]);
        $cable    = $cableStmt->fetch();
        $lengthKm = max(100, (int) ($cable['length_km'] ?? 5000));
        $baseRtt  = max(12.0, ($lengthKm / 200.0) * 1.35);

        $reachable  = true;
        $avgRtt     = round($baseRtt * (0.95 + (mt_rand(0, 10) / 100)), 2);
        $jitterMs   = round($avgRtt * 0.06, 2);
        $packetLoss = round(max(0.05, min(2.0, 0.12 + (mt_rand(0, 10) / 100))), 2);
        $samples    = $targetSamples;
        $method     = 'synthetic_fallback';
        $sentAt     = null;
        $receivedAt = null;

        $triedList = implode(' → ', array_map('strtoupper', $probeSteps));
        $note = filter_var($targetIp, FILTER_VALIDATE_IP)
            ? "IP {$targetIp} não respondeu via {$triedList}. Estimativa sintética baseada no comprimento do cabo ({$lengthKm} km)."
            : 'IP inválido ou não fornecido. Usando estimativa sintética.';
    }

    $throughputMbps = ($avgRtt > 0) ? round((1500 * 8 / ($avgRtt / 1000)) / 1e6, 3) : null;

    $hopCount    = max(3, min(14, (int) round($avgRtt / 12)));
    $hops        = [];
    $accumulated = 0;
    for ($i = 0; $i < $hopCount; $i++) {
        $accumulated += round($avgRtt / $hopCount * (0.8 + (mt_rand(0, 40) / 100)), 2);
        $hops[] = [
            'ip'     => $i === 0 ? '10.0.0.1' : ($i === $hopCount - 1 ? ($targetIp ?: '*') : null),
            'host'   => $i === 0 ? 'gateway.local' : ($i === $hopCount - 1 ? 'endpoint-target' : null),
            'rtt_ms' => $accumulated,
        ];
    }

    return [
        'reachable'        => $reachable,
        'target_ip'        => $targetIp ?: null,
        'avg_rtt_ms'       => $avgRtt,
        'jitter_ms'        => $jitterMs,
        'packet_loss_pct'  => $packetLoss,
        'throughput_mbps'  => $throughputMbps,
        'samples'          => $samples,
        'method'           => $method,
        'probe_steps'      => $probeSteps,
        'hops'             => $hops,
        'note'             => $note,
        'sent_at'          => $sentAt,
        'received_at'      => $receivedAt,
        'total_elapsed_ms' => round((microtime(true) - $startAll) * 1000, 2),
        'ports_tested'     => $probePorts,
        'full_scan'        => $fullScan,
    ];
}

function saveProbeToHistory(
    array $probeResult,
    string $cableId,
    string $endpointId,
    string $workspaceRoot,
    bool $fullScan
): string {
    $logDir = rtrim($workspaceRoot, '/') . '/data/local/log/probe';
    if (!is_dir($logDir)) {
        mkdir($logDir, 0775, true);
    }

    $dateStr  = gmdate('Y-m-d');
    $filePath = $logDir . '/' . $dateStr . '.jsonl';

    $r      = $probeResult;
    $isReal = ($r['method'] ?? '') !== 'synthetic_fallback';

    $record = [
        'cable_id'                => $cableId,
        'measured_at'             => gmdate('Y-m-d\TH:i:sP'),
        'sent_at'                 => $r['sent_at'] ?? null,
        'received_at'             => $r['received_at'] ?? null,
        'latency_ms'              => $r['avg_rtt_ms'],
        'jitter_ms'               => $r['jitter_ms'] ?? null,
        'packet_loss_pct'         => $r['packet_loss_pct'],
        'throughput_mbps'         => $r['throughput_mbps'] ?? null,
        'packets_per_second'      => null,
        'availability_pct'        => $r['reachable']
            ? round(100.0 - (float) ($r['packet_loss_pct'] ?? 0), 2)
            : 0.0,
        'route_error_count'       => $r['reachable'] ? 0 : 1,
        'source'                  => 'network_probe',
        'method'                  => $isReal ? (string) $r['method'] : 'heuristic',
        'confidence_score'        => $isReal ? 0.78 : 0.55,
        'estimated'               => !$isReal,
        'telemetry_model_version' => '1.2.0',
        'metadata'                => [
            'collector'          => 'api.php/probe',
            'probe_protocol'     => $isReal ? 'tcp-connect' : 'heuristic',
            'target_host'        => $r['target_ip'] ?? null,
            'target_endpoint_id' => $endpointId ?: null,
            'total_elapsed_ms'   => $r['total_elapsed_ms'] ?? null,
            'total_hops'         => count($r['hops'] ?? []),
            'hops_visible'       => count(array_filter(
                $r['hops'] ?? [],
                static fn(array $h): bool => !empty($h['ip'])
            )),
            'ports_tested'       => $r['ports_tested'] ?? [443, 80, 22],
            'samples'            => $r['samples'] ?? 0,
            'full_scan'          => $fullScan,
        ],
        'hops'                    => $r['hops'] ?? [],
    ];

    file_put_contents(
        $filePath,
        json_encode($record, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . "\n",
        FILE_APPEND | LOCK_EX
    );

    return 'data/local/log/probe/' . $dateStr . '.jsonl';
}

function telemetryTableAvailable(PDO $pdo): bool
{
    $stmt = $pdo->query("SELECT to_regclass('public.cable_telemetry_samples') AS table_name");
    $row = $stmt->fetch();
    return !empty($row['table_name']);
}

function fetchTelemetrySeries(PDO $pdo, string $cableId, int $hours = 24): array
{
    if (!telemetryTableAvailable($pdo)) {
        return [];
    }

    // Resolução Dinâmica: 
    // < 24h: Raw (1 amostra/min aprox)
    // < 7 dias: Rollup 5m
    // > 7 dias: Rollup 1h
    $table = 'cable_telemetry_samples';
    $timeColumn = 'measured_at';
    $fields = 'measured_at, latency_ms, jitter_ms, packet_loss_pct, throughput_mbps, packets_per_second, availability_pct, route_error_count, confidence_score, estimated';
    
    if ($hours > 168) {
        $table = 'telemetry_rollup_1h';
        $timeColumn = 'bucket';
        $fields = 'bucket AS measured_at, avg_latency_ms AS latency_ms, avg_jitter_ms AS jitter_ms, avg_packet_loss_pct AS packet_loss_pct, avg_throughput_mbps AS throughput_mbps, NULL::bigint AS packets_per_second, NULL::double precision AS availability_pct, NULL::integer AS route_error_count, confidence_avg AS confidence_score, false AS estimated';
    } elseif ($hours > 24) {
        $table = 'telemetry_rollup_5m';
        $timeColumn = 'bucket';
        $fields = 'bucket AS measured_at, avg_latency_ms AS latency_ms, avg_jitter_ms AS jitter_ms, avg_packet_loss_pct AS packet_loss_pct, NULL::double precision AS throughput_mbps, NULL::bigint AS packets_per_second, NULL::double precision AS availability_pct, NULL::integer AS route_error_count, 1.0 AS confidence_score, false AS estimated';
    }

    $stmt = $pdo->prepare(
        "SELECT {$fields} FROM {$table} 
            WHERE cable_id = :cable_id AND {$timeColumn} >= (NOW() - (:hours::text || ' hours')::interval)
            ORDER BY {$timeColumn} ASC LIMIT 1000"
    );
    $stmt->bindValue(':cable_id', $cableId, PDO::PARAM_STR);
    $stmt->bindValue(':hours', $hours, PDO::PARAM_INT);
    $stmt->execute();

    $rows = $stmt->fetchAll();
    foreach ($rows as &$row) {
        $row['latency_ms'] = ($row['latency_ms'] ?? null) !== null ? (float) $row['latency_ms'] : null;
        $row['jitter_ms'] = ($row['jitter_ms'] ?? null) !== null ? (float) $row['jitter_ms'] : null;
        $row['packet_loss_pct'] = ($row['packet_loss_pct'] ?? null) !== null ? (float) $row['packet_loss_pct'] : null;
        $row['throughput_mbps'] = ($row['throughput_mbps'] ?? null) !== null ? (float) $row['throughput_mbps'] : null;
        $row['packets_per_second'] = ($row['packets_per_second'] ?? null) !== null ? (int) $row['packets_per_second'] : null;
        $row['availability_pct'] = ($row['availability_pct'] ?? null) !== null ? (float) $row['availability_pct'] : null;
        $row['route_error_count'] = ($row['route_error_count'] ?? null) !== null ? (int) $row['route_error_count'] : null;
        $row['confidence_score'] = ($row['confidence_score'] ?? null) !== null ? (float) $row['confidence_score'] : 0.5;
        $row['estimated'] = normalizeBool($row['estimated'] ?? null) ?? true;
    }
    unset($row);

    return $rows;
}

function generateSyntheticTelemetrySeries(array $details, int $points = 96, int $stepMinutes = 15): array
{
    $base = cableTelemetry($details);
    $now = time();
    $series = [];

    for ($i = $points - 1; $i >= 0; $i--) {
        $phase = ($points - $i) / max(1, $points - 1);
        $wave = sin($phase * M_PI * 2);
        $latency = max(3.0, $base['latency_ms'] * (1 + ($wave * 0.08)) + (($i % 5) - 2) * 0.15);
        $throughput = max(10.0, $base['traffic_mbps'] * (0.9 + (($wave + 1) * 0.08)));
        $pps = max(1000, (int) round($base['packets_per_second'] * (0.88 + (($wave + 1) * 0.09))));
        $jitter = max(0.6, $latency * 0.06);
        $packetLoss = max(0.02, min(5.0, 0.12 + abs($wave) * 0.18));
        $availability = max(98.5, min(100.0, 99.95 - ($packetLoss * 0.03)));

        $series[] = [
            'measured_at' => gmdate('c', $now - ($i * $stepMinutes * 60)),
            'latency_ms' => round($latency, 3),
            'jitter_ms' => round($jitter, 3),
            'packet_loss_pct' => round($packetLoss, 3),
            'throughput_mbps' => round($throughput, 3),
            'packets_per_second' => $pps,
            'availability_pct' => round($availability, 3),
            'route_error_count' => (int) round($packetLoss > 0.25 ? 1 : 0),
            'source' => 'synthetic',
            'method' => 'heuristic-timeseries',
            'confidence_score' => 0.55,
            'estimated' => true,
            'telemetry_model_version' => '1.0.0',
        ];
    }

    return $series;
}

function percentile(array $values, float $p): ?float
{
    $clean = array_values(array_filter($values, static fn($v): bool => $v !== null));
    $n = count($clean);
    if ($n === 0) {
        return null;
    }

    sort($clean, SORT_NUMERIC);
    $rank = ($p / 100.0) * ($n - 1);
    $low = (int) floor($rank);
    $high = (int) ceil($rank);
    if ($low === $high) {
        return (float) $clean[$low];
    }

    $weight = $rank - $low;
    return ((1 - $weight) * (float) $clean[$low]) + ($weight * (float) $clean[$high]);
}

function mean(array $values): ?float
{
    $clean = array_values(array_filter($values, static fn($v): bool => $v !== null));
    $n = count($clean);
    if ($n === 0) {
        return null;
    }
    return array_sum($clean) / $n;
}

function stddev(array $values): ?float
{
    $clean = array_values(array_filter($values, static fn($v): bool => $v !== null));
    $n = count($clean);
    if ($n < 2) {
        return null;
    }
    $avg = array_sum($clean) / $n;
    $variance = 0.0;
    foreach ($clean as $value) {
        $variance += ($value - $avg) ** 2;
    }
    return sqrt($variance / ($n - 1));
}

function loadRealHistoryAvg(string $cableId, int $hours, string $workspaceRoot): array
{
    $histDir = dirname(__DIR__) . '/data/local/historico';
    if (!is_dir($histDir)) return [];

    $cutoff = time() - ($hours * 3600);

    // Collect probe_full e checkpoint — raiz (legado) + YYYY-MM-DD/ subdiretórios
    $files = array_merge(
        glob($histDir . '/*_probe_full_*.json')       ?: [],
        glob($histDir . '/*_checkpoint_*.json')       ?: [],
        glob($histDir . '/*/*_probe_full_*.json')     ?: [],
        glob($histDir . '/*/*_checkpoint_*.json')     ?: []
    );

    $records = [];
    foreach ($files as $file) {
        // Quick date check from filename to avoid reading old files
        if (preg_match('/^(\d{4}-\d{2}-\d{2})_/', basename($file), $m)) {
            $fileDay = strtotime($m[1]);
            if ($fileDay !== false && $fileDay < ($cutoff - 86400)) continue;
        }

        $raw = @file_get_contents($file);
        if (!$raw) continue;
        $data = json_decode($raw, true);
        if (!is_array($data)) continue;

        $regs = $data['registros'] ?? [];
        foreach ($regs as $reg) {
            if (($reg['cable_id'] ?? '') !== $cableId) continue;
            $ts = strtotime($reg['measured_at'] ?? '');
            if ($ts === false || $ts < $cutoff) continue;
            $records[] = [
                'measured_at'      => $reg['measured_at'],
                'latency_ms'       => isset($reg['latency_ms'])       ? (float) $reg['latency_ms']       : null,
                'jitter_ms'        => isset($reg['jitter_ms'])        ? (float) $reg['jitter_ms']        : null,
                'packet_loss_pct'  => isset($reg['packet_loss_pct'])  ? (float) $reg['packet_loss_pct']  : null,
                'throughput_mbps'  => isset($reg['throughput_mbps'])  ? (float) $reg['throughput_mbps']  : null,
                'availability_pct' => isset($reg['availability_pct']) ? (float) $reg['availability_pct'] : null,
                'source'           => $reg['source'] ?? 'history',
                'confidence_score' => isset($reg['confidence_score']) ? (float) $reg['confidence_score'] : 0.95,
                'estimated'        => false,
            ];
        }
    }

    if (!$records) return [];

    // Deduplicate by measured_at (probe_full and checkpoint may overlap)
    $seen = [];
    $unique = [];
    foreach ($records as $rec) {
        $key = $rec['measured_at'];
        if (!isset($seen[$key])) {
            $seen[$key] = true;
            $unique[] = $rec;
        }
    }
    usort($unique, static fn($a, $b) => strcmp($a['measured_at'], $b['measured_at']));

    $n = count($unique);
    $keys = ['latency_ms', 'jitter_ms', 'packet_loss_pct', 'throughput_mbps', 'availability_pct'];
    $sum = array_fill_keys($keys, 0.0);
    $cnt = array_fill_keys($keys, 0);

    foreach ($unique as $rec) {
        foreach ($keys as $k) {
            if ($rec[$k] !== null) {
                $sum[$k] += $rec[$k];
                $cnt[$k]++;
            }
        }
    }

    $avg = [];
    foreach ($keys as $k) {
        $avg[$k] = $cnt[$k] > 0 ? round($sum[$k] / $cnt[$k], 3) : null;
    }

    // Quality score: mean confidence × availability factor
    $confSum = 0.0; $confCnt = 0;
    foreach ($unique as $rec) {
        $confSum += $rec['confidence_score'];
        $confCnt++;
    }
    $qualityScore = $confCnt > 0 ? round(($confSum / $confCnt) * 100, 1) : null;

    return [
        'sample_count'         => $n,
        'window_start'         => $unique[0]['measured_at'],
        'window_end'           => $unique[$n - 1]['measured_at'],
        'avg_latency_ms'       => $avg['latency_ms'],
        'avg_jitter_ms'        => $avg['jitter_ms'],
        'avg_packet_loss_pct'  => $avg['packet_loss_pct'],
        'avg_throughput_mbps'  => $avg['throughput_mbps'],
        'avg_availability_pct' => $avg['availability_pct'],
        'quality_score'        => $qualityScore,
        'source'               => 'active_tcp_connect',
        'history'              => $unique,
    ];
}

function buildTelemetryPayload(array $details, array $series, bool $fallbackUsed): array
{
    $base = cableTelemetry($details);
    $latest = $series[count($series) - 1] ?? null;

    if ($latest === null) {
        return array_merge($base, [
            'estimated' => true,
            'fallback_used' => true,
            'source' => 'heuristic',
            'method' => 'baseline-only',
            'telemetry_model_version' => '1.0.0',
            'window_start' => null,
            'window_end' => null,
            'sample_size' => 0,
            'percentiles' => [],
            'confidence_interval_latency_ms' => null,
            'data_quality_score' => 0.35,
            'history' => [],
        ]);
    }

    $latencies = array_map(static fn(array $r): ?float => $r['latency_ms'], $series);
    $throughputs = array_map(static fn(array $r): ?float => $r['throughput_mbps'], $series);
    $packetLosses = array_map(static fn(array $r): ?float => $r['packet_loss_pct'], $series);
    $confScores = array_map(static fn(array $r): ?float => $r['confidence_score'], $series);

    $avgLatency = mean($latencies);
    $latStd = stddev($latencies);
    $n = count(array_filter($latencies, static fn($v): bool => $v !== null));
    $ciHalfWidth = ($latStd !== null && $n > 1) ? (1.96 * ($latStd / sqrt($n))) : null;
    $ciLatency = ($ciHalfWidth !== null && $avgLatency !== null)
        ? [round(max(0.0, $avgLatency - $ciHalfWidth), 3), round($avgLatency + $ciHalfWidth, 3)]
        : null;

    $meanConfidence = mean($confScores) ?? 0.5;
    $completeness = $n > 0 ? min(1.0, $n / max(1, count($series))) : 0.0;
    $dataQualityScore = round((($meanConfidence * 0.7) + ($completeness * 0.3)) * 100.0, 2);

    $trafficMbps = (float) ($latest['throughput_mbps'] ?? $base['traffic_mbps']);
    $packetsPerSecond = (int) ($latest['packets_per_second'] ?? $base['packets_per_second']);

    return array_merge($base, [
        'latency_ms' => round((float) ($latest['latency_ms'] ?? $base['latency_ms']), 3),
        'traffic_mbps' => round($trafficMbps, 3),
        'packets_per_second' => $packetsPerSecond,
        'connected_machines_estimate' => (int) round(($trafficMbps * 1000000.0) / 2500000.0),
        'jitter_ms' => round((float) ($latest['jitter_ms'] ?? 0.0), 3),
        'packet_loss_pct' => round((float) ($latest['packet_loss_pct'] ?? 0.0), 3),
        'availability_pct' => round((float) ($latest['availability_pct'] ?? 99.9), 3),
        'route_error_count' => (int) ($latest['route_error_count'] ?? 0),
        'estimated' => (bool) ($latest['estimated'] ?? true),
        'fallback_used' => $fallbackUsed,
        'source' => (string) ($latest['source'] ?? 'heuristic'),
        'method' => (string) ($latest['method'] ?? 'heuristic-timeseries'),
        'telemetry_model_version' => (string) ($latest['telemetry_model_version'] ?? '1.0.0'),
        'window_start' => $series[0]['measured_at'] ?? null,
        'window_end' => $latest['measured_at'] ?? null,
        'sample_size' => count($series),
        'percentiles' => [
            'latency_ms' => [
                'p50' => round((float) (percentile($latencies, 50) ?? 0.0), 3),
                'p95' => round((float) (percentile($latencies, 95) ?? 0.0), 3),
                'p99' => round((float) (percentile($latencies, 99) ?? 0.0), 3),
            ],
            'throughput_mbps' => [
                'p50' => round((float) (percentile($throughputs, 50) ?? 0.0), 3),
                'p95' => round((float) (percentile($throughputs, 95) ?? 0.0), 3),
                'p99' => round((float) (percentile($throughputs, 99) ?? 0.0), 3),
            ],
            'packet_loss_pct' => [
                'p50' => round((float) (percentile($packetLosses, 50) ?? 0.0), 3),
                'p95' => round((float) (percentile($packetLosses, 95) ?? 0.0), 3),
                'p99' => round((float) (percentile($packetLosses, 99) ?? 0.0), 3),
            ],
        ],
        'confidence_interval_latency_ms' => $ciLatency,
        'data_quality_score' => $dataQualityScore,
        'history' => $series,
    ]);
}

if (isset($_GET['api']) || isset($_GET['action']) || isset($_GET['mode']) || isset($_POST['action'])) {

    // ── Endpoints que NÃO precisam de banco (ficheiros locais) ───────────────
    $_reqAll    = array_merge($_GET, $_POST);
    $_modeNow   = (string) ($_reqAll['mode'] ?? $_reqAll['action'] ?? '');

    // ── summary: estatísticas gerais para homepage ────────────────────────────
    if ($_modeNow === 'summary') {
        $out = ['cables_total' => 0, 'cables_operational' => 0, 'telemetry_samples' => 0, 'last_measured_at' => null, 'tables' => 12];
        if (extension_loaded('pdo_pgsql')) {
            try {
                $pdoS = getDb($pgSocket, $pgPort, $pgDbname, $pgUser, $pgPass, $pgSsl);
                $out['cables_total']       = (int) $pdoS->query("SELECT COUNT(*) FROM cables")->fetchColumn();
                $out['cables_operational'] = (int) $pdoS->query("SELECT COUNT(*) FROM cables WHERE is_operational = TRUE")->fetchColumn();
                $out['telemetry_samples']  = (int) $pdoS->query("SELECT COUNT(*) FROM cable_telemetry_samples")->fetchColumn();
                $last = $pdoS->query("SELECT MAX(measured_at) FROM cable_telemetry_samples")->fetchColumn();
                $out['last_measured_at']   = $last ?: null;
            } catch (Throwable) { /* retorna zeros se BD indisponível */ }
        }
        jsonResponse($out);
    }

    // ── hop_geo_points: GeoJSON de comutadores geolocalizados ────────────────
    if ($_modeNow === 'hop_geo_points') {
        try {
            $pdoGeo  = getDb($pgSocket, $pgPort, $pgDbname, $pgUser, $pgPass, $pgSsl);
            $rows    = $pdoGeo->query("
                SELECT hg.hop_ip::text AS ip, hg.lat, hg.lng,
                       hg.city, hg.region, hg.country, hg.country_code,
                       hg.isp, hg.org, hg.as_number, hg.as_name,
                       COUNT(DISTINCT cth.cable_id)::int AS cable_count
                FROM hop_switch_geolocations hg
                LEFT JOIN cable_telemetry_hops cth ON cth.hop_ip = hg.hop_ip
                WHERE hg.lat IS NOT NULL AND hg.lng IS NOT NULL
                GROUP BY hg.hop_ip, hg.lat, hg.lng, hg.city, hg.region,
                         hg.country, hg.country_code, hg.isp, hg.org, hg.as_number, hg.as_name
                ORDER BY cable_count DESC
            ")->fetchAll();

            $features = array_map(fn($r) => [
                'type'       => 'Feature',
                'geometry'   => ['type' => 'Point', 'coordinates' => [(float)$r['lng'], (float)$r['lat']]],
                'properties' => [
                    'ip'           => $r['ip'],
                    'city'         => $r['city'],
                    'region'       => $r['region'],
                    'country'      => $r['country'],
                    'country_code' => $r['country_code'],
                    'isp'          => $r['isp'],
                    'org'          => $r['org'],
                    'as_number'    => $r['as_number'],
                    'as_name'      => $r['as_name'],
                    'cable_count'  => (int)$r['cable_count'],
                ],
            ], $rows);
            jsonResponse(['type' => 'FeatureCollection', 'features' => $features]);
        } catch (Throwable) {
            jsonResponse(['type' => 'FeatureCollection', 'features' => []]);
        }
    }

    // ── hop_trace: executa MTR e retorna análise salto a salto ──────────────
    if ($_modeNow === 'hop_trace') {
        $cableId    = trim((string) ($_reqAll['cable_id'] ?? ''));
        $endpointId = trim((string) ($_reqAll['endpoint_id'] ?? ''));
        $targetIp   = trim((string) ($_reqAll['ip'] ?? ''));
        $cycles     = max(3, min(10, (int) ($_reqAll['cycles'] ?? 5)));

        if ($cableId === '') { jsonResponse(['error' => 'cable_id obrigatório.'], 400); }

        try {
            $pdoH = getDb($pgSocket, $pgPort, $pgDbname, $pgUser, $pgPass, $pgSsl);

            // Resolve IP: parâmetro direto → endpoint_id → primeiro endpoint do cabo
            if ($targetIp !== '') {
                assertProbeIpAllowed($pdoH, $targetIp);
            } elseif ($endpointId !== '') {
                $st = $pdoH->prepare('SELECT ip_start FROM endpoint_ip_samples WHERE endpoint_id=:eid ORDER BY sample_order LIMIT 1');
                $st->execute([':eid' => $endpointId]);
                $targetIp = (string)($st->fetchColumn() ?: '');
            }
            if ($targetIp === '') {
                $st = $pdoH->prepare(
                    'SELECT eis.ip_start FROM endpoint_ip_samples eis
                     JOIN cable_endpoints ce ON ce.endpoint_id = eis.endpoint_id
                     WHERE ce.cable_id = :cid ORDER BY ce.endpoint_order, eis.sample_order LIMIT 1'
                );
                $st->execute([':cid' => $cableId]);
                $targetIp = (string)($st->fetchColumn() ?: '');
            }
            if ($targetIp === '') { jsonResponse(['error' => 'Nenhum IP disponível para este cabo.'], 422); }

            // Executa MTR com timeout
            $cmd    = 'mtr --report --report-cycles=' . $cycles . ' --no-dns --json ' . escapeshellarg($targetIp) . ' 2>/dev/null';
            $output = '';
            $handle = @popen($cmd, 'r');
            if ($handle) {
                $deadline = time() + ($cycles * 3) + 20;
                while (!feof($handle) && time() < $deadline) {
                    $output .= fread($handle, 4096);
                }
                pclose($handle);
            }

            $hops   = [];
            $hubs   = json_decode($output, true)['report']['hubs'] ?? [];
            $prevRtt = 0.0;
            foreach ($hubs as $hub) {
                $ip     = ($hub['host'] ?? '') !== '???' ? ($hub['host'] ?? null) : null;
                $avgRtt = round((float)($hub['Avg']    ?? 0), 2);
                $delta  = $prevRtt > 0 ? round($avgRtt - $prevRtt, 2) : null;
                if ($avgRtt > 0) $prevRtt = $avgRtt;
                $hops[] = [
                    'hop'       => (int)($hub['count']   ?? 0),
                    'ip'        => $ip,
                    'loss_pct'  => round((float)($hub['Loss%'] ?? 0), 1),
                    'snt'       => (int)($hub['Snt']     ?? 0),
                    'rtt_last'  => round((float)($hub['Last']  ?? 0), 2),
                    'rtt_avg'   => $avgRtt,
                    'rtt_best'  => round((float)($hub['Best']  ?? 0), 2),
                    'rtt_worst' => round((float)($hub['Wrst']  ?? 0), 2),
                    'rtt_stdev' => round((float)($hub['StDev'] ?? 0), 2),
                    'delta_ms'  => $delta,
                ];
            }

            // Persiste no banco se houve hops reais
            if (!empty($hops)) {
                $finalRtt = null;
                foreach (array_reverse($hops) as $h) {
                    if ($h['ip'] && $h['rtt_avg'] > 0) { $finalRtt = $h['rtt_avg']; break; }
                }
                if ($finalRtt) {
                    try {
                        $pdoH->beginTransaction();
                        $ins = $pdoH->prepare(
                            'INSERT INTO cable_telemetry_samples
                             (cable_id, measured_at, latency_ms, source, method, confidence_score, estimated, telemetry_model_version)
                             VALUES (:cid, NOW(), :lat, \'active_mtr\', \'mtr-hop-trace\', 0.88, FALSE, \'1.4.0\')
                             RETURNING sample_id'
                        );
                        $ins->execute([':cid' => $cableId, ':lat' => $finalRtt]);
                        $sampleId = $ins->fetchColumn();
                        $hopQ = $pdoH->prepare(
                            'INSERT INTO cable_telemetry_hops
                             (sample_id, cable_id, hop_number, hop_ip,
                              rtt_last_ms, rtt_avg_ms, rtt_best_ms, rtt_worst_ms, rtt_stdev_ms, loss_pct)
                             VALUES (:sid,:cid,:hn,:ip::inet,:rl,:ra,:rb,:rw,:rs,:lp)
                             ON CONFLICT (sample_id, hop_number) DO NOTHING'
                        );
                        foreach ($hops as $h) {
                            $hopQ->execute([
                                ':sid' => $sampleId, ':cid' => $cableId,
                                ':hn'  => $h['hop'],   ':ip'  => $h['ip'],
                                ':rl'  => $h['rtt_last']  ?: null,
                                ':ra'  => $h['rtt_avg']   ?: null,
                                ':rb'  => $h['rtt_best']  ?: null,
                                ':rw'  => $h['rtt_worst'] ?: null,
                                ':rs'  => $h['rtt_stdev'] ?: null,
                                ':lp'  => $h['loss_pct'],
                            ]);
                        }
                        $pdoH->commit();
                    } catch (Throwable) { if ($pdoH->inTransaction()) $pdoH->rollBack(); }
                }
            }

            jsonResponse([
                'cable_id'  => $cableId,
                'target_ip' => $targetIp,
                'hop_count' => count($hops),
                'hops'      => $hops,
            ]);
        } catch (Throwable $e) { jsonResponse(['error' => $e->getMessage()], 500); }
    }

    // ── hop_history: último rastreamento armazenado para um cabo ─────────────
    if ($_modeNow === 'hop_history') {
        $cableId = trim((string) ($_reqAll['cable_id'] ?? ''));
        if ($cableId === '') { jsonResponse(['error' => 'cable_id obrigatório.'], 400); }
        try {
            $pdoH = getDb($pgSocket, $pgPort, $pgDbname, $pgUser, $pgPass, $pgSsl);
            $st   = $pdoH->prepare(
                'SELECT h.hop_number, h.hop_ip::text AS ip, h.rtt_avg_ms, h.rtt_best_ms,
                        h.rtt_worst_ms, h.rtt_stdev_ms, h.loss_pct, h.recorded_at
                 FROM cable_telemetry_hops h
                 JOIN (SELECT sample_id FROM cable_telemetry_samples
                       WHERE cable_id = :cid AND source = \'active_mtr\'
                       ORDER BY measured_at DESC LIMIT 1) s ON s.sample_id = h.sample_id
                 ORDER BY h.hop_number'
            );
            $st->execute([':cid' => $cableId]);
            $rows    = $st->fetchAll();
            $prevRtt = 0.0;
            foreach ($rows as &$row) {
                $avg = (float)($row['rtt_avg_ms'] ?? 0);
                $row['delta_ms'] = $prevRtt > 0 && $avg > 0 ? round($avg - $prevRtt, 2) : null;
                if ($avg > 0) $prevRtt = $avg;
            }
            unset($row);
            jsonResponse(['cable_id' => $cableId, 'hops' => $rows]);
        } catch (Throwable $e) { jsonResponse(['error' => $e->getMessage()], 500); }
    }

    // ── debug_env: diagnóstico de variáveis de ambiente (remover após uso) ───
    if ($_modeNow === 'debug_env') {
        $envKeys = ['DATABASE_URL','DATABASE_PUBLIC_URL','PGHOST','PGPORT','PGDATABASE','PGUSER','PGPASSWORD','POSTGRES_DB','POSTGRES_USER','POSTGRES_PASSWORD','PG_SOCKET','RAILWAY_ENVIRONMENT','RAILWAY_SERVICE_NAME'];
        $out = ['resolved_host' => $pgSocket, 'resolved_port' => $pgPort, 'resolved_db' => $pgDbname, 'resolved_ssl' => $pgSsl];
        foreach ($envKeys as $k) {
            $fromGetenv = getenv($k);
            $fromEnv    = $_ENV[$k]    ?? null;
            $fromServer = $_SERVER[$k] ?? null;
            $sensitive  = str_contains(strtoupper($k), 'PASS') || str_contains(strtoupper($k), 'URL');
            $val = $fromGetenv !== false ? $fromGetenv : ($fromEnv ?? $fromServer);
            $display = $val ? ($sensitive ? 'SET(len=' . strlen((string)$val) . ')' : $val) : 'NOT SET';
            $out[$k] = $display . ' [getenv=' . ($fromGetenv !== false ? ($sensitive ? 'SET' : $fromGetenv) : 'false') . ' _ENV=' . ($fromEnv !== null ? ($sensitive ? 'SET' : $fromEnv) : 'null') . ' _SERVER=' . ($fromServer !== null ? ($sensitive ? 'SET' : $fromServer) : 'null') . ']';
        }
        // Mostra todas as vars de ambiente — primeiros 60 para não explodir
        $procAllVars = [];
        if (is_readable('/proc/self/environ')) {
            foreach (explode("\0", file_get_contents('/proc/self/environ')) as $e) {
                if (str_contains($e, '=')) {
                    [$k2, $v2] = explode('=', $e, 2);
                    $procAllVars[$k2] = (str_contains(strtoupper($k2), 'PASS') || str_contains(strtoupper($k2), 'URL') || str_contains(strtoupper($k2), 'SECRET'))
                        ? 'SET(len=' . strlen($v2) . ')' : $v2;
                }
            }
        }
        // vars de ambiente que contêm "PG" ou "DATABASE" ou "POSTGRES" ou "RAILWAY"
        $dbVars = [];
        foreach ($procAllVars as $k => $v) {
            if (preg_match('/PG|DATABASE|POSTGRES|RAILWAY/i', $k)) $dbVars[$k] = $v;
        }
        $out['proc_db_railway_vars'] = $dbVars ?: 'NONE FOUND IN /proc/self/environ';
        $out['proc_all_keys']        = array_keys($procAllVars) ?: 'NOT READABLE';
        // Diagnóstico PHP
        $out['php_variables_order'] = ini_get('variables_order');
        $out['php_sapi']            = PHP_SAPI;
        $out['getenv_all_count']    = count(getenv() ?: []);
        jsonResponse($out);
    }
    $_projectDir = dirname(__DIR__);
    $_noDbModes = ['daily_start', 'daily_status', 'daily_cancel'];

    if (in_array($_modeNow, $_noDbModes, true)) {
        try {

            // ── daily_start ──────────────────────────────────────────────────
            if ($_modeNow === 'daily_start') {
                if (!function_exists('exec')) {
                    jsonResponse(['status' => 'error', 'message' => 'exec() não disponível neste servidor.'], 503);
                }
                $script = $_projectDir . '/scripts/run_daily_telemetry_tasks.sh';
                if (!file_exists($script)) {
                    jsonResponse(['status' => 'error', 'message' => 'Script não encontrado: ' . $script], 404);
                }
                $date    = gmdate('Y-m-d');
                $histDir = $_projectDir . '/data/local/historico/' . $date;
                @mkdir($histDir, 0775, true);
                $ts      = gmdate('His');
                $logFile = $histDir . '/run_daily_' . $ts . '.log';
                $runFile = $histDir . '/.running';

                if (file_exists($runFile)) {
                    $info = @json_decode(@file_get_contents($runFile), true);
                    $pid  = (string) ($info['pid'] ?? '');
                    if ($pid !== '' && file_exists('/proc/' . $pid)) {
                        jsonResponse(['status' => 'already_running', 'log_file' => $info['log_file'] ?? '', 'pid' => $pid]);
                    }
                    @unlink($runFile);
                }

                $fullScan  = ($_reqAll['full_scan']  ?? '0') === '1' ? '1' : '0';
                $mtrCycles = max(1, min(100, (int) ($_reqAll['mtr_cycles'] ?? 5)));
                $cmd = sprintf(
                    'LOG_FILE=%s FULL_SCAN=%s MTR_CYCLES=%s nohup bash %s > /dev/null 2>&1 & echo $!',
                    escapeshellarg($logFile),
                    escapeshellarg($fullScan),
                    escapeshellarg((string) $mtrCycles),
                    escapeshellarg($script)
                );
                exec($cmd, $out);
                $pid = trim($out[0] ?? '');
                if ($pid) {
                    file_put_contents($runFile, json_encode([
                        'pid' => $pid, 'log_file' => $logFile, 'started_at' => gmdate('c'),
                    ]));
                }
                jsonResponse(['status' => 'started', 'log_file' => $logFile, 'pid' => $pid, 'date' => $date]);
            }

            // ── daily_status ─────────────────────────────────────────────────
            if ($_modeNow === 'daily_status') {
                $logFile = trim((string) ($_reqAll['log'] ?? ''));
                $projHistDir = $_projectDir . '/data/local/historico';

                if ($logFile === '') {
                    $date = gmdate('Y-m-d');
                    $logs = glob($projHistDir . '/' . $date . '/run_daily_*.log') ?: [];
                    if ($logs) { natsort($logs); $logFile = end($logs); }
                }

                if ($logFile === '') { jsonResponse(['status' => 'idle']); }

                // Segurança: garante path dentro do diretório esperado
                $normLog  = str_replace(['../', '..\\'], '', $logFile);
                if (!str_starts_with($normLog, $projHistDir) && !str_starts_with($logFile, $projHistDir)) {
                    jsonResponse(['status' => 'error', 'message' => 'Caminho de log inválido.'], 400);
                }
                if (!file_exists($logFile)) { jsonResponse(['status' => 'starting', 'log_file' => $logFile]); }

                $content = (string) (@file_get_contents($logFile) ?: '');
                // Strip ANSI color codes before any parsing
                $content = (string) preg_replace('/\033\[[0-9;]*m/', '', $content);
                $lines   = explode("\n", $content);

                // ── Progresso de cabos (formato do ingest Python) ────────────
                $progress = null;
                foreach (array_reverse($lines) as $line) {
                    if (preg_match(
                        '/\[\s*(\d+)\/(\d+)\]\s+([\d.]+)%\s+(\S+)\s+proto=(.+?)\s+lat=\s*([\d.]+)ms/',
                        $line, $m
                    )) {
                        $progress = [
                            'current'    => (int)   $m[1],
                            'total'      => (int)   $m[2],
                            'percent'    => (float) $m[3],
                            'cable'      => $m[4],
                            'proto'      => $m[5],
                            'latency_ms' => (float) $m[6],
                        ];
                        break;
                    }
                }

                // ── Resumo final (Python ingest) ─────────────────────────────
                $summary = null;
                if (preg_match('/Cabos inseridos\s*:\s*(\d+)/m', $content, $m)) {
                    $summary = ['inserted' => (int) $m[1]];
                    if (preg_match('/TCP SYN-ACK\s*:\s*(\d+)/m',  $content, $m2)) $summary['tcp']       = (int) $m2[1];
                    if (preg_match('/Sintetico\s*:\s*(\d+)/m',     $content, $m2)) $summary['synthetic'] = (int) $m2[1];
                    if (preg_match('/mtr \(hops\)\s*:\s*(\d+)/m',  $content, $m2)) $summary['mtr']       = (int) $m2[1];
                    if (preg_match('/HTTP curl\s*:\s*(\d+)/m',      $content, $m2)) $summary['http']      = (int) $m2[1];
                    if (preg_match('/(\d+) anomalias/m',            $content, $m2)) $summary['anomalies'] = (int) $m2[1];
                    if (preg_match('/Concluido em\s*(\S+)/m',       $content, $m2)) $summary['completed_at'] = $m2[1];
                }

                // ── Live TCP/Synth durante execução ──────────────────────────
                $liveTcp   = preg_match_all('/proto=tcp-\d+pkt/m',    $content);
                $liveSynth = preg_match_all('/proto=heuristic\b/m',    $content);

                // ── PRÉ-FLIGHT: probes ICMP por IP ───────────────────────────
                $probes = [];
                preg_match_all(
                    '/\[[\d:]+\]\s+[✔⚠✖\s]+\s*([\d.]+):\s+perda=(\d+)%\s+RTT=([\d.]+)ms(.*)/',
                    $content, $probeM, PREG_SET_ORDER
                );
                foreach ($probeM as $pm) {
                    $rest   = (string) $pm[4];
                    $pstatus = str_contains($rest, 'inacessível') ? 'fail'
                             : (str_contains($rest, 'degradado')  ? 'warn' : 'ok');
                    $probes[] = [
                        'ip'     => $pm[1],
                        'loss'   => (int)   $pm[2],
                        'rtt'    => (float) $pm[3],
                        'status' => $pstatus,
                    ];
                }

                // Reachability: "Conectividade: X/Y endpoints OK"
                $reachable = null;
                if (preg_match('/Conectividade:\s*(\d+)\/(\d+)\s+endpoints/', $content, $rm)) {
                    $reachable = ['ok' => (int) $rm[1], 'total' => (int) $rm[2]];
                }

                // Checks de fase: null = seção ainda não apareceu, true/false = resultado
                $depsOk = str_contains($content, 'PRÉ-FLIGHT')
                    ? !preg_match('/Dependência ausente|dep_missing/', $content)
                    : null;
                $dnsOk  = str_contains($content, 'Resolução DNS')
                    ? !str_contains($content, 'dns_total_failure')
                    : null;
                $httpOk = str_contains($content, 'Verificação HTTP')
                    ? !preg_match('/http_fail.*:000|HTTP 000/', $content)
                    : null;

                // Cabeçalhos de fase "=== ... ===" — último visto é o atual
                $currentPhase = '';
                preg_match_all('/\[[\d:]+\] === (.+?) ===/', $content, $phaseM);
                if (!empty($phaseM[1])) { $currentPhase = trim(end($phaseM[1])); }

                // Tarefa Python atual: última linha "  label (tentativa N/M)..."
                $currentTask = null;
                preg_match_all(
                    '/\[[\d:]+\]\s{2}(.+?) \(tentativa (\d+)\/(\d+)\)/',
                    $content, $taskM, PREG_SET_ORDER
                );
                if (!empty($taskM)) {
                    $last = end($taskM);
                    $currentTask = [
                        'label'   => trim((string) $last[1]),
                        'attempt' => (int) $last[2],
                        'max'     => (int) $last[3],
                    ];
                }

                // Tarefas Python concluídas com sucesso
                $taskDone = [];
                preg_match_all('/✔\s+(.+?)\s+→\s+sucesso/', $content, $tdM);
                $taskDone = array_map('trim', $tdM[1] ?? []);

                // Sumário final (avisos/erros do bash)
                $finalSummary = null;
                if (preg_match('/Endpoints alcançáveis\s*:\s*([\d\/]+)/', $content, $fsm)) {
                    $finalSummary = ['reachable' => $fsm[1]];
                    if (preg_match('/Avisos\s*:\s*(\d+)/', $content, $m2)) $finalSummary['warnings'] = (int) $m2[1];
                    if (preg_match('/Erros\s*:\s*(\d+)/',  $content, $m2)) $finalSummary['errors']   = (int) $m2[1];
                }

                // Listas de avisos e erros (linhas com "• ")
                $warnList = [];
                preg_match_all('/⚠\s+•\s+(.+)/', $content, $wm);
                $warnList = array_map('trim', $wm[1] ?? []);

                $errorList = [];
                preg_match_all('/✖\s+•\s+(.+)/', $content, $em);
                $errorList = array_map('trim', $em[1] ?? []);

                // ── Status do ciclo ──────────────────────────────────────────
                $date    = gmdate('Y-m-d');
                $runFile = $projHistDir . '/' . $date . '/.running';
                $isRun   = false;
                $runPid  = '';
                if (file_exists($runFile)) {
                    $info    = @json_decode(@file_get_contents($runFile), true);
                    $runPid  = (string) ($info['pid'] ?? '');
                    $startTs = strtotime((string) ($info['started_at'] ?? '')) ?: 0;
                    if ($runPid !== '') {
                        // /proc check (Linux); posix_kill fallback (containers sem /proc visível)
                        $pidAlive = file_exists('/proc/' . $runPid)
                                 || (function_exists('posix_kill') && @posix_kill((int)$runPid, 0));
                        if ($pidAlive) {
                            $isRun = true;
                        } elseif ((time() - $startTs) < 600) {
                            // Mantém "running" por até 10 min após o start
                            // (containers podem ter namespaces de PID isolados)
                            $isRun = true;
                        }
                    }
                }

                // Log modificado nos últimos 5 min → ainda em execução
                $lastMod = @filemtime($logFile) ?: 0;
                if (!$isRun && (time() - $lastMod) < 300) { $isRun = true; }

                // ── Live status gravado pelo script Python após cada cabo ─────
                // Tem prioridade sobre o parsing do log: mais granular e inclui
                // cable_name (nome real) em vez de cable_id (slug).
                $liveFile = $projHistDir . '/' . gmdate('Y-m-d') . '/.probe_live.json';
                if (file_exists($liveFile)) {
                    $liveAge  = time() - (@filemtime($liveFile) ?: 0);
                    $liveData = @json_decode(@file_get_contents($liveFile), true);
                    // Live status válido por 5 min (uma entrada por cabo; cabos lentos podem demorar)
                    if ($liveData && isset($liveData['current']) && $liveAge < 300) {
                        $progress = [
                            'current'      => (int)   ($liveData['current']      ?? 0),
                            'total'        => (int)   ($liveData['total']        ?? 699),
                            'percent'      => (float) ($liveData['percent']      ?? 0),
                            'cable'        => (string)($liveData['cable_name']   ?? $liveData['cable_id'] ?? ''),
                            'proto'        => (string)($liveData['proto']        ?? ''),
                            'latency_ms'   => (float) ($liveData['latency_ms']   ?? 0),
                            'hop_count'    => (int)   ($liveData['hop_count']    ?? 0),
                            'switch_count' => (int)   ($liveData['switch_count'] ?? 0),
                            'hops_preview' => (array) ($liveData['hops_preview'] ?? []),
                        ];
                        if (!$isRun) { $isRun = true; }
                    }
                }

                $completed = isset($summary['completed_at'])
                          || str_contains($content, 'Ciclo concluído com sucesso')
                          || str_contains($content, 'Ciclo concluído COM ERROS');
                $status    = $completed ? 'completed'
                           : ($isRun    ? 'running'
                           : ($progress !== null ? 'running' : 'idle'));
                if ($completed && file_exists($runFile)) { @unlink($runFile); }

                jsonResponse([
                    'status'        => $status,
                    'progress'      => $progress,
                    'summary'       => $summary,
                    'log_file'      => $logFile,
                    'live_tcp'      => $liveTcp,
                    'live_synth'    => $liveSynth,
                    // PRÉ-FLIGHT
                    'probes'        => $probes,
                    'reachable'     => $reachable,
                    'deps_ok'       => $depsOk,
                    'dns_ok'        => $dnsOk,
                    'http_ok'       => $httpOk,
                    // Pipeline
                    'current_phase' => $currentPhase,
                    'current_task'  => $currentTask,
                    'task_done'     => $taskDone,
                    // Sumário bash
                    'final_summary' => $finalSummary,
                    'warn_list'     => $warnList,
                    'error_list'    => $errorList,
                ]);
            }

            // ── daily_cancel ─────────────────────────────────────────────────
            if ($_modeNow === 'daily_cancel') {
                $date    = gmdate('Y-m-d');
                $runFile = $_projectDir . '/data/local/historico/' . $date . '/.running';
                $killed  = false;
                $logDeleted = false;

                if (file_exists($runFile)) {
                    $info = @json_decode(@file_get_contents($runFile), true);
                    $pid  = (string) ($info['pid'] ?? '');
                    $log  = (string) ($info['log_file'] ?? '');

                    if ($pid !== '') {
                        // Kill process group (negative PID kills all children too)
                        exec('kill -TERM -' . (int) $pid . ' 2>/dev/null; kill -TERM ' . (int) $pid . ' 2>/dev/null');
                        sleep(1);
                        exec('kill -KILL -' . (int) $pid . ' 2>/dev/null; kill -KILL ' . (int) $pid . ' 2>/dev/null');
                        $killed = true;
                    }
                    if ($log !== '' && str_starts_with($log, $_projectDir) && file_exists($log)) {
                        @unlink($log);
                        $logDeleted = true;
                    }
                    @unlink($runFile);
                }
                jsonResponse(['status' => 'cancelled', 'killed' => $killed, 'log_deleted' => $logDeleted]);
            }

        } catch (Throwable $_e) {
            jsonResponse(['error' => $_e->getMessage()], 500);
        }
    }

    // ── history_list: log sempre + BD opcional ──────────────────────────────
    if ($_modeNow === 'history_list') {
        try {
            $historicDir = $_projectDir . '/data/local/historico';
            $dates       = [];

            // 1. Datas com log local
            if (is_dir($historicDir)) {
                foreach (glob($historicDir . '/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]') ?: [] as $dir) {
                    $d    = basename($dir);
                    $logs = glob($dir . '/run_daily_*.log') ?: [];
                    if ($logs) {
                        natsort($logs);
                        $dates[$d] = ['date' => $d, 'run_count' => count($logs), 'source' => 'log'];
                    }
                }
            }

            // 2. Datas do BD (complementa quando não há log local)
            if (extension_loaded('pdo_pgsql')) {
                try {
                    $pdo2 = getDb($pgSocket, $pgPort, $pgDbname, $pgUser, $pgPass, $pgSsl);
                    $stmt = $pdo2->query("
                        SELECT DISTINCT measured_at::date AS d, COUNT(*) AS samples
                        FROM cable_telemetry_samples
                        GROUP BY measured_at::date
                        ORDER BY d DESC
                    ");
                    foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $row) {
                        $d = (string) $row['d'];
                        if (!isset($dates[$d])) {
                            $dates[$d] = ['date' => $d, 'run_count' => 0, 'samples' => (int) $row['samples'], 'source' => 'db'];
                        } else {
                            $dates[$d]['samples'] = (int) $row['samples'];
                        }
                    }
                } catch (Throwable $_) { /* BD indisponível */ }
            }

            usort($dates, static fn($a, $b) => strcmp($b['date'], $a['date']));
            jsonResponse(['dates' => array_values($dates)]);
        } catch (Throwable $_e) {
            jsonResponse(['error' => $_e->getMessage()], 500);
        }
    }

    // ── history_day: log sempre + BD opcional ───────────────────────────────
    if ($_modeNow === 'history_day') {
        try {
            $date = preg_replace('/[^0-9\-]/', '', trim((string) ($_reqAll['date'] ?? '')));
            if (!preg_match('/^\d{4}-\d{2}-\d{2}$/', $date)) {
                jsonResponse(['status' => 'error', 'message' => 'Data inválida.'], 400);
            }

            // 1. Lê arquivo de log (fonte primária — sem BD)
            $cables  = [];
            $summary = null;
            $logDir  = $_projectDir . '/data/local/historico/' . $date;
            $logs    = glob($logDir . '/run_daily_*.log') ?: [];
            if ($logs) {
                natsort($logs);
                $content = (string) (@file_get_contents(end($logs)) ?: '');
                foreach (explode("\n", $content) as $line) {
                    if (preg_match(
                        '/\[\s*(\d+)\/(\d+)\]\s+([\d.]+)%\s+(\S+)\s+proto=(.+?)\s+lat=\s*([\d.]+)ms/',
                        $line, $m
                    )) {
                        $cables[$m[4]] = [
                            'index'      => (int)   $m[1],
                            'cable_id'   => $m[4],
                            'proto'      => trim($m[5]),
                            'latency_ms' => (float) $m[6],
                            'source'     => 'log',
                        ];
                    }
                }
                if (preg_match('/Cabos inseridos\s*:\s*(\d+)/m', $content, $m)) {
                    $summary = ['inserted' => (int) $m[1]];
                    if (preg_match('/TCP SYN-ACK\s*:\s*(\d+)/m',  $content, $m2)) $summary['tcp']          = (int) $m2[1];
                    if (preg_match('/Sintetico\s*:\s*(\d+)/m',     $content, $m2)) $summary['synthetic']    = (int) $m2[1];
                    if (preg_match('/mtr \(hops\)\s*:\s*(\d+)/m',  $content, $m2)) $summary['mtr']          = (int) $m2[1];
                    if (preg_match('/HTTP curl\s*:\s*(\d+)/m',      $content, $m2)) $summary['http']         = (int) $m2[1];
                    if (preg_match('/(\d+) anomalias/m',            $content, $m2)) $summary['anomalies']    = (int) $m2[1];
                    if (preg_match('/Concluido em\s*(\S+)/m',       $content, $m2)) $summary['completed_at'] = $m2[1];
                }
            }

            // 2. Complementa com cable_telemetry_samples se BD disponível
            if (extension_loaded('pdo_pgsql')) {
                try {
                    $pdo2 = getDb($pgSocket, $pgPort, $pgDbname, $pgUser, $pgPass, $pgSsl);
                    $stmt = $pdo2->prepare("
                        SELECT cable_id,
                               method                              AS proto,
                               ROUND(AVG(latency_ms)::numeric, 2) AS latency_ms,
                               COUNT(*)                            AS samples
                        FROM cable_telemetry_samples
                        WHERE measured_at::date = :d
                        GROUP BY cable_id, method
                        ORDER BY cable_id
                    ");
                    $stmt->execute([':d' => $date]);
                    $idx = count($cables) + 1;
                    foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $row) {
                        $cid = $row['cable_id'];
                        if (!isset($cables[$cid])) {
                            $cables[$cid] = [
                                'index'      => $idx++,
                                'cable_id'   => $cid,
                                'proto'      => (string) $row['proto'],
                                'latency_ms' => (float)  $row['latency_ms'],
                                'samples'    => (int)    $row['samples'],
                                'source'     => 'db',
                            ];
                        }
                    }
                } catch (Throwable $_) { /* BD indisponível ou tabela ausente */ }
            }

            $allCablesArr = array_values($cables);

            // Resumo calculado quando o log não tinha summary (dados só do BD)
            if (!$summary && $allCablesArr) {
                $cntTcp   = count(array_filter($allCablesArr, fn($c) => str_contains($c['proto'], 'tcp')));
                $cntSynth = count(array_filter($allCablesArr, fn($c) => str_contains($c['proto'], 'heuristic') || str_contains($c['proto'], 'synthetic')));
                $cntMtr   = count(array_filter($allCablesArr, fn($c) => str_contains($c['proto'], 'mtr')));
                $cntHttp  = count(array_filter($allCablesArr, fn($c) => str_contains($c['proto'], 'http')));
                $summary  = ['inserted' => count($allCablesArr), 'tcp' => $cntTcp, 'synthetic' => $cntSynth, 'mtr' => $cntMtr, 'http' => $cntHttp, 'anomalies' => 0];
            }

            // Lista de protocolos únicos — normaliza removendo métricas por cabo
            // (ex: "tcp-100pkt-ports[80, 443]-p95=45ms" → "tcp-100pkt-ports[80, 443]")
            $protoCount = [];
            foreach ($allCablesArr as $c) {
                $cat = preg_replace('/-p95=[\d.]+ms$/', '', $c['proto']);
                $protoCount[$cat] = ($protoCount[$cat] ?? 0) + 1;
            }
            arsort($protoCount);
            $protoList = array_keys($protoCount);

            // Filtros opcionais via query string
            $fCable = trim((string) ($_reqAll['cable'] ?? ''));
            $fProto = trim((string) ($_reqAll['proto'] ?? ''));
            if ($fCable !== '') {
                $allCablesArr = array_values(array_filter($allCablesArr,
                    fn($c) => stripos($c['cable_id'], $fCable) !== false));
            }
            if ($fProto !== '') {
                $allCablesArr = array_values(array_filter($allCablesArr,
                    fn($c) => str_contains($c['proto'], $fProto)));
            }

            if (!$allCablesArr && !$protoList) {
                jsonResponse(['status' => 'error', 'message' => 'Nenhum dado para ' . $date . '. Execute a Sonda Diária primeiro.'], 404);
            }

            jsonResponse([
                'date'       => $date,
                'total'      => count($allCablesArr),
                'cables'     => $allCablesArr,
                'summary'    => $summary,
                'proto_list' => $protoList,
            ]);
        } catch (Throwable $_e) {
            jsonResponse(['error' => $_e->getMessage()], 500);
        }
    }

    // ── Endpoints COM banco de dados ─────────────────────────────────────────
    try {
        if (!extension_loaded('pdo_pgsql')) {
            throw new RuntimeException('Extensão pdo_pgsql não está carregada.');
        }

        $pdo = getDb($pgSocket, $pgPort, $pgDbname, $pgUser, $pgPass, $pgSsl);
        $req  = array_merge($_GET, $_POST);
        $mode = (string) ($req['mode'] ?? $req['action'] ?? 'all');
        checkRateLimit($pdo, $mode);
        if ($mode === 'search' && isset($req['mode'])) {
            $mode = (string) $req['mode'];
        }
        $q = trim((string) ($req['q'] ?? $req['cable_id'] ?? ''));
        $cols = cableColumns('c');
        $countryA = trim((string) ($req['countryA'] ?? $req['country_a'] ?? ''));
        $countryB = trim((string) ($req['countryB'] ?? $req['country_b'] ?? ''));

        // ── db_browse: navegador de tabela com paginação e filtros ───────────
        if ($mode === 'db_browse') {
            $dbAllowed = [
                'cables'                   => ['cable_id','cable_name','owners','capacity_gbps','length_km','rfs_year','is_operational','landing_points_count','suppliers'],
                'cable_metadata'           => ['cable_id','public_url','source_url','notes','is_planned','raw_length','raw_rfs'],
                'landing_points'           => ['endpoint_id','endpoint_name','city_name','country_name','country_code','latitude','longitude','geo_source'],
                'cable_landing_points'     => ['cable_id','endpoint_id','endpoint_order','is_tbd'],
                'nodes'                    => ['node_id','node_name','latitude','longitude','country_code','asn'],
                'cable_telemetry_samples'  => ['sample_id','cable_id','measured_at','latency_ms','jitter_ms','packet_loss_pct','throughput_mbps','source','method','confidence_score','estimated'],
                'telemetry_rollup_1h'      => ['cable_id','bucket','avg_latency_ms','avg_jitter_ms','max_latency_ms','p95_latency_ms','avg_packet_loss_pct','avg_throughput_mbps','confidence_avg','sample_count'],
                'telemetry_rollup_5m'      => ['cable_id','bucket','avg_latency_ms','avg_jitter_ms','max_latency_ms','avg_packet_loss_pct','avg_throughput_mbps','sample_count'],
                'telemetry_alerts'         => ['alert_id','cable_id','metric','severity','current_value','baseline_value','detected_at','resolved_at','description'],
                'cable_telemetry_reference'=> ['cable_id','baseline_latency_ms','baseline_jitter_ms','baseline_packet_loss_pct','baseline_throughput_mbps','baseline_availability_pct','updated_at'],
                'endpoint_ip_samples'      => ['endpoint_id','sample_order','country_name','country_code','region','city','latitude','longitude'],
                'cable_telemetry_hops'     => ['hop_id','sample_id','cable_id','hop_number','hop_ip','rtt_avg_ms','rtt_best_ms','rtt_worst_ms','rtt_stdev_ms','loss_pct','recorded_at'],
                'hop_switch_geolocations'  => ['id','hop_ip','lat','lng','city','region','country','country_code','isp','org','as_number','as_name','queried_at'],
            ];

            $table = (string) ($req['table'] ?? 'cables');
            if (!array_key_exists($table, $dbAllowed)) {
                jsonResponse(['error' => 'Tabela não permitida.'], 403);
            }
            $tableCols = $dbAllowed[$table];

            $page  = max(1, (int) ($req['page'] ?? 1));
            $limit = min(100, max(10, (int) ($req['limit'] ?? 25)));
            $offset = ($page - 1) * $limit;

            $sortCol = (string) ($req['sort_col'] ?? $tableCols[0]);
            $sortDir = strtoupper((string) ($req['sort_dir'] ?? 'ASC')) === 'DESC' ? 'DESC' : 'ASC';
            if (!in_array($sortCol, $tableCols, true)) $sortCol = $tableCols[0];

            $filterCol = (string) ($req['filter_col'] ?? '');
            $filterOp  = (string) ($req['filter_op']  ?? 'ilike');
            $filterVal = (string) ($req['filter_val'] ?? '');
            $opMap     = ['ilike' => 'ILIKE', 'eq' => '=', 'gt' => '>', 'lt' => '<', 'gte' => '>=', 'lte' => '<='];
            if (!in_array($filterCol, $tableCols, true)) $filterCol = '';
            if (!array_key_exists($filterOp, $opMap))   $filterOp  = 'ilike';

            $where  = '';
            $params = [];
            if ($filterCol !== '' && $filterVal !== '') {
                $op     = $opMap[$filterOp];
                $where  = "WHERE \"$filterCol\" $op ?";
                $params = [$filterOp === 'ilike' ? "%{$filterVal}%" : $filterVal];
            }

            $quotedTable = '"' . $table . '"';
            $countStmt = $pdo->prepare("SELECT COUNT(*) FROM {$quotedTable} {$where}");
            $countStmt->execute($params);
            $total = (int) $countStmt->fetchColumn();

            $dataStmt = $pdo->prepare(
                "SELECT * FROM {$quotedTable} {$where} ORDER BY \"{$sortCol}\" {$sortDir} LIMIT {$limit} OFFSET {$offset}"
            );
            $dataStmt->execute($params);
            $rows = $dataStmt->fetchAll(PDO::FETCH_ASSOC);

            jsonResponse([
                'table'   => $table,
                'total'   => $total,
                'page'    => $page,
                'limit'   => $limit,
                'columns' => $tableCols,
                'rows'    => $rows,
            ]);
        }

        switch ($mode) {
            case 'countries':
                jsonResponse(['data' => listCountries($pdo)]);

            case 'all':
                $stmt = $pdo->query("SELECT {$cols} FROM cables c ORDER BY c.cable_id");
                $rows = $stmt->fetchAll();
                foreach ($rows as &$row) {
                    $row['is_operational'] = normalizeBool($row['is_operational']);
                }
                unset($row);
                jsonResponse(['data' => $rows, 'total' => count($rows)]);

            case 'name':
            case 'owners':
            case 'suppliers':
                $fieldMap = [
                    'name' => 'c.cable_name',
                    'owners' => 'c.owners',
                    'suppliers' => 'c.suppliers',
                ];
                $stmt = $pdo->prepare(
                    "SELECT {$cols} FROM cables c WHERE {$fieldMap[$mode]} ILIKE :q ORDER BY c.cable_name"
                );
                $stmt->execute([':q' => "%{$q}%"]);
                break;

            case 'year':
                $year = filter_var($q, FILTER_VALIDATE_INT);
                if ($year === false) {
                    throw new InvalidArgumentException('Ano inválido.');
                }
                $stmt = $pdo->prepare(
                    "SELECT {$cols} FROM cables c WHERE c.rfs_year = :year ORDER BY c.cable_name"
                );
                $stmt->execute([':year' => $year]);
                break;

            case 'operational':
                $normalized = strtolower($q);
                if (!in_array($normalized, ['true', 'false', '1', '0'], true)) {
                    throw new InvalidArgumentException('Valor inválido para is_operational.');
                }
                $stmt = $pdo->prepare(
                    "SELECT {$cols} FROM cables c WHERE c.is_operational = :value ORDER BY c.cable_name"
                );
                $stmt->execute([':value' => in_array($normalized, ['true', '1'], true)]);
                break;

            case 'landing':
                $points = filter_var($q, FILTER_VALIDATE_INT);
                if ($points === false) {
                    throw new InvalidArgumentException('Número de endpoints inválido.');
                }
                $stmt = $pdo->prepare(
                    "SELECT {$cols} FROM cables c WHERE c.landing_points_count = :points ORDER BY c.cable_name"
                );
                $stmt->execute([':points' => $points]);
                break;

            case 'endpoint':
                $stmt = $pdo->prepare(
                    "SELECT DISTINCT {$cols}
                     FROM cables c
                     JOIN cable_landing_points clp ON clp.cable_id = c.cable_id
                     JOIN landing_points lp ON lp.endpoint_id = clp.endpoint_id
                     WHERE lp.endpoint_name ILIKE :q OR lp.city_name ILIKE :q
                     ORDER BY c.cable_name"
                );
                $stmt->execute([':q' => "%{$q}%"]);
                break;

            case 'country':
                $stmt = $pdo->prepare(
                    "SELECT DISTINCT {$cols}
                     FROM cables c
                     JOIN cable_landing_points clp ON clp.cable_id = c.cable_id
                     JOIN landing_points lp ON lp.endpoint_id = clp.endpoint_id
                     WHERE lp.country_name ILIKE :q OR lp.country_code ILIKE :q
                     ORDER BY c.cable_name"
                );
                $stmt->execute([':q' => "%{$q}%"]);
                break;

            case 'detail':
            case 'details':
                if ($q === '') {
                    throw new InvalidArgumentException('Selecione um cabo para visualizar os detalhes.');
                }
                jsonResponse(fetchCableDetails($pdo, $q));

            case 'top_routes':
                if ($countryA === '' || $countryB === '') {
                    throw new InvalidArgumentException('Selecione dois paises para calcular as rotas.');
                }
                $graph = buildCountryGraph($pdo);
                $routes = findTopRoutes($graph, $countryA, $countryB, 3, null);
                jsonResponse([
                    'from' => $countryA,
                    'to' => $countryB,
                    'total' => count($routes),
                    'routes' => $routes,
                ]);

            case 'critical_cuts':
            case 'critical_cables':
                if ($countryA === '' || $countryB === '') {
                    throw new InvalidArgumentException('Selecione dois paises para analisar isolamento por corte de cabo.');
                }
                $graph = buildCountryGraph($pdo);
                $routes = findTopRoutes($graph, $countryA, $countryB, 3, null);
                $cables = [];
                foreach ($routes as $route) {
                    $routeCables = is_array($route['cables'] ?? null) ? $route['cables'] : [];
                    foreach ($routeCables as $cable) {
                        $id = (string) ($cable['cable_id'] ?? '');
                        if ($id === '') {
                            continue;
                        }
                        if (!isset($cables[$id])) {
                            $cables[$id] = [
                                'cable_id' => $id,
                                'cable_name' => (string) ($cable['cable_name'] ?? $id),
                                'routes_hit' => 0,
                            ];
                        }
                        $cables[$id]['routes_hit']++;
                    }
                }

                $critical = [];
                foreach ($cables as $cableId => $meta) {
                    $stillConnected = hasCountryPath($graph, $countryA, $countryB, $cableId, 8);
                    if (!$stillConnected) {
                        $meta['impact'] = 'isolamento entre os paises alvo';
                        $critical[] = $meta;
                    }
                }

                usort($critical, static fn(array $a, array $b): int => $b['routes_hit'] <=> $a['routes_hit']);
                jsonResponse([
                    'from' => $countryA,
                    'to' => $countryB,
                    'routes_analyzed' => count($routes),
                    'critical_cables' => array_values($critical),
                ]);

            case 'endpoint_risk':
                $q = $q ?: trim((string) ($req['country'] ?? ''));
                if ($q === '') {
                    throw new InvalidArgumentException('Selecione um pais para listar endpoints com risco.');
                }
                $stmt = $pdo->prepare(
                    "SELECT
                        lp.endpoint_id,
                        lp.endpoint_name,
                        lp.city_name,
                        lp.country_name,
                        COUNT(DISTINCT clp.cable_id) AS cable_degree,
                        COUNT(DISTINCT lp2.country_name) AS country_reach
                     FROM landing_points lp
                     JOIN cable_landing_points clp ON clp.endpoint_id = lp.endpoint_id
                     LEFT JOIN cable_landing_points clp2 ON clp2.cable_id = clp.cable_id
                     LEFT JOIN landing_points lp2 ON lp2.endpoint_id = clp2.endpoint_id
                     WHERE lp.country_name = :country
                     GROUP BY lp.endpoint_id, lp.endpoint_name, lp.city_name, lp.country_name
                     ORDER BY cable_degree DESC, country_reach DESC, lp.endpoint_name
                     LIMIT 25"
                );
                $stmt->execute([':country' => $q]);
                $endpoints = $stmt->fetchAll();
                foreach ($endpoints as &$endpoint) {
                    $degree = (int) $endpoint['cable_degree'];
                    $reach = (int) $endpoint['country_reach'];
                    $riskScore = round(($degree * 3.2) + ($reach * 1.8), 2);
                    $endpoint['risk_score'] = $riskScore;
                    $endpoint['packet_loss_risk_pct'] = min(95, round(18 + ($riskScore * 1.7), 1));
                    $endpoint['congestion_level'] = $riskScore >= 38 ? 'alto' : ($riskScore >= 24 ? 'medio' : 'baixo');
                }
                unset($endpoint);
                jsonResponse([
                    'country' => $q,
                    'total' => count($endpoints),
                    'endpoints' => $endpoints,
                ]);

            case 'telemetry':
                if ($q === '') {
                    throw new InvalidArgumentException('Selecione um cabo para visualizar telemetria.');
                }
                $hours = filter_var($req['hours'] ?? 24, FILTER_VALIDATE_INT) ?: 24;
                $details = fetchCableDetails($pdo, $q);
                $series = fetchTelemetrySeries($pdo, $q, $hours);
                $fallbackUsed = false;
                if (!$series) {
                    $series = generateSyntheticTelemetrySeries($details, 96, 15);
                    $fallbackUsed = true;
                }
                $realAvg = loadRealHistoryAvg($q, $hours, $workspaceRoot);
                jsonResponse([
                    'details'  => $details,
                    'telemetry' => buildTelemetryPayload($details, $series, $fallbackUsed),
                    'real_avg' => $realAvg ?: null,
                ]);

            case 'alerts':
                $alerts = fetchActiveAlerts($pdo);
                jsonResponse(['total' => count($alerts), 'alerts' => $alerts]);

            case 'globe_data':
                $paths = [];
                $pts   = [];
                $seen  = [];

                // Load status for all cables from DB (is_operational + active alerts)
                $statusRows = $pdo->query("
                    SELECT c.cable_id, c.cable_name, c.is_operational,
                           EXISTS(
                               SELECT 1 FROM telemetry_alerts ta
                               WHERE ta.cable_id = c.cable_id AND ta.resolved_at IS NULL
                           ) AS has_alert
                    FROM cables c
                ")->fetchAll();
                $cableStatus = [];
                foreach ($statusRows as $sr) {
                    $cableStatus[$sr['cable_id']] = [
                        'cable_name'     => $sr['cable_name'],
                        'is_operational' => (bool) $sr['is_operational'],
                        'has_alert'      => (bool) $sr['has_alert'],
                    ];
                }

                // Load set of cable IDs with verified real TCP probe data
                // Source 1: DB (primary — works on Railway and locally with DB)
                $tcpProbedIds = [];
                try {
                    $tcpRows = $pdo->query("
                        SELECT DISTINCT cable_id
                        FROM cable_telemetry_samples
                        WHERE source = 'active_tcp_connect'
                          AND measured_at > NOW() - INTERVAL '72 hours'
                    ")->fetchAll(PDO::FETCH_COLUMN);
                    foreach ($tcpRows as $cid) {
                        $tcpProbedIds[$cid] = true;
                    }
                } catch (\Throwable) {}

                // Source 2: local JSON history files (fallback / offline)
                $histDir = dirname(__DIR__) . '/data/local/historico';
                if (is_dir($histDir)) {
                    $probeFiles = array_merge(
                        glob($histDir . '/*_probe_full_*.json')     ?: [],
                        glob($histDir . '/*/*_probe_full_*.json')   ?: []
                    );
                    if ($probeFiles) {
                        rsort($probeFiles);
                        $probeJson = @file_get_contents($probeFiles[0]);
                        if ($probeJson) {
                            $probeData = json_decode($probeJson, true);
                            foreach (($probeData['registros'] ?? []) as $reg) {
                                if (($reg['source'] ?? '') === 'active_tcp_connect') {
                                    $tcpProbedIds[$reg['cable_id']] = true;
                                }
                            }
                        }
                    }
                }

                // Load landing-point city/country for coordinate-enriched points
                $lpRows = $pdo->query("
                    SELECT lp.endpoint_id, lp.city_name, lp.country_name,
                           lp.latitude, lp.longitude,
                           (SELECT ip_start::text FROM endpoint_ip_samples eis
                            WHERE eis.endpoint_id = lp.endpoint_id
                            ORDER BY sample_order LIMIT 1) AS ip_sample
                    FROM landing_points lp
                    WHERE lp.latitude IS NOT NULL AND lp.longitude IS NOT NULL
                ")->fetchAll();
                // Index by rounded coords for quick lookup
                $lpByCoord = [];
                foreach ($lpRows as $lr) {
                    $key = round((float)$lr['latitude'], 3) . ',' . round((float)$lr['longitude'], 3);
                    $lpByCoord[$key] = $lr;
                }

                // Read the GeoJSON with actual cable paths (all 710 cables, 1908 segments)
                $geoPath = $dataExternal . '/submarine_cables.geojson';
                if (!file_exists($geoPath)) {
                    jsonResponse(['error' => 'GeoJSON não encontrado.'], 500);
                }
                $geo = json_decode(file_get_contents($geoPath), true);
                if (!isset($geo['features'])) {
                    jsonResponse(['error' => 'GeoJSON inválido.'], 500);
                }

                foreach ($geo['features'] as $feature) {
                    $props   = $feature['properties'];
                    $cableId = $props['cable_id'] ?? '';
                    $status  = $cableStatus[$cableId] ?? null;
                    $cableName   = $status['cable_name']     ?? $props['cable_name'] ?? $cableId;
                    $isOp        = $status['is_operational'] ?? true;
                    $hasAlert    = $status['has_alert']      ?? false;
                    $hasTcpProbe = isset($tcpProbedIds[$cableId]);

                    $segments = $feature['geometry']['coordinates'] ?? [];

                    foreach ($segments as $seg) {
                        if (count($seg) < 2) continue;

                        // GeoJSON: [lng, lat] → convert each point to [lat, lng] for globe.gl
                        $pathPts = array_map(
                            fn($c) => [(float)$c[1], (float)$c[0]],
                            $seg
                        );

                        [$startLat, $startLng] = $pathPts[0];
                        [$endLat,   $endLng]   = $pathPts[count($pathPts) - 1];

                        $paths[] = [
                            'cable_id'       => $cableId,
                            'cable_name'     => $cableName,
                            'is_operational' => $isOp,
                            'has_alert'      => $hasAlert,
                            'has_tcp_probe'  => $hasTcpProbe,
                            'startLat'       => $startLat,
                            'startLng'       => $startLng,
                            'endLat'         => $endLat,
                            'endLng'         => $endLng,
                            'points'         => $pathPts,
                        ];

                        // Landing-point dots at each segment endpoint
                        foreach ([[$startLat, $startLng], [$endLat, $endLng]] as [$lat, $lng]) {
                            $key = round($lat, 3) . ',' . round($lng, 3);
                            if (isset($seen[$key])) continue;
                            $seen[$key] = true;
                            $lp = $lpByCoord[$key] ?? null;
                            $pts[] = [
                                'endpoint_id'    => $lp['endpoint_id'] ?? $key,
                                'lat'            => $lat,
                                'lng'            => $lng,
                                'city_name'      => $lp['city_name']    ?? '',
                                'country_name'   => $lp['country_name'] ?? '',
                                'cable_id'       => $cableId,
                                'cable_name'     => $cableName,
                                'ip_sample'      => $lp['ip_sample']    ?? '',
                                'is_operational' => $isOp,
                                'has_alert'      => $hasAlert,
                                'has_tcp_probe'  => $hasTcpProbe,
                            ];
                        }
                    }
                }
                jsonResponse(['paths' => $paths, 'points' => $pts]);
                break;

            case 'endpoints':
                if ($q === '') {
                    throw new InvalidArgumentException('Selecione um cabo para listar endpoints.');
                }
                $epStmt = $pdo->prepare(
                    "SELECT lp.endpoint_id, lp.city_name, lp.country_name,
                            lp.latitude, lp.longitude,
                            (SELECT ip_start::text FROM endpoint_ip_samples eis
                             WHERE eis.endpoint_id = lp.endpoint_id
                             ORDER BY sample_order LIMIT 1) AS ip_sample
                     FROM cable_landing_points clp
                     JOIN landing_points lp ON lp.endpoint_id = clp.endpoint_id
                     WHERE clp.cable_id = :cid
                     ORDER BY clp.endpoint_order"
                );
                $epStmt->execute([':cid' => $q]);
                jsonResponse(['endpoints' => $epStmt->fetchAll()]);

            case 'probe':
                if ($q === '') {
                    throw new InvalidArgumentException('Selecione um cabo para a sonda de rede.');
                }
                $endpointId = trim((string) ($req['endpoint_id'] ?? ''));
                $targetIp   = trim((string) ($req['ip'] ?? ''));
                $fullScan   = filter_var($req['full'] ?? false, FILTER_VALIDATE_BOOLEAN);

                // Restringe IP direto (fornecido pelo usuário) a IPs do banco
                if ($targetIp !== '') {
                    assertProbeIpAllowed($pdo, $targetIp);
                }

                if ($targetIp === '' && $endpointId !== '') {
                    $ipStmt = $pdo->prepare(
                        'SELECT ip_start FROM endpoint_ip_samples
                         WHERE endpoint_id = :eid ORDER BY sample_order LIMIT 1'
                    );
                    $ipStmt->execute([':eid' => $endpointId]);
                    $ipRow    = $ipStmt->fetch();
                    $targetIp = (string) ($ipRow['ip_start'] ?? '');
                }

                $probeResult = runNetworkProbe($targetIp, $q, $endpointId, $pdo, $fullScan);
                $historyFile = saveProbeToHistory($probeResult, $q, $endpointId, $workspaceRoot, $fullScan);
                jsonResponse([
                    'cable_id'     => $q,
                    'endpoint_id'  => $endpointId ?: null,
                    'target'       => $targetIp ?: null,
                    'result'       => $probeResult,
                    'history_file' => $historyFile,
                ]);

            default:
                jsonResponse(['error' => 'Modo desconhecido.'], 400);
        }

        $rows = $stmt->fetchAll();
        foreach ($rows as &$row) {
            $row['is_operational'] = normalizeBool($row['is_operational']);
        }
        unset($row);
        jsonResponse(['data' => $rows, 'total' => count($rows)]);
    } catch (Throwable $e) {
        jsonResponse(['error' => $e->getMessage()], 500);
    }
}
