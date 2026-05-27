<?php
/**
 * SaaS schema migration — runs once on startup.
 * Skipped if the `users` table already exists.
 */
$db_url = getenv('DATABASE_URL');
if (!$db_url) {
    echo "[migrate] DATABASE_URL not set — skipping SaaS migration.\n";
    exit(0);
}

// Convert postgres://user:pass@host:port/db  →  DSN for PDO
$parts = parse_url($db_url);
$dsn = sprintf(
    'pgsql:host=%s;port=%d;dbname=%s',
    $parts['host'],
    $parts['port'] ?? 5432,
    ltrim($parts['path'], '/')
);
$user = $parts['user'] ?? '';
$pass = $parts['pass'] ?? '';

try {
    $pdo = new PDO($dsn, $user, $pass, [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);
} catch (Exception $e) {
    echo "[migrate] DB connection failed: " . $e->getMessage() . "\n";
    exit(0); // non-fatal — web server still starts
}

// Check whether users table exists already
$exists = $pdo->query("SELECT to_regclass('public.users')")->fetchColumn();
if ($exists) {
    echo "[migrate] SaaS schema already applied — skipping.\n";
    exit(0);
}

echo "[migrate] Applying SaaS schema...\n";
$sql = file_get_contents(__DIR__ . '/saas_schema.sql');
try {
    $pdo->exec($sql);
    echo "[migrate] SaaS schema applied successfully.\n";
} catch (Exception $e) {
    echo "[migrate] Schema error: " . $e->getMessage() . "\n";
    // Non-fatal — let the server start even if SaaS schema failed
}
