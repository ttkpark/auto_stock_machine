/* ===================================================================
   Auto Stock Machine — Mobile PWA
   =================================================================== */

// ------------------------------------------------------------------
// API Client
// ------------------------------------------------------------------
const API = {
  base: '/api/v1',
  async req(method, path, body) {
    const opts = { method, credentials: 'same-origin', headers: {} };
    if (body) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    const r = await fetch(this.base + path, opts);
    if (r.status === 401) { App.logout(); throw new Error('세션 만료'); }
    const j = await r.json();
    if (!j.ok && j.error) throw new Error(j.error);
    return j;
  },
  get(p) { return this.req('GET', p); },
  post(p, b) { return this.req('POST', p, b); },
  put(p, b) { return this.req('PUT', p, b); },
  del(p) { return this.req('DELETE', p); },
};

// ------------------------------------------------------------------
// Helpers
// ------------------------------------------------------------------
function $(sel, ctx) { return (ctx || document).querySelector(sel); }
function $$(sel, ctx) { return [...(ctx || document).querySelectorAll(sel)]; }
function fmt(n) { return n == null ? '-' : Number(n).toLocaleString('ko-KR'); }
function fmtPct(n) { return n == null ? '-' : (n >= 0 ? '+' : '') + Number(n).toFixed(2) + '%'; }
function pnlClass(n) { return n > 0 ? 'c-up' : n < 0 ? 'c-down' : 'c-flat'; }
function escHtml(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

function showLoading() { $('#loading').style.display = 'flex'; }
function hideLoading() { $('#loading').style.display = 'none'; }

function toast(msg, type = 'info') {
  const el = $('#toast');
  el.textContent = msg;
  el.className = 'toast ' + type + ' show';
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), 2500);
}

function showModal(title, contentHtml) {
  const existing = $('.modal-overlay');
  if (existing) existing.remove();
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `<div class="modal">
    <div class="modal-title"><span>${escHtml(title)}</span><div class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</div></div>
    <div class="modal-body">${contentHtml}</div>
  </div>`;
  overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
  document.body.appendChild(overlay);
  return overlay;
}

// ------------------------------------------------------------------
// App Controller
// ------------------------------------------------------------------
const App = {
  current: null,
  user: null,

  init() {
    // Login form
    $('#login-form').addEventListener('submit', async e => {
      e.preventDefault();
      const u = $('#login-username').value.trim();
      const p = $('#login-password').value;
      if (!u || !p) return;
      try {
        showLoading();
        const r = await API.post('/auth/login', { username: u, password: p });
        App.user = r.user;
        App.showApp();
        toast('로그인 성공', 'success');
      } catch (e) { toast(e.message, 'error'); }
      finally { hideLoading(); }
    });

    // Tab bar
    $$('#tab-bar .tab').forEach(btn => {
      btn.addEventListener('click', () => App.navigate(btn.dataset.screen));
    });

    // Check session
    this.checkSession();

    // Register service worker
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('/static/mobile/sw.js').catch(() => {});
    }
  },

  async checkSession() {
    try {
      const r = await API.get('/me');
      this.user = r.user;
      this.showApp();
    } catch { /* not logged in */ }
  },

  showApp() {
    $('#screen-login').classList.remove('active');
    $('#app-shell').style.display = '';
    this.navigate('dashboard');
  },

  logout() {
    API.post('/auth/logout').catch(() => {});
    this.user = null;
    $('#app-shell').style.display = 'none';
    $('#screen-login').classList.add('active');
    $('#login-password').value = '';
  },

  navigate(screen) {
    if (this.current === screen) {
      // Re-tap = refresh
      Screens[screen]?.load?.();
      return;
    }
    this.current = screen;
    // Update tabs
    $$('#tab-bar .tab').forEach(t => t.classList.toggle('active', t.dataset.screen === screen));
    // Update screens
    $$('#screen-container .screen').forEach(s => s.classList.remove('active'));
    const el = $(`#screen-${screen}`);
    if (el) el.classList.add('active');
    // Update header
    const titles = { dashboard:'대시보드', holdings:'보유종목', trade:'매매', bots:'봇 관리',
      tradelog:'체결내역', ai:'AI 분석', monitor:'모니터링', settings:'설정', schedule:'스케줄', more:'더보기' };
    $('#header-title').textContent = titles[screen] || screen;
    // Load screen
    Screens[screen]?.load?.();
  },

  refreshCurrentScreen() {
    if (this.current) Screens[this.current]?.load?.();
    toast('새로고침', 'info');
  },
};

