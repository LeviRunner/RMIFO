'use strict';
/**
 * api/server.js — RMIFO Probe API (monolito)
 * Expoe todas as ferramentas de probe num unico servico.
 * Alternativa ao deploy granular em services/.
 */
const express = require('express');
const tcp  = require('./lib/tcp');
const dnsp = require('./lib/dns');
const icmp = require('./lib/icmp');
const mtr  = require('./lib/mtr');
const httpProbe = require('./lib/http');
const bgp  = require('./lib/bgp');
const sw   = require('./lib/switch');
const { scanCable } = require('./lib/scanner');

const app = express(), PORT = process.env.PORT || 3000;
app.use(express.json({ limit:'2mb' }));

// Batch state is local to this process, lost on restart and expires two hours after completion.
const scans = new Map();
const newId = () => Date.now().toString(36)+Math.random().toString(36).slice(2,6);

app.get('/health', (_,res)=>res.json({ ok:true, service:'probe-api', version:'1.0.0' }));

// ── Probe direto ──────────────────────────────────────────────────────────────
app.post('/probe/tcp', async (req,res)=>{ const {host,ports=[443,80,22],packets=20}=req.body||{};
  if(!host)return res.status(400).json({error:'host obrigatorio'});
  try{const r=await tcp.probe(host,{ports,packets});res.json(r||{host,reachable:false,open_ports:[],avg_ms:null});}catch(e){res.status(500).json({error:e.message});} });
app.post('/probe/ping', async (req,res)=>{ const {host,count=4}=req.body||{};
  if(!host)return res.status(400).json({error:'host obrigatorio'});
  try{res.json(await icmp.ping(host,{count}));}catch(e){res.status(500).json({error:e.message});} });
app.post('/probe/traceroute', async (req,res)=>{ const {host,cycles=5,max_hops=15}=req.body||{};
  if(!host)return res.status(400).json({error:'host obrigatorio'});
  try{res.json(await mtr.trace(host,{cycles,maxHops:max_hops}));}catch(e){res.status(500).json({error:e.message});} });
app.post('/probe/http', async (req,res)=>{ const {host,port,scheme}=req.body||{};
  if(!host)return res.status(400).json({error:'host obrigatorio'});
  try{res.json({host,...(await httpProbe.head(host,{port,scheme}))});}catch(e){res.status(500).json({error:e.message});} });
app.post('/probe/dns', async (req,res)=>{ const {host,server}=req.body||{};
  if(!host)return res.status(400).json({error:'host obrigatorio'});
  try{res.json(await dnsp.resolve(host,{server}));}catch(e){res.status(500).json({error:e.message});} });
app.post('/probe/tcp/tamper', async (req,res)=>{ const {host,ports=[443,80,22,53]}=req.body||{};
  if(!host)return res.status(400).json({error:'host obrigatorio'});
  try{res.json(await tcp.tamperProbe(host,ports));}catch(e){res.status(500).json({error:e.message});} });

// ── BGP ─────────────────────────────────────────────────────────────────────
app.post('/network/bgp', async (req,res)=>{ const {prefix}=req.body||{};
  if(!prefix)return res.status(400).json({error:'prefix obrigatorio'});
  try{res.json(await bgp.lookupPrefix(prefix));}catch(e){res.status(500).json({error:e.message});} });
app.post('/network/bgp/ip', async (req,res)=>{ const {ip}=req.body||{};
  if(!ip)return res.status(400).json({error:'ip obrigatorio'});
  try{res.json({ip,...(await bgp.ipToAsn(ip))});}catch(e){res.status(500).json({error:e.message});} });

// ── Switch ────────────────────────────────────────────────────────────────────
app.post('/network/switch/scan', async (req,res)=>{ const {cidr,step=8,cycles=5,enrich_asn=false}=req.body||{};
  if(!cidr)return res.status(400).json({error:'cidr obrigatorio'});
  try{res.json(await sw.scanCidr(cidr,{step,cycles,enrichAsn:enrich_asn}));}catch(e){res.status(500).json({error:e.message});} });
