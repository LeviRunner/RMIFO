<?php
/**
 * RMIFO SaaS — API Gateway
 * Suporta dois modos:
 *  1) Dashboard (POST com {"action":"..."} + JWT Bearer)
 *  2) REST externa (rotas /v1/... + X-API-Key)
 */
declare(strict_types=1);

ini_set('display_errors', '0');
error_reporting(E_ALL);

header('Content-Type: application/json');
header('X-Content-Type-Options: nosniff');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
header('Access-Control-Allow-Headers: Authorization, Content-Type, X-API-Key');

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') { http_response_code(204); exit; }

// ── Helpers ───────────────────────────────────────────────────────────────

function env(string $k, string $d = ''): string {
    return $_ENV[$k] ?? getenv($k) ?: $d;
}

function respond(int $code, array $data): never {
    http_response_code($code);
    echo json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

function err(int $code, string $msg, array $extra = []): never {
    respond($code, array_merge(['error' => $msg, 'code' => $code], $extra));
}

function db(): PDO {
    static $pdo = null;
    if ($pdo === null) {
        $u = parse_url(env('DATABASE_URL'));
        if (!$u) err(500, 'DATABASE_URL não configurada.');
        $dsn = sprintf('pgsql:host=%s;port=%d;dbname=%s;sslmode=require',
            $u['host'], $u['port'] ?? 5432, ltrim($u['path'] ?? '/', '/'));
        $pdo = new PDO($dsn, $u['user'] ?? '', $u['pass'] ?? '', [
            PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]);
    }
    return $pdo;
}

function body(): array {
    static $p = null;
    if ($p === null) $p = json_decode(file_get_contents('php://input') ?: '{}', true) ?? [];
    return $p;
}

// ── JWT ──────────────────────────────────────────────────────────────────

function b64u(string $s): string {
    return rtrim(strtr(base64_encode($s), '+/', '-_'), '=');
}
function b64uDec(string $s): string {
    return base64_decode(strtr($s, '-_', '+/') . str_repeat('=', (4 - strlen($s) % 4) % 4));
}

function jwtMake(int $uid): string {
    $h = b64u(json_encode(['typ'=>'JWT','alg'=>'HS256']));
    $p = b64u(json_encode(['sub'=>$uid,'exp'=>time()+86400*30,'iat'=>time()]));
    $s = b64u(hash_hmac('sha256', "$h.$p", env('JWT_SECRET','rmifo-dev-secret'), true));
    return "$h.$p.$s";
}

function jwtVerify(string $tok): ?int {
    $parts = explode('.', $tok);
    if (count($parts) !== 3) return null;
    [$h, $p, $s] = $parts;
    $exp = b64u(hash_hmac('sha256', "$h.$p", env('JWT_SECRET','rmifo-dev-secret'), true));
    if (!hash_equals($exp, $s)) return null;
    $payload = json_decode(b64uDec($p), true);
    if (!$payload || ($payload['exp'] ?? 0) < time()) return null;
    return (int)($payload['sub'] ?? 0);
}

// ── Auth dashboard (JWT Bearer) ───────────────────────────────────────────

function authJwt(): array {
    $auth = $_SERVER['HTTP_AUTHORIZATION'] ?? '';
    $tok  = str_starts_with($auth, 'Bearer ') ? substr($auth, 7) : '';
    if (!$tok) err(401, 'Token ausente. Faça login primeiro.');
    $uid = jwtVerify($tok);
    if (!$uid) err(401, 'Token inválido ou expirado.');
    $stmt = db()->prepare('SELECT id,email,name,credits,suspended FROM users WHERE id=?');
    $stmt->execute([$uid]);
    $u = $stmt->fetch();
    if (!$u || $u['suspended']) err(401, 'Conta suspensa ou inexistente.');
    return $u;
}

// ── Auth REST (X-API-Key) ─────────────────────────────────────────────────

function authApiKey(): array {
    $raw = $_SERVER['HTTP_X_API_KEY'] ?? '';
    if (!$raw) {
        $a = $_SERVER['HTTP_AUTHORIZATION'] ?? '';
        if (str_starts_with($a, 'Bearer ')) $raw = substr($a, 7);
    }
    // Se parece com JWT (tem pontos), rejeita aqui — é token de dashboard
    if (substr_count($raw, '.') === 2) err(401, 'Use X-API-Key para acesso à API REST.');
    if (!$raw) err(401, 'API key ausente. Use o header X-API-Key.');

    $hash = hash('sha256', $raw);
    $stmt = db()->prepare('
        SELECT ak.id AS key_id, ak.user_id, u.credits, u.suspended, u.email
        FROM api_keys ak
        JOIN users u ON u.id = ak.user_id
        WHERE ak.key_hash=? AND ak.active=TRUE AND u.suspended=FALSE
    ');
    $stmt->execute([$hash]);
    $row = $stmt->fetch();
    if (!$row) err(401, 'Chave API inválida ou desativada.');
    db()->prepare('UPDATE api_keys SET last_used=NOW() WHERE id=?')->execute([$row['key_id']]);
    return $row;
}

// ── Débito de créditos ────────────────────────────────────────────────────

function spend(int $uid, ?int $key_id, string $service, int $cost): void {
    // debit_credits(user_id, key_id, service, credits, ip, duration) RETURNS BOOLEAN
    $stmt = db()->prepare('SELECT debit_credits(?,?,?,?,NULL,0)');
    $stmt->execute([$uid, $key_id, $service, $cost]);
    $ok = (bool)$stmt->fetchColumn();
    if (!$ok) {
        $bal = (int)db()->prepare('SELECT credits FROM users WHERE id=?')
                        ->execute([$uid]) && false ?: 0;
        err(402, 'Créditos insuficientes.', ['service' => $service, 'cost' => $cost]);
    }
}

// ─────────────────────────────────────────────────────────────────────────
// ROTEADOR PRINCIPAL
// ─────────────────────────────────────────────────────────────────────────

$path   = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH);
$method = $_SERVER['REQUEST_METHOD'];

// Webhook Stripe: sem auth
if ($path === '/api_gateway.php' && isset($_GET['stripe_webhook'])) {
    handleStripeWebhook(); exit;
}
if ($path === '/v1/billing/webhook' && $method === 'POST') {
    handleStripeWebhook(); exit;
}

// Dashboard: identifica pelo campo action no body
$b = body();
if (isset($b['action'])) {
    dispatchDashboard($b['action']); exit;
}

// REST API via /v1/... com X-API-Key
$ctx = authApiKey();
match(true) {
    $path === '/v1/geo/lookup'       && $method === 'POST' => handleGeoLookup($ctx),
    $path === '/v1/geo/bulk'         && $method === 'POST' => handleGeoBulk($ctx),
    $path === '/v1/security/check'   && $method === 'POST' => handleSecurityCheck($ctx),
    $path === '/v1/asn/info'         && $method === 'POST' => handleAsnInfo($ctx),
    $path === '/v1/bgp/prefix'       && $method === 'POST' => handleBgpPrefix($ctx),
    $path === '/v1/probe/tcp'        && $method === 'POST' => handleProbeTcp($ctx),
    $path === '/v1/probe/mtr'        && $method === 'POST' => handleProbeMtr($ctx),
    $path === '/v1/tunnel/create'    && $method === 'POST' => handleTunnelCreateRest($ctx),
    $path === '/v1/tunnel/delete'    && $method === 'POST' => handleTunnelDeleteRest($ctx),
    str_starts_with($path,'/v1/tunnel/config/') && $method==='GET' => handleTunnelConfig($ctx, basename($path)),
    $path === '/v1/download/request' && $method === 'POST' => handleDownloadRequest($ctx),
    $path === '/v1/account/usage'    && $method === 'GET'  => handleAccountUsageRest($ctx),
    $path === '/v1/billing/checkout' && $method === 'POST' => handleBillingCheckoutRest($ctx),
    default => err(404, 'Endpoint não encontrado.', ['path' => $path]),
};


// ═════════════════════════════════════════════════════════════════════════
// DASHBOARD — ação via {"action":"..."} + JWT
// ═════════════════════════════════════════════════════════════════════════

function dispatchDashboard(string $action): never {
    // Endpoints públicos
    if ($action === 'auth_register') { handleAuthRegister(); }
    if ($action === 'auth_login')    { handleAuthLogin(); }

    // Restante precisa de JWT
    $user = authJwt();
    match($action) {
        'account_usage'   => handleAccountUsage($user),
        'key_list'        => handleKeyList($user),
        'key_create'      => handleKeyCreate($user),
        'key_delete'      => handleKeyDelete($user),
        'tunnel_list'     => handleTunnelList($user),
        'tunnel_create'   => handleTunnelCreate($user),
        'tunnel_delete'   => handleTunnelDelete($user),
        'db_list'         => handleDbList($user),
        'db_download'     => handleDbDownload($user),
        'usage_log'       => handleUsageLog($user),
        'billing_checkout'=> handleBillingCheckout($user),
        default           => err(404, "Ação desconhecida: $action"),
    };
}

// ── auth_register ─────────────────────────────────────────────────────────
function handleAuthRegister(): never {
    $b    = body();
    $email = trim($b['email'] ?? '');
    $pass  = $b['password'] ?? '';
    $name  = trim($b['name'] ?? '');

    if (!filter_var($email, FILTER_VALIDATE_EMAIL)) err(400, 'E-mail inválido.');
    if (strlen($pass) < 6) err(400, 'Senha deve ter pelo menos 6 caracteres.');

    $stmt = db()->prepare('SELECT id FROM users WHERE email=?');
    $stmt->execute([$email]);
    if ($stmt->fetch()) err(409, 'E-mail já cadastrado.');

    $hash = password_hash($pass, PASSWORD_BCRYPT);
    $ins  = db()->prepare('INSERT INTO users (email,password_hash,name,credits) VALUES (?,?,?,100) RETURNING id,email,name,credits');
    $ins->execute([$email, $hash, $name]);
    $user = $ins->fetch();

    respond(201, ['token' => jwtMake($user['id']), 'user' => $user]);
}

// ── auth_login ────────────────────────────────────────────────────────────
function handleAuthLogin(): never {
    $b     = body();
    $email = trim($b['email'] ?? '');
    $pass  = $b['password'] ?? '';

    $stmt = db()->prepare('SELECT id,email,name,credits,password_hash,suspended FROM users WHERE email=?');
    $stmt->execute([$email]);
    $user = $stmt->fetch();

    if (!$user || !password_verify($pass, $user['password_hash'])) err(401, 'E-mail ou senha incorretos.');
    if ($user['suspended']) err(403, 'Conta suspensa.');

    unset($user['password_hash'], $user['suspended']);
    respond(200, ['token' => jwtMake($user['id']), 'user' => $user]);
}

// ── account_usage ─────────────────────────────────────────────────────────
function handleAccountUsage(array $u): never {
    $uid = $u['id'];

    // Refresh credits
    $row = db()->prepare('SELECT credits FROM users WHERE id=?');
    $row->execute([$uid]);
    $credits = (int)$row->fetchColumn();

    // Stats hoje
    $today = db()->prepare("
        SELECT COUNT(*) AS req, COALESCE(SUM(credits),0) AS spent
        FROM usage_log WHERE user_id=? AND created_at >= CURRENT_DATE
    ");
    $today->execute([$uid]);
    $td = $today->fetch();

    // Stats total
    $total = db()->prepare('SELECT COUNT(*) FROM usage_log WHERE user_id=?');
    $total->execute([$uid]);
    $req_total = (int)$total->fetchColumn();

    // Túneis ativos
    $tun = db()->prepare("SELECT COUNT(*) FROM wg_tunnels WHERE user_id=? AND status='active'");
    $tun->execute([$uid]);
    $active_tunnels = (int)$tun->fetchColumn();

    // Chaves ativas
    $keys = db()->prepare('SELECT COUNT(*) FROM api_keys WHERE user_id=? AND active=TRUE');
    $keys->execute([$uid]);
    $api_keys = (int)$keys->fetchColumn();

    // Histórico 7 dias (créditos gastos por dia)
    $hist = db()->prepare("
        SELECT TO_CHAR(created_at,'YYYY-MM-DD') AS date,
               COALESCE(SUM(credits),0) AS credits
        FROM usage_log WHERE user_id=? AND created_at >= NOW()-INTERVAL '7 days'
        GROUP BY 1 ORDER BY 1
    ");
    $hist->execute([$uid]);
    $credits_history = $hist->fetchAll();

    // Breakdown por serviço
    $bk = db()->prepare("
        SELECT service, COUNT(*) AS count
        FROM usage_log WHERE user_id=? AND created_at >= NOW()-INTERVAL '30 days'
        GROUP BY service ORDER BY count DESC LIMIT 8
    ");
    $bk->execute([$uid]);
    $service_breakdown = $bk->fetchAll();

    // Compras recentes
    $pur = db()->prepare("
        SELECT cp.id, cp.credits, cp.amount_cents, cp.status, cp.created_at,
               pk.name AS pack_name
        FROM credit_purchases cp
        LEFT JOIN credit_packs pk ON pk.id = cp.pack_id
        WHERE cp.user_id=? ORDER BY cp.created_at DESC LIMIT 10
    ");
    $pur->execute([$uid]);
    $purchases = $pur->fetchAll();

    // Downloads recentes
    $dl = db()->prepare('SELECT db_name,version,url,created_at,downloaded FROM db_downloads WHERE user_id=? ORDER BY created_at DESC LIMIT 10');
    $dl->execute([$uid]);
    $downloads = $dl->fetchAll();

    // Pacotes de crédito disponíveis
    $packs = db()->query('SELECT id,name,credits,price_cents FROM credit_packs WHERE active=TRUE ORDER BY price_cents');
    $credit_packs = $packs->fetchAll();

    respond(200, [
        'email'              => $u['email'],
        'name'               => $u['name'],
        'credits'            => $credits,
        'requests_today'     => (int)$td['req'],
        'credits_spent_today'=> (int)$td['spent'],
        'requests_total'     => $req_total,
        'active_tunnels'     => $active_tunnels,
        'api_keys'           => $api_keys,
        'credits_history'    => $credits_history,
        'service_breakdown'  => $service_breakdown,
        'purchases'          => $purchases,
        'downloads'          => $downloads,
        'credit_packs'       => $credit_packs,
    ]);
}

// ── key_list ──────────────────────────────────────────────────────────────
function handleKeyList(array $u): never {
    $stmt = db()->prepare('SELECT id,label,key_prefix,active,created_at,last_used FROM api_keys WHERE user_id=? ORDER BY created_at DESC');
    $stmt->execute([$u['id']]);
    respond(200, ['keys' => $stmt->fetchAll()]);
}

// ── key_create ────────────────────────────────────────────────────────────
function handleKeyCreate(array $u): never {
    $label = trim(body()['label'] ?? '');

    $raw    = 'rmifo_' . bin2hex(random_bytes(24));
    $prefix = substr($raw, 0, 14);
    $hash   = hash('sha256', $raw);

    $stmt = db()->prepare('INSERT INTO api_keys (user_id,key_hash,key_prefix,label) VALUES (?,?,?,?)');
    $stmt->execute([$u['id'], $hash, $prefix, $label ?: null]);

    respond(201, ['api_key' => $raw, 'prefix' => $prefix, 'note' => 'Salve esta chave — não será mostrada novamente.']);
}

// ── key_delete ────────────────────────────────────────────────────────────
function handleKeyDelete(array $u): never {
    $id = (int)(body()['key_id'] ?? 0);
    if (!$id) err(400, 'key_id obrigatório.');
    $stmt = db()->prepare('UPDATE api_keys SET active=FALSE WHERE id=? AND user_id=?');
    $stmt->execute([$id, $u['id']]);
    respond(200, ['message' => 'Chave revogada.']);
}

// ── tunnel_list ───────────────────────────────────────────────────────────
function handleTunnelList(array $u): never {
    $stmt = db()->prepare("
        SELECT tunnel_id,label,client_ip,status,last_handshake,bytes_rx,bytes_tx,created_at
        FROM wg_tunnels WHERE user_id=? AND status!='deleted' ORDER BY created_at DESC
    ");
    $stmt->execute([$u['id']]);
    respond(200, ['tunnels' => $stmt->fetchAll()]);
}

// ── tunnel_create ─────────────────────────────────────────────────────────
function handleTunnelCreate(array $u): never {
    $label = trim(body()['label'] ?? '');
    $uid   = $u['id'];

    // Verificar créditos (50)
    if ($u['credits'] < 50) err(402, 'Créditos insuficientes. Necessário: 50.', ['credits' => $u['credits']]);

    // Gerar keypair WireGuard
    $privkey = ''; $pubkey = ''; $psk = '';
    exec('wg genkey 2>/dev/null', $pk, $rc);
    if ($rc === 0 && !empty($pk[0])) {
        $privkey = trim($pk[0]);
        $pb = []; exec('echo ' . escapeshellarg($privkey) . ' | wg pubkey 2>/dev/null', $pb);
        $pubkey = trim($pb[0] ?? '');
        $ps = []; exec('wg genpsk 2>/dev/null', $ps);
        $psk = trim($ps[0] ?? '');
    }
    if (!$pubkey) err(503, 'Geração de chave WireGuard falhou. Verifique o serviço.');

    // Alocar IP cliente
    $cnt_stmt = db()->prepare("SELECT COUNT(*) FROM wg_tunnels WHERE status!='deleted'");
    $cnt_stmt->execute();
    $cnt = (int)$cnt_stmt->fetchColumn();
    $client_ip = '10.88.' . (int)floor($cnt / 253) . '.' . ($cnt % 253 + 2);

    // Chave pública do servidor
    $srv_pub = trim((string)shell_exec('wg show wg0 public-key 2>/dev/null') ?: env('WG_SERVER_PUBKEY', ''));

    // Inserir no banco
    $ins = db()->prepare('
        INSERT INTO wg_tunnels (user_id,client_pubkey,client_ip,server_pubkey,preshared_key,label)
        VALUES (?,?,?,?,?,?) RETURNING tunnel_id,client_ip
    ');
    $ins->execute([$uid, $pubkey, $client_ip, $srv_pub, $psk, $label ?: null]);
    $row = $ins->fetch();

    // Debitar créditos
    spend($uid, null, 'tunnel_create', 50);

    respond(201, [
        'tunnel_id'      => $row['tunnel_id'],
        'client_ip'      => $row['client_ip'],
        'client_pubkey'  => $pubkey,
        'client_privkey' => $privkey,
        'preshared_key'  => $psk,
        'server_pubkey'  => $srv_pub,
        'server_host'    => env('WG_SERVER_HOST', ''),
        'server_port'    => env('WG_SERVER_PORT', '51820'),
    ]);
}

// ── tunnel_delete ─────────────────────────────────────────────────────────
function handleTunnelDelete(array $u): never {
    $tid = trim(body()['tunnel_id'] ?? '');
    if (!$tid) err(400, 'tunnel_id obrigatório.');
    $stmt = db()->prepare("UPDATE wg_tunnels SET status='deleted',updated_at=NOW() WHERE tunnel_id=? AND user_id=?");
    $stmt->execute([$tid, $u['id']]);
    respond(200, ['message' => 'Túnel marcado para remoção.']);
}

// ── db_list ───────────────────────────────────────────────────────────────
function handleDbList(array $u): never {
    $catalog = [
        ['db_name'=>'geo_standard',   'name'=>'IP Geolocation Standard',          'version'=>date('Y-m'), 'size_human'=>'~180 MB', 'credits_cost'=>50],
        ['db_name'=>'geo_advanced',   'name'=>'IP Geolocation Advanced',           'version'=>date('Y-m'), 'size_human'=>'~340 MB', 'credits_cost'=>100],
        ['db_name'=>'security_pro',   'name'=>'IP Security / Threat Intelligence', 'version'=>date('Y-m'), 'size_human'=>'~220 MB', 'credits_cost'=>100],
        ['db_name'=>'asn_db',         'name'=>'IP-to-ASN Database',               'version'=>date('Y-m'), 'size_human'=>'~95 MB',  'credits_cost'=>75],
        ['db_name'=>'rmifo_cables',   'name'=>'RMIFO Cabos Submarinos DB',         'version'=>date('Y-m'), 'size_human'=>'~12 MB',  'credits_cost'=>50],
        ['db_name'=>'country_ranges', 'name'=>'Country IP Ranges',                 'version'=>date('Y-m'), 'size_human'=>'~55 MB',  'credits_cost'=>30],
    ];
    respond(200, ['databases' => $catalog]);
}

// ── db_download ───────────────────────────────────────────────────────────
function handleDbDownload(array $u): never {
    $db_name = trim(body()['db_name'] ?? '');
    $version = trim(body()['version'] ?? date('Y-m'));
    $cost    = 500;

    if ($u['credits'] < $cost) err(402, 'Créditos insuficientes. Necessário: ' . $cost);

    $cdn  = rtrim(env('CDN_BASE', 'https://cdn.rmifo.io/databases'), '/');
    $tok  = hash_hmac('sha256', "$db_name-$version-" . $u['id'], env('JWT_SECRET','dev'));
    $url  = "$cdn/{$db_name}_{$version}.mmdb?token=$tok&exp=" . (time() + 86400);
    $exp  = date('Y-m-d H:i:s', time() + 86400);

    db()->prepare('INSERT INTO db_downloads (user_id,db_name,version,url,expires_at) VALUES (?,?,?,?,?)')
        ->execute([$u['id'], $db_name, $version, $url, $exp]);

    spend($u['id'], null, 'db_download', $cost);
    respond(200, ['url' => $url, 'expires_at' => $exp]);
}

// ── usage_log ─────────────────────────────────────────────────────────────
function handleUsageLog(array $u): never {
    $limit  = min((int)(body()['limit']  ?? 50), 200);
    $offset = (int)(body()['offset'] ?? 0);
    $stmt = db()->prepare('SELECT service,ip,credits,status,created_at FROM usage_log WHERE user_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?');
    $stmt->execute([$u['id'], $limit, $offset]);
    respond(200, ['logs' => $stmt->fetchAll()]);
}

// ── billing_checkout ──────────────────────────────────────────────────────
function handleBillingCheckout(array $u): never {
    $pack_id = (int)(body()['pack_id'] ?? 0);
    if (!$pack_id) err(400, 'pack_id obrigatório.');

    $stmt = db()->prepare('SELECT * FROM credit_packs WHERE id=? AND active=TRUE');
    $stmt->execute([$pack_id]);
    $pack = $stmt->fetch();
    if (!$pack) err(404, 'Pacote não encontrado.');

    $stripe_key = env('STRIPE_SECRET_KEY');
    if (!$stripe_key) err(503, 'Pagamentos não configurados. Contate o suporte.');

    $app = rtrim(env('APP_BASE_URL', 'https://global-network-resilience.up.railway.app'), '/');

    $post = http_build_query([
        'mode'                    => 'payment',
        'payment_method_types[]'  => 'card',
        'line_items[0][price]'    => $pack['stripe_price_id'] ?? '',
        'line_items[0][quantity]' => '1',
        'success_url'             => "$app/dashboard.html?paid=1",
        'cancel_url'              => "$app/dashboard.html",
        'metadata[user_id]'       => (string)$u['id'],
        'metadata[pack_id]'       => (string)$pack_id,
        'metadata[credits]'       => (string)$pack['credits'],
    ]);

    $ch = curl_init('https://api.stripe.com/v1/checkout/sessions');
    curl_setopt_array($ch, [
        CURLOPT_POST           => true,
        CURLOPT_POSTFIELDS     => $post,
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_HTTPHEADER     => ["Authorization: Bearer $stripe_key"],
    ]);
    $resp = json_decode((string)curl_exec($ch), true);
    curl_close($ch);

    if (empty($resp['url'])) err(503, 'Erro ao criar sessão de pagamento Stripe.', ['stripe' => $resp['error']['message'] ?? '']);

    db()->prepare("INSERT INTO credit_purchases (user_id,pack_id,credits,amount_cents,stripe_session_id,status) VALUES (?,?,?,?,?,'pending')")
        ->execute([$u['id'], $pack_id, $pack['credits'], $pack['price_cents'], $resp['id']]);

    respond(200, ['url' => $resp['url'], 'session_id' => $resp['id']]);
}

// ── Stripe Webhook ────────────────────────────────────────────────────────
function handleStripeWebhook(): never {
    $payload = (string)file_get_contents('php://input');
    $sig     = $_SERVER['HTTP_STRIPE_SIGNATURE'] ?? '';
    $sec     = env('STRIPE_WEBHOOK_SEC');

    if ($sec) {
        $parts = [];
        foreach (explode(',', $sig) as $part) {
            [$k,$v] = array_pad(explode('=', $part, 2), 2, '');
            $parts[$k] = $v;
        }
        $expected = hash_hmac('sha256', ($parts['t'] ?? '0') . '.' . $payload, $sec);
        if (!hash_equals($expected, $parts['v1'] ?? '')) {
            http_response_code(400); echo '{"error":"invalid signature"}'; exit;
        }
    }

    $event = json_decode($payload, true);
    $type  = $event['type'] ?? '';

    // Idempotência
    $ev_id = $event['id'] ?? '';
    if ($ev_id) {
        $chk = db()->prepare('SELECT 1 FROM webhook_events WHERE stripe_event_id=?');
        $chk->execute([$ev_id]);
        if ($chk->fetchColumn()) { respond(200, ['status' => 'already_processed']); }
        db()->prepare('INSERT INTO webhook_events (stripe_event_id,event_type) VALUES (?,?)')->execute([$ev_id, $type]);
    }

    if ($type === 'checkout.session.completed') {
        $session = $event['data']['object'];
        $meta    = $session['metadata'] ?? [];

        $upd = db()->prepare("UPDATE credit_purchases SET status='completed',completed_at=NOW() WHERE stripe_session_id=? AND status='pending'");
        $upd->execute([$session['id']]);

        if ($upd->rowCount() > 0 && isset($meta['user_id'], $meta['credits'])) {
            db()->prepare('UPDATE users SET credits=credits+?,updated_at=NOW() WHERE id=?')
                ->execute([(int)$meta['credits'], (int)$meta['user_id']]);
        }
    }

    respond(200, ['received' => true]);
}


// ═════════════════════════════════════════════════════════════════════════
// REST API HANDLERS (X-API-Key)
// ═════════════════════════════════════════════════════════════════════════

function handleGeoLookup(array $ctx): never {
    $ip = body()['ip'] ?? $_GET['ip'] ?? '';
    if (!filter_var($ip, FILTER_VALIDATE_IP)) err(400, 'IP inválido.');
    spend($ctx['user_id'], $ctx['key_id'], 'geo_lookup', 1);

    $key  = env('IPGEO_API_KEY');
    $data = json_decode(@file_get_contents("https://api.ipgeolocation.io/ipgeo?apiKey=$key&ip=$ip") ?: '{}', true) ?? [];

    respond(200, [
        'ip'           => $ip,
        'country'      => $data['country_name']      ?? null,
        'country_code' => $data['country_code2']     ?? null,
        'region'       => $data['state_prov']        ?? null,
        'city'         => $data['city']              ?? null,
        'latitude'     => (float)($data['latitude']  ?? 0),
        'longitude'    => (float)($data['longitude'] ?? 0),
        'isp'          => $data['isp']               ?? null,
        'credits_used' => 1,
    ]);
}

function handleGeoBulk(array $ctx): never {
    $ips = body()['ips'] ?? [];
    if (!is_array($ips) || count($ips) === 0 || count($ips) > 10) err(400, 'Envie entre 1 e 10 IPs.');
    spend($ctx['user_id'], $ctx['key_id'], 'geo_bulk', count($ips));

    $key     = env('IPGEO_API_KEY');
    $results = [];
    foreach ($ips as $ip) {
        if (!filter_var($ip, FILTER_VALIDATE_IP)) { $results[$ip] = ['error'=>'inválido']; continue; }
        $d = json_decode(@file_get_contents("https://api.ipgeolocation.io/ipgeo?apiKey=$key&ip=$ip") ?: '{}', true) ?? [];
        $results[$ip] = ['country'=>$d['country_code2']??null,'city'=>$d['city']??null,'isp'=>$d['isp']??null,'lat'=>(float)($d['latitude']??0),'lon'=>(float)($d['longitude']??0)];
    }
    respond(200, ['results' => $results, 'credits_used' => count($ips)]);
}

function handleSecurityCheck(array $ctx): never {
    $ip = body()['ip'] ?? '';
    if (!filter_var($ip, FILTER_VALIDATE_IP)) err(400, 'IP inválido.');
    spend($ctx['user_id'], $ctx['key_id'], 'security', 2);

    $key  = env('IPGEO_API_KEY');
    $data = json_decode(@file_get_contents("https://api.ipgeolocation.io/ip-security?apiKey=$key&ip=$ip") ?: '{}', true) ?? [];
    respond(200, ['ip'=>$ip,'is_vpn'=>$data['vpn']??false,'is_proxy'=>$data['proxy']??false,'is_tor'=>$data['tor']??false,'threat_score'=>$data['threat_score']??0,'credits_used'=>2]);
}

function handleAsnInfo(array $ctx): never {
    $asn = body()['asn'] ?? body()['ip'] ?? '';
    spend($ctx['user_id'], $ctx['key_id'], 'asn', 1);
    $key  = env('IPGEO_API_KEY');
    $data = json_decode(@file_get_contents("https://api.ipgeolocation.io/asn?apiKey=$key&asn=$asn") ?: '{}', true) ?? [];
    respond(200, array_merge($data, ['credits_used'=>1]));
}

function handleBgpPrefix(array $ctx): never {
    $prefix = body()['prefix'] ?? '';
    if (empty($prefix)) err(400, 'Campo prefix obrigatório.');
    spend($ctx['user_id'], $ctx['key_id'], 'bgp', 3);

    $vis  = json_decode(@file_get_contents('https://stat.ripe.net/data/visibility/data.json?resource='.urlencode($prefix)) ?: '{}', true) ?? [];
    $rpki = json_decode(@file_get_contents('https://stat.ripe.net/data/rpki-validation/data.json?resource='.urlencode($prefix)) ?: '{}', true) ?? [];
    $v4   = $vis['data']['visibilities'][0]['ipv4'] ?? [];
    $peers= $v4['ris_peers_seeing'] ?? 0; $total = $v4['total_ris_peers'] ?? 1;
    respond(200, ['prefix'=>$prefix,'peers_seeing'=>$peers,'visibility_pct'=>$total>0?round($peers/$total*100,2):0,'roa_status'=>$rpki['data']['status']??'unknown','credits_used'=>3]);
}

function handleProbeTcp(array $ctx): never {
    $host  = body()['host']  ?? '';
    $ports = body()['ports'] ?? [80,443];
    if (empty($host)) err(400, 'Campo host obrigatório.');
    if (!is_array($ports)) $ports = [$ports];
    spend($ctx['user_id'], $ctx['key_id'], 'tcp_probe', 5);

    $res = [];
    foreach (array_slice($ports, 0, 5) as $port) {
        $t = microtime(true);
        $s = @fsockopen($host, (int)$port, $en, $es, 3);
        $ms= round((microtime(true)-$t)*1000, 2);
        $res[$port] = $s ? ['open'=>true,'latency_ms'=>$ms] : ['open'=>false,'error'=>$es];
        if ($s) fclose($s);
    }
    respond(200, ['host'=>$host,'ports'=>$res,'credits_used'=>5]);
}

function handleProbeMtr(array $ctx): never {
    $dst = body()['dst'] ?? '';
    if (!filter_var($dst, FILTER_VALIDATE_IP) && !preg_match('/^[a-z0-9.\-]+$/i', $dst)) err(400, 'Destino inválido.');
    spend($ctx['user_id'], $ctx['key_id'], 'mtr_probe', 20);

    exec('mtr --report --report-cycles=5 --no-dns --json '.escapeshellarg($dst).' 2>/dev/null', $out);
    $mtr  = json_decode(implode('', $out), true);
    $hops = [];
    foreach ($mtr['report']['hubs'] ?? [] as $h) {
        $hops[] = ['hop'=>$h['count']??null,'ip'=>$h['host']??'???','loss'=>$h['Loss%']??0,'rtt_ms'=>$h['Avg']??null];
    }
    respond(200, ['dst'=>$dst,'hops'=>$hops,'credits_used'=>20]);
}

function handleTunnelCreateRest(array $ctx): never {
    // Redireciona para o handler de dashboard (mesma lógica)
    $fake_user = ['id'=>$ctx['user_id'],'credits'=>$ctx['credits'],'suspended'=>false,'email'=>$ctx['email'],'name'=>''];
    handleTunnelCreate($fake_user);
}

function handleTunnelDeleteRest(array $ctx): never {
    $fake_user = ['id'=>$ctx['user_id']];
    handleTunnelDelete($fake_user);
}

function handleTunnelConfig(array $ctx, string $tunnel_id): never {
    $stmt = db()->prepare("SELECT * FROM wg_tunnels WHERE tunnel_id=? AND user_id=? AND status='active'");
    $stmt->execute([$tunnel_id, $ctx['user_id']]);
    $t = $stmt->fetch();
    if (!$t) err(404, 'Túnel não encontrado.');

    $cfg = "[Interface]\n# Adicione sua chave privada aqui\nAddress = {$t['client_ip']}/32\nDNS = 1.1.1.1\n\n[Peer]\nPublicKey = {$t['server_pubkey']}\n" .
           ($t['preshared_key'] ? "PresharedKey = {$t['preshared_key']}\n" : '') .
           "Endpoint = " . env('WG_SERVER_HOST') . ':' . env('WG_SERVER_PORT','51820') . "\nAllowedIPs = 0.0.0.0/0\nPersistentKeepalive = 25\n";

    header('Content-Type: text/plain');
    header('Content-Disposition: attachment; filename="rmifo-' . preg_replace('/[^a-z0-9\-]/i','', $t['label'] ?? $tunnel_id) . '.conf"');
    echo $cfg; exit;
}

function handleDownloadRequest(array $ctx): never {
    $fake = ['id'=>$ctx['user_id'],'credits'=>$ctx['credits']];
    handleDbDownload($fake);
}

function handleAccountUsageRest(array $ctx): never {
    $fake = ['id'=>$ctx['user_id'],'email'=>$ctx['email'],'name'=>'','credits'=>$ctx['credits']];
    handleAccountUsage($fake);
}

function handleBillingCheckoutRest(array $ctx): never {
    $fake = ['id'=>$ctx['user_id'],'credits'=>$ctx['credits']];
    handleBillingCheckout($fake);
}