// ------------------------------------------------------------------
// Screens
// ------------------------------------------------------------------
const Screens = {};

// ==================== Dashboard ====================
Screens.dashboard = {
  async load() {
    const el = $('#screen-dashboard');
    el.innerHTML = '<div class="empty"><div class="spinner"></div></div>';
    try {
      const d = await API.get('/dashboard');
      const holdingsHtml = (d.holdings || []).map(h => {
        const pnl = parseFloat(h.evlu_pfls_rt || h.profit_rate || 0);
        return `<div class="holding-card">
          <div class="holding-header">
            <span class="holding-name">${escHtml(h.prdt_name || h.stock_name || h.ticker || '?')}</span>
            <span class="holding-pnl ${pnlClass(pnl)}">${fmtPct(pnl)}</span>
          </div>
          <div class="holding-detail">
            <div class="holding-detail-item"><span>수량</span><span>${fmt(h.hldg_qty || h.qty)}</span></div>
            <div class="holding-detail-item"><span>평가금</span><span>${fmt(h.evlu_amt || h.eval_amount)}원</span></div>
          </div>
        </div>`;
      }).join('');

      const actionsHtml = (d.today_actions || []).map(a =>
        `<div class="list-item" style="cursor:default">
          <div class="list-item-left">
            <span class="list-item-title">${escHtml(a.action || a.mode || '')}</span>
            <span class="list-item-sub">${escHtml(a.time || '')}</span>
          </div>
          <div class="list-item-right">
            <span class="list-item-value">${escHtml(a.result || a.status || '')}</span>
          </div>
        </div>`
      ).join('');

      el.innerHTML = `
        <div class="card">
          <div class="card-header">
            <span class="card-title">총 자산</span>
            <span class="badge ${d.trading_mode === '실전투자' ? 'badge-red' : 'badge-blue'}">${escHtml(d.trading_mode)}</span>
          </div>
          <div class="card-value">${fmt(d.total_assets)}원</div>
          ${d.broker_error ? `<div style="color:var(--danger);font-size:12px;margin-top:4px">${escHtml(d.broker_error)}</div>` : ''}
        </div>

        <div class="kpi-row">
          <div class="kpi">
            <div class="kpi-label">예수금</div>
            <div class="kpi-value">${fmt(d.balance)}원</div>
          </div>
          <div class="kpi">
            <div class="kpi-label">평가손익</div>
            <div class="kpi-value ${pnlClass(d.total_profit_amount)}">${fmt(d.total_profit_amount)}원</div>
          </div>
        </div>

        <div class="section-title">빠른 실행</div>
        <div class="action-grid">
          <div class="action-btn" onclick="Screens.dashboard.runAction('buy')">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="var(--danger)" stroke-width="2"><path d="M12 19V5M5 12l7-7 7 7"/></svg>
            <span>AI 매수</span>
          </div>
          <div class="action-btn" onclick="Screens.dashboard.runAction('sell')">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="var(--primary)" stroke-width="2"><path d="M12 5v14M19 12l-7 7-7-7"/></svg>
            <span>AI 매도</span>
          </div>
          <div class="action-btn" onclick="Screens.dashboard.runAction('status')">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="var(--success)" stroke-width="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
            <span>현황</span>
          </div>
        </div>

        ${(d.holdings || []).length > 0 ? `<div class="section-title">보유종목 (${d.holdings.length})</div>${holdingsHtml}` : ''}
        ${(d.today_actions || []).length > 0 ? `<div class="section-title">오늘 실행</div>${actionsHtml}` : ''}
      `;
    } catch (e) {
      el.innerHTML = `<div class="empty"><p>${escHtml(e.message)}</p></div>`;
    }
  },

  async runAction(mode) {
    if (!confirm(`${mode === 'buy' ? 'AI 매수' : mode === 'sell' ? 'AI 매도' : '현황 조회'}를 실행하시겠습니까?`)) return;
    try {
      showLoading();
      await API.post('/actions/run', { action: mode });
      toast(`${mode} 실행 시작`, 'success');
    } catch (e) { toast(e.message, 'error'); }
    finally { hideLoading(); }
  },
};

