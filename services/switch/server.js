const express = require('express');
const sw = require('./lib/switch');
const app = express(), PORT = process.env.PORT || 3000;
app.use(express.json({ limit:'1mb' }));

// scans em memoria (escala por instancia)
const scans = new Map();
const newId = () => Date.now().toString(36)+Math.random().toString(36).slice(2,6);

app.get('/health', (_,res)=>res.json({ ok:true, service:'switch', version:'1.0.0' }));
app.post('/scan', async (req,res) => {
  const { cidr, step=8, cycles=5, enrich_asn=false } = req.body||{};
  if (!cidr) return res.status(400).json({ error:'cidr obrigatorio' });
  try { res.json(await sw.scanCidr(cidr,{step,cycles,enrichAsn:enrich_asn})); } catch(e){ res.status(500).json({ error:e.message }); }
});
app.post('/mgmt', async (req,res) => {
  const { ip } = req.body||{};
  if (!ip) return res.status(400).json({ error:'ip obrigatorio' });
  try { res.json(await sw.probeMgmt(ip)); } catch(e){ res.status(500).json({ error:e.message }); }
});
// batch assincrono de varios CIDRs
app.post('/batch', (req,res) => {
  const { cidrs=[], step=8, cycles=5, enrich_asn=false } = req.body||{};
  if (!cidrs.length) return res.status(400).json({ error:'cidrs[] obrigatorio' });
  const id = newId();
  const st = { status:'running', current:0, total:cidrs.length, switches:[], started_at:new Date().toISOString() };
  scans.set(id, st);
  res.json({ scan_id:id, total:cidrs.length, status:'started' });
  (async()=>{ for (const c of cidrs){ if(st.status==='cancelled')break;
    try{ const r=await sw.scanCidr(c,{step,cycles,enrichAsn:enrich_asn}); st.switches.push(...r.switches); st.current++; }catch{} }
    st.status='completed'; setTimeout(()=>scans.delete(id),7200000); })();
});
app.get('/scan/:id', (req,res)=>{ const s=scans.get(req.params.id); res.json(s||{status:'idle'}); });
app.delete('/scan/:id', (req,res)=>{ const s=scans.get(req.params.id); if(s)s.status='cancelled'; res.json({status:'cancelled'}); });
app.listen(PORT, ()=>console.log(`[switch] :${PORT}`));
