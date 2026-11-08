// 个股K线 - 单实例多grid
let mainChart = null;
let currentCode = null, currentName = "", currentPeriod = "day";
let chartData = null;

// ---------- 搜索 ----------
const searchInput = document.getElementById("searchInput");
const dropdown = document.getElementById("searchDropdown");
let debounceTimer = null;
searchInput.addEventListener("input", function () {
  const kw = this.value.trim();
  clearTimeout(debounceTimer);
  if (!kw) { dropdown.style.display = "none"; return; }
  debounceTimer = setTimeout(() => doSearch(kw), 300);
});
document.addEventListener("click", function (e) {
  if (!e.target.closest(".search-box") && !e.target.closest(".search-dropdown"))
    dropdown.style.display = "none";
});
async function doSearch(keyword) {
  try {
    const res = await fetch(`/api/search?keyword=${encodeURIComponent(keyword)}`);
    const j = await res.json();
    dropdown.innerHTML = (j.list||[]).map(s =>
      `<div class="item" data-qtmk="${s.qtmk}" data-name="${s.name}">${s.name} (${s.code})</div>`).join("") || '<div class="item">无匹配</div>';
    dropdown.style.display = "block";
    dropdown.querySelectorAll(".item").forEach(el => {
      el.onclick = () => { dropdown.style.display = "none"; loadStock(el.dataset.qtmk, el.dataset.name); };
    });
  } catch(e) { console.error(e); }
}

// ---------- 周期切换 ----------
document.getElementById("periodTabs").addEventListener("click", function(e) {
  const btn = e.target.closest("button"); if(!btn) return;
  this.querySelectorAll("button").forEach(b=>b.classList.remove("active"));
  btn.classList.add("active");
  currentPeriod = btn.dataset.period;
  if(currentCode) loadStock(currentCode, currentName);
});

// ---------- 指标计算 ----------
function calcMA(arr,n){const o=[];for(let i=0;i<arr.length;i++){if(i<n-1){o.push("-");continue;}let s=0;for(let j=0;j<n;j++)s+=arr[i-j];o.push(+(s/n).toFixed(3));}return o;}
function calcEMA(arr,n){const o=[],k=2/(n+1);let e=arr[0];for(let i=0;i<arr.length;i++){e=i===0?arr[0]:arr[i]*k+e*(1-k);o.push(e);}return o;}
function calcMACD(c){const e12=calcEMA(c,12),e26=calcEMA(c,26);const d=c.map((_,i)=>e12[i]-e26[i]);const dea=calcEMA(d,9);const m=d.map((v,i)=>(v-dea[i])*2);return{dif:d,dea,macd:m};}
function calcKDJ(h,l,c,n=9){const ka=[],da=[],ja=[];let k=50,d=50;for(let i=0;i<c.length;i++){const s=Math.max(0,i-n+1);let hn=-Infinity,ln=Infinity;for(let j=s;j<=i;j++){if(h[j]>hn)hn=h[j];if(l[j]<ln)ln=l[j];}const rsv=hn===ln?50:(c[i]-ln)/(hn-ln)*100;k=2/3*k+1/3*rsv;d=2/3*d+1/3*k;ka.push(k);da.push(d);ja.push(3*k-2*d);}return{k:ka,d:da,j:ja};}

// ---------- 加载数据 ----------
async function loadStock(qtmk, name) {
  currentCode = qtmk; currentName = name;
  const [kr, qr] = await Promise.all([
    fetch(`/api/kline?code=${qtmk}&period=${currentPeriod}&count=200`).then(r=>r.json()).catch(()=>({bars:[]})),
    fetch(`/api/quote?code=${qtmk}`).then(r=>r.json()).catch(()=>({})),
  ]);
  const bars = kr.bars || [];
  if(!qr.name) qr.name = currentName;
  if(!qr.code) qr.code = currentCode;
  if(!qr.price && bars.length){
    const last = bars[bars.length-1];
    const prev = bars.length>1 ? bars[bars.length-2].close : last.close;
    qr.price = last.close;
    qr.change = last.close - prev;
    qr.change_pct = (last.close - prev)/prev*100;
    qr.high = last.high; qr.low = last.low;
    qr.open = last.open; qr.prev_close = prev;
  }
  renderQuote(qr);
  if(qtmk.startsWith("BK")){
    document.getElementById("fundBox").style.display = "none";
    loadSectorStocks(qtmk);
  } else {
    loadFundamental(qtmk);
  }
  if(!bars.length) return;
  const closes=bars.map(b=>b.close), highs=bars.map(b=>b.high), lows=bars.map(b=>b.low), vols=bars.map(b=>b.volume);
  chartData = {
    dates:bars.map(b=>b.date), ohlc:bars.map(b=>[b.open,b.close,b.low,b.high]),
    closes, highs, lows, vols, bars,
    ma5:calcMA(closes,5),ma10:calcMA(closes,10),ma20:calcMA(closes,20),ma60:calcMA(closes,60),ma90:calcMA(closes,90),ma120:calcMA(closes,120),
    macd:calcMACD(closes), kdj:calcKDJ(highs,lows,closes), volMA5:calcMA(vols,5), volMA10:calcMA(vols,10),
  };
  renderAll();
}

