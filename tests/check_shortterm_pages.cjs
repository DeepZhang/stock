// 先运行 scripts/run_shortterm.py --date 2026-11-06，再从仓库根目录运行 node tests/check_shortterm_pages.cjs。
// 使用真实本地JSON验证渲染及轮询逻辑；不代表浏览器或HTTP预览验收。
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const result = JSON.parse(fs.readFileSync('data/shortterm_picks.json','utf8'));
const elements = new Map();
const doc = { getElementById(id) { if (!elements.has(id)) elements.set(id,{textContent:'',innerHTML:'',style:{},disabled:false}); return elements.get(id); }, querySelectorAll() {return [];} };
const code = fs.readFileSync('static/shortterm.html','utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
const context = vm.createContext({document:doc,fetch:async()=>({ok:true,json:async()=>result}),console,encodeURIComponent});
vm.runInContext(code,context);
setImmediate(async()=>{
 assert(doc.getElementById('meta').textContent.includes('2026-11-06'));
 assert.equal((doc.getElementById('limit_up').innerHTML.match(/<article/g)||[]).length,10);
 assert.equal((doc.getElementById('oversold').innerHTML.match(/<article/g)||[]).length,3);
 assert(doc.getElementById('history').innerHTML.includes('63.027%'));
 assert(doc.getElementById('history').innerHTML.includes('69.032%'));
 vm.runInContext('render({as_of:"",limit_up:[],oversold:[],backtest:{rows:[]}})',context);
 assert(doc.getElementById('meta').textContent.includes('暂无每日结果'));
 const screener = fs.readFileSync('static/screener.html','utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
 const calls=[];
 let scans=0;
 const cached=JSON.parse(fs.readFileSync('data/stock_picks.json','utf8'));
 const screenContext=vm.createContext({document:doc,console,encodeURIComponent,setInterval(){},setTimeout(fn){fn();},fetch:async url=>{
  calls.push(url);
  if(url.includes('?force=1'))return {ok:true,json:async()=>({...cached,scanning:true})};
  if(url==='/api/screen_cached')return {ok:true,json:async()=>({...cached,time:++scans===1 ? cached.time : 'completed-task-time'})};
  return {ok:true,json:async()=>({})};
 }});
 vm.runInContext(screener,screenContext);
 await new Promise(resolve=>setImmediate(resolve));
 await vm.runInContext('runScreen()',screenContext);
 assert(calls.includes('/api/screen?force=1'));
 assert(scans>=2);
 assert(doc.getElementById('updateTime').textContent.includes('2026-11-06'));
 assert(doc.getElementById('updateTime').textContent.includes('completed-task-time'));
 assert.equal(doc.getElementById('btnScreen').disabled,false);
 console.log('PASS: shortterm render 10+3, historical figures, empty state; screener force, polling, actual data date');
});
