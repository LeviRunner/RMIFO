<?php
declare(strict_types=1);

$workspaceRoot = getenv('WORKSPACE_ROOT') ?: dirname(__DIR__, 3);
$pgSocket = getenv('PG_SOCKET') ?: $workspaceRoot . '/.local/run';
$pgPort = (int) (getenv('PG_PORT') ?: 55432);
$pgDbname = 'global_network_resilience';
$pgUser = 'postgres';

// Evita warnings/notices em HTML antes do JSON da API.
ini_set('display_errors', '0');
set_error_handler(static function (int $severity, string $message, string $file, int $line): bool {
    if (!(error_reporting() & $severity)) {
        return false;
    }
    throw new ErrorException($message, 0, $severity, $file, $line);
});

function getDb(string $socket, int $port, string $dbname, string $user): PDO
{
    $dsn = "pgsql:host={$socket};port={$port};dbname={$dbname}";
    return new PDO($dsn, $user, '', [
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

function jsonResponse(array $payload, int $status = 200): never
{
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    header('X-Content-Type-Options: nosniff');
    header('Cache-Control: no-store');
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
    $histDir = rtrim($workspaceRoot, '/') . '/data/local/history';
    if (!is_dir($histDir)) return [];

    $cutoff = time() - ($hours * 3600);

    // Collect probe_full e checkpoint — raiz (legado) + MM/DD/ subdiretórios
    $files = array_merge(
        glob($histDir . '/*_probe_full_*.json')        ?: [],
        glob($histDir . '/*_checkpoint_*.json')        ?: [],
        glob($histDir . '/*/*/*_probe_full_*.json')    ?: [],
        glob($histDir . '/*/*/*_checkpoint_*.json')    ?: []
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
    try {
        if (!extension_loaded('pdo_pgsql')) {
            throw new RuntimeException('Extensão pdo_pgsql não está carregada.');
        }

        $pdo = getDb($pgSocket, $pgPort, $pgDbname, $pgUser);
        $req  = array_merge($_GET, $_POST);
        $mode = (string) ($req['mode'] ?? $req['action'] ?? 'all');
        if ($mode === 'search' && isset($req['mode'])) {
            $mode = (string) $req['mode'];
        }
        $q = trim((string) ($req['q'] ?? $req['cable_id'] ?? ''));
        $cols = cableColumns('c');
        $countryA = trim((string) ($req['countryA'] ?? $req['country_a'] ?? ''));
        $countryB = trim((string) ($req['countryB'] ?? $req['country_b'] ?? ''));

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

                // Load set of cable IDs with verified real TCP probe data from latest history
                $tcpProbedIds = [];
                $histDir = $workspaceRoot . '/data/local/history';
                if (is_dir($histDir)) {
                    $probeFiles = array_merge(
                        glob($histDir . '/*_probe_full_*.json')       ?: [],
                        glob($histDir . '/*/*/*_probe_full_*.json')   ?: []
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
                $geoPath = $workspaceRoot . '/data/external/submarine_cables.geojson';
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
