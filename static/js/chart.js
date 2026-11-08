// ============================================
// 板块颜色配置
// ============================================
const SECTOR_COLORS = {
  '机器人':     '#ff6b6b',  // 红
  'AI应用':     '#4ecdc4',  // 青
  'CPO':        '#45b7d1',  // 蓝
  '光纤':       '#96ceb4',  // 浅绿
  '白酒':       '#feca57',  // 黄
  '创新药':     '#ff9ff3',  // 粉
  '油气资源':   '#f368e0',  // 紫粉
  '证券':       '#a29bfe',  // 紫
  '液冷':       '#00d2d3',  // 亮青
  '军工':       '#54a0ff',  // 亮蓝
  '稀土永磁':   '#5f27cd',  // 深紫
  '商业航天':   '#ff9f43',  // 橙
  '化工':       '#10ac84',  // 深绿
  '锂矿':       '#ee5253',  // 深红
  '电池':       '#ff7675',  // 浅红
  '有色金属':   '#e17055',  // 砖红
  '电网':       '#00b894',  // 翠绿
  '光伏':       '#00cec9',  // 薄荷青
  '储能':       '#6c5ce7',  // 靛蓝
  '半导体':     '#fd79a8',  // 玫红
  '先进封装':   '#e84393',  // 深粉
  '玻璃基板':   '#fdcb6e',  // 金黄
  '算力概念':   '#e84118',  // 橙红
  'PCB':        '#0097e6',  // 天蓝
  '微盘股':     '#7f8c8d',  // 灰
  '培育钻石':   '#c0392b',  // 暗红
  '电力':       '#f1c40f',  // 亮黄
  '电网概念':   '#2ecc71',  // 草绿
  '光伏概念':   '#27ae60',  // 深绿
  '固态电池':   '#9b59b6',  // 紫
  '锂矿概念':   '#d35400',  // 橙棕
  '化工原料':   '#16a085',  // 青绿
  'CPO概念':    '#45b7d1',  // 蓝（同CPO）
  '光纤概念':   '#96ceb4',  // 浅绿（同光纤）
  '液冷服务器': '#00d2d3',  // 亮青（同液冷）
  '储能概念':   '#6c5ce7',  // 靛蓝（同储能）
};

const DEFAULT_COLOR = '#6b7280';

function getColor(name) {
  return SECTOR_COLORS[name] || DEFAULT_COLOR;
}

