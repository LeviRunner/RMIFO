const express = require('express');
const bgp = require('./lib/bgp');
const app = express(), PORT = process.env.PORT || 3000;
app.use(express.json({ limit:'1mb' }));
app.get('/health', (_,res)=>res.json({ ok:true, service:'bgp', version:'1.0.0' }));
app.post('/prefix', async (req,res) => {
  const { prefix } = req.body||{};
  if (!prefix) return res.status(400).json({ error:'prefix obrigatorio' });
  try { res.json(await bgp.lookupPrefix(prefix)); } catch(e){ res.status(500).json({ error:e.message }); }
});
app.post('/asn', async (req,res) => {
  const { ip } = req.body||{};
  if (!ip) return res.status(400).json({ error:'ip obrigatorio' });
  try { res.json({ ip, ...(await bgp.ipToAsn(ip)) }); } catch(e){ res.status(500).json({ error:e.message }); }
});
app.listen(PORT, ()=>console.log(`[bgp] :${PORT}`));
