
// ── CHARTS ──────────────────────────────────────────────────────────────────

let equityChart = null;
let donutChart = null;

function initEquityChart(data) {
  const categories = data.map(d => d.date);
  const values = data.map(d => d.pnl);
  const lastVal = values[values.length - 1] || 0;
  const color = lastVal >= 0 ? '#00e676' : '#ff4757';

  const options = {
    series: [{ name: 'PnL %', data: values }],
    chart: {
      type: 'area',
      height: 280,
      background: 'transparent',
      toolbar: { show: false },
      animations: { enabled: true, easing: 'easeinout', speed: 800 },
    },
    theme: { mode: 'dark' },
    colors: [color],
    fill: {
      type: 'gradient',
      gradient: {
        shadeIntensity: 1,
        opacityFrom: 0.5,
        opacityTo: 0.02,
        stops: [0, 100],
      },
    },
    stroke: { curve: 'smooth', width: 2.5 },
    dataLabels: { enabled: false },
    grid: {
      borderColor: 'rgba(255,255,255,0.05)',
      strokeDashArray: 4,
    },
    xaxis: {
      categories,
      labels: { style: { colors: '#475569', fontSize: '11px', fontFamily: 'JetBrains Mono' }, rotate: 0, hideOverlappingLabels: true },
      axisBorder: { show: false },
      axisTicks: { show: false },
    },
    yaxis: {
      labels: {
        style: { colors: '#475569', fontSize: '11px', fontFamily: 'JetBrains Mono' },
        formatter: v => v.toFixed(1) + '%',
      },
    },
    tooltip: {
      theme: 'dark',
      y: { formatter: v => v.toFixed(2) + '%' },
    },
    markers: { size: 0 },
  };

  if (equityChart) {
    equityChart.updateOptions(options);
  } else {
    equityChart = new ApexCharts(document.getElementById('equity-chart'), options);
    equityChart.render();
  }
}

function initDonutChart(won, lost) {
  if (won === 0 && lost === 0) { won = 1; lost = 1; }
  const options = {
    series: [won, lost],
    chart: {
      type: 'donut',
      height: 200,
      background: 'transparent',
      toolbar: { show: false },
      animations: { enabled: true, speed: 800 },
    },
    theme: { mode: 'dark' },
    colors: ['#00e676', '#ff4757'],
    labels: ['Победы', 'Убытки'],
    dataLabels: { enabled: false },
    legend: { show: false },
    plotOptions: {
      pie: {
        donut: {
          size: '72%',
          labels: {
            show: true,
            total: {
              show: true,
              label: 'Win Rate',
              fontSize: '13px',
              fontFamily: 'Inter',
              color: '#94a3b8',
              formatter: () => {
                const total = won + lost;
                return total > 0 ? Math.round(won / total * 100) + '%' : '—';
              },
            },
            value: {
              color: '#e2e8f0',
              fontSize: '22px',
              fontWeight: '800',
              fontFamily: 'Inter',
            },
          },
        },
      },
    },
    stroke: { show: false },
    tooltip: { theme: 'dark' },
  };

  if (donutChart) {
    donutChart.updateSeries([won, lost]);
  } else {
    donutChart = new ApexCharts(document.getElementById('donut-chart'), options);
    donutChart.render();
  }
}

// ── HELPERS ─────────────────────────────────────────────────────────────────

function fmt(price) {
  if (!price) return '—';
  if (price >= 1000) return '$' + price.toLocaleString('en', {maximumFractionDigits: 2});
  if (price >= 1) return '$' + price.toFixed(4);
  return '$' + price.toFixed(6);
}

function fmtPnl(pnl) {
  if (pnl === null || pnl === undefined) return '<span class="pnl-neutral">—</span>';
  const cls = pnl >= 0 ? 'pnl-positive' : 'pnl-negative';
  const sign = pnl >= 0 ? '+' : '';
  return `<span class="${cls}">${sign}${pnl.toFixed(2)}%</span>`;
}

function fmtDate(dt) {
  if (!dt) return '—';
  try {
    const d = new Date(dt.replace(' ', 'T') + 'Z');
    return d.toLocaleDateString('ru', {day:'2-digit', month:'2-digit'}) + ' ' +
           d.toLocaleTimeString('ru', {hour:'2-digit', minute:'2-digit'});
  } catch { return String(dt).slice(0, 16); }
}

