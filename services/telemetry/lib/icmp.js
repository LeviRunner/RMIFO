'use strict';
/** lib/icmp.js — ICMP ping via subprocess. Auto-contido. */
const { execFile } = require('child_process');
function ping(host, opts = {}) {
  const { count = 4, timeoutS = 3 } = opts;
  return new Promise(resolve => {
    execFile('ping', ['-c',String(count),'-W',String(timeoutS),'-q',host],
      { timeout:(timeoutS*count+5)*1000 }, (err, stdout) => {
      const res = { host, reachable:false, avg_ms:null, loss_pct:100, method:'icmp' };
      if (!stdout) return resolve(res);
      const lm = stdout.match(/(\d+(?:\.\d+)?)%\s+packet loss/);
      if (lm) { res.loss_pct = parseFloat(lm[1]); res.reachable = res.loss_pct < 100; }
      const rm = stdout.match(/(?:rtt|round-trip)\s+\S+\s*=\s*([\d.]+)\/([\d.]+)/);
      if (rm) { res.avg_ms = parseFloat(rm[2]); res.reachable = true; }
      resolve(res);
    });
  });
}
module.exports = { ping };