// ---------- 顶部行情 ----------
function fmtAmt(v){return v>=10000?(v/10000).toFixed(2)+"亿":v.toFixed(0)+"万";}
function fmtMv(v){return v>=10000?(v/10000).toFixed(2)+"万亿":v.toFixed(0)+"亿";}
function fmtVol(v){return v>=10000?(v/10000).toFixed(2)+"万手":v.toFixed(0)+"手";}
function renderQuote(q){ window._lastQuote = q;
  document.getElementById("quoteBar").style.display="flex";
  document.getElementById("qName").textContent=q.name||"";
  document.getElementById("qCode").textContent=q.code||currentCode;
  if(!q||!q.price) return;
  const up=q.change>=0;
  const pe=document.getElementById("qPrice");
  pe.textContent=q.price.toFixed(2); pe.className="q-price "+(up?"up":"down");
  const ce=document.getElementById("qChg");
  ce.textContent=`${up?"+":""}${q.change.toFixed(2)} ${up?"+":""}${q.change_pct.toFixed(2)}%`;
  ce.className="q-chg "+(up?"up up-bg":"down down-bg");
  const nf = v => v!=null ? v : "--";
  const stats=[["今开",nf(q.open)],["最高",nf(q.high)],["最低",nf(q.low)],["昨收",nf(q.prev_close)],["换手",q.turnover?q.turnover.toFixed(2)+"%":"--"],["成交量",q.volume?fmtVol(q.volume):"--"],["成交额",q.amount?fmtAmt(q.amount):"--"],["市盈",q.pe?q.pe.toFixed(2):"--"],["振幅",q.amplitude?q.amplitude.toFixed(2)+"%":"--"],["总市值",q.total_mv?fmtMv(q.total_mv):"--"],["流通值",q.circ_mv?fmtMv(q.circ_mv):"--"]];
  document.getElementById("qStats").innerHTML=stats.map(([k,v])=>`<span class="item">${k}<span>${v}</span></span>`).join("");
  const ob=document.getElementById("orderbook");
  if(!q.ask1){ ob.innerHTML=''; return; }
  let html='<div class="ob-header">五档盘口</div>';
  [q.ask5,q.ask4,q.ask3,q.ask2,q.ask1].forEach((a,i)=>{
    html+=`<div class="ob-row ob-ask"><span class="label">卖${5-i}</span><span class="ob-price price">${a.price.toFixed(2)}</span><span>${a.vol.toFixed(0)}</span></div>`;
  });
  html+=`<div class="ob-row" style="border-top:1px solid #1f2937;border-bottom:1px solid #1f2937;margin:2px 0;padding:4px;"><span class="ob-price ${up?'up':'down'}">${q.price.toFixed(2)}</span></div>`;
  [q.bid1,q.bid2,q.bid3,q.bid4,q.bid5].forEach((b,i)=>{
    html+=`<div class="ob-row ob-bid"><span class="label">买${i+1}</span><span class="ob-price price">${b.price.toFixed(2)}</span><span>${b.vol.toFixed(0)}</span></div>`;
  });
  ob.innerHTML=html;
}