app.post('/network/switch/mgmt', async (req,res)=>{ const {ip}=req.body||{};
  if(!ip)return res.status(400).json({error:'ip obrigatorio'});
  try{res.json(await sw.probeMgmt(ip));}catch(e){res.status(500).json({error:e.message});} });
app.post('/network/switch/batch', (req,res)=>{ const {cidrs=[],step=8,cycles=5,enrich_asn=false}=req.body||{};
  if(!cidrs.length)return res.status(400).json({error:'cidrs[] obrigatorio'});
  const id=newId(); const st={status:'running',current:0,total:cidrs.length,switches:[],started_at:new Date().toISOString()};
  scans.set(id,st); res.json({scan_id:id,total:cidrs.length,status:'started'});
  (async()=>{for(const c of cidrs){if(st.status==='cancelled')break;try{const r=await sw.scanCidr(c,{step,cycles,enrichAsn:enrich_asn});st.switches.push(...r.switches);st.current++;}catch{}}st.status='completed';setTimeout(()=>scans.delete(id),7200000);})(); });
app.get('/network/switch/scan/:id',(req,res)=>{const s=scans.get(req.params.id);res.json(s||{status:'idle'});});
app.delete('/network/switch/scan/:id',(req,res)=>{const s=scans.get(req.params.id);if(s)s.status='cancelled';res.json({status:'cancelled'});});

// ── Telemetry ───────────────────────────────────────────────────────────────
app.post('/telemetry/scan', async (req,res)=>{ const cable=req.body||{};
  if(!cable.cable_id)return res.status(400).json({error:'cable_id obrigatorio'});
  try{res.json(await scanCable(cable,{fullScan:cable.full_scan}));}catch(e){res.status(500).json({error:e.message});} });
app.post('/telemetry/batch',(req,res)=>{ const {cables=[],full_scan=false}=req.body||{};
  if(!cables.length)return res.status(400).json({error:'cables[] obrigatorio'});
  const id=newId(); const st={status:'running',started_at:new Date().toISOString(),
    progress:{current:0,total:cables.length,percent:0,cable:'',proto:'',latency_ms:0},
    summary:{inserted:0,tcp:0,tcp_jitter:0,synthetic:0,http:0,anomalies:0},results:[],deps_ok:true,dns_ok:true,http_ok:true};
  scans.set(id,st); res.json({scan_id:id,total:cables.length,status:'started'});
  (async()=>{for(let i=0;i<cables.length;i++){if(st.status==='cancelled')break;
    try{const r=await scanCable(cables[i],{fullScan:full_scan});st.results.push(r);const m=r.method||'';
      if(m.includes('tcp')){st.summary.tcp_jitter++;st.summary.tcp++;}else if(m==='icmp-echo')st.summary.tcp++;
      else if(m==='http-head')st.summary.http++;else if(m==='synthetic')st.summary.synthetic++;st.summary.inserted++;
      st.progress={current:i+1,total:cables.length,percent:+((i+1)/cables.length*100).toFixed(1),cable:cables[i].cable_name||cables[i].cable_id,proto:m,latency_ms:r.latency_ms||0};
    }catch{st.summary.anomalies++;}}st.status='completed';st.summary.completed_at=new Date().toISOString();setTimeout(()=>scans.delete(id),7200000);})(); });
app.get('/telemetry/scan/:id',(req,res)=>{const s=scans.get(req.params.id);
  if(!s)return res.json({status:'idle'});
  res.json({status:s.status,progress:s.progress,summary:s.summary,deps_ok:s.deps_ok,dns_ok:s.dns_ok,http_ok:s.http_ok,
    current_phase:s.progress.proto||'',live_tcp:s.summary.tcp,live_synth:s.summary.synthetic,probes:[],warn_list:[],error_list:[]});});
app.delete('/telemetry/scan/:id',(req,res)=>{const s=scans.get(req.params.id);if(s)s.status='cancelled';res.json({status:'cancelled'});});

app.use((req,res)=>res.status(404).json({error:'endpoint nao encontrado'}));
app.listen(PORT,()=>console.log(`[probe-api] :${PORT}`));
module.exports = app;