// ==================== Holdings ====================
Screens.holdings = {
  async load() {
    const el = $('#screen-holdings');
    el.innerHTML = '<div class="empty"><div class="spinner"></div></div>';
    try {
      const d = await API.get('/holdings');
      if (!d.holdings || d.holdings.length === 0) {
        el.innerHTML = '<div class="empty"><svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M21 12V7H5a2 2 0 010-4h14v4"/><path d="M3 5v14a2 2 0 002 2h16v-5"/><path d="M18 12a2 2 0 000 4h4v-4h-4z"/></svg><p>보유 종목이 없습니다</p></div>';
        return;
      }
      el.innerHTML = d.holdings.map(h => {
        const pnl = parseFloat(h.evlu_pfls_rt || h.profit_rate || 0);
        const pnlAmt = parseInt(h.evlu_pfls_amt || h.profit_amount || 0);
        return `<div class="holding-card">
          <div class="holding-header">
            <div>
              <div class="holding-name">${escHtml(h.prdt_name || h.stock_name || '?')}</div>
              <div style="font-size:12px;color:var(--text-sec)">${escHtml(h.pdno || h.ticker || '')}</div>
            </div>
            <div style="text-align:right">
              <div class="holding-pnl ${pnlClass(pnl)}">${fmtPct(pnl)}</div>
              <div style="font-size:12px" class="${pnlClass(pnlAmt)}">${fmt(pnlAmt)}원</div>
            </div>
          </div>
          <div class="holding-detail">
            <div class="holding-detail-item"><span>보유수량</span><span>${fmt(h.hldg_qty || h.qty)}주</span></div>
            <div class="holding-detail-item"><span>평균단가</span><span>${fmt(h.pchs_avg_pric || h.avg_price)}원</span></div>
            <div class="holding-detail-item"><span>현재가</span><span>${fmt(h.prpr || h.current_price)}원</span></div>
            <div class="holding-detail-item"><span>평가금액</span><span>${fmt(h.evlu_amt || h.eval_amount)}원</span></div>
            <div class="holding-detail-item"><span>매입금액</span><span>${fmt(h.pchs_amt || h.buy_amount)}원</span></div>
          </div>
          <div class="profit-bar"><div class="profit-bar-fill" style="width:${Math.min(Math.abs(pnl) * 5, 100)}%;background:${pnl >= 0 ? 'var(--danger)' : 'var(--primary)'}"></div></div>
        </div>`;
      }).join('');
    } catch (e) {
      el.innerHTML = `<div class="empty"><p>${escHtml(e.message)}</p></div>`;
    }
  },
};

// ==================== Trade ====================
Screens.trade = {
  mode: 'buy',
  searchResult: null,

  load() {
    const el = $('#screen-trade');
    el.innerHTML = `
      <div class="segment">
        <div class="segment-btn ${this.mode === 'buy' ? 'active' : ''}" onclick="Screens.trade.setMode('buy')">매수</div>
        <div class="segment-btn ${this.mode === 'sell' ? 'active' : ''}" onclick="Screens.trade.setMode('sell')">매도</div>
      </div>

      <div class="form-group">
        <label class="form-label">종목명 검색</label>
        <div class="ai-input-bar">
          <input type="text" id="trade-search" class="form-input" placeholder="종목명 입력 (예: 삼성전자)">
          <button class="btn btn-primary btn-sm" onclick="Screens.trade.search()">검색</button>
        </div>
      </div>

      <div id="trade-search-result" style="display:none" class="card">
        <div class="card-header">
          <span class="card-title" id="trade-stock-name">-</span>
          <span class="badge badge-blue" id="trade-stock-ticker">-</span>
        </div>
        <div class="card-value" id="trade-stock-price">-</div>
        <div style="color:var(--text-sec);font-size:12px;margin-top:2px">현재가</div>
      </div>

      <div class="form-group">
        <label class="form-label">종목코드 (직접 입력)</label>
        <input type="text" id="trade-ticker" class="form-input" placeholder="005930">
      </div>

      <div class="form-group">
        <label class="form-label">수량</label>
        <input type="number" id="trade-qty" class="form-input" placeholder="1" min="1" value="1">
      </div>

      <button class="btn ${this.mode === 'buy' ? 'btn-danger' : 'btn-primary'} btn-block" onclick="Screens.trade.execute()">
        ${this.mode === 'buy' ? '매수 주문' : '매도 주문'}
      </button>
    `;
  },

  setMode(m) { this.mode = m; this.load(); },

  async search() {
    const name = $('#trade-search').value.trim();
    if (!name) return;
    try {
      showLoading();
      const r = await API.post('/trade/search-stock', { name });
      this.searchResult = r;
      $('#trade-search-result').style.display = '';
      $('#trade-stock-name').textContent = r.name;
      $('#trade-stock-ticker').textContent = r.ticker;
      $('#trade-stock-price').textContent = fmt(r.current_price) + '원';
      $('#trade-ticker').value = r.ticker;
    } catch (e) { toast(e.message, 'error'); }
    finally { hideLoading(); }
  },

  async execute() {
    const ticker = $('#trade-ticker').value.trim();
    const qty = parseInt($('#trade-qty').value);
    if (!ticker) { toast('종목코드를 입력하세요', 'error'); return; }
    if (!qty || qty < 1) { toast('수량을 입력하세요', 'error'); return; }
    if (!confirm(`${ticker} ${qty}주 ${this.mode === 'buy' ? '매수' : '매도'} 하시겠습니까?`)) return;
    try {
      showLoading();
      await API.post('/trade/manual', { action: this.mode, ticker, qty });
      toast('주문 완료', 'success');
    } catch (e) { toast(e.message, 'error'); }
    finally { hideLoading(); }
  },
};

