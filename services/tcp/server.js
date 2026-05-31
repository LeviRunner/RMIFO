const express = require('express');
const tcp = require('./lib/tcp');
const app = express(), PORT = process.env.PORT || 3000;
app.use(express.json({ limit:'1mb' }));
app.get('/health', (_,res)=>res.json({ ok:true, service:'tcp', version:'1.0.0' }));
app.post('/probe', async (req,res) => {
  const { host, ports=[443,80,22], packets=20 } = req.body||{};
  if (!host) return res.status(400).json({ error:'host obrigatorio' });
  try { const r = await tcp.probe(host,{ports,packets}); res.json(r||{host,reachable:false,open_ports:[],avg_ms:null}); }
  catch(e){ res.status(500).json({ error:e.message }); }
});
app.listen(PORT, ()=>console.log(`[tcp] :${PORT}`));
