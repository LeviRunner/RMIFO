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

if (isset($_GET['api'])) {
    try {
        if (!extension_loaded('pdo_pgsql')) {
            throw new RuntimeException('Extensão pdo_pgsql não está carregada.');
        }

        $pdo = getDb($pgSocket, $pgPort, $pgDbname, $pgUser);
        $mode = $_GET['mode'] ?? 'all';
        $q = trim((string) ($_GET['q'] ?? ''));
        $cols = cableColumns('c');
        $countryA = trim((string) ($_GET['countryA'] ?? ''));
        $countryB = trim((string) ($_GET['countryB'] ?? ''));

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
                $hours = filter_var($_GET['hours'] ?? 24, FILTER_VALIDATE_INT) ?: 24;
                $details = fetchCableDetails($pdo, $q);
                $series = fetchTelemetrySeries($pdo, $q, $hours);
                $fallbackUsed = false;
                if (!$series) {
                    $series = generateSyntheticTelemetrySeries($details, 96, 15);
                    $fallbackUsed = true;
                }
                jsonResponse([
                    'details' => $details,
                    'telemetry' => buildTelemetryPayload($details, $series, $fallbackUsed),
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
