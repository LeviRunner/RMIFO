'use strict';
/** lib/mtr.js — MTR/traceroute. Requer binário mtr (nixpacks). Auto-contido. */
const { execFile } = require('child_process');
const { promisify } = require('util');
const exec = promisify(execFile);
const CY = parseInt(process.env.MTR_CYCLES || '10', 10);
const TO = parseInt(process.env.MTR_TIMEOUT_S || '60', 10);
async function trace(host, opts = {}) {
  const { cycles=CY, maxHops=15, timeoutS=TO } = opts;
  const hops = [];
  try {
    const { stdout } = await exec('mtr',['--report','--report-cycles',String(cycles),
      '--max-ttl',String(maxHops),'--json',host],{timeout:timeoutS*1000});
    const data = JSON.parse(stdout);
    for (const h of (data?.report?.hubs||[])) {
      const ip = (h.host && h.host!=='???' && h.host!=='TIMEOUT') ? h.host : null;
      hops.push({hop:h.count,ip,avg_ms:h.Avg!=null?+h.Avg.toFixed(2):null,
        loss_pct:h['Loss%']!=null?+h['Loss%'].toFixed(1):100});
    }
  } catch {
    try {
      const { stdout } = await exec('traceroute',['-n','-m',String(maxHops),'-w','2',host],{timeout:timeoutS*1000});
      let i=0;
      for (const line of stdout.split('\n').slice(1)) {
        const p = line.trim().split(/\s+/); if (p.length<2) continue; i++;
        const ip = p[1]!=='*'?p[1]:null, rtt = p[2]&&p[2]!=='*'?parseFloat(p[2]):null;
        hops.push({hop:i,ip,avg_ms:rtt,loss_pct:ip?0:100});
      }
    } catch {}
  }
  const last = [...hops].reverse().find(h=>h.ip);
  return { host, hops, total_hops:hops.length, visible_hops:hops.filter(h=>h.ip).length,
    final_rtt_ms:last?.avg_ms??null, method:hops.length?'mtr':'unavailable' };
}
module.exports = { trace };