// ==================== Bots ====================
Screens.bots = {
  data: null,

  async load() {
    const el = $('#screen-bots');
    el.innerHTML = '<div class="empty"><div class="spinner"></div></div>';
    try {
      const r = await API.get('/bots');
      this.data = r;
      if (!r.bots || r.bots.length === 0) {
        el.innerHTML = '<div class="empty"><p>등록된 봇이 없습니다</p></div>';
        return;
      }
      const typeLabels = { auto_buy: '자동매수', auto_sell: '자동매도', monitor: '모니터링', manual: '수동매매', ask: '질문' };
      el.innerHTML = r.bots.map(b => `
        <div class="list-item" onclick="Screens.bots.detail(${b.id})">
          <div class="list-item-left">
            <span class="list-item-title">${escHtml(b.name)}</span>
            <span class="list-item-sub">${escHtml(typeLabels[b.bot_type] || b.bot_type)}</span>
          </div>
          <div class="list-item-right" style="display:flex;align-items:center;gap:10px">
            <label class="toggle" onclick="event.stopPropagation()">
              <input type="checkbox" ${b.enabled ? 'checked' : ''} onchange="Screens.bots.toggle(${b.id})">
              <span class="toggle-slider"></span>
            </label>
            <button class="btn btn-sm btn-outline" onclick="event.stopPropagation();Screens.bots.exec(${b.id})">실행</button>
          </div>
        </div>
      `).join('');
    } catch (e) {
      el.innerHTML = `<div class="empty"><p>${escHtml(e.message)}</p></div>`;
    }
  },

  async toggle(id) {
    try { await API.post(`/bots/${id}/toggle`); }
    catch (e) { toast(e.message, 'error'); this.load(); }
  },

  async exec(id) {
    if (!confirm('봇을 실행하시겠습니까?')) return;
    try {
      showLoading();
      const r = await API.post(`/bots/${id}/execute`);
      toast(r.message || '실행 완료', 'success');
    } catch (e) { toast(e.message, 'error'); }
    finally { hideLoading(); }
  },

  async detail(id) {
    try {
      const r = await API.get(`/bots/${id}`);
      const b = r.bot;
      const cfg = b.config_json ? (typeof b.config_json === 'string' ? JSON.parse(b.config_json) : b.config_json) : {};
      showModal(b.name, `
        <div class="form-group"><label class="form-label">봇 타입</label><div>${escHtml(b.bot_type)}</div></div>
        <div class="form-group"><label class="form-label">상태</label><div>${b.enabled ? '<span class="badge badge-green">활성</span>' : '<span class="badge badge-gray">비활성</span>'}</div></div>
        <div class="form-group"><label class="form-label">생성일</label><div style="font-size:13px;color:var(--text-sec)">${escHtml(b.created_at || '')}</div></div>
        <div class="form-group"><label class="form-label">설정</label><pre style="font-size:12px;color:var(--text-sec);white-space:pre-wrap">${escHtml(JSON.stringify(cfg, null, 2))}</pre></div>
        <div style="display:flex;gap:8px;margin-top:16px">
          <button class="btn btn-danger btn-sm" onclick="Screens.bots.del(${id})">삭제</button>
        </div>
      `);
    } catch (e) { toast(e.message, 'error'); }
  },

  async del(id) {
    if (!confirm('이 봇을 삭제하시겠습니까?')) return;
    try {
      await API.del(`/bots/${id}`);
      document.querySelector('.modal-overlay')?.remove();
      toast('삭제 완료', 'success');
      this.load();
    } catch (e) { toast(e.message, 'error'); }
  },
};

