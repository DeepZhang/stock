function renderRank(containerId, list, valueKey, isPercent) {
  const container = document.getElementById(containerId);
  if (!list || list.length === 0) {
    container.innerHTML = '<div class="loading">暂无数据</div>';
    return;
  }
  container.innerHTML = list.map((item, idx) => {
    const num = idx + 1;
    let numClass = 'normal';
    if (num === 1) numClass = 'top1';
    else if (num === 2) numClass = 'top2';
    else if (num === 3) numClass = 'top3';

    const val = item[valueKey];
    const isUp = val >= 0;
    const cls = isUp ? 'up' : 'down';
    const sign = isUp ? '+' : '';
    const valText = isPercent
      ? `${sign}${val.toFixed(2)}%`
      : `${sign}${val.toFixed(2)}亿`;

    let code = item.code || '';
    if (code && !code.startsWith('BK') && !code.startsWith('sh') && !code.startsWith('sz') && !code.startsWith('bj')) {
      code = code.startsWith('6') ? 'sh' + code : (code.startsWith('9') ? 'bj' + code : 'sz' + code);
    }
    const href = code ? `/kline?code=${encodeURIComponent(code)}&name=${encodeURIComponent(item.name||'')}` : '#';
    return `
      <div class="rank-item" style="cursor:pointer;" onclick="location.href='${href}'">
        <div class="rank-num ${numClass}">${num}</div>
        <div class="rank-name">${item.name}</div>
        <div class="rank-value ${cls}">${valText}</div>
      </div>
    `;
  }).join('');
}

async function loadRank() {
  try {
    // 板块资金流入流出
    const upFund = await fetch('/api/sector-rank?sort_by=fund_flow&order=desc&limit=10').then(r => r.json());
    renderRank('rankUpFund', upFund.list, 'fund_flow', false);

    const downFund = await fetch('/api/sector-rank?sort_by=fund_flow&order=asc&limit=10').then(r => r.json());
    renderRank('rankDownFund', downFund.list, 'fund_flow', false);

    // 个股资金流入流出
    const stockIn = await fetch('/api/stock-fund-rank?order=desc&limit=10').then(r => r.json());
    renderRank('rankStockIn', stockIn.list, 'fund_flow', false);

    const stockOut = await fetch('/api/stock-fund-rank?order=asc&limit=10').then(r => r.json());
    renderRank('rankStockOut', stockOut.list, 'fund_flow', false);

    // 板块涨跌幅
    const upPct = await fetch('/api/sector-rank?sort_by=change_pct&order=desc&limit=10').then(r => r.json());
    renderRank('rankUpPct', upPct.list, 'change_pct', true);

    const downPct = await fetch('/api/sector-rank?sort_by=change_pct&order=asc&limit=10').then(r => r.json());
    renderRank('rankDownPct', downPct.list, 'change_pct', true);

  } catch (e) {
    console.error('加载失败:', e);
  }
}

loadRank();
setInterval(loadRank, 30000);
