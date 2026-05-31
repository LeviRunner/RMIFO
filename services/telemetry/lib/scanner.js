'use strict';
/** lib/scanner.js — orquestra probe de um cabo: TCP -> ICMP -> HTTP -> sintético. */
const tcp = require('./tcp');
const icmp = require('./icmp');
const httpProbe = require('./http');
const { synth } = require('./synthetic');
const MODEL = '1.4.0';

async function scanCable(cable, opts = {}) {
  const { ports = opts.fullScan?[443,80,22,8080,8443]:[443,80], packets = opts.fullScan?50:20 } = opts;
  const start = Date.now();
  const ips = (cable.probe_ips||[]).map(ip=>ip.replace(/\/\d+$/,'')).filter(Boolean);
  let hit=null, method=null; const steps=[];
  for (const ip of ips) {
    steps.push('tcp');
    hit = await tcp.probe(ip,{ports,packets});
    if (hit) { method=hit.method; break; }
    steps.push('icmp');
    const p = await icmp.ping(ip,{count:4});
    if (p.reachable) { hit={...p,open_ports:[],samples:4,method:'icmp-echo'}; method='icmp-echo'; break; }
    steps.push('http');
    const h = await httpProbe.head(ip);
    if (h.reachable) { hit={avg_ms:h.avg_ms,jitter_ms:null,loss_pct:0,open_ports:[],samples:1,method:'http-head'}; method='http-head'; break; }
  }
  if (!hit) { const s=synth(cable); hit={avg_ms:s.lat,jitter_ms:s.jitter,loss_pct:s.loss,throughput:s.throughput,open_ports:[],samples:0,method:'synthetic'}; method='synthetic'; }
  const real = method!=='synthetic';
  return { cable_id:cable.cable_id, cable_name:cable.cable_name, measured_at:new Date().toISOString(),
    target_ip:ips[0]||null, method, probe_steps:steps, latency_ms:hit.avg_ms, jitter_ms:hit.jitter_ms??null,
    packet_loss_pct:hit.loss_pct??0, availability_pct:real?+(100-(hit.loss_pct||0)).toFixed(2):null,
    open_ports:hit.open_ports||[], samples:hit.samples||0, confidence:real?0.78:0.55,
    is_estimated:!real, telemetry_model:MODEL, elapsed_ms:Date.now()-start };
}
module.exports = { scanCable };