// ---------- 公司资料 ----------
async function loadSectorStocks(bk){
  const box = document.getElementById("sectorStocksBox");
  box.style.display = "block";
  const r = await fetch(`/api/sector_stocks?bk=${bk}`);
  const list = await r.json();
  let html = '<table style="width:100%;font-size:12px;border-collapse:collapse"><tr style="color:#6b7280"><td style="padding:4px">代码</td><td>名称</td><td>涨幅</td><td>资金(亿)</td></tr>';
  list.forEach(s=>{
    const code = s.code.startsWith("6")?"sh"+s.code:(s.code.startsWith("9")?"bj"+s.code:"sz"+s.code);
    const c = s.pct>=0?"#ef4444":"#22c55e";
    html += `<tr><td style="color:#6b7280;padding:3px">${s.code}</td><td><a href="/kline?code=${code}&name=${encodeURIComponent(s.name)}" style="color:#e5e7eb">${s.name}</a></td><td style="color:${c}">${s.pct>=0?"+":""}${s.pct}%</td><td style="color:${s.flow>=0?'#ef4444':'#22c55e'}">${s.flow>=0?"+":""}${s.flow}</td></tr>`;
  });
  html += '</table>';
  document.getElementById("sectorStocksList").innerHTML = html;
}
async function loadFundamental(code){
  try{
    const r = await fetch(`/api/fundamental?code=${code}`);
    const f = await r.json();
    if(!f || (!f.operate && !f.company)) return;
    document.getElementById("fundBox").style.display = "block";
    document.getElementById("fundReport").textContent = f.report || "";

    const yoy = (v)=>{
      if(v==null) return "--";
      const cls = v>=0?"color:#ef4444;":"color:#22c55e;";
      const sign = v>=0?"+":"";
      return `<span style="${cls}">${sign}${v.toFixed(2)}%</span>`;
    };
    const row = (k,v)=>`<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #1f293733;"><span style="color:#9ca3af;">${k}</span><span style="color:#e5e7eb;">${v}</span></div>`;

    // 操盘必读 (两列并排, 类似东方财富)
    const o = f.operate || {};
    const qq = window._lastQuote || {};
    const cell = (k,v)=>`<div style="display:flex;justify-content:space-between;padding:5px 0;"><span style="color:#9ca3af;">${k}</span><span style="color:#e5e7eb;font-weight:500;">${v}</span></div>`;
    const pair = (l,r)=>`<div style="display:grid;grid-template-columns:1fr 1fr;gap:0 16px;border-bottom:1px solid #1f293733;">${l}${r}</div>`;
    let h = '<div style="font-size:12px;">';
    h += '<div style="color:#9ca3af;font-size:11px;margin-bottom:4px;">以下数据基于最新财报</div>';
    h += pair(cell("市盈率动", qq.pe?qq.pe.toFixed(2):"--"), cell("市净率", qq.pb?qq.pb.toFixed(2):"--"));
    h += pair(cell("每股收益", o.eps!=null?o.eps.toFixed(4)+"元":"--"), cell("每股净资产", o.bps?o.bps+"元":"--"));
    h += pair(cell("营业总收入", o.revenue!=null?o.revenue+"亿":"--"), cell("营收同比", o.revenue_yoy!=null?yoy(o.revenue_yoy):"--"));
    h += pair(cell("归母净利润", o.net_profit!=null?o.net_profit+"亿":"--"), cell("净利润同比", o.np_yoy!=null?yoy(o.np_yoy):"--"));
    h += pair(cell("毛利率", o.gross_margin!=null?o.gross_margin+"%":"--"), cell("净利率", o.net_margin!=null?o.net_margin+"%":"--"));
    h += pair(cell("净资产收益率", o.roe!=null?o.roe+"%":"--"), cell("负债率", o.debt_ratio!=null?o.debt_ratio+"%":"--"));
    h += pair(cell("总股本", qq.total_mv&&qq.price?(qq.total_mv/qq.price).toFixed(2)+"亿":"--"), cell("总市值", qq.total_mv?qq.total_mv+"亿":"--"));
    h += pair(cell("流通A股", qq.circ_mv&&qq.price?(qq.circ_mv/qq.price).toFixed(2)+"亿":"--"), cell("流通市值", qq.circ_mv?qq.circ_mv+"亿":"--"));
    h += '</div>';
    document.getElementById("fundOperate").innerHTML = h;

    // 公司概况 (两列布局, 类似东方财富)
    const c = f.company || {};
    const qc = (k,v)=>`<div style="display:flex;justify-content:space-between;padding:5px 0;"><span style="color:#9ca3af;">${k}</span><span style="color:#e5e7eb;">${v}</span></div>`;
    const qp = (l,r)=>`<div style="display:grid;grid-template-columns:1fr 1fr;gap:0 16px;border-bottom:1px solid #1f293733;">${l}${r}</div>`;
    let h2 = '<div style="font-size:12px;">';
    h2 += `<div style="font-size:15px;font-weight:700;color:#fff;margin:8px 0;">${c.name||"--"}</div>`;
    h2 += qp(qc("A股代码", (window._lastQuote||{}).code||"--"), qc("A股简称", (window._lastQuote||{}).name||"--"));
    h2 += qp(qc("所属行业", c.industry||"--"), qc("上市交易所", c.exchange||"--"));
    h2 += qp(qc("董事长", c.chairman||"--"), qc("法人代表", c.legal||"--"));
    h2 += qp(qc("员工人数", c.employees?c.employees+"人":"--"), qc("注册资本", c.reg_capital||"--"));
    h2 += qp(qc("成立日期", c.founded||"--"), qc("上市日期", c.listed||"--"));
    h2 += qc("公司网站", c.website||"--");
    h2 += qc("办公地址", c.address||"--");
    if(c.concepts){
        h2 += `<div style="margin-top:8px;padding:8px 0;border-top:1px solid #1f293733;"><div style="color:#9ca3af;margin-bottom:6px;">所属概念</div><div style="display:flex;flex-wrap:wrap;gap:6px;">${c.concepts.split(/[,，]/).map(t=>`<span style="background:#1e3a5f;color:#60a5fa;padding:2px 8px;border-radius:4px;font-size:11px;">${t.trim()}</span>`).join("")}</div></div>`;
    }
    h2 += `<div style="margin-top:8px;padding-top:8px;border-top:1px solid #1f2937;"><div style="color:#9ca3af;margin-bottom:4px;">公司简介</div><div style="color:#d1d5db;line-height:1.6;">${c.intro||"--"}</div></div>`;
    h2 += '</div>';
    document.getElementById("fundCompany").innerHTML = h2;

    // 财务分析
    let hf = '<div style="display:grid;grid-template-columns:1fr 1fr;gap:0 20px;font-size:12px;">';
    hf += row("ROE(加权)", o.roe!=null?o.roe+"%":"--");
    hf += row("ROIC", o.roic!=null?o.roic+"%":"--");
    hf += row("毛利率", o.gross_margin!=null?o.gross_margin+"%":"--");
    hf += row("净利率", o.net_margin!=null?o.net_margin+"%":"--");
    hf += row("资产负债率", o.debt_ratio!=null?o.debt_ratio+"%":"--");
    hf += row("流动比率", o.current_ratio!=null?o.current_ratio:"--");
    hf += row("速动比率", o.quick_ratio!=null?o.quick_ratio:"--");
    hf += row("总资产周转率", o.asset_turnover!=null?o.asset_turnover:"--");
    hf += row("存货周转天数", o.inventory_days!=null?o.inventory_days+"天":"--");
    hf += row("每股收益", o.eps!=null?o.eps.toFixed(2)+"元":"--");
    hf += row("每股净资产", o.bps?o.bps+"元":"--");
    hf += row("营收同比", o.revenue_yoy!=null?yoy(o.revenue_yoy):"--");
    hf += row("净利润同比", o.np_yoy!=null?yoy(o.np_yoy):"--");
    hf += '</div>';
    document.getElementById("fundFinance").innerHTML = hf;

    // 股本股东
    let hs = '<div style="font-size:12px;">';
    const q = window._lastQuote || {};
    if(q.price && q.total_mv){
        hs += row("总股本", (q.total_mv/q.price).toFixed(2)+"亿股");
        hs += row("流通A股", q.circ_mv?(q.circ_mv/q.price).toFixed(2)+"亿股":"--");
        hs += row("总市值", q.total_mv+"亿");
        hs += row("流通市值", q.circ_mv?q.circ_mv+"亿":"--");
        hs += row("市盈率(动)", q.pe?q.pe.toFixed(2):"--");
        hs += row("市净率", q.pb?q.pb.toFixed(2):"--");
    }
    hs += '<div style="margin-top:10px;font-weight:700;color:#fff;">股东人数</div>';
    hs += '<div id="holderChart" style="width:100%;height:260px;margin-top:8px;"></div>';
    hs += '</div>';
    document.getElementById("fundShareholder").innerHTML = hs;

    // tab切换
    document.querySelectorAll(".fund-tab").forEach(btn=>{
      btn.onclick = ()=>{
        document.querySelectorAll(".fund-tab").forEach(b=>{
          b.classList.remove("active"); b.style.color="#9ca3af"; b.style.fontWeight="normal";
        });
        btn.classList.add("active"); btn.style.color="#f97316"; btn.style.fontWeight="700";
        const tab = btn.dataset.tab;
        document.getElementById("fundOperate").style.display = tab==="operate"?"":"none";
        document.getElementById("fundFinance").style.display = tab==="finance"?"":"none";
        document.getElementById("fundShareholder").style.display = tab==="shareholder"?"":"none";
        if(tab==="shareholder" && !window._holderLoaded){
          window._holderLoaded = true;
          setTimeout(()=>{
            fetch('/api/holders?code='+encodeURIComponent(currentCode||''))
              .then(r=>r.json())
              .then(data=>{
                if(!data || !data.length) return;
                data = data.slice(-8);
                const dates = data.map(d=>d.date.slice(5));
                const holders = data.map(d=>+(d.holders/10000).toFixed(2));
                const chart = echarts.init(document.getElementById('holderChart'),'dark');
                chart.setOption({
                  backgroundColor:'transparent',
                  grid:{left:50,right:20,top:30,bottom:30},
                  legend:{data:['股东人数(万户)'],textStyle:{color:'#9ca3af',fontSize:10},top:0},
                  xAxis:{type:'category',data:dates,axisLabel:{color:'#9ca3af',fontSize:10,interval:0},axisLine:{lineStyle:{color:'#374151'}},axisTick:{show:false}},
                  yAxis:{type:'value',axisLabel:{color:'#6b7280',fontSize:10},splitLine:{lineStyle:{color:'rgba(55,65,81,0.3)'}}},
                  series:[{
                    name:'股东人数(万户)',type:'bar',data:holders,
                    barWidth:22,
                    itemStyle:{color:'#3b82f6',borderRadius:[3,3,0,0]},
                    label:{show:true,position:'top',color:'#60a5fa',fontSize:9,formatter:p=>p.value+'万'}
                  }]
                });
                window.addEventListener('resize',()=>chart.resize());
              }).catch(e=>console.error(e));
          },100);
        }
        document.getElementById("fundCompany").style.display = tab==="company"?"":"none";
      };
    });
  }catch(e){console.error(e);}
}