// ── LOADERS ─────────────────────────────────────────────────────────────────

async function loadStats() {
  try {
    const r = await fetch('/api/stats');
    const d = await r.json();

    const pnlEl = document.getElementById('kpi-pnl');
    const pnlVal = d.pnl_sum || 0;
    pnlEl.textContent = (pnlVal >= 0 ? '+' : '') + pnlVal.toFixed(2) + '%';
    pnlEl.className = 'kpi-value ' + (pnlVal >= 0 ? 'text-green' : 'text-red');
    document.getElementById('kpi-pnl-sub').textContent = d.total + ' закрытых сделок';

    const wr = d.win_rate || 0;
    const wrEl = document.getElementById('kpi-wr');
    wrEl.textContent = wr.toFixed(1) + '%';
    wrEl.className = 'kpi-value ' + (wr >= 55 ? 'text-green' : wr >= 45 ? 'text-gold' : 'text-red');
    document.getElementById('kpi-wr-sub').textContent = `${d.won} / ${d.won + d.lost}`;

    document.getElementById('kpi-total').textContent = d.total;
    document.getElementById('kpi-trades-sub').textContent = `${d.won} побед, ${d.lost} убытков`;

    const openEl = document.getElementById('kpi-open');
    openEl.textContent = d.open;

    const bestEl = document.getElementById('kpi-best');
    bestEl.textContent = '+' + (d.best_trade || 0).toFixed(2) + '%';

    const worstEl = document.getElementById('kpi-worst');
    worstEl.textContent = (d.worst_trade || 0).toFixed(2) + '%';

    initDonutChart(d.won, d.lost);
  } catch (e) { console.error('Stats error', e); }
}

async function loadEquity() {
  try {
    const r = await fetch('/api/equity-curve');
    const data = await r.json();
    if (data && data.length > 1) {
      initEquityChart(data);
    } else {
      initEquityChart([{date:'Start',pnl:0},{date:'Ожидание',pnl:0}]);
    }
  } catch (e) { console.error('Equity error', e); }
}

async function loadOpenTrades() {
  try {
    const r = await fetch('/api/open-trades');
    const trades = await r.json();
    const el = document.getElementById('open-trades-container');
    const label = document.getElementById('open-count-label');

    label.textContent = trades.length + ' позиций';

    if (!trades.length) {
      el.innerHTML = '<div class="empty-state"><div class="emoji">🔍</div>Нет открытых позиций.<br>Бот ищет новые входы...</div>';
      return;
    }

    let html = `<table class="trade-table"><thead><tr>
      <th>Монета</th><th>Статус</th><th>Вход</th><th>Стоп</th><th>TP1</th><th>Позиция</th><th>Лайв Цена</th><th>PnL %</th><th>Открыта</th>
    </tr></thead><tbody>`;

    for (const t of trades) {
      const symClean = t.symbol.replace('/USDT','');
      const dirCls = t.direction === 'LONG' ? 'text-green' : 'text-red';
      let dirTxt = t.direction === 'LONG' ? '🟢 LONG' : '🔴 SHORT';
      
      if (t.status === 'BREAKEVEN') {
          dirTxt += ' <span style="color:var(--gold); margin-left:6px; padding:2px 6px; background:rgba(255,215,0,0.1); border-radius:4px;">🎯 TP1 HIT</span>';
      }
      
      html += `<tr style="cursor: pointer; transition: background 0.2s;" onmouseover="this.style.background='rgba(255,255,255,0.05)'" onmouseout="this.style.background='transparent'" onclick="showTradeDetails('${encodeURIComponent(JSON.stringify(t)).replace(/'/g, "%27")}')">
        <td><span class="symbol-cell">${symClean}</span></td>
        <td style="font-weight:700;font-size:11px;" class="${dirCls}">${dirTxt}</td>
        <td class="price-cell">${fmt(t.entry_price)}</td>
        <td class="price-cell text-red">${fmt(t.stop_loss)}</td>
        <td class="price-cell text-green">${fmt(t.take_profit_1)}</td>
        <td class="price-cell">$${(t.position_usd||0).toFixed(0)}</td>
        <td class="price-cell" id="live-price-${symClean}" style="font-family:'JetBrains Mono',monospace;color:var(--cyan)">—</td>
        <td class="price-cell" id="live-pnl-${symClean}" style="font-weight:700;" data-sym="${t.symbol}" data-entry="${t.entry_price}" data-dir="${t.direction}">—</td>
        <td class="price-cell">${fmtDate(t.opened_at)}</td>
      </tr>`;
    }
    html += '</tbody></table>';
    el.innerHTML = html;
    
    // Trigger an immediate live price update for the new rows
    loadLivePrices();
  } catch (e) { console.error('Open trades error', e); }
}

