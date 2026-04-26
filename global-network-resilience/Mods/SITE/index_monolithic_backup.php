<?php
declare(strict_types=1);

$workspaceRoot = getenv('WORKSPACE_ROOT') ?: dirname(__DIR__, 3);
$pgSocket = getenv('PG_SOCKET') ?: $workspaceRoot . '/.local/run';
$pgPort = (int) (getenv('PG_PORT') ?: 55432);
$pgDbname = 'global_network_resilience';
$pgUser = 'postgres';

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
            lp.country_name
         FROM cable_landing_points clp
         JOIN landing_points lp ON lp.endpoint_id = clp.endpoint_id
         JOIN cables c ON c.cable_id = clp.cable_id
         WHERE lp.country_name IS NOT NULL AND lp.country_name <> ''"
    );

    $cableCountries = [];
    $cableMeta = [];

    foreach ($stmt->fetchAll() as $row) {
        $cableId = (string) $row['cable_id'];
        $country = (string) $row['country_name'];
        $cableCountries[$cableId][$country] = true;
        $cableMeta[$cableId] = [
            'name' => (string) $row['cable_name'],
            'length_km' => (int) ($row['length_km'] ?? 0),
        ];
    }

    $graph = [];
    foreach ($cableCountries as $cableId => $countriesMap) {
        $countries = array_keys($countriesMap);
        $countryCount = count($countries);
        if ($countryCount < 2) {
            continue;
        }

        $length = max(1, (int) ($cableMeta[$cableId]['length_km'] ?? 0));
        $weight = max(20.0, $length / max(1, $countryCount - 1));

        for ($i = 0; $i < $countryCount; $i++) {
            for ($j = $i + 1; $j < $countryCount; $j++) {
                $a = $countries[$i];
                $b = $countries[$j];
                $edge = [
                    'to' => $b,
                    'cable_id' => $cableId,
                    'cable_name' => $cableMeta[$cableId]['name'],
                    'weight' => $weight,
                    'length_km' => $length,
                ];
                $graph[$a][] = $edge;
                $edge['to'] = $a;
                $graph[$b][] = $edge;
            }
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
                    foreach ($route['cables'] as $cable) {
                        $id = $cable['cable_id'];
                        if (!isset($cables[$id])) {
                            $cables[$id] = [
                                'cable_id' => $id,
                                'cable_name' => $cable['cable_name'],
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
                $details = fetchCableDetails($pdo, $q);
                jsonResponse([
                    'details' => $details,
                    'telemetry' => cableTelemetry($details),
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
?><!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Busca Binária - Cabos Submarinos</title>
<style>
* { box-sizing: border-box; }
body {
    margin: 0;
    font-family: "Segoe UI", system-ui, sans-serif;
    background: #0d1117;
    color: #e6edf3;
}
header {
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 16px 24px;
    border-bottom: 1px solid #30363d;
    background: #11161d;
    position: sticky;
    top: 0;
    z-index: 20;
}
header h1 {
    font-size: 16px;
    margin: 0;
}
header .pill {
    margin-left: auto;
    font-size: 12px;
    color: #8b949e;
    border: 1px solid #30363d;
    border-radius: 999px;
    padding: 4px 10px;
}
.layout {
    display: grid;
    grid-template-columns: 300px minmax(0, 1fr);
    gap: 18px;
    padding: 20px 24px 32px;
}
.panel {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 10px;
    overflow: hidden;
}
.panel h2,
.panel h3 {
    margin: 0;
    font-size: 12px;
    color: #8b949e;
    text-transform: uppercase;
    letter-spacing: 0.08em;
}
.panel-head {
    padding: 12px 14px;
    border-bottom: 1px solid #30363d;
}
.panel-body {
    padding: 14px;
}
.mode-list {
    display: flex;
    flex-direction: column;
    gap: 4px;
}
.mode-btn,
.tab-btn,
.btn,
.detail-action {
    cursor: pointer;
    border: 1px solid transparent;
    transition: background 0.14s ease, border-color 0.14s ease, color 0.14s ease;
}
.mode-btn {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 9px 10px;
    background: transparent;
    color: #e6edf3;
    border-radius: 8px;
    text-align: left;
}
.mode-btn:hover,
.tab-btn:hover,
.btn:hover,
.detail-action:hover {
    background: #21262d;
}
.mode-btn.active,
.tab-btn.active {
    background: rgba(88, 166, 255, 0.13);
    border-color: rgba(88, 166, 255, 0.35);
    color: #58a6ff;
}
.mode-meta {
    margin-left: auto;
    color: #8b949e;
    font-size: 11px;
}
.field {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin-top: 14px;
}
.field label {
    font-size: 12px;
    color: #8b949e;
}
input,
select {
    width: 100%;
    padding: 10px 12px;
    border-radius: 8px;
    border: 1px solid #30363d;
    background: #0d1117;
    color: #e6edf3;
}
.hint {
    margin-top: 10px;
    padding: 10px 12px;
    border-radius: 8px;
    border: 1px solid rgba(88, 166, 255, 0.3);
    background: rgba(88, 166, 255, 0.08);
    font-size: 12px;
    color: #9ecbff;
}
.btn-row,
.detail-pick {
    display: flex;
    gap: 8px;
    margin-top: 12px;
}
.btn,
.detail-action {
    padding: 9px 14px;
    border-radius: 8px;
    background: #21262d;
    color: #e6edf3;
}
.btn.primary,
.detail-action.primary {
    background: #58a6ff;
    color: #0d1117;
}
.tabs {
    display: flex;
    gap: 8px;
    padding: 12px 14px 0;
}
.tab-btn {
    border-radius: 8px 8px 0 0;
    background: #0d1117;
    color: #8b949e;
    padding: 10px 14px;
}
.tab-panel {
    display: none;
}
.tab-panel.active {
    display: block;
}
.result-bar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 10px;
    margin-bottom: 12px;
}
.result-count {
    color: #8b949e;
    font-size: 13px;
}
.error {
    display: none;
    margin-bottom: 12px;
    padding: 11px 12px;
    border-radius: 8px;
    border: 1px solid rgba(248, 81, 73, 0.4);
    background: rgba(248, 81, 73, 0.11);
    color: #ff938b;
}
.error.show {
    display: block;
}
.empty {
    padding: 56px 20px;
    text-align: center;
    color: #8b949e;
}
.table-wrap {
    overflow: auto;
    border: 1px solid #30363d;
    border-radius: 10px;
}
table {
    width: 100%;
    border-collapse: collapse;
    min-width: 1000px;
}
th,
td {
    padding: 10px 12px;
    border-bottom: 1px solid #30363d;
    text-align: left;
    vertical-align: top;
}
th {
    font-size: 11px;
    color: #8b949e;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    background: #11161d;
}
tbody tr:hover {
    background: rgba(255, 255, 255, 0.03);
}
.mono {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.badge {
    display: inline-flex;
    padding: 3px 8px;
    border-radius: 999px;
    font-size: 11px;
    border: 1px solid transparent;
}
.yes {
    background: rgba(63, 185, 80, 0.15);
    color: #3fb950;
    border-color: rgba(63, 185, 80, 0.35);
}
.no {
    background: rgba(248, 81, 73, 0.12);
    color: #ff938b;
    border-color: rgba(248, 81, 73, 0.35);
}
.muted {
    color: #8b949e;
}
.binary-box {
    display: none;
    margin-bottom: 14px;
    padding: 14px;
    border-radius: 10px;
    border: 1px solid #30363d;
    background: #0f151c;
}
.binary-box.show {
    display: block;
}
.binary-controls {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-top: 12px;
}
.binary-step {
    line-height: 1.7;
    font-size: 13px;
}
.binary-log {
    margin-top: 12px;
    max-height: 170px;
    overflow: auto;
    border-top: 1px solid #30363d;
    padding-top: 10px;
    font-size: 12px;
    color: #8b949e;
}
.progress {
    margin-top: 10px;
    height: 4px;
    border-radius: 999px;
    background: #30363d;
    overflow: hidden;
}
.progress > div {
    height: 100%;
    background: #58a6ff;
    width: 0;
}
tr.bs-eliminated {
    opacity: 0.2;
}
tr.bs-range {
    background: rgba(88, 166, 255, 0.05);
}
tr.bs-mid {
    background: rgba(240, 230, 70, 0.12);
}
tr.bs-found {
    background: rgba(63, 185, 80, 0.14);
}
.detail-summary {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
    gap: 10px;
    margin-bottom: 16px;
}
.summary-card,
.endpoint-card {
    border: 1px solid #30363d;
    border-radius: 10px;
    background: #0f151c;
    padding: 12px;
}
.summary-card .k,
.endpoint-meta .k {
    font-size: 11px;
    color: #8b949e;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}
.summary-card .v {
    margin-top: 6px;
    font-size: 14px;
    word-break: break-word;
}
.endpoint-list {
    display: grid;
    gap: 12px;
}
.endpoint-head {
    display: flex;
    justify-content: space-between;
    gap: 10px;
    align-items: start;
    margin-bottom: 10px;
}
.endpoint-title {
    font-size: 15px;
    margin: 0;
}
.endpoint-meta {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 10px;
    margin-bottom: 12px;
}
.subhead {
    margin: 12px 0 8px;
    font-size: 12px;
    color: #8b949e;
    text-transform: uppercase;
    letter-spacing: 0.08em;
}
.ip-table {
    width: 100%;
    min-width: 0;
}
.ip-table th,
.ip-table td {
    padding: 8px 10px;
}
.function-grid {
    display: grid;
    gap: 14px;
    grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
}
.function-card {
    border: 1px solid #30363d;
    border-radius: 10px;
    background: #0f151c;
    padding: 12px;
}
.fn-name {
    display: inline-block;
    font-size: 12px;
    letter-spacing: 0.07em;
    text-transform: uppercase;
    color: #f6f8fa;
    background: rgba(246, 248, 250, 0.08);
    border: 1px solid rgba(246, 248, 250, 0.25);
    border-radius: 999px;
    padding: 5px 10px;
    margin-bottom: 10px;
    font-weight: 700;
}
.fn-action {
    margin-top: 10px;
    width: 100%;
}
.function-result {
    margin-top: 14px;
    border: 1px solid #30363d;
    border-radius: 10px;
    background: #0f151c;
    padding: 14px;
}
.route-card {
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 10px;
    margin-bottom: 10px;
    background: rgba(88, 166, 255, 0.05);
}
.telemetry-grid {
    display: grid;
    gap: 12px;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    margin-top: 10px;
}
.gauge {
    border: 1px solid #30363d;
    border-radius: 10px;
    background: #11161d;
    padding: 12px;
    text-align: center;
}
.gauge svg {
    width: 120px;
    height: 120px;
}
.gauge .value {
    font-size: 18px;
    font-weight: 700;
}
.gauge .label {
    color: #8b949e;
    font-size: 12px;
    margin-top: 5px;
}
#alert-ticker {
    background: #161b22;
    border-bottom: 1px solid #30363d;
    padding: 8px 24px;
}
.alert-item {
    padding: 8px 12px;
    border-radius: 6px;
    margin-bottom: 4px;
    font-size: 13px;
    border: 1px solid transparent;
}
.alert-item:last-child { margin-bottom: 0; }
.alert-item.warning {
    background: rgba(210, 153, 34, 0.15);
    color: #d29922;
    border-color: rgba(210, 153, 34, 0.35);
}
.alert-item.critical {
    background: rgba(248, 81, 73, 0.15);
    color: #ff7b72;
    border-color: rgba(248, 81, 73, 0.35);
}
@media (max-width: 920px) {
    .layout {
        grid-template-columns: 1fr;
    }
}
</style>
</head>
<body>
<header>
    <div>🌐</div>
    <h1>Busca Binária e Catálogo Detalhado de Cabos</h1>
    <div class="pill">PostgreSQL local · porta 55432</div>
</header>
<div id="alert-ticker" style="display:none"></div>

<div class="layout">
    <aside class="panel">
        <div class="panel-head"><h2>Métodos de busca</h2></div>
        <div class="panel-body">
            <div class="mode-list" id="mode-list">
                <button class="mode-btn active" data-mode="cable_id">🔢 cable_id <span class="mode-meta">binária</span></button>
                <button class="mode-btn" data-mode="name">📛 cable_name <span class="mode-meta">texto</span></button>
                <button class="mode-btn" data-mode="owners">🏢 owners <span class="mode-meta">texto</span></button>
                <button class="mode-btn" data-mode="year">📅 rfs_year <span class="mode-meta">ano</span></button>
                <button class="mode-btn" data-mode="operational">⚡ is_operational <span class="mode-meta">bool</span></button>
                <button class="mode-btn" data-mode="landing">⚓ landing_points <span class="mode-meta">número</span></button>
                <button class="mode-btn" data-mode="suppliers">🔧 suppliers <span class="mode-meta">texto</span></button>
                <button class="mode-btn" data-mode="endpoint">📍 endpoint <span class="mode-meta">novo</span></button>
                <button class="mode-btn" data-mode="country">🗺 país do endpoint <span class="mode-meta">novo</span></button>
            </div>

            <div class="field">
                <label for="query-input" id="query-label">ID exato do cabo</label>
                <input id="query-input" type="text" placeholder="ex: 2africa" autocomplete="off">
                <select id="bool-input" style="display:none">
                    <option value="true">true</option>
                    <option value="false">false</option>
                </select>
            </div>

            <div class="hint" id="binary-hint">
                A busca binária trabalha sobre a lista ordenada por <span class="mono">cable_id</span> e mostra cada midpoint ate encontrar o cabo.
            </div>

            <div class="btn-row">
                <button class="btn primary" id="search-btn">Buscar</button>
                <button class="btn" id="clear-btn">Limpar</button>
            </div>
        </div>
    </aside>

    <main class="panel">
        <div class="tabs">
            <button class="tab-btn active" data-tab="search">Busca</button>
            <button class="tab-btn" data-tab="detail">Detalhes do cabo</button>
            <button class="tab-btn" data-tab="functions">Funções de análise</button>
        </div>

        <div class="panel-body tab-panel active" id="tab-search">
            <div class="result-bar">
                <div class="result-count" id="result-count">Carregando catálogo...</div>
            </div>

            <div class="error" id="search-error"></div>

            <div class="binary-box" id="binary-box">
                <div class="binary-step" id="binary-step">-</div>
                <div class="progress"><div id="binary-progress"></div></div>
                <div class="binary-controls">
                    <button class="btn primary" id="binary-play">Reproduzir</button>
                    <button class="btn" id="binary-prev">Anterior</button>
                    <button class="btn" id="binary-next">Proximo</button>
                    <button class="btn" id="binary-reset">Reiniciar</button>
                </div>
                <div class="binary-log" id="binary-log"></div>
            </div>

            <div class="empty" id="search-empty">Selecione um método de busca e execute a consulta.</div>
            <div class="table-wrap" id="search-table-wrap" style="display:none">
                <table>
                    <thead id="search-head"></thead>
                    <tbody id="search-body"></tbody>
                </table>
            </div>
        </div>

        <div class="panel-body tab-panel" id="tab-detail">
            <div class="detail-pick">
                <input id="detail-picker" list="cable-list" placeholder="Digite ou selecione um cable_id para abrir os detalhes">
                <datalist id="cable-list"></datalist>
                <button class="detail-action primary" id="detail-load">Abrir detalhes</button>
            </div>

            <div class="error" id="detail-error"></div>
            <div class="empty" id="detail-empty">Abra um cabo para ver endpoints, geografia e amostras de IP publico por endpoint.</div>
            <div id="detail-content" style="display:none">
                <div class="detail-summary" id="detail-summary"></div>
                <div class="subhead">Endpoints e geografia</div>
                <div class="endpoint-list" id="endpoint-list"></div>
            </div>
        </div>

        <div class="panel-body tab-panel" id="tab-functions">
            <div class="function-grid">
                <section class="function-card">
                    <div class="fn-name">FUNCAO_ROTA_TOP3</div>
                    <div class="field">
                        <label for="route-country-a">Pais origem</label>
                        <select id="route-country-a"></select>
                    </div>
                    <div class="field">
                        <label for="route-country-b">Pais destino</label>
                        <select id="route-country-b"></select>
                    </div>
                    <button class="btn primary fn-action" id="btn-top-routes">Top 3 Rotas</button>
                </section>

                <section class="function-card">
                    <div class="fn-name">FUNCAO_CORTE_ISOLAMENTO</div>
                    <div class="field">
                        <label for="cut-country-a">Pais origem</label>
                        <select id="cut-country-a"></select>
                    </div>
                    <div class="field">
                        <label for="cut-country-b">Pais destino</label>
                        <select id="cut-country-b"></select>
                    </div>
                    <button class="btn primary fn-action" id="btn-critical-cuts">Cabos que isolam</button>
                </section>

                <section class="function-card">
                    <div class="fn-name">FUNCAO_ENDPOINT_RISCO</div>
                    <div class="field">
                        <label for="risk-country">Pais</label>
                        <select id="risk-country"></select>
                    </div>
                    <button class="btn primary fn-action" id="btn-endpoint-risk">Endpoints com risco</button>
                </section>

                <section class="function-card">
                    <div class="fn-name">FUNCAO_TELEMETRIA_CABO</div>
                    <div class="field">
                        <label for="telemetry-cable">Cabo</label>
                        <input id="telemetry-cable" list="cable-list" placeholder="Selecione um cable_id">
                    </div>
                    <div class="field">
                        <label for="telemetry-hours">Janela Histórica</label>
                        <select id="telemetry-hours">
                            <option value="24">Últimas 24 horas (Tempo Real)</option>
                            <option value="168">Últimos 7 dias (Agregado 5m)</option>
                            <option value="720">Últimos 30 dias (Agregado 1h)</option>
                            <option value="2160">Últimos 90 dias (Tendência)</option>
                        </select>
                    </div>
                    <button class="btn primary fn-action" id="btn-telemetry">Buscar telemetria</button>
                </section>
            </div>

            <div class="error" id="function-error"></div>
            <div class="function-result" id="function-result">
                Escolha uma função e execute a análise. A busca principal permanece inalterada.
            </div>
        </div>
    </main>
</div>

<script>
const state = {
    mode: 'cable_id',
    allCables: [],
    filteredRows: [],
    binarySteps: [],
    binaryIndex: 0,
    binaryTimer: null,
    selectedCable: null,
    countries: [],
};

const modes = {
    cable_id: { label: 'ID exato do cabo', placeholder: 'ex: 2africa', type: 'text', api: 'all' },
    name: { label: 'Nome do cabo', placeholder: 'ex: Africa', type: 'text', api: 'name' },
    owners: { label: 'Owner', placeholder: 'ex: AT&T', type: 'text', api: 'owners' },
    year: { label: 'Ano RFS', placeholder: 'ex: 2024', type: 'number', api: 'year' },
    operational: { label: 'Status operacional', placeholder: '', type: 'bool', api: 'operational' },
    landing: { label: 'Quantidade de endpoints', placeholder: 'ex: 4', type: 'number', api: 'landing' },
    suppliers: { label: 'Fornecedor', placeholder: 'ex: ASN', type: 'text', api: 'suppliers' },
    endpoint: { label: 'Nome/cidade do endpoint', placeholder: 'ex: Marseille', type: 'text', api: 'endpoint' },
    country: { label: 'Pais do endpoint', placeholder: 'ex: Brazil ou BR', type: 'text', api: 'country' },
};

const dom = {
    modeList: document.getElementById('mode-list'),
    queryLabel: document.getElementById('query-label'),
    queryInput: document.getElementById('query-input'),
    boolInput: document.getElementById('bool-input'),
    searchBtn: document.getElementById('search-btn'),
    clearBtn: document.getElementById('clear-btn'),
    binaryHint: document.getElementById('binary-hint'),
    resultCount: document.getElementById('result-count'),
    searchError: document.getElementById('search-error'),
    searchEmpty: document.getElementById('search-empty'),
    tableWrap: document.getElementById('search-table-wrap'),
    searchHead: document.getElementById('search-head'),
    searchBody: document.getElementById('search-body'),
    tabs: Array.from(document.querySelectorAll('.tab-btn')),
    panels: {
        search: document.getElementById('tab-search'),
        detail: document.getElementById('tab-detail'),
        functions: document.getElementById('tab-functions'),
    },
    binaryBox: document.getElementById('binary-box'),
    binaryStep: document.getElementById('binary-step'),
    binaryProgress: document.getElementById('binary-progress'),
    binaryLog: document.getElementById('binary-log'),
    binaryPlay: document.getElementById('binary-play'),
    binaryPrev: document.getElementById('binary-prev'),
    binaryNext: document.getElementById('binary-next'),
    binaryReset: document.getElementById('binary-reset'),
    detailPicker: document.getElementById('detail-picker'),
    detailDatalist: document.getElementById('cable-list'),
    detailLoad: document.getElementById('detail-load'),
    detailError: document.getElementById('detail-error'),
    detailEmpty: document.getElementById('detail-empty'),
    detailContent: document.getElementById('detail-content'),
    detailSummary: document.getElementById('detail-summary'),
    endpointList: document.getElementById('endpoint-list'),
    functionError: document.getElementById('function-error'),
    functionResult: document.getElementById('function-result'),
    routeCountryA: document.getElementById('route-country-a'),
    routeCountryB: document.getElementById('route-country-b'),
    cutCountryA: document.getElementById('cut-country-a'),
    cutCountryB: document.getElementById('cut-country-b'),
    riskCountry: document.getElementById('risk-country'),
    telemetryCable: document.getElementById('telemetry-cable'),
    btnTopRoutes: document.getElementById('btn-top-routes'),
    btnCriticalCuts: document.getElementById('btn-critical-cuts'),
    btnEndpointRisk: document.getElementById('btn-endpoint-risk'),
    btnTelemetry: document.getElementById('btn-telemetry'),
};

function esc(value) {
    if (value === null || value === undefined || value === '') {
        return '<span class="muted">-</span>';
    }
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function fmtBool(value) {
    if (value === true || value === 'true' || value === 't') {
        return '<span class="badge yes">Sim</span>';
    }
    if (value === false || value === 'false' || value === 'f') {
        return '<span class="badge no">Nao</span>';
    }
    return '<span class="muted">-</span>';
}

function showError(node, message) {
    node.textContent = message;
    node.classList.add('show');
}

function clearError(node) {
    node.textContent = '';
    node.classList.remove('show');
}

async function api(mode, q = '') {
    const response = await fetch(`?api=1&mode=${encodeURIComponent(mode)}&q=${encodeURIComponent(q)}`);
    const payload = await response.json();
    if (payload.error) {
        throw new Error(payload.error);
    }
    return payload;
}

async function apiWithParams(mode, params = {}) {
    const searchParams = new URLSearchParams({ api: '1', mode });
    Object.entries(params).forEach(([key, value]) => {
        if (value !== null && value !== undefined) {
            searchParams.set(key, String(value));
        }
    });
    const response = await fetch(`?${searchParams.toString()}`);
    const payload = await response.json();
    if (payload.error) {
        throw new Error(payload.error);
    }
    return payload;
}

function switchTab(tab) {
    dom.tabs.forEach((button) => button.classList.toggle('active', button.dataset.tab === tab));
    Object.entries(dom.panels).forEach(([key, panel]) => panel.classList.toggle('active', key === tab));
}

function switchMode(mode) {
    state.mode = mode;
    const cfg = modes[mode];
    dom.queryLabel.textContent = cfg.label;
    dom.queryInput.placeholder = cfg.placeholder;
    dom.queryInput.type = cfg.type === 'number' ? 'number' : 'text';
    dom.queryInput.style.display = cfg.type === 'bool' ? 'none' : '';
    dom.boolInput.style.display = cfg.type === 'bool' ? '' : 'none';
    dom.binaryHint.style.display = mode === 'cable_id' ? '' : 'none';
    dom.modeList.querySelectorAll('.mode-btn').forEach((button) => {
        button.classList.toggle('active', button.dataset.mode === mode);
    });
    resetSearchSurface();
}

function resetBinary() {
    if (state.binaryTimer) {
        clearInterval(state.binaryTimer);
        state.binaryTimer = null;
    }
    dom.binaryPlay.textContent = 'Reproduzir';
    dom.binaryBox.classList.remove('show');
    dom.binaryStep.innerHTML = '-';
    dom.binaryLog.innerHTML = '';
    dom.binaryProgress.style.width = '0%';
    state.binarySteps = [];
    state.binaryIndex = 0;
}

function resetSearchSurface() {
    clearError(dom.searchError);
    resetBinary();
    dom.searchEmpty.style.display = '';
    dom.tableWrap.style.display = 'none';
    dom.resultCount.textContent = 'Selecione uma busca.';
    dom.searchHead.innerHTML = '';
    dom.searchBody.innerHTML = '';
}

function renderRows(rows) {
    const headers = [
        'acao', '#', 'cable_id', 'cable_name', 'owners', 'length_km', 'rfs_year', 'is_operational', 'landing_points_count', 'suppliers'
    ];
    dom.searchHead.innerHTML = `<tr>${headers.map((header) => `<th>${esc(header)}</th>`).join('')}</tr>`;
    dom.searchBody.innerHTML = rows.map((row, index) => `
        <tr data-cable-id="${esc(row.cable_id)}">
            <td><button class="detail-action" data-open-detail="${esc(row.cable_id)}">Detalhes</button></td>
            <td class="mono">${index}</td>
            <td class="mono">${esc(row.cable_id)}</td>
            <td>${esc(row.cable_name)}</td>
            <td>${esc(row.owners)}</td>
            <td>${esc(row.length_km)}</td>
            <td>${esc(row.rfs_year)}</td>
            <td>${fmtBool(row.is_operational)}</td>
            <td>${esc(row.landing_points_count)}</td>
            <td>${esc(row.suppliers)}</td>
        </tr>
    `).join('');
    dom.searchEmpty.style.display = rows.length ? 'none' : '';
    dom.tableWrap.style.display = rows.length ? '' : 'none';
}

function buildBinarySteps(target) {
    const rows = state.allCables;
    const normalizedTarget = target.toLowerCase();
    let lo = 0;
    let hi = rows.length - 1;
    const steps = [];
    while (lo <= hi) {
        const mid = Math.floor((lo + hi) / 2);
        const current = String(rows[mid].cable_id).toLowerCase();
        const cmp = current.localeCompare(normalizedTarget);
        steps.push({ lo, hi, mid, current, cmp, found: cmp === 0 });
        if (cmp === 0) {
            break;
        }
        if (cmp < 0) {
            lo = mid + 1;
        } else {
            hi = mid - 1;
        }
    }
    if (!steps.length || !steps[steps.length - 1].found) {
        steps.push({ lo, hi, mid: -1, current: null, cmp: null, found: false, notFound: true });
    }
    return steps;
}

function describeBinaryStep(step, index, total) {
    if (step.notFound) {
        return `Passo ${index + 1}/${total}: intervalo vazio (lo=${step.lo}, hi=${step.hi}). Cabo nao encontrado.`;
    }
    if (step.found) {
        return `Passo ${index + 1}/${total}: midpoint [${step.mid}] = ${step.current}. Cabo encontrado.`;
    }
    if (step.cmp < 0) {
        return `Passo ${index + 1}/${total}: midpoint [${step.mid}] = ${step.current}. Valor menor que o alvo, seguir para a direita.`;
    }
    return `Passo ${index + 1}/${total}: midpoint [${step.mid}] = ${step.current}. Valor maior que o alvo, seguir para a esquerda.`;
}

function applyBinaryStep(index) {
    state.binaryIndex = index;
    const step = state.binarySteps[index];
    const total = state.binarySteps.length;
    const rows = Array.from(dom.searchBody.querySelectorAll('tr'));
    rows.forEach((row, rowIndex) => {
        row.classList.remove('bs-eliminated', 'bs-range', 'bs-mid', 'bs-found');
        if (step.notFound) {
            row.classList.add('bs-eliminated');
            return;
        }
        if (rowIndex < step.lo || rowIndex > step.hi) {
            row.classList.add('bs-eliminated');
        } else if (rowIndex === step.mid) {
            row.classList.add(step.found ? 'bs-found' : 'bs-mid');
        } else {
            row.classList.add('bs-range');
        }
    });
    dom.binaryStep.textContent = describeBinaryStep(step, index, total);
    dom.binaryProgress.style.width = `${((index + 1) / total) * 100}%`;
    dom.binaryLog.innerHTML = state.binarySteps
        .slice(0, index + 1)
        .map((entry, entryIndex) => `<div>${esc(describeBinaryStep(entry, entryIndex, total))}</div>`)
        .join('');
}

function toggleBinaryPlayback() {
    if (state.binaryTimer) {
        clearInterval(state.binaryTimer);
        state.binaryTimer = null;
        dom.binaryPlay.textContent = 'Reproduzir';
        return;
    }
    dom.binaryPlay.textContent = 'Pausar';
    state.binaryTimer = setInterval(() => {
        if (state.binaryIndex < state.binarySteps.length - 1) {
            applyBinaryStep(state.binaryIndex + 1);
        } else {
            clearInterval(state.binaryTimer);
            state.binaryTimer = null;
            dom.binaryPlay.textContent = 'Reproduzir';
        }
    }, 850);
}

async function performSearch() {
    clearError(dom.searchError);
    resetBinary();
    const cfg = modes[state.mode];
    const query = cfg.type === 'bool' ? dom.boolInput.value : dom.queryInput.value.trim();

    try {
        if (state.mode === 'cable_id') {
            if (!state.allCables.length) {
                const payload = await api('all');
                state.allCables = payload.data;
                populateDatalist();
            }
            renderRows(state.allCables);
            state.filteredRows = state.allCables;
            if (!query) {
                dom.resultCount.textContent = `${state.allCables.length} cabos carregados.`;
                return;
            }
            state.binarySteps = buildBinarySteps(query);
            dom.binaryBox.classList.add('show');
            applyBinaryStep(0);
            const found = state.binarySteps[state.binarySteps.length - 1].found;
            dom.resultCount.textContent = found
                ? `1 resultado. Busca binaria em ${state.binarySteps.length} passos.`
                : `0 resultados. Busca binaria encerrada em ${state.binarySteps.length} passos.`;
            return;
        }

        if (!query) {
            throw new Error('Digite um valor para buscar.');
        }

        const payload = await api(cfg.api, query);
        state.filteredRows = payload.data;
        renderRows(payload.data);
        dom.resultCount.textContent = `${payload.total} resultado(s).`;
    } catch (error) {
        showError(dom.searchError, error.message);
    }
}

function resetDetails() {
    clearError(dom.detailError);
    dom.detailEmpty.style.display = '';
    dom.detailContent.style.display = 'none';
    dom.detailSummary.innerHTML = '';
    dom.endpointList.innerHTML = '';
}

function renderSummary(cable) {
    const summaryItems = [
        ['cable_id', cable.cable_id],
        ['nome', cable.cable_name],
        ['owners', cable.owners],
        ['fornecedores', cable.suppliers],
        ['comprimento bruto', cable.raw_length || cable.length_km],
        ['ano RFS', cable.raw_rfs || cable.rfs_year],
        ['operacional', cable.is_operational === null ? null : (cable.is_operational ? 'Sim' : 'Nao')],
        ['planejado', cable.is_planned === null ? null : (cable.is_planned ? 'Sim' : 'Nao')],
        ['endpoints', cable.landing_points_count],
        ['url publica', cable.public_url],
        ['fonte', cable.source_url],
        ['notas', cable.notes],
    ];
    dom.detailSummary.innerHTML = summaryItems.map(([label, value]) => `
        <div class="summary-card">
            <div class="k">${esc(label)}</div>
            <div class="v">${esc(value)}</div>
        </div>
    `).join('');
}

function renderEndpoints(endpoints) {
    dom.endpointList.innerHTML = endpoints.map((endpoint) => {
        const samples = endpoint.ip_samples || [];
        const sampleRows = samples.length ? samples.map((sample) => `
            <tr>
                <td>${esc(sample.sample_order)}</td>
                <td class="mono">${esc(sample.network || `${sample.ip_start} - ${sample.ip_end}`)}</td>
                <td>${esc(sample.city)}</td>
                <td>${esc(sample.region)}</td>
                <td>${esc(sample.country_name)} ${sample.country_code ? `(${esc(sample.country_code)})` : ''}</td>
                <td>${esc(sample.latitude)}, ${esc(sample.longitude)}</td>
            </tr>
        `).join('') : '<tr><td colspan="6" class="muted">Sem amostras de IP publicas para este endpoint.</td></tr>';

        return `
            <div class="endpoint-card">
                <div class="endpoint-head">
                    <div>
                        <h4 class="endpoint-title">${esc(endpoint.endpoint_order)}. ${esc(endpoint.endpoint_name)}</h4>
                        <div class="muted mono">${esc(endpoint.endpoint_id)}</div>
                    </div>
                    <div>${endpoint.is_tbd ? '<span class="badge no">TBD</span>' : '<span class="badge yes">ativo no catalogo</span>'}</div>
                </div>

                <div class="endpoint-meta">
                    <div><div class="k">cidade</div><div>${esc(endpoint.city_name)}</div></div>
                    <div><div class="k">pais</div><div>${esc(endpoint.country_name)} ${endpoint.country_code ? `(${esc(endpoint.country_code)})` : ''}</div></div>
                    <div><div class="k">latitude</div><div>${esc(endpoint.latitude)}</div></div>
                    <div><div class="k">longitude</div><div>${esc(endpoint.longitude)}</div></div>
                    <div><div class="k">fonte geo</div><div>${esc(endpoint.geo_source)}</div></div>
                    <div><div class="k">amostras de IP</div><div>${esc(samples.length)}</div></div>
                </div>

                <div class="subhead">IPs publicos associados por cidade/pais</div>
                <div class="table-wrap">
                    <table class="ip-table">
                        <thead>
                            <tr>
                                <th>#</th>
                                <th>network / faixa</th>
                                <th>cidade</th>
                                <th>regiao</th>
                                <th>pais</th>
                                <th>coordenadas</th>
                            </tr>
                        </thead>
                        <tbody>${sampleRows}</tbody>
                    </table>
                </div>
            </div>
        `;
    }).join('');
}

async function loadDetails(cableId) {
    if (!cableId) {
        showError(dom.detailError, 'Selecione um cabo valido.');
        return;
    }
    clearError(dom.detailError);
    try {
        const payload = await api('details', cableId);
        state.selectedCable = cableId;
        dom.detailPicker.value = cableId;
        renderSummary(payload.cable);
        renderEndpoints(payload.endpoints);
        dom.detailEmpty.style.display = 'none';
        dom.detailContent.style.display = '';
        switchTab('detail');
    } catch (error) {
        showError(dom.detailError, error.message);
    }
}

function populateDatalist() {
    dom.detailDatalist.innerHTML = state.allCables.map((row) => (
        `<option value="${esc(row.cable_id)}">${esc(row.cable_name)}</option>`
    )).join('');
}

function setFunctionResult(html) {
    dom.functionResult.innerHTML = html;
}

function populateCountrySelects(countries) {
    const options = countries.map((country) => `<option value="${esc(country)}">${esc(country)}</option>`).join('');
    [dom.routeCountryA, dom.routeCountryB, dom.cutCountryA, dom.cutCountryB, dom.riskCountry].forEach((select) => {
        select.innerHTML = options;
    });
    if (countries.length > 1) {
        dom.routeCountryA.value = countries[0];
        dom.cutCountryA.value = countries[0];
        dom.routeCountryB.value = countries[1];
        dom.cutCountryB.value = countries[1];
        dom.riskCountry.value = countries[0];
    }
}

function renderTopRoutes(payload) {
    if (!payload.routes.length) {
        setFunctionResult('<div class="muted">Nenhuma rota encontrada para o par de paises selecionado.</div>');
        return;
    }

    const html = payload.routes.map((route, idx) => {
        const cables = route.cables.map((cable) => `${esc(cable.cable_name)} <span class="muted">(${esc(cable.from)} → ${esc(cable.to)})</span>`).join('<br>');
        return `
            <div class="route-card">
                <strong>#${idx + 1}</strong> score ${esc(route.score)} · hops ${esc(route.hops)} · ${esc(route.distance_km)} km<br>
                <div class="muted">${esc(route.countries.join(' → '))}</div>
                <div style="margin-top:8px">${cables}</div>
            </div>
        `;
    }).join('');

    setFunctionResult(`<h3>Top 3 rotas (${esc(payload.from)} → ${esc(payload.to)})</h3>${html}`);
}

function renderCriticalCuts(payload) {
    if (!payload.critical_cables.length) {
        setFunctionResult('<div class="muted">Nao foram encontrados cabos que isolem completamente o par de paises dentro das rotas analisadas.</div>');
        return;
    }

    const rows = payload.critical_cables.map((cable) => `
        <tr>
            <td class="mono">${esc(cable.cable_id)}</td>
            <td>${esc(cable.cable_name)}</td>
            <td>${esc(cable.routes_hit)}</td>
            <td>${esc(cable.impact)}</td>
        </tr>
    `).join('');

    setFunctionResult(`
        <h3>Cabos cujo corte pode isolar ${esc(payload.from)} ↔ ${esc(payload.to)}</h3>
        <div class="table-wrap">
            <table>
                <thead><tr><th>cable_id</th><th>cabo</th><th>rotas impactadas</th><th>impacto</th></tr></thead>
                <tbody>${rows}</tbody>
            </table>
        </div>
    `);
}

function renderEndpointRisk(payload) {
    if (!payload.endpoints.length) {
        setFunctionResult('<div class="muted">Nao ha endpoints suficientes para calcular risco no pais selecionado.</div>');
        return;
    }

    const rows = payload.endpoints.map((endpoint) => `
        <tr>
            <td class="mono">${esc(endpoint.endpoint_id)}</td>
            <td>${esc(endpoint.endpoint_name)}</td>
            <td>${esc(endpoint.city_name)}</td>
            <td>${esc(endpoint.cable_degree)}</td>
            <td>${esc(endpoint.country_reach)}</td>
            <td>${esc(endpoint.risk_score)}</td>
            <td>${esc(endpoint.packet_loss_risk_pct)}%</td>
            <td>${esc(endpoint.congestion_level)}</td>
        </tr>
    `).join('');

    setFunctionResult(`
        <h3>Endpoints com risco em ${esc(payload.country)}</h3>
        <div class="table-wrap">
            <table>
                <thead><tr><th>endpoint_id</th><th>endpoint</th><th>cidade</th><th>conexoes</th><th>alcance</th><th>risco</th><th>perda pacotes</th><th>nivel</th></tr></thead>
                <tbody>${rows}</tbody>
            </table>
        </div>
    `);
}

function animateGauge(gaugeId, valueNodeId, target, maxValue, unit) {
    const circle = document.getElementById(gaugeId);
    const valueNode = document.getElementById(valueNodeId);
    const radius = 48;
    const circumference = 2 * Math.PI * radius;
    const clampedTarget = Math.min(target, maxValue);
    const decimalPlaces = unit === 'pps' || unit === 'maq' ? 0 : 1;
    const start = performance.now();

    function tick(now) {
        const progress = Math.min(1, (now - start) / 1300);
        const current = clampedTarget * progress;
        const ratio = current / maxValue;
        const offset = circumference * (1 - ratio);
        circle.style.strokeDasharray = `${circumference}`;
        circle.style.strokeDashoffset = `${offset}`;
        valueNode.textContent = `${current.toFixed(decimalPlaces)} ${unit}`;
        if (progress < 1) {
            requestAnimationFrame(tick);
        }
    }

    requestAnimationFrame(tick);
}

function renderTelemetry(payload) {
    const cable = payload.details.cable;
    const telemetry = payload.telemetry;
    setFunctionResult(`
        <h3>Telemetria simulada · ${esc(cable.cable_name)} (${esc(cable.cable_id)})</h3>
        <div class="telemetry-grid">
            <div class="gauge">
                <svg viewBox="0 0 120 120">
                    <circle cx="60" cy="60" r="48" fill="none" stroke="#30363d" stroke-width="10"></circle>
                    <circle id="gauge-latency" cx="60" cy="60" r="48" fill="none" stroke="#58a6ff" stroke-width="10" transform="rotate(-90 60 60)"></circle>
                </svg>
                <div class="value" id="value-latency">0 ms</div>
                <div class="label">latencia estimada</div>
            </div>
            <div class="gauge">
                <svg viewBox="0 0 120 120">
                    <circle cx="60" cy="60" r="48" fill="none" stroke="#30363d" stroke-width="10"></circle>
                    <circle id="gauge-pps" cx="60" cy="60" r="48" fill="none" stroke="#3fb950" stroke-width="10" transform="rotate(-90 60 60)"></circle>
                </svg>
                <div class="value" id="value-pps">0 pps</div>
                <div class="label">pacotes por segundo</div>
            </div>
            <div class="gauge">
                <svg viewBox="0 0 120 120">
                    <circle cx="60" cy="60" r="48" fill="none" stroke="#30363d" stroke-width="10"></circle>
                    <circle id="gauge-machines" cx="60" cy="60" r="48" fill="none" stroke="#f2cc60" stroke-width="10" transform="rotate(-90 60 60)"></circle>
                </svg>
                <div class="value" id="value-machines">0 maq</div>
                <div class="label">maquinas conectadas</div>
            </div>
        </div>
        <div style="margin-top:10px" class="muted">
            trafego estimado: ${esc(telemetry.traffic_mbps)} Mbps · endpoints: ${esc(cable.landing_points_count)}
        </div>
    `);

    animateGauge('gauge-latency', 'value-latency', Number(telemetry.latency_ms), 250, 'ms');
    animateGauge('gauge-pps', 'value-pps', Number(telemetry.packets_per_second), 3000000, 'pps');
    animateGauge('gauge-machines', 'value-machines', Number(telemetry.connected_machines_estimate), 1200000, 'maq');
}

async function runFunction(handler) {
    clearError(dom.functionError);
    try {
        await handler();
        switchTab('functions');
    } catch (error) {
        showError(dom.functionError, error.message);
    }
}

dom.modeList.addEventListener('click', (event) => {
    const button = event.target.closest('.mode-btn');
    if (!button) {
        return;
    }
    switchMode(button.dataset.mode);
});

dom.tabs.forEach((button) => {
    button.addEventListener('click', () => switchTab(button.dataset.tab));
});

dom.searchBtn.addEventListener('click', performSearch);
dom.clearBtn.addEventListener('click', () => {
    dom.queryInput.value = '';
    dom.boolInput.value = 'true';
    resetSearchSurface();
});
dom.queryInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
        performSearch();
    }
});
dom.detailLoad.addEventListener('click', () => loadDetails(dom.detailPicker.value.trim()));
dom.detailPicker.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
        loadDetails(dom.detailPicker.value.trim());
    }
});
dom.searchBody.addEventListener('click', (event) => {
    const button = event.target.closest('[data-open-detail]');
    if (!button) {
        return;
    }
    loadDetails(button.dataset.openDetail);
});

dom.binaryPlay.addEventListener('click', toggleBinaryPlayback);
dom.binaryPrev.addEventListener('click', () => {
    if (state.binaryIndex > 0) {
        if (state.binaryTimer) {
            toggleBinaryPlayback();
        }
        applyBinaryStep(state.binaryIndex - 1);
    }
});
dom.binaryNext.addEventListener('click', () => {
    if (state.binaryIndex < state.binarySteps.length - 1) {
        if (state.binaryTimer) {
            toggleBinaryPlayback();
        }
        applyBinaryStep(state.binaryIndex + 1);
    }
});
dom.binaryReset.addEventListener('click', () => {
    if (state.binarySteps.length) {
        if (state.binaryTimer) {
            toggleBinaryPlayback();
        }
        applyBinaryStep(0);
    }
});

dom.btnTopRoutes.addEventListener('click', () => runFunction(async () => {
    const payload = await apiWithParams('top_routes', {
        countryA: dom.routeCountryA.value,
        countryB: dom.routeCountryB.value,
    });
    renderTopRoutes(payload);
}));

dom.btnCriticalCuts.addEventListener('click', () => runFunction(async () => {
    const payload = await apiWithParams('critical_cables', {
        countryA: dom.cutCountryA.value,
        countryB: dom.cutCountryB.value,
    });
    renderCriticalCuts(payload);
}));

dom.btnEndpointRisk.addEventListener('click', () => runFunction(async () => {
    const payload = await api('endpoint_risk', dom.riskCountry.value);
    renderEndpointRisk(payload);
}));

dom.btnTelemetry.addEventListener('click', () => runFunction(async () => {
    const cableId = dom.telemetryCable.value.trim();
    if (!cableId) {
        throw new Error('Selecione um cabo para a telemetria.');
    }
    const payload = await api('telemetry', cableId);
    renderTelemetry(payload);
}));

(async function init() {
    try {
        const [payload, countriesPayload] = await Promise.all([
            api('all'),
            api('countries'),
        ]);
        state.allCables = payload.data;
        state.countries = countriesPayload.data;
        populateDatalist();
        populateCountrySelects(state.countries);
        dom.resultCount.textContent = `${payload.total} cabos carregados e prontos para busca.`;
    } catch (error) {
        showError(dom.searchError, `Falha ao carregar o catalogo: ${error.message}`);
    }
})();
</script>
</body>
</html>