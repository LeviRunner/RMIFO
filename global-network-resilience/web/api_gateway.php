<?php
/**
 * RMIFO SaaS — API Gateway
 * ========================
 * Ponto único de entrada para todos os serviços RMIFO.
 * Autentica via chave API, debita créditos, roteia para o serviço.
 *
 * Variáveis de ambiente requeridas:
 *   DATABASE_URL         postgresql://user:pass@host:5432/db
 *   IPGEO_API_KEY        chave ipgeolocation.io
 *   STRIPE_SECRET_KEY    sk_live_...
 *   STRIPE_WEBHOOK_SEC   whsec_...
 *   WG_SERVER_HOST       endpoint público do servidor WireGuard
 *   WG_SERVER_PORT       porta UDP (padrão 51820)
 *   WG_VPN_SUBNET        10.99.0.0/16
 *   JWT_SECRET           segredo para tokens temporários
 *   ENCRYPT_KEY          chave AES-256 para privkeys WireGuard
 *
 * Endpoints:
 *   POST /v1/geo/lookup         IP geolocation
 *   POST /v1/geo/bulk           Batch até 10 IPs
 *   POST /v1/security/check     Threat intelligence
 *   POST /v1/asn/info           ASN lookup
 *   POST /v1/bgp/prefix         BGP visibility
 *   POST /v1/probe/tcp          TCP/ICMP probe
 *   POST /v1/probe/mtr          MTR traceroute
 *   POST /v1/tunnel/create      Criar túnel WireGuard
 *   POST /v1/tunnel/delete      Remover túnel
 *   GET  /v1/tunnel/config/{id} Baixar config WireGuard
 *   POST /v1/download/request   Solicitar download de DB
 *   GET  /v1/account/usage      Uso e créditos
 *   POST /v1/billing/checkout   Criar sessão Stripe
 *   POST /v1/billing/webhook    Webhook Stripe (sem auth)
 */

declare(strict_types=1);

// ── Bootstrap ─────────────────────────────────────────────────────────────
ini_set('display_errors', '0');
error_reporting(E_ALL);

header('Content-Type: application/json');
header('X-Content-Type-Options: nosniff');
header('X-Powered-By: RMIFO-API/1.0');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
header('Access-Control-Allow-Headers: Authorization, Content-Type, X-API-Key');

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') { http_response_code(204); exit; }

// ── Helpers ───────────────────────────────────────────────────────────────
function env(string $key, string $default = ''): string {
    return $_ENV[$key] ?? getenv($key) ?: $default;
}

