const express = require('express');
const icmp = require('./lib/icmp');
const app = express(), PORT = process.env.PORT || 3000;
app.use(express.json({ limit:'1mb' }));
app.get('/health', (_,res)=>res.json({ ok:true, service:'ping', version:'1.0.0' }));
app.post('/probe', async (req,res) => {
  const { host, count=4 } = req.body||{};
  if (!host) return res.status(400).json({ error:'host obrigatorio' });
  try { res.json(await icmp.ping(host,{count})); } catch(e){ res.status(500).json({ error:e.message }); }
});
app.listen(PORT, ()=>console.log(`[ping] :${PORT}`));