async function loadHistory() {
  try {
    const r = await fetch('/api/trades');
    const trades = await r.json();
    const el = document.getElementById('history-container');

    if (!trades.length) {
      el.innerHTML = '<div class="empty-state"><div class="emoji">📋</div>Сделок пока нет.<br>Бот уже сканирует рынок...</div>';
      return;
    }

    let html = `<table class="trade-table"><thead><tr>
      <th>Монета</th><th>Статус</th><th>Вход</th><th>PnL</th><th>Позиция $</th><th>Открыта</th><th>Закрыта</th>
    </tr></thead><tbody>`;

    for (const t of trades) {
      let statusHtml = '';
      if (t.status === 'OPEN')           statusHtml = '<span class="status-badge badge-open">⏳ OPEN</span>';
      else if (t.status === 'BREAKEVEN') statusHtml = '<span class="status-badge badge-open" style="color:var(--gold);">🎯 TP1 HIT</span>';
      else if (t.status === 'WON')       statusHtml = '<span class="status-badge badge-won">✅ WIN</span>';
      else if (t.status === 'LOST')      statusHtml = '<span class="status-badge badge-lost">❌ LOSS</span>';
      else if (t.status === 'WON_BREAKEVEN') statusHtml = '<span class="status-badge badge-won" style="background:rgba(255,215,0,0.1);color:var(--gold);">🎯 TP1 EXIT</span>';
      else if (t.status === 'TIMEOUT')   statusHtml = '<span class="status-badge badge-cancelled">⏱ TIMEOUT</span>';
      else                               statusHtml = '<span class="status-badge badge-cancelled">— CANCEL</span>';

      html += `<tr style="cursor: pointer; transition: background 0.2s;" onmouseover="this.style.background='rgba(255,255,255,0.05)'" onmouseout="this.style.background='transparent'" onclick="showTradeDetails('${encodeURIComponent(JSON.stringify(t)).replace(/'/g, "%27")}')">
        <td><span class="symbol-cell">${t.symbol}</span></td>
        <td>${statusHtml}</td>
        <td class="price-cell">${fmt(t.entry_price)}</td>
        <td>${fmtPnl(t.pnl_pct)}</td>
        <td class="price-cell">$${(t.position_usd||0).toFixed(0)}</td>
        <td class="price-cell">${fmtDate(t.opened_at)}</td>
        <td class="price-cell">${fmtDate(t.closed_at)}</td>
      </tr>`;
    }
    html += '</tbody></table>';
    el.innerHTML = html;
  } catch (e) { console.error('History error', e); }
}

async function loadRsMatrix() {
  try {
    const r = await fetch('/api/rs-matrix');
    const d = await r.json();
    const el = document.getElementById('rs-matrix-container');
    document.getElementById('rs-updated').textContent = d.last_updated || '—';

    if (!d.top || !d.top.length) {
      el.innerHTML = '<div class="empty-state" style="padding:16px;"><div class="emoji">📡</div>Данные загружаются...</div>';
      return;
    }

    const maxRs = Math.max(...d.top.map(c => Math.abs(c.rs_score || 0)), 1);
    const medals = ['gold-rank', 'silver-rank', 'bronze-rank'];
    const rankNums = ['1', '2', '3', '4', '5', '6', '7', '8'];

    let html = '';
    for (let i = 0; i < Math.min(d.top.length, 6); i++) {
      const c = d.top[i];
      const rs = c.rs_score || 0;
      const barW = Math.min(100, Math.abs(rs) / maxRs * 100);
      const cls = medals[i] || '';
      const chg = c.change_24h || 0;
      const chgColor = chg >= 0 ? 'var(--green)' : 'var(--red)';
      const chgSign = chg >= 0 ? '+' : '';

      html += `
      <div class="rs-row">
        <div class="rs-rank ${cls}">${rankNums[i]}</div>
        <div class="rs-name">${c.symbol.replace('/USDT','')}</div>
        <div class="rs-bar-wrap">
          <div class="rs-bar-bg">
            <div class="rs-bar-fill" style="width:${barW}%"></div>
          </div>
        </div>
        <div class="rs-pct" style="color:${chgColor}">${chgSign}${chg.toFixed(1)}%</div>
      </div>`;
    }
    el.innerHTML = html;
  } catch (e) { console.error('RS Matrix error', e); }
}