// ==================== Trade Log ====================
Screens.tradelog = {
  async load() {
    const el = $('#screen-tradelog');
    el.innerHTML = '<div class="empty"><div class="spinner"></div></div>';
    try {
      const r = await API.get('/trades?limit=100');
      if (!r.trades || r.trades.length === 0) {
        el.innerHTML = '<div class="empty"><p>체결 내역이 없습니다</p></div>';
        return;
      }
      el.innerHTML = r.trades.map(t => {
        const isBuy = (t.action || '').includes('buy') || (t.action || '').includes('매수');
        return `<div class="list-item" onclick="Screens.tradelog.detail(${t.id})">
          <div class="list-item-left">
            <span class="list-item-title">
              <span class="badge ${isBuy ? 'badge-red' : 'badge-blue'}" style="margin-right:6px">${isBuy ? '매수' : '매도'}</span>
              ${escHtml(t.stock_name || t.ticker || '?')}
            </span>
            <span class="list-item-sub">${escHtml((t.created_at || '').slice(0, 16))}</span>
          </div>
          <div class="list-item-right">
            <div class="list-item-value">${fmt(t.qty)}주</div>
            <div class="list-item-detail">${fmt(t.price)}원</div>
          </div>
        </div>`;
      }).join('');
    } catch (e) {
      el.innerHTML = `<div class="empty"><p>${escHtml(e.message)}</p></div>`;
    }
  },

  async detail(id) {
    try {
      showLoading();
      const r = await API.get(`/trades/${id}`);
      const t = r.trade;
      let tracesHtml = '';
      if (r.ai_traces && r.ai_traces.length > 0) {
        tracesHtml = '<div class="section-title">AI 판단</div>' +
          r.ai_traces.map(tr => `<div class="card" style="font-size:13px">
            <div style="font-weight:600;margin-bottom:4px">${escHtml(tr.event_type)}</div>
            <div style="color:var(--text-sec)">${escHtml(tr.payload?.ai_model || '')}</div>
            <div style="margin-top:4px;white-space:pre-wrap">${escHtml(tr.payload?.reason || tr.payload?.action || JSON.stringify(tr.payload).slice(0, 200))}</div>
          </div>`).join('');
      }
      showModal('체결 상세', `
        <div class="holding-detail" style="margin-bottom:12px">
          <div class="holding-detail-item"><span>종목</span><span>${escHtml(t.stock_name || '')} (${escHtml(t.ticker || '')})</span></div>
          <div class="holding-detail-item"><span>액션</span><span>${escHtml(t.action || '')}</span></div>
          <div class="holding-detail-item"><span>수량</span><span>${fmt(t.qty)}주</span></div>
          <div class="holding-detail-item"><span>가격</span><span>${fmt(t.price)}원</span></div>
          <div class="holding-detail-item"><span>일시</span><span>${escHtml((t.created_at || '').slice(0, 19))}</span></div>
          ${t.reason ? `<div class="holding-detail-item"><span>사유</span><span>${escHtml(t.reason)}</span></div>` : ''}
        </div>
        ${tracesHtml}
      `);
    } catch (e) { toast(e.message, 'error'); }
    finally { hideLoading(); }
  },
};

