'use strict';
/** lib/dns.js — resolucao DNS com medicao de tempo. Auto-contido (so usa 'dns'). */
const dns = require('dns');
const TO = parseInt(process.env.DNS_TIMEOUT_MS || '5000', 10);
function resolveTimed(host, opts = {}) {
  const server = opts.server || null;
  let resolver;
  try {
    resolver = new dns.Resolver({ timeout: TO, tries: 1 });
  } catch { resolver = new dns.Resolver(); }
  if (server) { try { resolver.setServers([server]); } catch {} }
  const t0 = Date.now();
  return new Promise(resolve => {
    resolver.resolve4(host, (err, addrs) => {
      const ms = Date.now() - t0;
      if (!err && addrs && addrs.length) {
        return resolve({ host, resolved: true, dns_ms: ms, addresses: addrs, family: 4, server: server || 'system' });
      }
      const t1 = Date.now();
      resolver.resolve6(host, (e6, a6) => {
        const ms6 = Date.now() - t1 + ms;
        if (!e6 && a6 && a6.length) {
          return resolve({ host, resolved: true, dns_ms: ms6, addresses: a6, family: 6, server: server || 'system' });
        }
        resolve({ host, resolved: false, dns_ms: null, addresses: [], error: (err && err.code) || (e6 && e6.code) || 'NXDOMAIN', server: server || 'system' });
      });
    });
  });
}
module.exports = { resolve: resolveTimed };
