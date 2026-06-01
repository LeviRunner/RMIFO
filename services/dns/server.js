const express = require('express');
const dnsp = require('./lib/dns');
const app = express(), PORT = process.env.PORT || 3000;
app.use(express.json({ limit:'1mb' }));
app.get('/health', (_,res)=>res.json({ ok:true, service:'dns', version:'1.0.0' }));
app.post('/probe', async (req,res) => {
  const { host, server } = req.body||{};
  if (!host) return res.status(400).json({ error:'host obrigatorio' });
  try { res.json(await dnsp.resolve(host, { server })); } catch(e){ res.status(500).json({ error:e.message }); }
});
app.listen(PORT, ()=>console.log(`[dns] :${PORT}`));