async function loadSystemStatus() {
  try {
    const r = await fetch('/api/system-status');
    const d = await r.json();

    const regEl = document.getElementById('regime-badge');
    regEl.className = 'regime-badge regime-' + (d.regime || 'UNKNOWN');
    regEl.innerHTML = `<div class="live-dot" style="background:var(--text);animation:none"></div>${d.regime || '—'}`;

    document.getElementById('system-status-container').innerHTML = `
      <div class="status-row">
        <span class="status-key">Текущая пара</span>
        <span class="status-val">${d.current_symbol || '—'}</span>
      </div>
      <div class="status-row">
        <span class="status-key">Режим рынка</span>
        <span class="status-val">${d.regime || '—'}</span>
      </div>
      <div class="status-row">
        <span class="status-key">Сигналов сегодня</span>
        <span class="status-val">${d.signals_today || 0}</span>
      </div>
      <div class="status-row">
        <span class="status-key">Последний скан</span>
        <span class="status-val" style="font-size:10px;color:var(--text3)">${d.last_scan || '—'}</span>
      </div>
    `;

    document.getElementById('last-update').textContent =
      'Обновлено: ' + new Date().toLocaleTimeString('ru');
  } catch (e) { console.error('Status error', e); }
}

async function loadFeaturesStats() {
  try {
    const r = await fetch('/api/features-stats');
    const data = await r.json();
    const stats = data.regime_stats;
    const el = document.getElementById('ml-regime-container');
    
    if (!stats || !stats.length) {
      el.innerHTML = '<div class="empty-state" style="padding: 20px;">Пока нет закрытых сделок в Feature Store</div>';
      return;
    }
    
    let html = '<table class="trade-table"><tr><th>Режим рынка (ML)</th><th>Размер выборки</th><th>Historical Win-Rate</th></tr>';
    stats.forEach(s => {
      const color = s.win_rate >= 50 ? 'var(--green)' : 'var(--red)';
      html += `<tr>
        <td style="font-weight:700">${s.regime}</td>
        <td>${s.total} шт.</td>
        <td style="color:${color}; font-weight:700; font-family:\\'JetBrains Mono\\'">${s.win_rate.toFixed(1)}%</td>
      </tr>`;
    });
    html += '</table>';
    el.innerHTML = html;
  } catch (e) { console.error('Features stats error', e); }
}

