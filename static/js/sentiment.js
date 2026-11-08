async function loadSentiment() {
  try {
    // 获取行情总览数据
    const res = await fetch('/api/market-overview');
    const data = await res.json();

    document.getElementById('updateTime').textContent = '更新时间: ' + data.update_time;

    const stats = data.limit_stats;
    const total = stats.total || 1;

    // ========== 计算情绪分数 ==========
    const factors = [];

    // 因子1: 涨跌停比 (权重 30%)
    const ldRatio = stats.limit_up / Math.max(stats.limit_down, 1);
    let ldScore;
    if (ldRatio >= 5) ldScore = 100;
    else if (ldRatio >= 3) ldScore = 80;
    else if (ldRatio >= 1.5) ldScore = 60;
    else if (ldRatio >= 1) ldScore = 40;
    else if (ldRatio >= 0.5) ldScore = 20;
    else ldScore = 10;
    factors.push({
      name: '涨跌停比',
      score: ldScore,
      weight: 0.3,
      desc: ldRatio >= 5 ? '涨停远多于跌停，情绪高涨' :
            ldRatio >= 3 ? '涨停较多，情绪偏暖' :
            ldRatio >= 1.5 ? '涨跌停均衡，情绪中性' :
            ldRatio >= 1 ? '跌停偏多，情绪偏弱' :
            '跌停多于涨停，情绪低迷'
    });

    // 因子2: 上涨家数占比 (权重 25%)
    const upRatio = stats.up_count / total;
    let upScore;
    if (upRatio >= 0.65) upScore = 100;
    else if (upRatio >= 0.55) upScore = 80;
    else if (upRatio >= 0.45) upScore = 60;
    else if (upRatio >= 0.35) upScore = 40;
    else upScore = 20;
    factors.push({
      name: '上涨家数占比',
      score: upScore,
      weight: 0.25,
      desc: upRatio >= 0.65 ? '普涨行情，赚钱效应好' :
            upRatio >= 0.55 ? '多数个股上涨，情绪尚可' :
            upRatio >= 0.45 ? '涨跌各半，结构性行情' :
            upRatio >= 0.35 ? '多数个股下跌，情绪偏弱' :
            '普跌行情，亏钱效应明显'
    });

    // 因子3: 涨停家数绝对值 (权重 25%)
    const limitUp = stats.limit_up;
    let luScore;
    if (limitUp >= 80) luScore = 100;
    else if (limitUp >= 50) luScore = 80;
    else if (limitUp >= 30) luScore = 60;
    else if (limitUp >= 15) luScore = 40;
    else luScore = 20;
    factors.push({
      name: '涨停家数',
      score: luScore,
      weight: 0.25,
      desc: limitUp >= 80 ? '涨停潮，市场热度极高' :
            limitUp >= 50 ? '涨停活跃，资金追涨意愿强' :
            limitUp >= 30 ? '涨停适中，市场情绪一般' :
            limitUp >= 15 ? '涨停较少，市场偏冷清' :
            '涨停稀少，情绪冰点'
    });

    // 因子4: 跌停家数绝对值 (权重 20%)
    const limitDown = stats.limit_down;
    let ldScore2;
    if (limitDown <= 5) ldScore2 = 100;
    else if (limitDown <= 15) ldScore2 = 80;
    else if (limitDown <= 30) ldScore2 = 60;
    else if (limitDown <= 50) ldScore2 = 40;
    else ldScore2 = 20;
    factors.push({
      name: '跌停家数',
      score: ldScore2,
      weight: 0.2,
      desc: limitDown <= 5 ? '几乎无跌停，风险很低' :
            limitDown <= 15 ? '跌停较少，风险可控' :
            limitDown <= 30 ? '跌停适中，需留意风险' :
            limitDown <= 50 ? '跌停偏多，注意控制仓位' :
            '跌停潮，风险释放中'
    });

    // 总分
    const totalScore = Math.round(
      factors.reduce((sum, f) => sum + f.score * f.weight, 0)
    );

    // ========== 渲染仪表盘 ==========
    const circumference = 2 * Math.PI * 75; // ≈ 471.2
    const offset = circumference * (1 - totalScore / 100);

    const gaugeFg = document.getElementById('gaugeFg');
    gaugeFg.style.strokeDasharray = circumference;
    gaugeFg.style.strokeDashoffset = offset;

    // 颜色
    let color, level, desc;
    if (totalScore >= 80) {
      color = '#ef4444';
      level = '🔥 情绪高涨';
      desc = '市场做多情绪强烈，涨停潮出现，赚钱效应显著。可积极参与，但需警惕高位分歧。';
    } else if (totalScore >= 60) {
      color = '#f97316';
      level = '😊 情绪偏暖';
      desc = '市场情绪较好，涨停较多，多数个股上涨。可逢低布局，关注主线板块。';
    } else if (totalScore >= 40) {
      color = '#fbbf24';
      level = '😐 情绪中性';
      desc = '市场涨跌分化，结构性行情为主。操作上宜轻仓观望，等待方向明确。';
    } else if (totalScore >= 20) {
      color = '#3b82f6';
      level = '😔 情绪偏弱';
      desc = '市场下跌个股较多，跌停增加，赚钱效应差。建议控制仓位，谨慎操作。';
    } else {
      color = '#8b5cf6';
      level = '🥶 情绪冰点';
      desc = '市场恐慌性下跌，跌停潮出现。短期风险释放中，不宜抄底，等待企稳信号。';
    }

    gaugeFg.style.stroke = color;
    document.getElementById('gaugeScore').textContent = totalScore;
    document.getElementById('gaugeScore').style.color = color;
    document.getElementById('heroLevel').textContent = level;
    document.getElementById('heroLevel').style.color = color;
    document.getElementById('heroDesc').textContent = desc;

    // ========== 渲染指标卡片 ==========
    document.getElementById('limitUpVal').textContent = stats.limit_up;
    document.getElementById('limitDownVal').textContent = stats.limit_down;

    const ldRatioVal = (stats.limit_up / Math.max(stats.limit_down, 1)).toFixed(2) + ' : 1';
    document.getElementById('ldRatioVal').textContent = ldRatioVal;
    document.getElementById('ldRatioVal').className = ldRatio >= 2 ? 'up' : 'down';

    const upPct = (upRatio * 100).toFixed(1) + '%';
    document.getElementById('upRatioVal').textContent = upPct;
    document.getElementById('upRatioVal').className = upRatio >= 0.5 ? 'up' : 'down';

    // ========== 渲染情绪构成 ==========
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
        <div style="font-size:12px;color:#6b7280;margin-top:4px;">${f.desc}</div>
      </div>
    `).join('');

    // ========== 复盘结论 ==========
    let conclusion = '';
    if (totalScore >= 60) {
      conclusion = `
        <strong>今日市场情绪偏暖（${totalScore}分）</strong><br>
        ✅ 涨停家数 ${stats.limit_up} 家，跌停 ${stats.limit_down} 家，涨跌停比 ${ldRatioVal}<br>
        ✅ 上涨家数占比 ${upPct}，市场广度较好<br>
        <strong>复盘要点：</strong><br>
        1. 市场情绪处于偏暖区间，可关注资金流入的主线板块<br>
        2. 涨停家数 ${limitUp} 家，${luScore >= 60 ? '资金追涨意愿较强' : '追涨意愿一般'}<br>
        3. 操作上可逢低布局，但不宜追高，留意板块轮动节奏
      `;
    } else if (totalScore >= 40) {
      conclusion = `
        <strong>今日市场情绪中性（${totalScore}分）</strong><br>
        ⚠️ 涨停 ${stats.limit_up} 家 vs 跌停 ${stats.limit_down} 家，涨跌停比 ${ldRatioVal}<br>
        ⚠️ 上涨家数占比 ${upPct}，市场涨跌分化<br>
        <strong>复盘要点：</strong><br>
        1. 市场处于结构性行情，没有明确主线<br>
        2. 操作上宜轻仓观望，避免频繁追涨杀跌<br>
        3. 等待情绪明朗后再加大仓位
      `;
    } else {
      conclusion = `
        <strong>今日市场情绪偏弱（${totalScore}分）</strong><br>
        ❌ 跌停 ${stats.limit_down} 家，${limitDown > 30 ? '跌停潮出现，风险较高' : '跌停偏多'}<br>
        ❌ 上涨家数占比仅 ${upPct}，亏钱效应明显<br>
        <strong>复盘要点：</strong><br>
        1. 市场情绪低迷，建议控制仓位，以防守为主<br>
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

loadSentiment();
setInterval(loadSentiment, 60000); // 1分钟刷新一次