// ==================== AI ====================
Screens.ai = {
  load() {
    const el = $('#screen-ai');
    el.innerHTML = `
      <div class="section-title">종목 질문</div>
      <div class="ai-input-bar">
        <input type="text" id="ai-query" class="form-input" placeholder="종목에 대해 AI에게 질문하세요">
        <button class="btn btn-primary btn-sm" onclick="Screens.ai.ask()">질문</button>
      </div>
      <div id="ai-answer" class="ai-response" style="display:none"></div>

      <div class="section-title" style="margin-top:24px">최근 AI 실행</div>
      <div id="ai-runs-list"><div class="empty"><div class="spinner"></div></div></div>
    `;
    this.loadRuns();
    // Enter key
    setTimeout(() => {
      const input = $('#ai-query');
      if (input) input.addEventListener('keydown', e => { if (e.key === 'Enter') Screens.ai.ask(); });
    }, 50);
  },

  async ask() {
    const q = $('#ai-query').value.trim();
    if (!q) return;
    const ansEl = $('#ai-answer');
    ansEl.style.display = '';
    ansEl.textContent = '분석 중...';
    try {
      const r = await API.post('/ask', { query: q });
      ansEl.textContent = r.output || '응답이 없습니다.';
    } catch (e) {
      ansEl.textContent = '오류: ' + e.message;
    }
  },

  async loadRuns() {
    try {
      const r = await API.get('/ai/runs');
      const el = $('#ai-runs-list');
      if (!r.runs || r.runs.length === 0) {
        el.innerHTML = '<div class="empty" style="padding:24px"><p>실행 기록이 없습니다</p></div>';
        return;
      }
      el.innerHTML = r.runs.slice(0, 20).map(run => `
        <div class="list-item" onclick="Screens.ai.showRun('${escHtml(run.run_id)}')">
          <div class="list-item-left">
            <span class="list-item-title">${escHtml(run.mode || '?')}</span>
            <span class="list-item-sub">${escHtml((run.ended_at || '').slice(0, 16))} · ${run.event_count}건</span>
          </div>
          <div class="list-item-right">
            <span class="badge ${run.trading_mode === '실전' ? 'badge-red' : 'badge-blue'}">${escHtml(run.trading_mode || '')}</span>
          </div>
        </div>
      `).join('');
    } catch { $('#ai-runs-list').innerHTML = ''; }
  },

  async showRun(runId) {
    try {
      showLoading();
      const r = await API.get(`/ai/traces/${runId}`);
      let html = '';
      if (r.decisions && r.decisions.length > 0) {
        html += '<div class="section-title">판단</div>';
        html += r.decisions.map(d => `<div class="card" style="font-size:13px">
          <div style="display:flex;justify-content:space-between;margin-bottom:4px">
            <span style="font-weight:600">${escHtml(d.event_type)}</span>
            <span style="color:var(--text-sec)">${escHtml(d.ai_model)}</span>
          </div>
          <div>${escHtml(d.action)}</div>
          ${d.reason ? `<div style="color:var(--text-sec);margin-top:4px">${escHtml(d.reason)}</div>` : ''}
        </div>`).join('');
      }
      if (r.prompts && r.prompts.length > 0) {
        html += '<div class="section-title">프롬프트</div>';
        html += r.prompts.map(p => `<div class="card" style="font-size:12px">
          <div style="font-weight:600;margin-bottom:4px">${escHtml(p.ai_model)}</div>
          <div style="max-height:200px;overflow:auto;white-space:pre-wrap;color:var(--text-sec)">${escHtml((p.prompt || '').slice(0, 1000))}</div>
        </div>`).join('');
      }
      showModal(`실행: ${r.mode || ''}`, html || '<p>상세 정보가 없습니다.</p>');
    } catch (e) { toast(e.message, 'error'); }
    finally { hideLoading(); }
  },
};

// ==================== Monitor ====================
Screens.monitor = {
  config: null,

  async load() {
    const el = $('#screen-monitor');
    el.innerHTML = '<div class="empty"><div class="spinner"></div></div>';
    try {
      const r = await API.get('/monitor');
      this.config = r.config;
      el.innerHTML = `
        <div class="setting-row">
          <div class="setting-row-left">
            <div class="setting-row-label">모니터링 활성화</div>
            <div class="setting-row-value">보유종목 자동 감시</div>
          </div>
          <label class="toggle">
            <input type="checkbox" id="mon-enabled" ${r.config.enabled ? 'checked' : ''}>
            <span class="toggle-slider"></span>
          </label>
        </div>

        <div class="section-title">임계값 설정</div>
        <div class="form-group">
          <label class="form-label">수익 임계값 (%)</label>
          <input type="number" id="mon-profit" class="form-input" value="${r.config.profit_threshold}" step="0.5">
        </div>
        <div class="form-group">
          <label class="form-label">손실 임계값 (%)</label>
          <input type="number" id="mon-loss" class="form-input" value="${r.config.loss_threshold}" step="0.5">
        </div>
        <div class="form-group">
          <label class="form-label">변동성 임계값 (%)</label>
          <input type="number" id="mon-vol" class="form-input" value="${r.config.volatility_threshold}" step="0.5">
        </div>
        <div class="form-group">
          <label class="form-label">점검 간격 (초)</label>
          <input type="number" id="mon-interval" class="form-input" value="${r.config.check_interval_sec}" min="60">
        </div>

        <div class="setting-row">
          <div class="setting-row-left"><div class="setting-row-label">자동 매도</div></div>
          <label class="toggle">
            <input type="checkbox" id="mon-autosell" ${r.config.auto_sell_enabled ? 'checked' : ''}>
            <span class="toggle-slider"></span>
          </label>
        </div>

        <button class="btn btn-primary btn-block" style="margin-top:16px" onclick="Screens.monitor.save()">저장</button>

        ${r.recent_alerts && r.recent_alerts.length > 0 ? `
          <div class="section-title" style="margin-top:24px">최근 알림 (${r.recent_alerts.length})</div>
          ${r.recent_alerts.map(a => `<div class="card" style="font-size:13px">${escHtml(JSON.stringify(a).slice(0, 200))}</div>`).join('')}
        ` : `
          <div class="section-title" style="margin-top:24px">상태</div>
          <div class="card" style="font-size:13px;color:var(--text-sec)">
            실행: ${r.state.running ? '동작 중' : '정지'} · 마지막 점검: ${escHtml(r.state.last_check || '없음')}
          </div>
        `}
      `;
    } catch (e) {
      el.innerHTML = `<div class="empty"><p>${escHtml(e.message)}</p></div>`;
    }
  },

  async save() {
    try {
      showLoading();
      await API.post('/monitor', {
        enabled: $('#mon-enabled').checked,
        profit_threshold: parseFloat($('#mon-profit').value),
        loss_threshold: parseFloat($('#mon-loss').value),
        volatility_threshold: parseFloat($('#mon-vol').value),
        check_interval_sec: parseInt($('#mon-interval').value),
        auto_sell_enabled: $('#mon-autosell').checked,
        notify_on_threshold: true,
      });
      toast('저장 완료', 'success');
    } catch (e) { toast(e.message, 'error'); }
    finally { hideLoading(); }
  },
};