async function loadLivePrices() {
  try {
    const r = await fetch('/api/live-prices');
    const data = await r.json();
    if (!data || Object.keys(data).length === 0) return;

    let totalLivePnlUsd = 0;
    let totalLivePnlPct = 0;

    // Update Open Trades table rows directly
    const pnlCells = document.querySelectorAll('td[id^="live-pnl-"]');
    pnlCells.forEach(cell => {
      const sym = cell.getAttribute('data-sym');
      const entry = parseFloat(cell.getAttribute('data-entry'));
      const dir = cell.getAttribute('data-dir');
      const posUsd = parseFloat(cell.getAttribute('data-pos') || 0);
      const cleanSym = sym.replace('/USDT', '');
      
      const liveData = data[sym];
      if (liveData && liveData.price) {
        const livePrice = liveData.price;
        
        // Update Price Cell
        const priceCell = document.getElementById(`live-price-${cleanSym}`);
        if (priceCell) priceCell.textContent = fmt(livePrice);
        
        // Calculate PnL %
        let pnlPct = 0;
        if (dir === 'LONG') {
          pnlPct = ((livePrice - entry) / entry) * 100;
        } else {
          pnlPct = ((entry - livePrice) / entry) * 100;
        }
        
        // Add to total
        totalLivePnlUsd += posUsd * (pnlPct / 100);
        totalLivePnlPct += pnlPct;
        
        const isUp = pnlPct >= 0;
        const color = isUp ? 'var(--green)' : 'var(--red)';
        const sign = isUp ? '+' : '';
        
        cell.textContent = `${sign}${pnlPct.toFixed(2)}%`;
        cell.style.color = color;
      }
    });

    // Update Total Live PnL indicator
    const totalPnlEl = document.getElementById('live-total-pnl');
    if (totalPnlEl && pnlCells.length > 0) {
      const isUp = totalLivePnlPct >= 0;
      const color = isUp ? 'var(--green)' : 'var(--red)';
      const sign = isUp ? '+' : '';
      const bg = isUp ? 'rgba(0, 255, 136, 0.1)' : 'rgba(255, 68, 68, 0.1)';
      const border = isUp ? 'var(--green)' : 'var(--red)';
      
      // If we have actual USD size, show it, otherwise just show %
      let displayValue = totalLivePnlUsd !== 0 ? 
                         `$${Math.abs(totalLivePnlUsd).toFixed(2)} (${sign}${Math.abs(totalLivePnlPct).toFixed(2)}%)` : 
                         `${sign}${Math.abs(totalLivePnlPct).toFixed(2)}%`;
                         
      totalPnlEl.textContent = `Live: ${sign}${displayValue.replace('+', '')}`;
      totalPnlEl.style.color = color;
      totalPnlEl.style.background = bg;
      totalPnlEl.style.borderColor = border;
    } else if (totalPnlEl) {
      totalPnlEl.textContent = `Live: 0.00%`;
      totalPnlEl.style.color = 'var(--text3)';
      totalPnlEl.style.background = 'rgba(255,255,255,0.03)';
      totalPnlEl.style.borderColor = 'var(--border)';
    }

  } catch(e) { console.error('Live prices error', e); }
}

// ── MODAL LOGIC ─────────────────────────────────────────────────────────────

function closeModal(e) {
  if (e) {
    if (e.target.classList.contains('modal-overlay') || e.target.classList.contains('close-btn')) {
      document.getElementById('interactive-modal').classList.remove('active');
    }
  } else {
    document.getElementById('interactive-modal').classList.remove('active');
  }
}

function closeDetailsModal(e) {
  if (e) {
    if (e.target.classList.contains('modal-overlay') || e.target.classList.contains('close-btn')) {
      document.getElementById('trade-details-modal').classList.remove('active');
    }
  } else {
    document.getElementById('trade-details-modal').classList.remove('active');
  }
}