// ============================================
// 自定义插件：右侧等间距标签 + 斜线连接
// ============================================
let hoverIdx = -1;
const fundFlowPlugin = {
  id: 'fundFlowPlugin',

  afterDatasetsDraw(chart) {
    const { ctx, chartArea: { top, bottom, left, right } } = chart;
    const datasets = chart.data.datasets;
    if (!datasets.length) return;

    ctx.save();

    // ---- 1. 零线 ----
    const zeroY = chart.scales.y.getPixelForValue(0);
    ctx.beginPath();
    ctx.strokeStyle = 'rgba(0,0,0,0.2)';
    ctx.lineWidth = 1;
    ctx.setLineDash([5, 4]);
    ctx.moveTo(left, zeroY);
    ctx.lineTo(right + 8, zeroY);
    ctx.stroke();
    ctx.setLineDash([]);

    // ---- 2. 收集数据点并按数值排序 ----
    const items = datasets
      .map((ds, idx) => {
        const meta = chart.getDatasetMeta(idx);
        if (!meta.data.length) return null;
        // 找到最后一个有数据的点
        let lastDataIdx = -1;
        for (let i = ds.data.length - 1; i >= 0; i--) {
          if (ds.data[i] !== null && ds.data[i] !== undefined) {
            lastDataIdx = i;
            break;
          }
        }
        if (lastDataIdx < 0) return null;
        const point = meta.data[lastDataIdx];
        const val = ds.data[lastDataIdx];
        const color = getColor(ds.label);
        return {
          ds, idx, val,
          pointX: point.x,
          pointY: point.y,
          color: color,
          changePct: sectorChangePct[ds.label] !== undefined ? sectorChangePct[ds.label] : null,
          text: isMobile ? `${ds.label} ${val.toFixed(1)}亿(${(sectorChangePct[ds.label] || 0).toFixed(1)}%)` : `${ds.label} ${val >= 0 ? '+' : ''}${val.toFixed(2)}亿(${(sectorChangePct[ds.label] >= 0 ? '+' : '')}${(sectorChangePct[ds.label] || 0).toFixed(2)}%)`
        };
      })
      .filter(Boolean);

    // 根据排序模式排序
    if (sortMode === 'fund') {
      items.sort((a, b) => b.val - a.val);
    } else {
      items.sort((a, b) => (b.changePct || 0) - (a.changePct || 0));
    }



    // ---- 3. 计算标签尺寸 ----
    const fontSize = isMobile ? 8 : 14;
    ctx.font = `bold ${fontSize}px "PingFang SC", "Microsoft YaHei"`;
    const maxTextW = Math.max(...items.map(i => ctx.measureText(i.text).width));
    const boxW = maxTextW + (isMobile ? 8 : 12);
    const boxH = isMobile ? 14 : 24;

    // ---- 4. 标签固定在右边 ----
    // 标签统一对齐到曲线最右端右边，垂直等间距排列
    let maxPointX = left;
    items.forEach(item => {
      if (item.pointX > maxPointX) maxPointX = item.pointX;
    });
    const labelX = maxPointX + 10;

    // 垂直等间距分布
    const totalH = bottom - top;
    const gap = totalH / (items.length + 1);
    
    items.forEach((item, i) => {
      item.finalY = top + gap * (i + 1);
      item.boxX = labelX;
      item.boxY = item.finalY - boxH / 2;
      item.boxW = boxW;
      item.boxH = boxH;
    });

    // ---- 5. 绘制短斜线连接 ----
    items.forEach(item => {
      const { pointX, pointY, finalY, color, boxX } = item;
      const isHover = item.idx === hoverIdx;
      ctx.globalAlpha = isHover ? 1 : (hoverIdx >= 0 ? 0.15 : 0.6);
      
      ctx.beginPath();
      ctx.arc(pointX, pointY, isHover ? 4 : 3, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();

      ctx.beginPath();
      ctx.strokeStyle = color;
      ctx.lineWidth = isHover ? 2 : 1;
      ctx.moveTo(pointX, pointY);
      ctx.lineTo(boxX - 4, finalY);
      ctx.stroke();
      ctx.globalAlpha = 1;
    });

    // ---- 6. 绘制标签 ----
    items.forEach(item => {
      const { text, color, boxX, boxY, boxW, boxH, finalY } = item;

      // 圆角背景框
      ctx.beginPath();
      roundRect(ctx, boxX, boxY, boxW, boxH, 4);
      ctx.fillStyle = '#111827';
      ctx.fill();
      ctx.strokeStyle = color;
      ctx.lineWidth = (item.idx === hoverIdx) ? 3 : 1.5;
      ctx.stroke();

      // 文字
      ctx.fillStyle = color;
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      ctx.fillText(text, boxX + 8, finalY + 0.5);
    });

    chart._labelItems = items;
    ctx.restore();
  }
};

function roundRect(ctx, x, y, w, h, r) {
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

// ============================================
// 生成完整时间轴（9:15 - 15:00）
// ============================================
function generateFullTimeLabels() {
  const labels = [];
  // 上午：9:15 - 11:30
  for (let h = 9; h <= 11; h++) {
    for (let m = 0; m < 60; m++) {
      if (h === 9 && m < 15) continue;
      if (h === 11 && m > 30) break;
      labels.push(`${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`);
    }
  }
  // 下午：13:00 - 15:00
  for (let h = 13; h <= 15; h++) {
    for (let m = 0; m < 60; m++) {
      if (h === 15 && m > 0) break;
      labels.push(`${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`);
    }
  }
  return labels;
}

const fullTimeLabels = generateFullTimeLabels();
const isMobile = window.innerWidth < 768;
let sortMode = 'fund';  // fund: 按资金排序, pct: 按涨跌幅排序

// ============================================
// 主图表
// ============================================
const ctx = document.getElementById('fundChart').getContext('2d');

const chart = new Chart(ctx, {
  type: 'line',
  data: { labels: fullTimeLabels, datasets: [] },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 500, easing: 'easeOutQuart' },
    layout: { 
      padding: { 
        right: isMobile ? 120 : 240, 
        top: 10, 
        bottom: 10, 
        left: 10 
      } 
    },
    plugins: { legend: { display: false }, tooltip: { enabled: false } },
    interaction: { mode: 'nearest', intersect: true },
    scales: {
      x: {
        type: 'category',
        grid: { display: false },
        ticks: { 
          color: 'rgba(255,255,255,0.4)', 
          font: { size: 11 }, 
          maxRotation: 0, 
          autoSkip: false,
          callback: function(value, index) {
            var label = this.getLabelForValue(value);
            if (label && (label.endsWith(':30') || label === '15:00')) return label;
            return '';
          }
        },
        border: { display: false }
      },
      y: {
        position: 'left',
        grid: { color: 'rgba(255,255,255,0.06)', drawBorder: false },
        ticks: { 
          color: 'rgba(255,255,255,0.4)', 
          font: { size: isMobile ? 9 : 12 }, 
          padding: 4, 
          callback(v) { return isMobile ? v : v + '亿'; } 
        },
        border: { display: false }
      }
    },
    elements: {
      line: { tension: 0.3, borderWidth: 2 },
      point: { radius: 0, hitRadius: 6, hoverRadius: 3 }
    }
  },
  plugins: [fundFlowPlugin]
});