// ---------- 渲染：单实例4 grid ----------
function renderAll(){
  const d=chartData, upC="#ef4444", downC="#22c55e";
  const volColors=d.bars.map(b=>b.close>=b.open?upC:downC);
  const mcColors=d.macd.macd.map(v=>v>=0?upC:downC);

  if(mainChart) mainChart.dispose();
  const mcEl=document.getElementById("mainChart");mcEl.style.touchAction="none";mainChart = echarts.init(mcEl,"dark");

  // 隐藏其他容器（已合并到mainChart）
  ["volChart","macdChart","kdjChart"].forEach(id=>{
    document.getElementById(id).style.display="none";
  });

  const catAxis = {type:"category",data:d.dates,boundaryGap:true,axisLine:{lineStyle:{color:"#374151"}},axisLabel:{color:"#6b7280",fontSize:9},splitLine:{show:false}};

  mainChart.setOption({
    backgroundColor:"transparent", animation:false,
    legend:{data:["MA5","MA10","MA20","MA60","MA90","MA120"],top:2,left:10,textStyle:{color:"#9ca3af",fontSize:9},itemWidth:12,itemHeight:2},
    tooltip:{trigger:"axis",axisPointer:{type:"cross",label:{show:true}},backgroundColor:"rgba(17,24,39,0.95)",borderColor:"#374151",textStyle:{color:"#e5e7eb",fontSize:11},
      formatter:function(params){
        const idx=params[0].dataIndex, bar=d.bars[idx];
        const latest=d.closes[d.closes.length-1];
        const chg=(latest-bar.close)/bar.close*100, cc=chg>=0?upC:downC, sg=chg>=0?"+":"";
        const up=bar.close>=bar.open, col=up?upC:downC;
        let h=`<div style="font-weight:700;margin-bottom:3px;">${bar.date}</div>`;
        h+=`<div style="display:flex;justify-content:space-between;gap:16px;"><span style="color:#9ca3af;">开</span><span style="color:${col}">${bar.open.toFixed(2)}</span></div>`;
        h+=`<div style="display:flex;justify-content:space-between;gap:16px;"><span style="color:#9ca3af;">收</span><span style="color:${col}">${bar.close.toFixed(2)}</span></div>`;
        h+=`<div style="display:flex;justify-content:space-between;gap:16px;"><span style="color:#9ca3af;">低</span><span>${bar.low.toFixed(2)}</span></div>`;
        h+=`<div style="display:flex;justify-content:space-between;gap:16px;"><span style="color:#9ca3af;">高</span><span>${bar.high.toFixed(2)}</span></div>`;
        h+=`<div style="border-top:1px solid #374151;margin-top:3px;padding-top:3px;display:flex;justify-content:space-between;gap:16px;"><span style="color:#ff9f43;">至今涨幅</span><span style="color:${cc};font-weight:700;">${sg}${chg.toFixed(2)}%</span></div>`;
        return h;
      }
    },
    axisPointer:{link:[{xAxisIndex:"all"}]},
    // 4个grid，不重叠
    grid:[
      {left:35,right:35,top:28,height:"42%"},
      {left:35,right:35,top:"58%",height:"13%"},
      {left:35,right:35,top:"74%",height:"13%"},
      {left:35,right:35,top:"90%",height:"9%"},
    ],
    xAxis:[
      {type:"category",data:d.dates,gridIndex:0,boundaryGap:true,axisLine:{lineStyle:{color:"#374151"}},axisLabel:{show:false},axisPointer:{label:{show:false}},splitLine:{show:false}},
      {type:"category",data:d.dates,gridIndex:1,boundaryGap:true,axisLine:{lineStyle:{color:"#374151"}},axisLabel:{show:false},axisPointer:{label:{show:false}},splitLine:{show:false}},
      {type:"category",data:d.dates,gridIndex:2,boundaryGap:true,axisLine:{lineStyle:{color:"#374151"}},axisLabel:{show:false},axisPointer:{label:{show:false}},splitLine:{show:false}},
      {type:"category",data:d.dates,gridIndex:3,boundaryGap:true,axisLine:{lineStyle:{color:"#374151"}},axisLabel:{color:"#6b7280",fontSize:9,interval:Math.floor(d.dates.length/8)},splitLine:{show:false}},
    ],
    yAxis:[
      {scale:true,position:"right",gridIndex:0,axisLabel:{color:"#6b7280",fontSize:9},splitLine:{lineStyle:{color:"rgba(55,65,81,0.3)"}}},
      {scale:true,position:"right",gridIndex:1,axisLabel:{color:"#6b7280",fontSize:9,formatter:v=>v>=10000?(v/10000).toFixed(0)+"万":v},splitLine:{show:false}},
      {scale:true,position:"right",gridIndex:2,axisLabel:{color:"#6b7280",fontSize:9},splitLine:{show:false}},
      {scale:true,position:"right",gridIndex:3,axisLabel:{color:"#6b7280",fontSize:9},splitLine:{show:false}},
    ],
    dataZoom:[
      {type:"inside",xAxisIndex:[0,1,2,3],start:60,end:100,zoomOnMouseWheel:true,moveOnMouseMove:true,moveOnMouseWheel:false,moveOnTouchMove:false,zoomOnTouchMove:false},
    ],
    series:[
      {name:"K线",type:"candlestick",xAxisIndex:0,yAxisIndex:0,data:d.ohlc,itemStyle:{color:upC,color0:downC,borderColor:upC,borderColor0:downC}},
      {name:"MA5",type:"line",xAxisIndex:0,yAxisIndex:0,data:d.ma5,symbol:"none",lineStyle:{width:1,color:"#fbbf24"}},
      {name:"MA10",type:"line",xAxisIndex:0,yAxisIndex:0,data:d.ma10,symbol:"none",lineStyle:{width:1,color:"#3b82f6"}},
      {name:"MA20",type:"line",xAxisIndex:0,yAxisIndex:0,data:d.ma20,symbol:"none",lineStyle:{width:1,color:"#a855f7"}},
      {name:"MA60",type:"line",xAxisIndex:0,yAxisIndex:0,data:d.ma60,symbol:"none",lineStyle:{width:1,color:"#22c55e"}},
      {name:"MA90",type:"line",xAxisIndex:0,yAxisIndex:0,data:d.ma90,symbol:"none",lineStyle:{width:1,color:"#f97316"}},
      {name:"MA120",type:"line",xAxisIndex:0,yAxisIndex:0,data:d.ma120,symbol:"none",lineStyle:{width:1,color:"#ec4899"}},
      {name:"VOL",type:"bar",xAxisIndex:1,yAxisIndex:1,data:d.vols.map((v,i)=>({value:v,itemStyle:{color:volColors[i]}}))},
      {name:"VOL_MA5",type:"line",xAxisIndex:1,yAxisIndex:1,data:d.volMA5,symbol:"none",lineStyle:{width:1,color:"#fbbf24"}},
      {name:"VOL_MA10",type:"line",xAxisIndex:1,yAxisIndex:1,data:d.volMA10,symbol:"none",lineStyle:{width:1,color:"#3b82f6"}},
      {name:"MACD",type:"bar",xAxisIndex:2,yAxisIndex:2,data:d.macd.macd.map((v,i)=>({value:v,itemStyle:{color:mcColors[i]}}))},
      {name:"DIF",type:"line",xAxisIndex:2,yAxisIndex:2,data:d.macd.dif,symbol:"none",lineStyle:{width:1,color:"#fbbf24"}},
      {name:"DEA",type:"line",xAxisIndex:2,yAxisIndex:2,data:d.macd.dea,symbol:"none",lineStyle:{width:1,color:"#3b82f6"}},
      {name:"K",type:"line",xAxisIndex:3,yAxisIndex:3,data:d.kdj.k,symbol:"none",lineStyle:{width:1,color:"#fbbf24"}},
      {name:"D",type:"line",xAxisIndex:3,yAxisIndex:3,data:d.kdj.d,symbol:"none",lineStyle:{width:1,color:"#3b82f6"}},
      {name:"J",type:"line",xAxisIndex:3,yAxisIndex:3,data:d.kdj.j,symbol:"none",lineStyle:{width:1,color:"#a855f7"}},
    ],
    graphic:[
      {type:"text",left:40,top:"59%",style:{text:"VOL",fill:"#9ca3af",fontSize:10}},
      {type:"text",left:40,top:"75%",style:{text:"MACD",fill:"#9ca3af",fontSize:10}},
      {type:"text",left:40,top:"91%",style:{text:"KDJ",fill:"#9ca3af",fontSize:10}},
    ],
  });

  // 手机触摸：单指移动十字线，不平移
  const chartDom = document.getElementById("mainChart");
  let touchStartX = 0, touchStartIdx = null, isPinching = false;
  chartDom.addEventListener("touchstart", function(e){
    e.stopImmediatePropagation();
    if(e.touches.length === 2){ isPinching = true; return; }
    isPinching = false;
    touchStartX = e.touches[0].clientX;
    const idx = getIndexByX(touchStartX);
    touchStartIdx = idx;
    if(idx!=null) showCross(idx);
  }, {passive:false,capture:true});
  chartDom.addEventListener("touchmove", function(e){
    if(e.touches.length >= 2){ isPinching = true; return; }
    e.stopImmediatePropagation();
    if(isPinching) return;
    e.preventDefault();
    const x = e.touches[0].clientX;
    const idx = getIndexByX(x);
    if(idx!=null && idx !== touchStartIdx){
      touchStartIdx = idx;
      showCross(idx);
    }
  }, {passive:false,capture:true});
  chartDom.addEventListener("touchend", function(){ isPinching=false; }, {passive:true});
  // PC端鼠标移动十字线
  chartDom.addEventListener("mousemove", function(e){
    if(isPinching) return;
    const idx = getIndexByX(e.clientX);
    if(idx!=null) showCross(idx);
  });

  function getIndexByX(clientX){
    const rect = chartDom.getBoundingClientRect();
    const x = clientX - rect.left;
    const opt = mainChart.getOption();
    const dz = opt.dataZoom[0];
    const total = d.dates.length;
    const startI = Math.floor(total * dz.start/100);
    const endI = Math.floor(total * dz.end/100);
    const visible = endI - startI;
    const plotLeft = 55, plotRight = rect.width - 55;
    const ratio = (x - plotLeft)/(plotRight - plotLeft);
    const idx = startI + Math.round(ratio * (visible-1));
    return Math.max(startI, Math.min(endI-1, idx));
  }
  function showCross(idx){
    mainChart.dispatchAction({type:"showTip",seriesIndex:0,dataIndex:idx});
    // 更新顶部价格栏为选中K线数据
    const bars = chartData.bars;
    if(bars && bars[idx]){
      const b = bars[idx];
      const prev = idx>0 ? bars[idx-1].close : b.open;
      const chg = b.close - prev;
      const pct = prev ? chg/prev*100 : 0;
      const up = chg >= 0;
      const pe=document.getElementById("qPrice");
      pe.textContent=b.close.toFixed(2); pe.className="q-price "+(up?"up":"down");
      const ce=document.getElementById("qChg");
      ce.textContent=(up?"+":"")+chg.toFixed(2)+" "+(up?"+":"")+pct.toFixed(2)+"%";
      ce.className="q-chg "+(up?"up up-bg":"down down-bg");
      document.getElementById("qStats").innerHTML =
        '<span class="item">日期<span>'+b.date+'</span></span>'+
        '<span class="item">今开<span>'+b.open.toFixed(2)+'</span></span>'+
        '<span class="item">最高<span>'+b.high.toFixed(2)+'</span></span>'+
        '<span class="item">最低<span>'+b.low.toFixed(2)+'</span></span>'+
        '<span class="item">昨收<span>'+prev.toFixed(2)+'</span></span>'+
        '<span class="item">成交量<span>'+fmtVol(b.volume)+'</span></span>'+
        '<span class="item">总市值<span>'+((window._lastQuote||{}).total_mv?fmtMv(window._lastQuote.total_mv):'--')+'</span></span>';
    }
  }

  // 左右平移箭头（小按钮，K线框内底部）
  chartDom.style.position = "relative";
  if(!document.getElementById("btnLeft")){
    const bl=document.createElement("button");
    bl.id="btnLeft";bl.textContent="◀";
    bl.style.cssText="position:absolute;top:44%;left:100px;z-index:99;background:rgba(31,41,55,0.7);color:#9ca3af;border:1px solid #374151;border-radius:3px;width:24px;height:20px;font-size:11px;cursor:pointer;padding:0;line-height:1;";
    chartDom.appendChild(bl);
    const br=document.createElement("button");
    br.id="btnRight";br.textContent="▶";
    br.style.cssText="position:absolute;top:44%;right:100px;z-index:99;background:rgba(31,41,55,0.7);color:#9ca3af;border:1px solid #374151;border-radius:3px;width:24px;height:20px;font-size:11px;cursor:pointer;padding:0;line-height:1;";
    chartDom.appendChild(br);
  }
  function pan(step){
    const opt = mainChart.getOption();
    const dz0 = opt.dataZoom[0];
    let s = dz0.start - step, e = dz0.end - step;
    if(s < 0){s=0;e=dz0.end-dz0.start;}
    if(e > 100){e=100;s=100-(dz0.end-dz0.start);}
    mainChart.dispatchAction({type:"dataZoom",start:s,end:e});
  }
  document.getElementById("btnLeft").onclick=()=>pan(8);
  document.getElementById("btnRight").onclick=()=>pan(-8);

  // 2. 可视范围最高价标记
  function markVisibleHigh(){
    const opt = mainChart.getOption();
    const dz0 = opt.dataZoom[0];
    const total = d.dates.length;
    const si = Math.floor(total*dz0.start/100);
    const ei = Math.min(total-1, Math.floor(total*dz0.end/100));
    let hi = -Infinity, hiIdx = si;
    for(let i=si;i<=ei;i++){ if(d.highs[i]>hi){hi=d.highs[i];hiIdx=i;} }
    mainChart.setOption({series:[{
      name:"K线",
      markPoint:{
        symbol:"circle",symbolSize:6,
        data:[{xAxis:d.dates[hiIdx],yAxis:hi,value:hi.toFixed(2)}],
        label:{show:true,formatter:"{c}",position:"top",fontSize:10,color:"#f59e0b",fontWeight:"bold"},
        itemStyle:{color:"#f59e0b"},
      }
    }]});
  }
  markVisibleHigh();
  mainChart.on("dataZoom", markVisibleHigh);

  window.onresize=()=>{mainChart&&mainChart.resize();};
}

// 默认加载
(function(){
  const p=new URLSearchParams(location.search);
  loadStock(p.get("code")||"sh600519", p.get("name")||"贵州茅台");
})();
