const express = require('express');
const httpProbe = require('./lib/http');
const app = express(), PORT = process.env.PORT || 3000;
app.use(express.json({ limit:'1mb' }));
app.get('/health', (_,res)=>res.json({ ok:true, service:'http', version:'1.0.0' }));
app.post('/probe', async (req,res) => {
  const { host, port, scheme } = req.body||{};
  if (!host) return res.status(400).json({ error:'host obrigatorio' });
  try { res.json({ host, ...(await httpProbe.head(host,{port,scheme})) }); } catch(e){ res.status(500).json({ error:e.message }); }
});
app.listen(PORT, ()=>console.log(`[http] :${PORT}`));