// ============================================
// 数据加载
// ============================================
let lastLen = 0;
let sectorChangePct = {};  // 各板块涨跌幅

function updateStats(sectors) {
  // 顶部统计卡片已删除，此函数暂时为空
}

function updateTime() {
  const now = new Date();
  const h = String(now.getHours()).padStart(2, '0');
  const m = String(now.getMinutes()).padStart(2, '0');
  var el = document.getElementById('currentTime'); if(el) el.textContent = `${h}:${m}`;
}

async function loadChangePct() {
  try {
    const res = await fetch('/api/sector-change-pct');
    const json = await res.json();
    sectorChangePct = json.data || {};
  } catch (e) {
    console.error('涨跌幅加载失败:', e);
  }
}

async function loadData() {
  try {
    const res = await fetch('/api/data' + (currentDate ? '?date=' + currentDate : ''));
    const json = await res.json();
    
    const data = json.data || {};
    const times = data.times || [];
    const sectors = data.sectors || {};

    // 更新标题日期
    if (json.date) {
      const d = json.date;
      const titleEl = document.getElementById('fundTitle');
      if (titleEl) titleEl.textContent = parseInt(d.slice(5,7)) + '月' + parseInt(d.slice(8,10)) + '日 资金流向';
    }

    // 更新时间
    updateTime();
    
    // 空数据处理：彻底清空所有线条和标题
    if (times.length === 0 || Object.keys(sectors).length === 0) {
      document.getElementById('loading').style.display = 'none';
      document.getElementById('emptyState').style.display = 'block';
      // 重置标题（不显示昨天的日期）
      const titleEl = document.getElementById('fundTitle');
      if (titleEl) titleEl.textContent = '资金流向';
      // 彻底清空图表数据
      chart.data.labels = [];
      chart.data.datasets = [];
      chart.update('none');
      lastLen = 0;
      return;
    }
    
    // 有数据时隐藏空状态
    document.getElementById('emptyState').style.display = 'none';
    
    document.getElementById('emptyState').style.display = 'none';
    document.getElementById('loading').style.display = 'none';

    // 首次加载，初始化图表
    if (chart.data.datasets.length === 0) {
      for (const name in sectors) {
        const color = getColor(name);
        const mappedData = fullTimeLabels.map(label => {
          const idx = times.indexOf(label);
          return idx >= 0 ? sectors[name][idx] : null;
        });
        chart.data.datasets.push({
          label: name,
          data: mappedData,
          borderColor: color,
          backgroundColor: color,
          borderWidth: isMobile ? 2.5 : 2,
          spanGaps: true
        });
      }
      lastLen = times.length;
      chart.update();
      updateStats(sectors);
      return;
    }

    // 增量更新：更新每个时间点的数据
    times.forEach((t, idx) => {
      const labelIdx = fullTimeLabels.indexOf(t);
      if (labelIdx >= 0) {
        Object.keys(sectors).forEach((name, i) => {
          if (chart.data.datasets[i]) {
            chart.data.datasets[i].data[labelIdx] = sectors[name][idx];
          }
        });
      }
    });
    lastLen = times.length;
    chart.update('none');
    updateStats(sectors);
  } catch (e) {
    console.error('数据加载失败:', e);
  }
}



// ===== 日历日期选择 =====
let currentDate = '';
let availDates = new Set();
let calYear, calMonth;

async function loadAvailDates() {
  try {
    const r = await fetch('/api/dates');
    const j = await r.json();
    availDates = new Set(j.dates || []);
  } catch(e) {}
}

