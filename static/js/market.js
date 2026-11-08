async function loadMarketData() {
  try {
    const res = await fetch('/api/market-overview');
    const data = await res.json();

    document.getElementById('updateTime').textContent = '更新时间: ' + data.update_time;
    const stats = data.limit_stats;
    const total = stats.total || 1;

    // ========== 三大指数卡片 ==========
    const indexGrid = document.getElementById('indexGrid');
    indexGrid.innerHTML = '';
    data.indices.forEach(idx => {
      const isUp = idx.change_pct >= 0;
      const cls = isUp ? 'up' : 'down';
      const sign = isUp ? '+' : '';
      const card = document.createElement('div');
      card.className = 'index-card';
      card.innerHTML = `
        <div class="name">${idx.name}</div>
        <div class="price ${cls}">${idx.price.toFixed(2)}</div>
        <div class="change ${cls}">${sign}${idx.change.toFixed(2)} (${sign}${idx.change_pct.toFixed(2)}%)</div>
      `;
      indexGrid.appendChild(card);
    });

    // ========== 情绪分数计算 ==========
    const factors = [];

    const ldRatio = stats.limit_up / Math.max(stats.limit_down, 1);
    let ldScore;
    if (ldRatio >= 5) ldScore = 100;
    else if (ldRatio >= 3) ldScore = 70;
    else if (ldRatio >= 1.5) ldScore = 50;
    else if (ldRatio >= 1) ldScore = 30;
    else if (ldRatio >= 0.5) ldScore = 15;
    else ldScore = 5;
    factors.push({
      name: '涨跌停比', score: ldScore, weight: 0.2,
      desc: ldRatio >= 5 ? '涨停远多于跌停，情绪高涨' :
            ldRatio >= 3 ? '涨停较多，情绪尚可' :
            ldRatio >= 1.5 ? '涨跌停均衡，情绪中性' :
            ldRatio >= 1 ? '跌停偏多，情绪偏弱' :
            '跌停多于涨停，情绪低迷'
    });

    const upRatio = stats.up_count / total;
    let upScore;
    if (upRatio >= 0.7) upScore = 100;
    else if (upRatio >= 0.6) upScore = 80;
    else if (upRatio >= 0.5) upScore = 60;
    else if (upRatio >= 0.35) upScore = 35;
    else if (upRatio >= 0.25) upScore = 15;
    else upScore = 5;
    factors.push({
      name: '上涨家数占比', score: upScore, weight: 0.4,
      desc: upRatio >= 0.7 ? '普涨行情，赚钱效应好' :
            upRatio >= 0.6 ? '多数个股上涨，情绪尚可' :
            upRatio >= 0.5 ? '涨跌各半，结构性行情' :
            upRatio >= 0.35 ? '多数个股下跌，情绪偏弱' :
            upRatio >= 0.25 ? '普跌行情，亏钱效应明显' :
            '极端普跌，情绪冰点'
    });

    const limitUp = stats.limit_up;
    let luScore;
    if (limitUp >= 80) luScore = 100;
    else if (limitUp >= 50) luScore = 75;
    else if (limitUp >= 30) luScore = 50;
    else if (limitUp >= 15) luScore = 25;
    else luScore = 10;
    factors.push({
      name: '涨停家数', score: luScore, weight: 0.2,
      desc: limitUp >= 80 ? '涨停潮，市场热度极高' :
            limitUp >= 50 ? '涨停活跃，资金追涨意愿强' :
            limitUp >= 30 ? '涨停适中，市场情绪一般' :
            limitUp >= 15 ? '涨停较少，市场偏冷清' :
            '涨停稀少，情绪冰点'
    });

    const limitDown = stats.limit_down;
    let ldScore2;
    if (limitDown <= 5) ldScore2 = 100;
    else if (limitDown <= 15) ldScore2 = 70;
    else if (limitDown <= 30) ldScore2 = 45;
    else if (limitDown <= 50) ldScore2 = 25;
    else ldScore2 = 10;
    factors.push({
      name: '跌停家数', score: ldScore2, weight: 0.2,
      desc: limitDown <= 5 ? '几乎无跌停，风险很低' :
            limitDown <= 15 ? '跌停较少，风险可控' :
            limitDown <= 30 ? '跌停适中，需留意风险' :
            limitDown <= 50 ? '跌停偏多，注意控制仓位' :
            '跌停潮，风险释放中'
    });

    const totalScore = Math.round(
      factors.reduce((sum, f) => sum + f.score * f.weight, 0)
    );

    // ========== 情绪仪表盘 ==========
    const circumference = 2 * Math.PI * 66;
    const offset = circumference * (1 - totalScore / 100);
    const gaugeFg = document.getElementById('gaugeFg');
    gaugeFg.style.strokeDasharray = circumference;
    gaugeFg.style.strokeDashoffset = offset;

    let color, level;
    if (totalScore >= 80) {
      color = '#ef4444'; level = '🔥 情绪高涨';
    } else if (totalScore >= 60) {
      color = '#f97316'; level = '😊 情绪偏暖';
    } else if (totalScore >= 40) {
      color = '#fbbf24'; level = '😐 情绪中性';
    } else if (totalScore >= 20) {
      color = '#3b82f6'; level = '😔 情绪偏弱';
    } else {
      color = '#8b5cf6'; level = '🥶 情绪冰点';
    }

    gaugeFg.style.stroke = color;
    document.getElementById('gaugeScore').textContent = totalScore;
    document.getElementById('gaugeScore').style.color = color;
    document.getElementById('heroLevel').textContent = level;
    document.getElementById('heroLevel').style.color = color;
    document.getElementById('heroDesc').textContent =
      totalScore >= 60 ? '市场做多情绪较强，涨停活跃，可关注主线板块。' :
      totalScore >= 40 ? '市场涨跌分化，结构性行情，操作宜谨慎。' :
      '市场情绪低迷，下跌个股较多，注意控制仓位。';

    // ========== 涨跌家数条 ==========
    const upPct = (upRatio * 100).toFixed(1);
    const downPct = (stats.down_count / total * 100).toFixed(1);
    const flatPct = (stats.flat_count / total * 100).toFixed(1);
    document.getElementById('breadthBar').innerHTML = `
      <div class="breadth-up" style="width:${upPct}%"></div>
      <div class="breadth-flat" style="width:${flatPct}%"></div>
      <div class="breadth-down" style="width:${downPct}%"></div>
    `;
    document.getElementById('upCount').textContent = stats.up_count;
    document.getElementById('downCount').textContent = stats.down_count;
    document.getElementById('flatCount').textContent = stats.flat_count;

    // ========== 涨跌停统计卡片 ==========
    document.getElementById('limitUpVal').textContent = limitUp;
    document.getElementById('limitDownVal').textContent = limitDown;

    // 连板高度暂时隐藏（数据字段错误）
    // const height = data.limit_height || {};
    // document.getElementById('maxHeightVal').textContent = height.max_height + ' 板';
    // document.getElementById('maxHeightSub').textContent = height.max_stock || '最高连板';

    document.getElementById('upRatioVal').textContent = upPct + '%';
    document.getElementById('upRatioVal').className = upRatio >= 0.5 ? 'up' : 'down';

    // ========== 市场资金分类 ==========
    const fund = data.fund_flow;
    if (fund) {
      document.getElementById('fundFlowGrid').style.display = 'grid';
      
      const institution = fund.super_net + fund.large_net;
      const hotMoney = fund.medium_net;
      const retail = fund.small_net;
      const main = fund.main_net;
      
      const fmt = (v) => {
        const cls = v >= 0 ? 'up' : 'down';
        return `${v >= 0 ? '+' : ''}${v.toFixed(1)}亿`;
      };
      
      document.getElementById('institutionVal').innerHTML = fmt(institution);
      document.getElementById('institutionVal').className = 'value ' + (institution >= 0 ? 'up' : 'down');
      
      document.getElementById('hotMoneyVal').innerHTML = fmt(hotMoney);
      document.getElementById('hotMoneyVal').className = 'value ' + (hotMoney >= 0 ? 'up' : 'down');
      
      document.getElementById('retailVal').innerHTML = fmt(retail);
      document.getElementById('retailVal').className = 'value ' + (retail >= 0 ? 'up' : 'down');
      
      document.getElementById('mainVal').innerHTML = fmt(main);
      document.getElementById('mainVal').className = 'value ' + (main >= 0 ? 'up' : 'down');
    }

    // ========== 复盘要点卡片 ==========
    const flow = data.flow_analysis || {};

    // 连续流入
    const ciEl = document.getElementById('consecutiveInflow');
    if (flow.consecutive_inflow && flow.consecutive_inflow.length > 0) {
      ciEl.innerHTML = flow.consecutive_inflow.map(([name, today, yesterday]) => `
        <div class="review-item">
          <span class="name">${name}</span>
          <span class="val up">+${today.toFixed(2)}亿</span>
        </div>
      `).join('');
    } else {
      ciEl.innerHTML = '<div style="color:#6b7280;font-size:13px;padding:8px 0;">暂无连续流入数据</div>';
    }

    // 单日爆量流入
    const siEl = document.getElementById('surgeInflow');
    if (flow.surge_inflow && flow.surge_inflow.length > 0) {
      siEl.innerHTML = flow.surge_inflow.map(([name, val]) => `
        <div class="review-item">
          <span class="name">${name}</span>
          <span class="val up">+${val.toFixed(2)}亿</span>
        </div>
      `).join('');
    } else {
      siEl.innerHTML = '<div style="color:#6b7280;font-size:13px;padding:8px 0;">暂无爆量流入数据</div>';
    }

    // 连续流出
    const coEl = document.getElementById('consecutiveOutflow');
    if (flow.consecutive_outflow && flow.consecutive_outflow.length > 0) {
      coEl.innerHTML = flow.consecutive_outflow.map(([name, today, yesterday]) => `
        <div class="review-item">
          <span class="name">${name}</span>
          <span class="val down">${today.toFixed(2)}亿</span>
        </div>
      `).join('');
    } else {
      coEl.innerHTML = '<div style="color:#6b7280;font-size:13px;padding:8px 0;">暂无连续流出数据</div>';
    }

    // ========== 情绪构成 ==========
    const factorList = document.getElementById('factorList');
    factorList.innerHTML = factors.map(f => `
      <div class="factor-item">
        <div class="factor-header">
          <span class="factor-name">${f.name} <span style="color:#6b7280;">(权重 ${f.weight * 100}%)</span></span>
          <span class="factor-score" style="color:${f.score >= 60 ? '#ef4444' : f.score >= 40 ? '#fbbf24' : '#22c55e'}">${f.score} 分</span>
        </div>
        <div class="factor-bar">
          <div class="factor-bar-fill" style="width:${f.score}%;background:${f.score >= 60 ? '#ef4444' : f.score >= 40 ? '#fbbf24' : '#22c55e'};"></div>
        </div>
        <div class="factor-desc">${f.desc}</div>
      </div>
    `).join('');

    // ========== 复盘结论 ==========
    let conclusion = '';
    if (totalScore >= 60) {
      conclusion = `
        <strong>今日市场情绪偏暖（${totalScore}分）</strong><br>
        ✅ 涨停 ${limitUp} 家 / 跌停 ${limitDown} 家，涨跌停比 ${ldRatio.toFixed(2)}:1<br>
        ✅ 上涨占比 ${upPct}%，市场广度较好<br>
        <strong>复盘要点：</strong><br>
        1. 情绪偏暖区间，可关注资金流入的主线板块<br>
        2. 涨停 ${limitUp} 家，${luScore >= 60 ? '资金追涨意愿较强' : '追涨意愿一般'}<br>
        3. 操作上可逢低布局，不宜追高，留意板块轮动
      `;
    } else if (totalScore >= 40) {
      conclusion = `
        <strong>今日市场情绪中性（${totalScore}分）</strong><br>
        ⚠️ 涨停 ${limitUp} 家 vs 跌停 ${limitDown} 家，涨跌停比 ${ldRatio.toFixed(2)}:1<br>
        ⚠️ 上涨占比 ${upPct}%，市场涨跌分化<br>
        <strong>复盘要点：</strong><br>
        1. 结构性行情，没有明确主线<br>
        2. 宜轻仓观望，避免频繁追涨杀跌<br>
        3. 等待情绪明朗后再加大仓位
      `;
    } else {
      conclusion = `
        <strong>今日市场情绪偏弱（${totalScore}分）</strong><br>
        ❌ 跌停 ${limitDown} 家，${limitDown > 30 ? '跌停潮出现，风险较高' : '跌停偏多'}<br>
        ❌ 上涨占比仅 ${upPct}%，亏钱效应明显<br>
        <strong>复盘要点：</strong><br>
        1. 建议控制仓位，以防守为主<br>
        2. 不宜盲目抄底，等待企稳信号<br>
        3. 关注明日是否出现情绪修复反弹
      `;
    }
    document.getElementById('conclusionText').innerHTML = conclusion;

  } catch (e) {
    console.error('加载失败:', e);
    document.getElementById('updateTime').textContent = '数据加载失败，请刷新重试';
  }
}

loadMarketData();
setInterval(loadMarketData, 60000);
