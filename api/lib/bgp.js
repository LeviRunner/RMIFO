'use strict';
/** lib/bgp.js — BGP/ROA via RIPE STAT. Auto-contido. */
const https = require('https');
const BASE = 'https://stat.ripe.net/data';
const TO = parseInt(process.env.BGP_TIMEOUT_MS || '10000', 10);
function ripe(ep, params) {
  const qs = new URLSearchParams(params).toString();
  return new Promise(resolve => {
    const req = https.get(`${BASE}/${ep}/data.json?${qs}`,{timeout:TO}, res => {
      let b=''; res.on('data',d=>b+=d); res.on('end',()=>{try{resolve(JSON.parse(b));}catch{resolve(null);}});
    });
    req.on('error',()=>resolve(null)); req.on('timeout',()=>{req.destroy();resolve(null);});
  });
}
async function lookupPrefix(prefix) {
  const out = {prefix,peers_seeing:0,visibility_pct:0,origin_asn:null,as_path:null,roa_status:'unknown',hijack_risk:false,source:'ripe-stat'};
  const vis = await ripe('visibility',{resource:prefix});
  if (vis?.status==='ok') {
    const v4 = vis.data?.visibilities?.[0]?.ipv4||{};
    const pk = v4.ris_peers_seeing||0, tt = v4.total_ris_peers||1;
    out.peers_seeing=pk; out.visibility_pct=+((pk/tt)*100).toFixed(2);
  }
  const r = await ripe('bgp-state',{resource:prefix});
  if (r?.status==='ok') {
    const e = r.data?.bgp_state?.[0];
    if (e) { const p=e.path||[]; out.origin_asn=String(p[p.length-1]??''); out.as_path=p.map(String).join(' -> '); }
  }
  const rpki = await ripe('rpki-validation',{resource:prefix});
  if (rpki?.status==='ok') { out.roa_status=rpki.data?.status??'unknown'; out.hijack_risk=['invalid','not-found'].includes(out.roa_status); }
  return out;
}
async function ipToAsn(ip) {
  const d = await ripe('prefix-overview',{resource:ip});
  const a = d?.data?.asns||[];
  return a.length ? {asn:String(a[0].asn),holder:a[0].holder||null} : {asn:null,holder:null};
}
module.exports = { lookupPrefix, ipToAsn };