function renderCalendar() {
  const title = document.getElementById('calendarTitle');
  const daysEl = document.getElementById('calendarDays');
  if (!title || !daysEl) return;
  title.textContent = calYear + '-' + String(calMonth+1).padStart(2,'0');
  daysEl.innerHTML = '';
  const firstDay = new Date(calYear, calMonth, 1).getDay();
  const daysInMonth = new Date(calYear, calMonth+1, 0).getDate();
  for (let i=0; i<firstDay; i++) daysEl.appendChild(document.createElement('div'));
  for (let d=1; d<=daysInMonth; d++) {
    const div = document.createElement('div');
    div.style.cssText = 'padding:6px 0;font-size:12px;cursor:pointer;border-radius:6px;position:relative;color:#9ca3af;';
    div.textContent = d;
    const dateStr = calYear + '-' + String(calMonth+1).padStart(2,'0') + '-' + String(d).padStart(2,'0');
    if (availDates.has(dateStr)) {
      div.style.color = '#fff';
      div.style.background = '#374151';
      const dot = document.createElement('span');
      dot.style.cssText = 'position:absolute;bottom:2px;left:50%;transform:translateX(-50%);width:4px;height:4px;border-radius:50%;background:#ef4444;';
      div.appendChild(dot);
      div.onclick = function() {
        currentDate = dateStr;
        document.getElementById('dateBtn').textContent = '📅 ' + dateStr;
        document.getElementById('calendarModal').style.display = 'none';
        chart.data.datasets = [];
        loadData();
      };
    } else {
      div.onclick = function() { div.style.opacity = '0.5'; };
    }
    daysEl.appendChild(div);
  }
}

function initCalendar() {
  const now = new Date();
  calYear = now.getFullYear();
  calMonth = now.getMonth();
  loadAvailDates().then(() => {
    renderCalendar();
    const btn = document.getElementById('dateBtn');
    const modal = document.getElementById('calendarModal');
    if (btn && modal) {
      btn.onclick = function() {
        modal.style.display = 'flex';
        renderCalendar();
      };
      modal.onclick = function(e) { if (e.target === modal) modal.style.display = 'none'; };
      document.getElementById('prevMonth').onclick = function() {
        calMonth--; if (calMonth<0) { calMonth=11; calYear--; }
        renderCalendar();
      };
      document.getElementById('nextMonth').onclick = function() {
        calMonth++; if (calMonth>11) { calMonth=0; calYear++; }
        renderCalendar();
      };
    }
  });
}
initCalendar();
loadData();
loadChangePct();
setInterval(function(){ if(!currentDate) loadData(); }, 2000);
setInterval(loadChangePct, 5000);
setInterval(updateTime, 1000);

// 排序下拉框
document.getElementById('sortSelect').addEventListener('change', (e) => {
  sortMode = e.target.value;
  chart.update();
});

window.addEventListener("resize", function(){ chart.resize(); });
setTimeout(function(){ chart.resize(); }, 500);


// Hover高亮：检测鼠标在哪个标签/线上
const canvas = document.getElementById('fundChart');
canvas.addEventListener('mousemove', function(e) {
  const rect = canvas.getBoundingClientRect();
  const mx = e.clientX - rect.left;
  const my = e.clientY - rect.top;
  const items = chart._labelItems || [];
  let found = -1;
  // 检测是否在标签框上
  for (const item of items) {
    if (mx >= item.boxX && mx <= item.boxX + item.boxW &&
        my >= item.boxY && my <= item.boxY + item.boxH) {
      found = item.idx;
      break;
    }
  }
  // 检测是否在线上：遍历每条线的数据点，找最近的
  if (found < 0) {
    let minDist = 10;
    const rect = canvas.getBoundingClientRect();
    for (let ds = 0; ds < chart.data.datasets.length; ds++) {
      const meta = chart.getDatasetMeta(ds);
      for (const pt of meta.data) {
        if (pt.x < chart.chartArea.left || pt.x > chart.chartArea.right) continue;
        const dx = pt.x - mx;
        const dy = pt.y - my;
        const d = Math.sqrt(dx*dx + dy*dy);
        if (d < minDist) { minDist = d; found = ds; }
      }
    }
  }
  if (found !== hoverIdx) {
    hoverIdx = found;
    chart.update('none');
  }
  canvas.style.cursor = found >= 0 ? 'pointer' : 'default';
});
canvas.addEventListener('mouseleave', function() {
  if (hoverIdx >= 0) {
    hoverIdx = -1;
    chart.update('none');
  }
});