function showTradeDetails(tradeJson) {
  const t = JSON.parse(decodeURIComponent(tradeJson));
  const modal = document.getElementById('trade-details-modal');
  const bodyEl = document.getElementById('details-body');
  
  let statusHtml = '';
  if (t.status === 'OPEN') statusHtml = '<span class="status-badge badge-open">⏳ OPEN</span>';
  else if (t.status === 'BREAKEVEN') statusHtml = '<span class="status-badge badge-open" style="color:var(--gold);">🎯 TP1 HIT (BREAKEVEN)</span>';
  else if (t.status === 'WON') statusHtml = '<span class="status-badge badge-won">✅ WIN</span>';
  else if (t.status === 'LOST') statusHtml = '<span class="status-badge badge-lost">❌ LOSS</span>';
  else if (t.status === 'WON_BREAKEVEN') statusHtml = '<span class="status-badge badge-won" style="background:rgba(255,215,0,0.1);color:var(--gold);">🎯 TP1 EXIT</span>';
  else statusHtml = `<span class="status-badge badge-cancelled">${t.status}</span>`;

  let pnlHtml = '—';
  if (t.pnl_pct !== null) {
    let cls = t.pnl_pct > 0 ? 'text-green' : (t.pnl_pct < 0 ? 'text-red' : '');
    pnlHtml = `<strong class="${cls}">${t.pnl_pct > 0 ? '+' : ''}${t.pnl_pct.toFixed(2)}%</strong>`;
  }

  bodyEl.innerHTML = `
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 20px;">
      <div style="font-size:24px; font-weight:800; color:var(--text);">${t.symbol}</div>
      <div>${statusHtml}</div>
    </div>
    
    <div style="background:rgba(255,255,255,0.03); border:1px solid var(--border); border-radius:12px; padding:16px; margin-bottom:20px;">
      <div style="display:grid; grid-template-columns:1fr 1fr; gap:16px;">
        <div>
          <div style="color:var(--text3); font-size:12px; margin-bottom:4px;">Направление</div>
          <div style="font-weight:700; color:${t.direction==='LONG'?'var(--green)':'var(--red)'}">${t.direction}</div>
        </div>
        <div>
          <div style="color:var(--text3); font-size:12px; margin-bottom:4px;">Стратегия</div>
          <div style="font-weight:700; color:var(--text2);">${t.strategy || 'TREND'}</div>
        </div>
        <div>
          <div style="color:var(--text3); font-size:12px; margin-bottom:4px;">Цена Входа</div>
          <div style="font-family:'JetBrains Mono',monospace; color:var(--cyan); font-weight:600;">$${t.entry_price.toFixed(5)}</div>
        </div>
        <div>
          <div style="color:var(--text3); font-size:12px; margin-bottom:4px;">Текущий PnL</div>
          <div>${pnlHtml}</div>
        </div>
      </div>
    </div>

    <div style="background:rgba(255,255,255,0.03); border:1px solid var(--border); border-radius:12px; padding:16px; margin-bottom:20px;">
      <div style="color:var(--text); font-weight:600; margin-bottom:12px; font-size:14px;">🎯 Цели и Защита</div>
      <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px;">
        <div style="padding:10px; background:rgba(255,71,87,0.1); border-radius:8px; border:1px solid rgba(255,71,87,0.2);">
          <div style="color:var(--red); font-size:11px; font-weight:700;">🛑 STOP LOSS</div>
          <div style="font-family:'JetBrains Mono'; font-weight:600; color:var(--text); margin-top:4px;">$${t.stop_loss ? t.stop_loss.toFixed(5) : '—'}</div>
        </div>
        <div style="padding:10px; background:rgba(46,213,115,0.1); border-radius:8px; border:1px solid rgba(46,213,115,0.2);">
          <div style="color:var(--green); font-size:11px; font-weight:700;">🎯 TAKE PROFIT 1</div>
          <div style="font-family:'JetBrains Mono'; font-weight:600; color:var(--text); margin-top:4px;">$${t.take_profit_1 ? t.take_profit_1.toFixed(5) : '—'}</div>
        </div>
        <div style="padding:10px; background:rgba(46,213,115,0.1); border-radius:8px; border:1px solid rgba(46,213,115,0.2);">
          <div style="color:var(--green); font-size:11px; font-weight:700;">🎯 TAKE PROFIT 2</div>
          <div style="font-family:'JetBrains Mono'; font-weight:600; color:var(--text); margin-top:4px;">$${t.take_profit_2 ? t.take_profit_2.toFixed(5) : '—'}</div>
        </div>
        <div style="padding:10px; background:rgba(46,213,115,0.1); border-radius:8px; border:1px solid rgba(46,213,115,0.2);">
          <div style="color:var(--green); font-size:11px; font-weight:700;">🚀 TAKE PROFIT 3</div>
          <div style="font-family:'JetBrains Mono'; font-weight:600; color:var(--text); margin-top:4px;">$${t.take_profit_3 ? t.take_profit_3.toFixed(5) : '—'}</div>
        </div>
      </div>
    </div>

    ${t.reasoning ? `
    <div style="background:rgba(255,255,255,0.03); border:1px solid var(--border); border-radius:12px; padding:16px;">
      <div style="color:var(--text); font-weight:600; margin-bottom:8px; font-size:14px;">🧠 Логика входа</div>
      <div style="color:var(--text3); font-size:13px; line-height:1.5;">${t.reasoning}</div>
    </div>
    ` : ''}
  `;
  modal.classList.add('active');
}


