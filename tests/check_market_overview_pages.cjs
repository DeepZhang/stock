// 验证两个页面不会将网关HTML、JSON解析错误或请求失败直接显示给用户。
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
(async () => {
  for (const file of ['market.html', 'market2.html']) {
    const source = fs.readFileSync('static/' + file, 'utf8');
    const code = source.match(/<script>\s*(async function loadDailyReview[\s\S]*?)<\/script>/)[1];
    for (const fetch of [
      async () => ({ok:false, json:async () => {throw new SyntaxError('Unexpected token <');}}),
      async () => ({ok:true, json:async () => {throw new SyntaxError('Unexpected token <');}}),
      async () => {throw new TypeError('Failed to fetch');},
      async () => ({ok:true, json:async () => ({indices:[], limit_stats:null})}),
      async () => ({ok:true, json:async () => null}),
    ]) {
      const el = {innerHTML:''};
      const context = vm.createContext({document:{getElementById:() => el}, fetch, setInterval(){}});
      vm.runInContext(code.replace(/document.title=[\s\S]*$/, ''), context);
      await vm.runInContext('loadDailyReview()', context);
      assert(el.innerHTML.includes('数据加载中，稍后重试'), file);
      assert(!/SyntaxError|Unexpected token|Failed to fetch/.test(el.innerHTML), file);
    }
    const el = {innerHTML:''};
    const context = vm.createContext({
      document:{getElementById:id => id === 'dailyReview' ? el : null},
      setInterval(){},
      fetch:async url => ({ok:true, json:async () => url === '/api/market-overview' ? {
        indices:[{name:'上证指数', price:3649.18, change_pct:-1.1}],
        limit_stats:{up_count:716, down_count:4431, limit_up:32, limit_down:15}
      } : []})
    });
    vm.runInContext(code.replace(/document.title=[\s\S]*$/, ''), context);
    await vm.runInContext('loadDailyReview()', context);
    assert(el.innerHTML.includes('上证指数'), file);
    assert(el.innerHTML.includes('3649.2'), file);
    assert(!el.innerHTML.includes('稍后重试'), file);
  }
  console.log('PASS: market.html and market2.html: HTTP error, non-JSON, request failure, empty/missing data, valid rendering');
})().catch(error => { console.error(error); process.exitCode=1; });
