'use strict';
/** lib/synthetic.js — modelo sintético de latência. Puro. */
const r = (n,d=3)=>+n.toFixed(d);
function synth(cable = {}) {
  const len = Math.max(100, cable.length_km||1200);
  const eps = Math.max(2, cable.landing_points||2);
  const cap = cable.capacity_gbps || Math.max(2, 12000/len);
  const lat = Math.max(8, (len/200)*1.35);
  const tput = cap*1000*(0.52+Math.min(0.35, eps/120));
  const jit = Math.max(0.6, lat*0.06);
  const loss = Math.max(0.02, Math.min(2, 0.08+jit/80));
  return { lat:r(lat), jitter:r(jit), loss:r(loss), throughput:r(tput),
    availability:r(Math.max(98,Math.min(100,99.97-loss*0.04))), source:'synthetic', confidence:0.55 };
}
module.exports = { synth };