async function openModal(title, filterType) {
  const modal = document.getElementById('interactive-modal');
  const titleEl = document.getElementById('modal-title');
  const bodyEl = document.getElementById('modal-body');
  
  titleEl.innerHTML = `<span class="icon">📊</span> ${title}`;
  bodyEl.innerHTML = `<div class="empty-state"><div class="emoji">⏳</div>Загрузка данных...</div>`;
  modal.classList.add('active');

  try {
    const res = await fetch(`/api/trades?limit=500&filter_type=${filterType}`);
    const data = await res.json();
    
    if (!data || data.length === 0) {
      bodyEl.innerHTML = `<div class="empty-state"><div class="emoji">📭</div>Сделок не найдено</div>`;
      return;
    }

    let html = `
      <table class="table">
        <thead>
          <tr>
            <th>Монета</th>
            <th>Направление</th>
            <th>Вход</th>
            <th>Выход / Цена</th>
            <th>PnL %</th>
            <th>Дата</th>
          </tr>
        </thead>
        <tbody>
    `;

    data.forEach(t => {
      let isUp = t.direction === 'LONG';
      let dirClass = isUp ? 'up' : 'dn';
      
      let pnlText = "—";
      let pnlClass = "";
      if (t.pnl_pct !== null) {
        pnlClass = t.pnl_pct > 0 ? 'text-green' : (t.pnl_pct < 0 ? 'text-red' : '');
        pnlText = `${t.pnl_pct > 0 ? '+' : ''}${t.pnl_pct.toFixed(2)}%`;
      } else if (t.status === 'OPEN') {
        pnlClass = 'text-cyan';
        pnlText = "Открыта";
      }

      const dateStr = new Date(t.opened_at).toLocaleString('ru-RU', {day: '2-digit', month: '2-digit', hour: '2-digit', minute:'2-digit'});

      html += `
        <tr style="cursor: pointer; transition: background 0.2s;" onmouseover="this.style.background='rgba(255,255,255,0.05)'" onmouseout="this.style.background='transparent'" onclick="showTradeDetails('${encodeURIComponent(JSON.stringify(t)).replace(/'/g, "%27")}')">
          <td><strong>${t.symbol}</strong></td>
          <td><span class="status-badge ${dirClass}">${t.direction}</span></td>
          <td style="font-family: 'JetBrains Mono', monospace;">$${t.entry_price.toFixed(4)}</td>
          <td style="font-family: 'JetBrains Mono', monospace;">${t.status === 'OPEN' ? '—' : (t.closed_at ? '$' + (t.pnl_pct > 0 ? t.take_profit_1.toFixed(4) : t.stop_loss.toFixed(4)) : '—')}</td>
          <td class="${pnlClass}"><strong>${pnlText}</strong></td>
          <td style="color: var(--text3);">${dateStr}</td>
        </tr>
      `;
    });

    html += `</tbody></table>`;
    bodyEl.innerHTML = html;

  } catch(e) {
    bodyEl.innerHTML = `<div class="empty-state"><div class="emoji">❌</div>Ошибка загрузки</div>`;
  }
}

// ── FACTORY RESET ─────────────────────────────────────────────────────────────

async function factoryReset() {
  if (confirm("ВНИМАНИЕ!\nЭто полностью удалит всю историю сделок, обнулит графики PnL, WinRate и очистит базу данных.\nВы уверены?")) {
    try {
      const res = await fetch('/api/factory-reset', { method: 'POST' });
      const data = await res.json();
      if (data.status === 'success') {
        alert("База данных успешно очищена! Статистика обнулена.");
        loadAll();
      } else {
        alert("Ошибка при сбросе: " + data.message);
      }
    } catch (e) {
      alert("Ошибка сети при вызове сброса.");
    }
  }
}

// ── MAIN LOAD ────────────────────────────────────────────────────────────────

async function loadAll() {
  loadStats();
  loadEquity();
  loadOpenTrades();
  loadHistory();
  loadRsMatrix();
  loadSystemStatus();
  loadFeaturesStats();
  loadLivePrices();
}

// Initial load + auto-refresh every 30s
loadAll();
setInterval(loadAll, 30000);
// Live prices refresh faster (every 3s)
setInterval(loadLivePrices, 3000);