function respond(int $code, array $data): never {
    http_response_code($code);
    echo json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

function respondError(int $code, string $message, array $extra = []): never {
    respond($code, array_merge(['error' => $message, 'code' => $code], $extra));
}

function db(): PDO {
    static $pdo = null;
    if ($pdo === null) {
        $url    = env('DATABASE_URL');
        $parsed = parse_url($url);
        $dsn    = sprintf(
            'pgsql:host=%s;port=%d;dbname=%s;sslmode=require',
            $parsed['host'], $parsed['port'] ?? 5432, ltrim($parsed['path'] ?? '/db', '/')
        );
        $pdo = new PDO($dsn, $parsed['user'], $parsed['pass'], [
            PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]);
    }
    return $pdo;
}

function body(): array {
    static $parsed = null;
    if ($parsed === null) {
        $raw    = file_get_contents('php://input');
        $parsed = json_decode($raw ?: '{}', true) ?? [];
    }
    return $parsed;
}

// ── Autenticação via X-API-Key ────────────────────────────────────────────
function authenticate(): array {
    $raw_key = $_SERVER['HTTP_X_API_KEY'] ?? '';
    if (!$raw_key) {
        $auth = $_SERVER['HTTP_AUTHORIZATION'] ?? '';
        if (str_starts_with($auth, 'Bearer ')) {
            $raw_key = substr($auth, 7);
        }
    }
    if (!$raw_key) respondError(401, 'API key ausente. Use o header X-API-Key.');

    $key_hash = hash('sha256', $raw_key);
    $stmt = db()->prepare('
        SELECT ak.id AS key_id, ak.user_id, ak.rate_limit_per_min,
               u.credits, u.is_active, u.email
        FROM api_keys ak
        JOIN users u ON u.id = ak.user_id
        WHERE ak.key_hash = ? AND ak.is_active = TRUE AND u.is_active = TRUE
    ');
    $stmt->execute([$key_hash]);
    $row = $stmt->fetch();

    if (!$row) respondError(401, 'Chave API inválida ou desativada.');

    return $row;
}

// ── Débito de créditos ────────────────────────────────────────────────────
function debitCredits(array $ctx, string $service, array $input = []): void {
    $stmt = db()->prepare('
        SELECT success, credits_left, message
        FROM debit_credits(?, ?, ?, ?, NULL, 200)
    ');
    $stmt->execute([
        $ctx['user_id'],
        $service,
        $ctx['key_id'],
        json_encode($input),
    ]);
    $result = $stmt->fetch();
    if (!$result['success']) {
        respondError(402, 'Créditos insuficientes: ' . $result['message'], [
            'credits_remaining' => (int)$result['credits_left'],
            'buy_more'          => env('API_BASE_URL', 'https://api.rmifo.io') . '/v1/billing/checkout',
        ]);
    }
}

// ── Roteador ──────────────────────────────────────────────────────────────
$path   = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH);
$method = $_SERVER['REQUEST_METHOD'];

// Webhook Stripe não requer autenticação
if ($path === '/v1/billing/webhook' && $method === 'POST') {
    handleStripeWebhook();
    exit;
}

$ctx = authenticate();

// Dispatcher
match(true) {
    $path === '/v1/geo/lookup'        && $method === 'POST' => handleGeoLookup($ctx),
    $path === '/v1/geo/bulk'          && $method === 'POST' => handleGeoBulk($ctx),
    $path === '/v1/security/check'    && $method === 'POST' => handleSecurityCheck($ctx),
    $path === '/v1/asn/info'          && $method === 'POST' => handleAsnInfo($ctx),
    $path === '/v1/bgp/prefix'        && $method === 'POST' => handleBgpPrefix($ctx),
    $path === '/v1/probe/tcp'         && $method === 'POST' => handleProbeTcp($ctx),
    $path === '/v1/probe/mtr'         && $method === 'POST' => handleProbeMtr($ctx),
    $path === '/v1/tunnel/create'     && $method === 'POST' => handleTunnelCreate($ctx),
    $path === '/v1/tunnel/delete'     && $method === 'POST' => handleTunnelDelete($ctx),
    str_starts_with($path, '/v1/tunnel/config/') && $method === 'GET' => handleTunnelConfig($ctx, basename($path)),
    $path === '/v1/download/request'  && $method === 'POST' => handleDownloadRequest($ctx),
    $path === '/v1/account/usage'     && $method === 'GET'  => handleAccountUsage($ctx),
    $path === '/v1/billing/checkout'  && $method === 'POST' => handleBillingCheckout($ctx),
    default => respondError(404, 'Endpoint não encontrado.', ['path' => $path]),
};


// ─────────────────────────────────────────────────────────────────────────
// HANDLERS DE SERVIÇO
// ─────────────────────────────────────────────────────────────────────────

// ── GET /v1/geo/lookup ────────────────────────────────────────────────────
function handleGeoLookup(array $ctx): never {
    $ip = body()['ip'] ?? $_GET['ip'] ?? '';
    if (!filter_var($ip, FILTER_VALIDATE_IP)) respondError(400, 'IP inválido.');

    debitCredits($ctx, 'geo_lookup', ['ip' => $ip]);

    $api_key = env('IPGEO_API_KEY');
    $url     = "https://api.ipgeolocation.io/ipgeo?apiKey={$api_key}&ip={$ip}";
    $data    = json_decode(@file_get_contents($url) ?: '{}', true) ?? [];

    respond(200, [
        'ip'           => $ip,
        'country'      => $data['country_name']          ?? null,
        'country_code' => $data['country_code2']         ?? null,
        'region'       => $data['state_prov']            ?? null,
        'city'         => $data['city']                  ?? null,
        'latitude'     => (float)($data['latitude']      ?? 0),
        'longitude'    => (float)($data['longitude']     ?? 0),
        'timezone'     => $data['time_zone']['name']     ?? null,
        'isp'          => $data['isp']                   ?? null,
        'asn'          => $data['connection_type']       ?? null,
        'credits_used' => 1,
    ]);
}

// ── POST /v1/geo/bulk ─────────────────────────────────────────────────────
function handleGeoBulk(array $ctx): never {
    $ips = body()['ips'] ?? [];
    if (!is_array($ips) || count($ips) === 0 || count($ips) > 10) {
        respondError(400, 'Envie entre 1 e 10 IPs.');
    }

    debitCredits($ctx, 'geo_bulk', ['ips' => $ips]);

    $results = [];
    $api_key = env('IPGEO_API_KEY');
    foreach ($ips as $ip) {
        if (!filter_var($ip, FILTER_VALIDATE_IP)) {
            $results[$ip] = ['error' => 'inválido'];
            continue;
        }
        $data = json_decode(@file_get_contents(
            "https://api.ipgeolocation.io/ipgeo?apiKey={$api_key}&ip={$ip}"
        ) ?: '{}', true) ?? [];
        $results[$ip] = [
            'country' => $data['country_code2'] ?? null,
            'city'    => $data['city']           ?? null,
            'isp'     => $data['isp']            ?? null,
            'lat'     => (float)($data['latitude']  ?? 0),
            'lon'     => (float)($data['longitude'] ?? 0),
        ];
    }
    respond(200, ['results' => $results, 'credits_used' => 8]);
}

// ── POST /v1/security/check ───────────────────────────────────────────────
function handleSecurityCheck(array $ctx): never {
    $ip = body()['ip'] ?? '';
    if (!filter_var($ip, FILTER_VALIDATE_IP)) respondError(400, 'IP inválido.');

    debitCredits($ctx, 'security_lookup', ['ip' => $ip]);

    $api_key = env('IPGEO_API_KEY');
    $data    = json_decode(@file_get_contents(
        "https://api.ipgeolocation.io/ip-security?apiKey={$api_key}&ip={$ip}"
    ) ?: '{}', true) ?? [];

    respond(200, [
        'ip'           => $ip,
        'is_vpn'       => $data['vpn']          ?? false,
        'is_proxy'     => $data['proxy']         ?? false,
        'is_tor'       => $data['tor']           ?? false,
        'is_bot'       => $data['bot']           ?? false,
        'threat_score' => $data['threat_score']  ?? 0,
        'is_spam'      => $data['spam']          ?? false,
        'credits_used' => 2,
    ]);
}

// ── POST /v1/asn/info ─────────────────────────────────────────────────────
function handleAsnInfo(array $ctx): never {
    $asn = body()['asn'] ?? body()['ip'] ?? '';
    debitCredits($ctx, 'asn_lookup', ['asn' => $asn]);

    $api_key = env('IPGEO_API_KEY');
    $data    = json_decode(@file_get_contents(
        "https://api.ipgeolocation.io/asn?apiKey={$api_key}&asn={$asn}"
    ) ?: '{}', true) ?? [];

    respond(200, array_merge($data, ['credits_used' => 2]));
}

// ── POST /v1/bgp/prefix ───────────────────────────────────────────────────
function handleBgpPrefix(array $ctx): never {
    $prefix = body()['prefix'] ?? '';
    if (empty($prefix)) respondError(400, 'Campo prefix obrigatório.');

    debitCredits($ctx, 'bgp_prefix', ['prefix' => $prefix]);

    $vis  = json_decode(@file_get_contents(
        "https://stat.ripe.net/data/visibility/data.json?resource=" . urlencode($prefix)
    ) ?: '{}', true) ?? [];
    $rpki = json_decode(@file_get_contents(
        "https://stat.ripe.net/data/rpki-validation/data.json?resource=" . urlencode($prefix)
    ) ?: '{}', true) ?? [];

    $v4    = $vis['data']['visibilities'][0]['ipv4'] ?? [];
    $peers = $v4['ris_peers_seeing'] ?? 0;
    $total = $v4['total_ris_peers']  ?? 1;

    respond(200, [
        'prefix'         => $prefix,
        'peers_seeing'   => $peers,
        'visibility_pct' => $total > 0 ? round($peers / $total * 100, 2) : 0,
        'roa_status'     => $rpki['data']['status'] ?? 'unknown',
        'hijack_risk'    => in_array($rpki['data']['status'] ?? '', ['invalid', 'not-found']),
        'credits_used'   => 3,
    ]);
}

// ── POST /v1/probe/tcp ────────────────────────────────────────────────────
function handleProbeTcp(array $ctx): never {
    $host  = body()['host'] ?? '';
    $ports = body()['ports'] ?? [80, 443];
    if (empty($host)) respondError(400, 'Campo host obrigatório.');
    if (!is_array($ports)) $ports = [$ports];

    debitCredits($ctx, 'tcp_probe', ['host' => $host, 'ports' => $ports]);

    $results = [];
    foreach (array_slice($ports, 0, 5) as $port) {
        $start = microtime(true);
        $sock  = @fsockopen($host, (int)$port, $errno, $errstr, 3);
        $ms    = round((microtime(true) - $start) * 1000, 2);
        if ($sock) {
            fclose($sock);
            $results[$port] = ['open' => true, 'latency_ms' => $ms];
        } else {
            $results[$port] = ['open' => false, 'error' => $errstr];
        }
    }

    respond(200, ['host' => $host, 'ports' => $results, 'credits_used' => 3]);
}

// ── POST /v1/probe/mtr ────────────────────────────────────────────────────
function handleProbeMtr(array $ctx): never {
    $dst = body()['dst'] ?? '';
    if (!filter_var($dst, FILTER_VALIDATE_IP) && !filter_var($dst, FILTER_VALIDATE_DOMAIN)) {
        respondError(400, 'Destino inválido.');
    }
    debitCredits($ctx, 'mtr_trace', ['dst' => $dst]);

    $output = [];
    exec('mtr --report --report-cycles=5 --no-dns --json ' . escapeshellarg($dst) . ' 2>/dev/null', $out_lines, $code);
    $json_out = implode('', $out_lines);
    $mtr_data = json_decode($json_out, true);
    $hops = [];
    if (isset($mtr_data['report']['hubs'])) {
        foreach ($mtr_data['report']['hubs'] as $hub) {
            $hops[] = [
                'hop'    => $hub['count']             ?? null,
                'ip'     => $hub['host']              ?? '???',
                'loss'   => $hub['Loss%']             ?? 0,
                'rtt_ms' => $hub['Avg']               ?? null,
            ];
        }
    }

    respond(200, ['dst' => $dst, 'hops' => $hops, 'credits_used' => 5]);
}


// ─────────────────────────────────────────────────────────────────────────
// WIREGUARD TUNNEL (auto-provisionamento)
// ─────────────────────────────────────────────────────────────────────────

function handleTunnelCreate(array $ctx): never {
    $name    = body()['name'] ?? 'tunnel-' . substr(uniqid(), -6);
    $country = body()['exit_country'] ?? 'US';

    debitCredits($ctx, 'tunnel_create', ['name' => $name, 'exit_country' => $country]);

    // Gera par de chaves WireGuard
    exec('wg genkey 2>/dev/null', $priv_out, $rc);
    $privkey = trim($priv_out[0] ?? '');
    if (empty($privkey) || $rc !== 0) respondError(503, 'Servidor WireGuard indisponível.');

    $pub_out = [];
    exec('echo ' . escapeshellarg($privkey) . ' | wg pubkey 2>/dev/null', $pub_out);
    $pubkey = trim($pub_out[0] ?? '');

    // Aloca IP na VPN (subnet 10.99.0.0/16)
    $stmt = db()->prepare('SELECT COUNT(*) FROM wg_tunnels WHERE status != \'deleted\'');
    $stmt->execute();
    $cnt    = (int)$stmt->fetchColumn();
    $vpn_ip = '10.99.' . floor($cnt / 254) . '.' . ($cnt % 254 + 2) . '/32';

    // Criptografa a chave privada antes de salvar
    $enc_key  = base64_decode(env('ENCRYPT_KEY'));
    $iv       = random_bytes(16);
    $enc_priv = base64_encode($iv . openssl_encrypt($privkey, 'AES-256-CBC', $enc_key, OPENSSL_RAW_DATA, $iv));

    $server_pub  = trim((string)shell_exec('cat /etc/wireguard/server_public.key 2>/dev/null') ?: '');
    $server_host = env('WG_SERVER_HOST', 'vpn.rmifo.io');
    $server_port = env('WG_SERVER_PORT', '51820');

    $stmt = db()->prepare('
        INSERT INTO wg_tunnels
            (user_id, tunnel_name, server_pubkey, client_pubkey, client_privkey_enc,
             client_vpn_ip, server_vpn_ip, server_endpoint, exit_country)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
    ');
    $stmt->execute([
        $ctx['user_id'], $name, $server_pub, $pubkey, $enc_priv,
        $vpn_ip, '10.99.0.1', "{$server_host}:{$server_port}", $country,
    ]);
    $tunnel_id = $stmt->fetchColumn();

    // Adiciona peer no daemon WireGuard em background
    shell_exec('wg set wg0 peer ' . escapeshellarg($pubkey) . ' allowed-ips ' . escapeshellarg($vpn_ip) . ' &');

    $api_base = env('API_BASE_URL', 'https://api.rmifo.io');
    respond(201, [
        'tunnel_id'     => $tunnel_id,
        'name'          => $name,
        'client_ip'     => $vpn_ip,
        'server_pubkey' => $server_pub,
        'config_url'    => "{$api_base}/v1/tunnel/config/{$tunnel_id}",
        'exit_country'  => $country,
        'credits_used'  => 10,
        'note'          => 'Baixe o arquivo de configuração WireGuard no config_url.',
    ]);
}

function handleTunnelConfig(array $ctx, string $tunnel_id): never {
    $stmt = db()->prepare('
        SELECT * FROM wg_tunnels WHERE id = ? AND user_id = ? AND status = \'active\'
    ');
    $stmt->execute([$tunnel_id, $ctx['user_id']]);
    $t = $stmt->fetch();
    if (!$t) respondError(404, 'Túnel não encontrado.');

    // Descriptografa a chave privada
    $enc_key = base64_decode(env('ENCRYPT_KEY'));
    $raw     = base64_decode($t['client_privkey_enc']);
    $iv      = substr($raw, 0, 16);
    $enc     = substr($raw, 16);
    $privkey = openssl_decrypt($enc, 'AES-256-CBC', $enc_key, OPENSSL_RAW_DATA, $iv);

    $dns      = $t['dns_servers']  ?? '1.1.1.1,8.8.8.8';
    $allowed  = $t['allowed_ips']  ?? '0.0.0.0/0';
    $keepalive= $t['keepalive']    ?? 25;

    $config = "[Interface]\nPrivateKey = {$privkey}\nAddress = {$t['client_vpn_ip']}\nDNS = {$dns}\n\n[Peer]\nPublicKey = {$t['server_pubkey']}\nEndpoint = {$t['server_endpoint']}\nAllowedIPs = {$allowed}\nPersistentKeepalive = {$keepalive}\n";

    header('Content-Type: text/plain; charset=utf-8');
    header('Content-Disposition: attachment; filename="rmifo-' . preg_replace('/[^a-z0-9\-_]/i', '', $t['tunnel_name']) . '.conf"');
    echo $config;
    exit;
}

function handleTunnelDelete(array $ctx): never {
    $id   = body()['tunnel_id'] ?? '';
    $stmt = db()->prepare('
        UPDATE wg_tunnels SET status = \'deleted\', deleted_at = NOW()
        WHERE id = ? AND user_id = ? AND status = \'active\'
        RETURNING client_pubkey
    ');
    $stmt->execute([$id, $ctx['user_id']]);
    $row = $stmt->fetch();

    if ($row && $row['client_pubkey']) {
        shell_exec('wg set wg0 peer ' . escapeshellarg($row['client_pubkey']) . ' remove &');
    }

    respond(200, ['message' => 'Túnel removido com sucesso.']);
}


// ─────────────────────────────────────────────────────────────────────────
// DOWNLOADS DE BANCO DE DADOS
// ─────────────────────────────────────────────────────────────────────────

const DB_PRODUCTS = [
    'geo_standard'  => ['credits' => 50,  'label' => 'IP Geolocation Standard'],
    'geo_advanced'  => ['credits' => 100, 'label' => 'IP Geolocation Advanced'],
    'security_pro'  => ['credits' => 100, 'label' => 'IP Security / Threat Intelligence'],
    'asn_db'        => ['credits' => 75,  'label' => 'IP-to-ASN Database'],
    'abuse_db'      => ['credits' => 60,  'label' => 'IP Abuse Contact Database'],
    'whois_db'      => ['credits' => 80,  'label' => 'IP WHOIS Database'],
    'rmifo_cables'  => ['credits' => 50,  'label' => 'RMIFO Submarine Cables DB'],
    'country_ranges'=> ['credits' => 30,  'label' => 'Country IP Ranges (128 países)'],
];

function handleDownloadRequest(array $ctx): never {
    $product = body()['product'] ?? '';
    $format  = body()['format']  ?? 'mmdb';

    if (!isset(DB_PRODUCTS[$product])) {
        respondError(400, 'Produto inválido.', ['available' => array_keys(DB_PRODUCTS)]);
    }

    $credits = DB_PRODUCTS[$product]['credits'];
    debitCredits($ctx, 'db_download_std', ['product' => $product, 'format' => $format]);

    $version  = date('Y-m-d');
    $filename = "{$product}_{$version}.{$format}";
    $cdn_base = env('CDN_BASE', 'https://cdn.rmifo.io/databases');
    $token    = hash_hmac('sha256', $filename . time(), env('JWT_SECRET'));
    $dl_url   = "{$cdn_base}/{$filename}?token={$token}&expires=" . (time() + 86400);

    $stmt = db()->prepare("
        INSERT INTO db_downloads
            (user_id, product, format, version, download_url, url_expires_at, credits_used)
        VALUES (?, ?, ?, ?, ?, NOW() + INTERVAL '24 hours', ?)
        RETURNING id
    ");
    $stmt->execute([$ctx['user_id'], $product, $format, $version, $dl_url, $credits]);
    $dl_id = $stmt->fetchColumn();

    respond(200, [
        'download_id'    => $dl_id,
        'product'        => $product,
        'label'          => DB_PRODUCTS[$product]['label'],
        'format'         => $format,
        'version'        => $version,
        'download_url'   => $dl_url,
        'expires_in_sec' => 86400,
        'credits_used'   => $credits,
    ]);
}


// ─────────────────────────────────────────────────────────────────────────
// CONTA E BILLING
// ─────────────────────────────────────────────────────────────────────────

function handleAccountUsage(array $ctx): never {
    $stmt = db()->prepare('
        SELECT credits, credits_used, total_spent_usd, created_at FROM users WHERE id = ?
    ');
    $stmt->execute([$ctx['user_id']]);
    $user = $stmt->fetch();

    $stmt2 = db()->prepare("
        SELECT service, COUNT(*) AS calls, SUM(credits_used) AS credits
        FROM usage_log
        WHERE user_id = ? AND created_at > NOW() - INTERVAL '30 days'
        GROUP BY service ORDER BY credits DESC
    ");
    $stmt2->execute([$ctx['user_id']]);
    $breakdown = $stmt2->fetchAll();

    $api_base = env('API_BASE_URL', 'https://api.rmifo.io');
    respond(200, [
        'credits_remaining'  => (int)$user['credits'],
        'credits_used_total' => (int)$user['credits_used'],
        'total_spent_usd'    => (float)$user['total_spent_usd'],
        'member_since'       => $user['created_at'],
        'usage_30d'          => $breakdown,
        'buy_credits_url'    => "{$api_base}/v1/billing/checkout",
    ]);
}

function handleBillingCheckout(array $ctx): never {
    $pack_id = (int)(body()['pack_id'] ?? 1);
    $stmt    = db()->prepare('SELECT * FROM credit_packs WHERE id = ? AND is_active = TRUE');
    $stmt->execute([$pack_id]);
    $pack = $stmt->fetch();
    if (!$pack) respondError(404, 'Pacote não encontrado.');

    $stripe_key = env('STRIPE_SECRET_KEY');
    $api_base   = env('API_BASE_URL', 'https://api.rmifo.io');
    $app_base   = env('APP_BASE_URL', 'https://app.rmifo.io');

    $payload = json_encode([
        'mode'                 => 'payment',
        'payment_method_types' => ['card'],
        'line_items'           => [[
            'price'    => $pack['stripe_price_id'],
            'quantity' => 1,
        ]],
        'success_url' => "{$app_base}/dashboard?session_id={CHECKOUT_SESSION_ID}",
        'cancel_url'  => "{$app_base}/pricing",
        'metadata'    => [
            'user_id' => $ctx['user_id'],
            'pack_id' => $pack_id,
            'credits' => $pack['credits'],
        ],
    ]);

    $ch = curl_init('https://api.stripe.com/v1/checkout/sessions');
    curl_setopt_array($ch, [
        CURLOPT_POST           => true,
        CURLOPT_POSTFIELDS     => $payload,
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_HTTPHEADER     => [
            'Content-Type: application/json',
            "Authorization: Bearer {$stripe_key}",
        ],
    ]);
    $resp = json_decode((string)curl_exec($ch), true);
    curl_close($ch);

    if (!isset($resp['url'])) respondError(503, 'Erro ao criar sessão de pagamento.');

    $stmt2 = db()->prepare("
        INSERT INTO credit_purchases (user_id, pack_id, credits_added, amount_usd, stripe_session_id, status)
        VALUES (?, ?, ?, ?, ?, 'pending')
    ");
    $stmt2->execute([$ctx['user_id'], $pack_id, $pack['credits'], $pack['price_usd'], $resp['id']]);

    respond(200, [
        'checkout_url' => $resp['url'],
        'session_id'   => $resp['id'],
        'credits'      => (int)$pack['credits'],
        'amount_usd'   => (float)$pack['price_usd'],
    ]);
}

// ── Stripe Webhook (sem autenticação via chave API) ───────────────────────
function handleStripeWebhook(): never {
    $payload = (string)file_get_contents('php://input');
    $sig     = $_SERVER['HTTP_STRIPE_SIGNATURE'] ?? '';
    $sec     = env('STRIPE_WEBHOOK_SEC');

    $parts = [];
    foreach (explode(',', $sig) as $part) {
        [$k, $v] = explode('=', $part, 2);
        $parts[$k] = $v;
    }
    $ts       = $parts['t']  ?? 0;
    $expected = hash_hmac('sha256', "{$ts}.{$payload}", $sec);
    if (!hash_equals($expected, $parts['v1'] ?? '')) {
        http_response_code(400);
        echo '{"error":"invalid signature"}';
        exit;
    }

    $event = json_decode($payload, true);
    if (($event['type'] ?? '') === 'checkout.session.completed') {
        $session    = $event['data']['object'];
        $session_id = $session['id'];
        $metadata   = $session['metadata'];

        $stmt = db()->prepare("
            UPDATE credit_purchases SET status = 'paid', paid_at = NOW()
            WHERE stripe_session_id = ? AND status = 'pending'
        ");
        $stmt->execute([$session_id]);

        if ($stmt->rowCount() > 0) {
            db()->prepare("
                UPDATE users SET
                    credits         = credits + ?,
                    total_spent_usd = total_spent_usd + (
                        SELECT amount_usd FROM credit_purchases WHERE stripe_session_id = ?
                    )
                WHERE id = ?
            ")->execute([(int)$metadata['credits'], $session_id, $metadata['user_id']]);
        }
    }

    http_response_code(200);
    echo '{"received":true}';
    exit;
}
