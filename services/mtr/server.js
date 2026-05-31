const express = require('express');
const mtr = require('./lib/mtr');
const app = express(), PORT = process.env.PORT || 3000;
app.use(express.json({ limit:'1mb' }));
app.get('/health', (_,res)=>res.json({ ok:true, service:'mtr', version:'1.0.0' }));
app.post('/probe', async (req,res) => {
  const { host, cycles=5, max_hops=15 } = req.body||{};
  if (!host) return res.status(400).json({ error:'host obrigatorio' });
  try { res.json(await mtr.trace(host,{cycles,maxHops:max_hops})); } catch(e){ res.status(500).json({ error:e.message }); }
});
app.listen(PORT, ()=>console.log(`[mtr] :${PORT}`));