// ==================== Settings ====================
Screens.settings = {
  async load() {
    const el = $('#screen-settings');
    el.innerHTML = '<div class="empty"><div class="spinner"></div></div>';
    try {
      const r = await API.get('/settings');
      const keys = Object.keys(r.masked || {});
      const groups = {
        '한국투자증권 API': keys.filter(k => k.startsWith('KIS_')),
        'AI API': keys.filter(k => k.includes('API_KEY') || k.includes('MODEL_NAME')).filter(k => !k.startsWith('KIS_')),
        '텔레그램': keys.filter(k => k.startsWith('TELEGRAM_')),
        '매매 설정': keys.filter(k => ['BUY_BUDGET_RATIO','MIN_AI_CONSENSUS','MAX_BUY_STOCKS','TAKE_PROFIT_RATE','STOP_LOSS_RATE','IS_REAL_TRADING'].includes(k)),
        '기타': keys.filter(k => !['KIS_','TELEGRAM_','API_KEY','MODEL_NAME','BUY_BUDGET','MIN_AI','MAX_BUY','TAKE_PROFIT','STOP_LOSS','IS_REAL','WEB_ADMIN'].some(p => k.includes(p))),
      };

      let html = '';
      for (const [group, gkeys] of Object.entries(groups)) {
        if (gkeys.length === 0) continue;
        html += `<div class="section-title">${escHtml(group)}</div>`;
        html += gkeys.map(k => `
          <div class="setting-row" onclick="Screens.settings.edit('${escHtml(k)}','${escHtml(r.values[k] || '')}')">
            <div class="setting-row-left">
              <div class="setting-row-label">${escHtml(k)}</div>
              <div class="setting-row-value">${escHtml(r.masked[k] || '(비어있음)')}</div>
            </div>
            <div style="color:var(--text-dim);font-size:18px">&rsaquo;</div>
          </div>
        `).join('');
      }

      html += '<button class="btn btn-outline btn-block" style="margin-top:20px" onclick="App.logout()">로그아웃</button>';
      el.innerHTML = html;
    } catch (e) {
      el.innerHTML = `<div class="empty"><p>${escHtml(e.message)}</p></div>`;
    }
  },

  edit(key, currentValue) {
    const overlay = showModal('설정 변경', `
      <div class="form-group">
        <label class="form-label">${escHtml(key)}</label>
        <input type="text" id="setting-edit-value" class="form-input" value="${escHtml(currentValue)}">
      </div>
      <button class="btn btn-primary btn-block" id="setting-save-btn">저장</button>
    `);
    overlay.querySelector('#setting-save-btn').addEventListener('click', async () => {
      const val = overlay.querySelector('#setting-edit-value').value;
      try {
        showLoading();
        await API.post('/settings', { [key]: val });
        overlay.remove();
        toast('저장 완료', 'success');
        this.load();
      } catch (e) { toast(e.message, 'error'); }
      finally { hideLoading(); }
    });
  },
};

