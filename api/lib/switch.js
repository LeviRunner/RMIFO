'use strict';
/** lib/switch.js — descobre switches num CIDR via MTR. */
const mtr = require('./mtr');
const tcp = require('./tcp');
const { ipToAsn } = require('./bgp');

function cidrSample(cidr, step = 8) {
  const [base, prefix] = cidr.split('/');
  const pl = parseInt(prefix,10);
  if (isNaN(pl) || pl > 30) return [base];
  const parts = base.split('.').map(Number);
  const baseN = (parts[0]<<24)|(parts[1]<<16)|(parts[2]<<8)|parts[3];
  const mask = ~((1<<(32-pl))-1)>>>0;
  const net = (baseN & mask)>>>0;
  const total = (1<<(32-pl))-2;
  if (total <= 0) return [base];
  const toIp = n => [(n>>>24)&255,(n>>>16)&255,(n>>>8)&255,n&255].join('.');
  const ips = [toIp(net+1)];
  for (let o=step; o<total-1; o+=step) ips.push(toIp(net+o+1));
  ips.push(toIp(net+total));
  return [...new Set(ips)].slice(0,20);
}
async function scanCidr(cidr, opts = {}) {
  const { step=8, cycles=5, enrichAsn=false } = opts;
  const targets = cidrSample(cidr, step);
  const allHops = new Map();
  for (const t of targets) {
    const r = await mtr.trace(t, { cycles, maxHops:15 });
    for (const h of r.hops) {
      if (!h.ip) continue;
      if (!allHops.has(h.ip) || h.avg_ms < (allHops.get(h.ip).avg_ms??Infinity))
        allHops.set(h.ip, {...h, seen:[]});
      allHops.get(h.ip).seen.push(t);
    }
  }
  let switches = [...allHops.values()].map(h=>({ip:h.ip,hop_number:h.hop,avg_ms:h.avg_ms,
    loss_pct:h.loss_pct,seen_count:h.seen.length,asn:null})).sort((a,b)=>(a.avg_ms??999)-(b.avg_ms??999));
  if (enrichAsn) await Promise.all(switches.slice(0,20).map(async sw=>{sw.asn=await ipToAsn(sw.ip);}));
  return { cidr, targets_probed:targets.length, hops_found:switches.length, switches };
}
async function probeMgmt(ip) {
  const open = await tcp.scanPorts(ip, [22,161,443,830,8080], 1000);
  return { ip, mgmt_ports:open, is_accessible:open.length>0 };
}
module.exports = { scanCidr, probeMgmt, cidrSample };
