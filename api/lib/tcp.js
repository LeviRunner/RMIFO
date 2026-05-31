'use strict';
/** lib/tcp.js — TCP connect probe com jitter. Auto-contido (só usa 'net'). */
const net = require('net');
const T = parseInt(process.env.TCP_TIMEOUT_MS || '5000', 10);
const N = parseInt(process.env.TCP_JITTER_PACKETS || '50', 10);

function tcpConnect(host, port, timeoutMs = T) {
  return new Promise(resolve => {
    const t0 = Date.now(), sock = new net.Socket();
    sock.setTimeout(timeoutMs);
    sock.connect(port, host, () => { const rtt = Date.now() - t0; sock.destroy(); resolve(rtt); });
    const done = () => { sock.destroy(); resolve(null); };
    sock.on('timeout', done); sock.on('error', done);
  });
}
async function scanPorts(host, ports = [443,80,22,8080,8443], timeoutMs = 1500) {
  const r = await Promise.all(ports.map(async p => (await tcpConnect(host,p,timeoutMs)) != null ? p : null));
  return r.filter(Boolean).sort((a,b)=>a-b);
}
async function jitterProbe(host, openPorts, n = N, timeoutMs = T) {
  if (!openPorts.length) return null;
  const per = Math.max(1, Math.floor(n / openPorts.length));
  const tasks = openPorts.flatMap(p => Array.from({length:per}, () => tcpConnect(host,p,timeoutMs)));
  const rtts = (await Promise.all(tasks)).filter(v => v != null);
  if (rtts.length < 2) return null;
  const mean = rtts.reduce((a,b)=>a+b,0)/rtts.length;
  const jit = Math.sqrt(rtts.reduce((a,b)=>a+(b-mean)**2,0)/rtts.length);
  const sorted = [...rtts].sort((a,b)=>a-b);
  const p95 = sorted[Math.max(0, Math.floor(rtts.length*0.95)-1)];
  return {
    avg_ms:+mean.toFixed(2), jitter_ms:+jit.toFixed(2), p95_ms:+p95.toFixed(2),
    loss_pct:+((1 - rtts.length/tasks.length)*100).toFixed(1),
    samples:rtts.length, total:tasks.length, ports:openPorts,
  };
}
async function probe(host, opts = {}) {
  const { ports=[443,80,22], packets=N, timeoutMs=T } = opts;
  const open = await scanPorts(host, ports, timeoutMs);
  if (!open.length) return null;
  const j = await jitterProbe(host, open, packets, timeoutMs);
  if (!j) return null;
  return { host, open_ports:open, avg_ms:j.avg_ms, jitter_ms:j.jitter_ms, p95_ms:j.p95_ms,
    loss_pct:j.loss_pct, samples:j.samples, total:j.total,
    method:`tcp-jitter-${j.total}pkt`, reachable:true };
}
module.exports = { tcpConnect, scanPorts, jitterProbe, probe };
