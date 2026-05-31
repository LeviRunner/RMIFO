const express = require('express');
const { scanCable } = require('./lib/scanner');
const app = express(), PORT = process.env.PORT || 3000;
app.use(express.json({ limit:'2mb' }));

const scans = new Map();
const newId = () => Date.now().toString(36)+Math.random().toString(36).slice(2,6);

app.get('/health', (_,res)=>res.json({ ok:true, service:'telemetry', version:'1.0.0' }));

// scan sincrono de um cabo
app.post('/scan', async (req,res) => {
  const cable = req.body||{};
  if (!cable.cable_id) return res.status(400).json({ error:'cable_id obrigatorio' });
  try { res.json(await scanCable(cable,{fullScan:cable.full_scan})); } catch(e){ res.status(500).json({ error:e.message }); }
});

// batch assincrono
app.post('/batch', (req,res) => {
  const { cables=[], full_scan=false } = req.body||{};
  if (!cables.length) return res.status(400).json({ error:'cables[] obrigatorio' });
  const id = newId();
  const st = { status:'running', started_at:new Date().toISOString(),
    progress:{current:0,total:cables.length,percent:0,cable:'',proto:'',latency_ms:0},
    summary:{inserted:0,tcp:0,tcp_jitter:0,synthetic:0,http:0,anomalies:0}, results:[],
    deps_ok:true, dns_ok:true, http_ok:true };
  scans.set(id, st);
  res.json({ scan_id:id, total:cables.length, status:'started' });
  (async()=>{ for (let i=0;i<cables.length;i++){ if(st.status==='cancelled')break;
    try { const r=await scanCable(cables[i],{fullScan:full_scan}); st.results.push(r);
      const m=r.method||'';
      if(m.includes('tcp')){st.summary.tcp_jitter++;st.summary.tcp++;}
      else if(m==='icmp-echo')st.summary.tcp++;
      else if(m==='http-head')st.summary.http++;
      else if(m==='synthetic')st.summary.synthetic++;
      st.summary.inserted++;
      st.progress={current:i+1,total:cables.length,percent:+((i+1)/cables.length*100).toFixed(1),
        cable:cables[i].cable_name||cables[i].cable_id,proto:m,latency_ms:r.latency_ms||0};
    } catch { st.summary.anomalies++; } }
    st.status='completed'; st.summary.completed_at=new Date().toISOString();
    setTimeout(()=>scans.delete(id),7200000); })();
});
app.get('/scan/:id', (req,res)=>{ const s=scans.get(req.params.id);
  if(!s) return res.json({status:'idle'});
  res.json({ status:s.status, progress:s.progress, summary:s.summary,
    deps_ok:s.deps_ok, dns_ok:s.dns_ok, http_ok:s.http_ok,
    current_phase:s.progress.proto||'', live_tcp:s.summary.tcp, live_synth:s.summary.synthetic,
    probes:[], warn_list:[], error_list:[] }); });
app.delete('/scan/:id', (req,res)=>{ const s=scans.get(req.params.id); if(s)s.status='cancelled'; res.json({status:'cancelled'}); });
app.listen(PORT, ()=>console.log(`[telemetry] :${PORT}`));
