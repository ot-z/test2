/* 刷题助手 · 公共前端工具（普通 script，非 ES module；无任何外链资源，断网可用）
 * 全局对象：window.QA
 */
(function (global) {
  'use strict';

  var QA = {};

  /* ------------------------------------------------------------------ 基础 */
  QA.el = function (id) { return document.getElementById(id); };
  QA.errMsg = function (err) {
    if (!err) return '未知错误';
    return err.message || String(err);
  };
  QA.esc = function (str) {
    if (str === null || str === undefined) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  };
  /* 只允许 http(s) 链接作为 href，防止 javascript: 等注入 */
  QA.safeUrl = function (url) {
    var s = String(url || '').trim();
    return /^https?:\/\//i.test(s) ? s : '#';
  };
  QA.externalLink = function (url, text, cls) {
    var href = QA.safeUrl(url);
    if (href === '#') return '<span class="' + (cls || '') + '">' + QA.esc(text) + '</span>';
    return '<a class="' + (cls || '') + '" href="' + QA.esc(href) + '" target="_blank" rel="noopener noreferrer">' + QA.esc(text) + '</a>';
  };
  QA.query = function (params) {
    var parts = [];
    Object.keys(params || {}).forEach(function (k) {
      var v = params[k];
      if (v === undefined || v === null || v === '' || v === false) return;
      parts.push(encodeURIComponent(k) + '=' + encodeURIComponent(v));
    });
    return parts.length ? '?' + parts.join('&') : '';
  };
  QA.loading = function (text) {
    return '<div class="loading">' + QA.esc(text || '加载中…') + '</div>';
  };
  QA.empty = function (title, sub) {
    return '<div class="empty"><span class="empty-icon">◇</span>' +
      '<div class="empty-title">' + QA.esc(title || '暂无数据') + '</div>' +
      (sub ? '<div class="empty-sub">' + QA.esc(sub) + '</div>' : '') + '</div>';
  };
  QA.showError = function (container, err) {
    if (!container) return;
    container.innerHTML = '<div class="alert alert-error">加载失败：' + QA.esc(QA.errMsg(err)) + '</div>';
  };

  /* ------------------------------------------------------------------ fetch 包装 */
  QA.api = async function (path, options) {
    options = options || {};
    var cfg = {
      method: options.method || (options.body !== undefined && options.body !== null ? 'POST' : 'GET'),
      headers: Object.assign({ 'Content-Type': 'application/json', 'Accept': 'application/json' }, options.headers || {})
    };
    if (options.body !== undefined && options.body !== null) {
      cfg.body = (typeof options.body === 'string') ? options.body : JSON.stringify(options.body);
    }
    if (options.signal) cfg.signal = options.signal;

    var res;
    try {
      res = await fetch(path, cfg);
    } catch (e) {
      throw new Error('网络请求失败：' + (e.message || '无法连接本地服务'));
    }

    var text = await res.text();
    var data = null;
    if (text) {
      try { data = JSON.parse(text); } catch (e) { data = text; }
    }

    if (!res.ok) {
      /* 后端统一错误：{"error":{"code":"...","message":"..."}} */
      var msg = '请求失败 (HTTP ' + res.status + ')';
      if (data && typeof data === 'object' && data.error && data.error.message) {
        msg = data.error.message;
      } else if (typeof data === 'string' && data.trim()) {
        msg = data.trim().slice(0, 300);
      }
      var err = new Error(msg);
      err.status = res.status;
      err.code = (data && data.error && data.error.code) || '';
      err.payload = data;
      throw err;
    }
    return data;
  };

  QA.get = function (path, opts) { return QA.api(path, Object.assign({}, opts, { method: 'GET' })); };
  QA.post = function (path, body, opts) {
    return QA.api(path, Object.assign({}, opts, { method: 'POST', body: body === undefined ? {} : body }));
  };
  QA.put = function (path, body, opts) {
    return QA.api(path, Object.assign({}, opts, { method: 'PUT', body: body === undefined ? {} : body }));
  };
  QA.patch = function (path, body, opts) {
    return QA.api(path, Object.assign({}, opts, { method: 'PATCH', body: body === undefined ? {} : body }));
  };
  QA.del = function (path, opts) { return QA.api(path, Object.assign({}, opts, { method: 'DELETE' })); };

  /* ------------------------------------------------------------------ Toast */
  QA.toast = function (msg, type) {
    var root = QA.el('qa-toast-root');
    if (!root) return;
    var t = type || 'info';
    var div = document.createElement('div');
    div.className = 'toast toast-' + t;
    div.textContent = msg;
    root.appendChild(div);
    setTimeout(function () {
      div.classList.add('out');
      setTimeout(function () { if (div.parentNode) div.parentNode.removeChild(div); }, 260);
    }, 3200);
  };

  /* ------------------------------------------------------------------ 模态框 */
  var escHandler = null;
  QA.closeModal = function () {
    var root = QA.el('qa-modal-root');
    if (root) root.innerHTML = '';
    document.body.style.overflow = '';
    if (escHandler) { document.removeEventListener('keydown', escHandler); escHandler = null; }
  };
  /* 返回 modal-body 元素，便于调用方绑定事件 */
  QA.openModal = function (title, innerHtml, wide) {
    QA.closeModal();
    var root = QA.el('qa-modal-root');
    if (!root) return null;
    var wrap = document.createElement('div');
    wrap.className = 'modal-backdrop';
    wrap.innerHTML =
      '<div class="modal' + (wide ? ' modal-wide' : '') + '" role="dialog" aria-modal="true">' +
        '<div class="modal-head"><h3>' + QA.esc(title) + '</h3>' +
          '<button class="modal-close" aria-label="关闭">&times;</button></div>' +
        '<div class="modal-body">' + (innerHtml || '') + '</div>' +
      '</div>';
    root.appendChild(wrap);
    document.body.style.overflow = 'hidden';

    wrap.addEventListener('click', function (e) {
      if (e.target === wrap) QA.closeModal();
      if (e.target.classList && e.target.classList.contains('modal-close')) QA.closeModal();
    });
    escHandler = function (e) { if (e.key === 'Escape') QA.closeModal(); };
    document.addEventListener('keydown', escHandler);
    return wrap.querySelector('.modal-body');
  };

  /* 危险操作二次确认：明确警示 + 勾选确认 */
  QA.confirmDanger = function (title, message, onConfirm) {
    var html =
      '<div class="alert alert-danger">' + QA.esc(message) + '</div>' +
      '<label class="check" style="margin-top:14px"><input type="checkbox" id="qa-danger-ack"> 我已知晓风险，确认执行此操作</label>' +
      '<div class="modal-actions">' +
        '<button class="btn" id="qa-danger-cancel">取消</button>' +
        '<button class="btn btn-danger" id="qa-danger-ok" disabled>确认执行</button>' +
      '</div>';
    var body = QA.openModal(title, html);
    if (!body) return;
    var ack = body.querySelector('#qa-danger-ack');
    var ok = body.querySelector('#qa-danger-ok');
    var cancel = body.querySelector('#qa-danger-cancel');
    ack.addEventListener('change', function () { ok.disabled = !ack.checked; });
    cancel.addEventListener('click', QA.closeModal);
    ok.addEventListener('click', function () {
      if (!ack.checked) return;
      ok.disabled = true;
      Promise.resolve(onConfirm()).then(function () { QA.closeModal(); })
        .catch(function (e) { QA.toast(QA.errMsg(e), 'error'); ok.disabled = false; });
    });
  };

  /* ------------------------------------------------------------------ SSE 流 */
  /* 解析每行 data: {...}，事件对象 type = delta / done / error / retry */
  QA.stream = function (path, body, opts) {
    opts = opts || {};
    var signal = opts.signal;
    return fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
      body: JSON.stringify(body || {}),
      signal: signal
    }).then(async function (res) {
      if (!res.ok) {
        var m = 'HTTP ' + res.status;
        try {
          var j = await res.json();
          if (j && j.error && j.error.message) m = j.error.message;
        } catch (e) { /* ignore */ }
        var err = new Error(m);
        if (opts.onError) opts.onError(err);
        return;
      }
      if (!res.body) {
        if (opts.onError) opts.onError(new Error('当前浏览器不支持流式响应'));
        return;
      }
      var reader = res.body.getReader();
      var decoder = new TextDecoder('utf-8');
      var buffer = '';
      var dataLines = [];
      var full = '';
      var gotDone = false;

      function handleEvent() {
        if (!dataLines.length) return;
        var raw = dataLines.join('\n').trim();
        dataLines = [];
        if (!raw || raw === '[DONE]') return;
        var evt;
        try { evt = JSON.parse(raw); } catch (e) { return; }
        if (evt.type === 'delta') {
          var piece = evt.content || '';
          full += piece;
          if (opts.onDelta) opts.onDelta(piece, evt, full);
        } else if (evt.type === 'done') {
          gotDone = true;
          if (opts.onDone) opts.onDone(evt, full);
        } else if (evt.type === 'error') {
          if (opts.onError) opts.onError(new Error(evt.message || '生成出错'), evt);
        } else if (evt.type === 'retry') {
          if (opts.onRetry) opts.onRetry(evt);
          if (opts.onDelta) opts.onDelta('\n[系统] ' + (evt.message || '重试中') + '\n', evt, full);
        }
      }

      while (true) {
        var chunk;
        try { chunk = await reader.read(); }
        catch (e) {
          if (e && e.name === 'AbortError') return;   /* 用户点「停止生成」 */
          if (opts.onError) opts.onError(e);
          return;
        }
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true });
        buffer = buffer.replace(/\r\n/g, '\n');
        var idx;
        while ((idx = buffer.indexOf('\n')) !== -1) {
          var line = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 1);
          if (line === '') { handleEvent(); continue; }
          if (line.indexOf('data:') === 0) {
            dataLines.push(line.slice(5).replace(/^ /, ''));
          }
          /* 其余 SSE 字段（event/id/retry/:注释）忽略 */
        }
      }
      handleEvent();
      if (!gotDone && opts.onDone) opts.onDone({ type: 'done', content: full }, full);
    }).catch(function (e) {
      if (e && e.name === 'AbortError') return;      /* 主动停止，不算错误 */
      if (opts.onError) opts.onError(e);
    });
  };

  /* ------------------------------------------------------------------ 格式化 / 标签 */
  QA.fmtDate = function (v, withTime) {
    if (!v) return '—';
    var s = String(v).trim().replace('T', ' ');
    s = s.replace(/\.\d+/, '');
    var plus = s.indexOf('+');
    if (plus !== -1) s = s.slice(0, plus);
    if (/Z$/.test(s)) s = s.replace(/Z$/, '');
    s = s.trim();
    if (withTime === false) return s.slice(0, 10);
    return s.slice(0, 19);
  };

  var STATUS_LABELS = {
    pending: '待开始', learning: '学习中', learned: '已学题解', ac: '独立通过',
    reviewed: '已完成复习', skipped: '已跳过', overdue: '已逾期',
    draft: '草稿', confirmed: '已确认', active: '已激活',
    running: '进行中', ok: '正常', failed: '失败', stale: '已过期',
    error: '错误', aborted: '已中断', degraded: '降级', manual: '手工录入',
    synced: '已同步', not_configured: '未配置', unavailable: '不可用', configured: '已配置'
  };
  QA.statusLabel = function (s) {
    if (s === null || s === undefined || s === '') return '—';
    return STATUS_LABELS[s] || String(s);
  };
  var STATUS_CLASS = {
    pending: 'badge-muted', learning: 'badge-info', learned: 'badge-primary',
    ac: 'badge-success', reviewed: 'badge-success', skipped: 'badge-muted', overdue: 'badge-danger',
    draft: 'badge-muted', confirmed: 'badge-primary', running: 'badge-info',
    ok: 'badge-success', configured: 'badge-success', active: 'badge-success',
    failed: 'badge-danger', stale: 'badge-warn', error: 'badge-danger',
    degraded: 'badge-warn', not_configured: 'badge-warn', unavailable: 'badge-danger'
  };
  QA.statusClass = function (s) { return STATUS_CLASS[s] || 'badge-muted'; };
  QA.badge = function (text, cls) {
    return '<span class="badge ' + (cls || 'badge-muted') + '">' + QA.esc(text) + '</span>';
  };
  QA.statusBadge = function (s) {
    return '<span class="badge ' + QA.statusClass(s) + '">' + QA.esc(QA.statusLabel(s)) + '</span>';
  };

  var DIFFICULTY = { 1: '入门', 2: '基础', 3: '进阶', 4: '提高', 5: '竞赛' };
  QA.difficultyLabel = function (n) { return DIFFICULTY[n] || (n ? String(n) : '—'); };
  QA.difficultyBadge = function (n) {
    var cls = n >= 4 ? 'badge-danger' : (n === 3 ? 'badge-warn' : 'badge-primary');
    return '<span class="badge ' + cls + '">' + QA.esc(QA.difficultyLabel(n)) + ' · ' + QA.esc(n) + '/5</span>';
  };

  var SOURCES = {
    codeforces: 'Codeforces', luogu: '洛谷', leetcode: '力扣',
    atcoder: 'AtCoder', lanqiao: '蓝桥杯', other: '其他'
  };
  QA.sourceLabel = function (s) { return SOURCES[s] || String(s || '—'); };
  QA.sourceOptions = function (selected) {
    return Object.keys(SOURCES).map(function (k) {
      return '<option value="' + k + '"' + (k === selected ? ' selected' : '') + '>' + QA.esc(SOURCES[k]) + '</option>';
    }).join('');
  };
  var CATEGORIES = { knowledge: '知识点', data_structure: '数据结构', algorithm: '算法' };
  QA.categoryLabel = function (c) { return CATEGORIES[c] || String(c || '—'); };

  /* 极简 Markdown 渲染（先转义再变换，避免注入） */
  QA.mdToHtml = function (text) {
    var s = QA.esc(text || '');
    var blocks = [];
    var lines = s.split('\n');
    var inCode = false, codeBuf = [], listBuf = [];
    function flushList() {
      if (listBuf.length) { blocks.push('<ul><li>' + listBuf.join('</li><li>') + '</li></ul>'); listBuf = []; }
    }
    function inline(t) {
      return t.replace(/`([^`]+)`/g, '<code class="inline">$1</code>')
              .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    }
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      if (/^```/.test(line.trim())) {
        flushList();
        if (inCode) { blocks.push('<pre>' + codeBuf.join('\n') + '</pre>'); codeBuf = []; inCode = false; }
        else { inCode = true; }
        continue;
      }
      if (inCode) { codeBuf.push(line); continue; }
      var t = line.trim();
      if (/^###\s+/.test(t)) { flushList(); blocks.push('<h3>' + inline(t.replace(/^###\s+/, '')) + '</h3>'); }
      else if (/^##\s+/.test(t)) { flushList(); blocks.push('<h2>' + inline(t.replace(/^##\s+/, '')) + '</h2>'); }
      else if (/^#\s+/.test(t)) { flushList(); blocks.push('<h1>' + inline(t.replace(/^#\s+/, '')) + '</h1>'); }
      else if (/^[-*]\s+/.test(t)) { listBuf.push(inline(t.replace(/^[-*]\s+/, ''))); }
      else if (/^&gt;\s?/.test(t)) { flushList(); blocks.push('<blockquote>' + inline(t.replace(/^&gt;\s?/, '')) + '</blockquote>'); }
      else if (t === '') { flushList(); blocks.push('<p></p>'); }
      else { flushList(); blocks.push('<p>' + inline(line) + '</p>'); }
    }
    flushList();
    if (inCode && codeBuf.length) blocks.push('<pre>' + codeBuf.join('\n') + '</pre>');
    return '<div class="md">' + blocks.join('') + '</div>';
  };

  /* 浏览器触发文件下载（用于 JSON 快照） */
  QA.downloadBlob = function (content, filename, mime) {
    var blob = new Blob([content], { type: mime || 'application/json;charset=utf-8' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 1500);
  };

  global.QA = QA;
})(window);