// ==================== Schedule ====================
Screens.schedule = {
  async load() {
    const el = $('#screen-schedule');
    el.innerHTML = '<div class="empty"><div class="spinner"></div></div>';
    try {
      const r = await API.get('/schedule');
      const s = r.schedule || {};
      const weekdayStr = s.weekdays || '1,2,3,4,5';
      const activeDays = weekdayStr.split(',').map(Number);
      const dayNames = ['월','화','수','목','금','토','일'];

      el.innerHTML = `
        <div class="setting-row">
          <div class="setting-row-left">
            <div class="setting-row-label">자동 스케줄</div>
            <div class="setting-row-value">설정한 시간에 자동 실행</div>
          </div>
          <label class="toggle">
            <input type="checkbox" id="sched-enabled" ${s.enabled ? 'checked' : ''}>
            <span class="toggle-slider"></span>
          </label>
        </div>

        <div class="section-title">실행 요일</div>
        <div class="weekday-picker">
          ${dayNames.map((d, i) => `<div class="weekday-btn ${activeDays.includes(i + 1) ? 'active' : ''}" data-day="${i + 1}" onclick="this.classList.toggle('active')">${d}</div>`).join('')}
        </div>

        <div class="form-group">
          <label class="form-label">매수 시간 (HH:MM, 쉼표구분)</label>
          <input type="text" id="sched-buy" class="form-input" value="${escHtml(s.buy_times || '08:30')}" placeholder="08:30">
        </div>

        <div class="form-group">
          <label class="form-label">매도 시간 (HH:MM, 쉼표구분)</label>
          <input type="text" id="sched-sell" class="form-input" value="${escHtml(s.sell_times || '15:00')}" placeholder="15:00">
        </div>

        <div class="form-group">
          <label class="form-label">타임존</label>
          <input type="text" id="sched-tz" class="form-input" value="${escHtml(s.timezone || 'Asia/Seoul')}">
        </div>

        <button class="btn btn-primary btn-block" onclick="Screens.schedule.save()">저장</button>
      `;
    } catch (e) {
      el.innerHTML = `<div class="empty"><p>${escHtml(e.message)}</p></div>`;
    }
  },

  async save() {
    const days = $$('.weekday-picker .weekday-btn.active').map(b => b.dataset.day).join(',');
    try {
      showLoading();
      await API.post('/schedule', {
        enabled: $('#sched-enabled').checked,
        weekdays: days,
        buy_times: $('#sched-buy').value,
        sell_times: $('#sched-sell').value,
        timezone: $('#sched-tz').value,
      });
      toast('저장 완료', 'success');
    } catch (e) { toast(e.message, 'error'); }
    finally { hideLoading(); }
  },
};

// ==================== More ====================
Screens.more = {
  load() {
    const el = $('#screen-more');
    el.innerHTML = `
      <div class="menu-list">
        <div class="menu-item" onclick="App.navigate('tradelog')">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><path d="M14 2v6h6"/><path d="M16 13H8M16 17H8M10 9H8"/></svg>
          <div class="menu-item-text">
            <div class="menu-item-title">체결내역</div>
            <div class="menu-item-sub">매수/매도 거래 기록</div>
          </div>
          <span class="menu-item-arrow">&rsaquo;</span>
        </div>
        <div class="menu-item" onclick="App.navigate('ai')">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>
          <div class="menu-item-text">
            <div class="menu-item-title">AI 분석</div>
            <div class="menu-item-sub">종목 질문 & AI 판단 기록</div>
          </div>
          <span class="menu-item-arrow">&rsaquo;</span>
        </div>
        <div class="menu-item" onclick="App.navigate('monitor')">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
          <div class="menu-item-text">
            <div class="menu-item-title">모니터링</div>
            <div class="menu-item-sub">보유종목 자동 감시 설정</div>
          </div>
          <span class="menu-item-arrow">&rsaquo;</span>
        </div>
        <div class="menu-item" onclick="App.navigate('schedule')">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
          <div class="menu-item-text">
            <div class="menu-item-title">스케줄</div>
            <div class="menu-item-sub">자동 매수/매도 시간 설정</div>
          </div>
          <span class="menu-item-arrow">&rsaquo;</span>
        </div>
        <div class="menu-item" onclick="App.navigate('settings')">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"/></svg>
          <div class="menu-item-text">
            <div class="menu-item-title">설정</div>
            <div class="menu-item-sub">API키, 매매 전략, 환경변수</div>
          </div>
          <span class="menu-item-arrow">&rsaquo;</span>
        </div>
      </div>

      <div style="text-align:center;padding:32px;color:var(--text-dim);font-size:12px">
        Auto Stock Machine v1.0<br>
        ${App.user ? escHtml(App.user.username) : ''} ${App.user?.is_admin ? '(관리자)' : ''}
      </div>

      <button class="btn btn-outline btn-block" onclick="App.logout()">로그아웃</button>
    `;
  },
};


// ------------------------------------------------------------------
// Init
// ------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => App.init());
