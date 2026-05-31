'use strict';
/** lib/http.js — HTTP/HTTPS HEAD probe. Auto-contido. */
const http = require('http'), https = require('https');
const T = parseInt(process.env.HTTP_TIMEOUT_MS || '8000', 10);
function head(host, opts = {}) {
  const { timeoutMs = T } = opts;
  const attempts = [{port:opts.port||443,scheme:opts.scheme||'https'},{port:80,scheme:'http'}];
  const tryOne = ({port,scheme}) => new Promise(resolve => {
    const t0 = Date.now(), lib = scheme==='https'?https:http;
    const req = lib.request({hostname:host,port,path:'/',method:'HEAD',timeout:timeoutMs,
      rejectUnauthorized:false,headers:{'User-Agent':'rmifo-probe/1.0'}}, res => {
      const ms = Date.now()-t0; res.resume();
      resolve({reachable:true,http_code:res.statusCode,avg_ms:ms,scheme,method:'http'});
    });
    req.on('timeout',()=>{req.destroy();resolve(null);});
    req.on('error',()=>resolve(null)); req.end();
  });
  return attempts.reduce((c,a)=>c.then(r=>r??tryOne(a)),Promise.resolve(null))
    .then(r=>r??{host,reachable:false,http_code:null,avg_ms:null,scheme:'http',method:'http'});
}
module.exports = { head };
