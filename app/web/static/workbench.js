/* 题目工作台（FR-31）· 全局浮层组件
 * 依赖 app.js 的 window.QA；零外链、断网可用。
 * 用法：QA.openWorkbench(problemId) / QA.openWorkbench(id, { standalone: true })
 */
(function (global) {
  'use strict';
  var QA = global.QA;
  if (!QA) return;

  var ROOT_ID = 'qa-wb-root';
  var TABS = [
    ['statement', '题面'], ['code', '代码'], ['ai', 'AI 分析'],
    ['records', '学习记录'], ['ideas', '思路笔记']
  ];
  var state = null; // { id, data, standalone, controller, dirty, aiText, snapIndex }

  function ensureRoot() {
    var r = document.getElementById(ROOT_ID);
    if (!r) {
      r = document.createElement('div');
      r.id = ROOT_ID;
      document.body.appendChild(r);
    }
    return r;
  }

  function setStatus(text) {
    var el = document.getElementById('wb-status');
    if (el) el.textContent = text;
  }

  /* AI 输出里的【新知识】标记高亮（qa.mdToHtml 已转义，这里只插入固定标签） */
  function highlightNew(html) {
    return String(html || '').replace(/【新知识】/g, '<span class="wb-new">【新知识】</span>');
  }

  function abilityHtml(a) {
    if (!a) return '';
    function chips(list, cls, prefix) {
      return (list || []).map(function (n) {
        return '<span class="chip ' + cls + '">' + QA.esc(prefix + n.name) +
          ' <span class="small">' + n.level + '/5</span></span>';
      }).join('');
    }
    var marked = chips(a.new_knowledge, 'wb-chip-new', '【新知识】') +
      chips(a.weak, 'wb-chip-weak', '薄弱 · ') +
      chips(a.known, 'wb-chip-known', '已掌握 · ');
    return '<div class="wb-ability">' +
      '<div class="row-between"><div><strong>能力匹配：</strong>' +
        QA.badge(a.verdict, a.new_knowledge && a.new_knowledge.length ? 'badge-warn' :
          (a.verdict === '偏难' ? 'badge-danger' : 'badge-success')) +
        ' <span class="small muted">我的水平 ' + QA.esc(a.overall_level) + '/5 · 本题难度 ' +
        QA.esc(a.difficulty) + '/5</span></div>' +
        '<span class="small">' + (a.profile_ready ? '画像已启用' : '尚未建立画像') + '</span></div>' +
      '<div class="small" style="margin-top:6px">' + QA.esc(a.advice) + '</div>' +
      (marked ? '<div class="wb-chips" style="margin-top:8px">' + marked + '</div>'
        : '<div class="hint" style="margin-top:6px">这道题还没有标注知识点，可到「知识点与标签」页维护。</div>') +
      '<div class="hint" style="margin-top:6px">带【新知识】的知识点，AI 分析会单独标记并给出定义、例子、前置知识与最小可用模板；' +
        '掌握度可在左侧「能力画像」随时调整。</div>' +
    '</div>';
  }

  function shellHtml(standalone) {
    var tabs = TABS.map(function (t, i) {
      return '<button class="wb-tab' + (i === 0 ? ' active' : '') + '" data-tab="' + t[0] + '">' + t[1] + '</button>';
    }).join('');
    var panes = TABS.map(function (t, i) {
      return '<div class="wb-pane' + (i === 0 ? ' active' : '') + '" data-pane="' + t[0] + '" id="wb-pane-' + t[0] + '"></div>';
    }).join('');
    return '<div class="wb-window' + (standalone ? ' wb-window-standalone' : '') + '" id="wb-window">' +
      '<div class="wb-head" id="wb-head">' +
        '<div class="wb-head-title"><span class="wb-kicker">题目工作台</span>' +
          '<strong id="wb-title">加载中…</strong><span id="wb-sub" class="wb-sub"></span></div>' +
        '<div class="wb-head-actions">' +
          (standalone ? '' :
            '<button class="wb-icon" id="wb-popout" title="弹出为独立窗口">独立窗口</button>' +
            '<button class="wb-icon" id="wb-max" title="最大化 / 还原">最大化</button>') +
          '<button class="wb-icon wb-icon-close" id="wb-close" title="关闭">关闭</button>' +
        '</div>' +
      '</div>' +
      '<div class="wb-tabs">' + tabs + '</div>' +
      '<div class="wb-body">' + panes + '</div>' +
      '<div class="wb-foot"><span id="wb-status">正在打开…</span>' +
        '<span>题面快照仅本地 · AI 分析由你主动触发</span></div>' +
    '</div>';
  }

  /* ------------------------------------------------------------ 窗口行为 */
  function bindChrome(win) {
    var tabs = win.querySelectorAll('.wb-tab');
    Array.prototype.forEach.call(tabs, function (tab) {
      tab.addEventListener('click', function () {
        Array.prototype.forEach.call(tabs, function (t) { t.classList.remove('active'); });
        tab.classList.add('active');
        Array.prototype.forEach.call(win.querySelectorAll('.wb-pane'), function (p) {
          p.classList.toggle('active', p.getAttribute('data-pane') === tab.getAttribute('data-tab'));
        });
      });
    });
    var close = win.querySelector('#wb-close');
    if (close) close.addEventListener('click', QA.closeWorkbench);
    var pop = win.querySelector('#wb-popout');
    if (pop) pop.addEventListener('click', function () {
      global.open('/workbench/' + state.id, 'wb_' + state.id, 'width=1240,height=860');
      QA.toast('已弹出独立窗口，浮层仍保留', 'info');
    });
    var max = win.querySelector('#wb-max');
    if (max) max.addEventListener('click', function () {
      var on = win.classList.toggle('wb-maximized');
      max.textContent = on ? '还原' : '最大化';
    });

    if (!state.standalone) bindDrag(win);
  }

  function bindDrag(win) {
    var head = win.querySelector('#wb-head');
    var dragging = null;
    head.addEventListener('mousedown', function (e) {
      if (e.target.closest('button')) return;
      if (win.classList.contains('wb-maximized')) return;
      var rect = win.getBoundingClientRect();
      dragging = { dx: e.clientX - rect.left, dy: e.clientY - rect.top };
      win.style.left = rect.left + 'px';
      win.style.top = rect.top + 'px';
      win.style.right = 'auto';
      head.classList.add('dragging');
      e.preventDefault();
    });
    document.addEventListener('mousemove', function (e) {
      if (!dragging) return;
      var w = win.offsetWidth, h = win.offsetHeight;
      var left = Math.max(0, Math.min(global.innerWidth - 80, e.clientX - dragging.dx));
      var top = Math.max(0, Math.min(global.innerHeight - 60, e.clientY - dragging.dy));
      win.style.left = left + 'px';
      win.style.top = top + 'px';
      void w; void h;
    });
    document.addEventListener('mouseup', function () {
      dragging = null;
      head.classList.remove('dragging');
    });
  }

  /* ------------------------------------------------------------ 数据加载 */
  function fillHead(d) {
    var p = d.problem || {};
    document.getElementById('wb-title').textContent = p.title || ('题目 #' + state.id);
    var sub = document.getElementById('wb-sub');
    sub.innerHTML = QA.esc(QA.sourceLabel(p.source)) + ' · ' + QA.esc(p.source_key || '') +
      ' · ' + QA.difficultyBadge(p.difficulty_level) +
      (p.url ? ' · ' + QA.externalLink(p.url, '原题 ↗', 'small') : '');
  }

  function reload() {
    setStatus('正在加载题目工作台…');
    return QA.get('/api/problems/' + state.id + '/workbench').then(function (d) {
      state.data = d;
      state.snapIndex = 0;
      fillHead(d);
      renderStatement(d);
      renderCode(d);
      renderAI(d);
      renderRecords(d);
      renderIdeas(d);
      setStatus('已就绪 · 题面 ' + (d.snapshots || []).length + ' 份 · 记录 ' +
        (d.records || []).length + ' 条 · AI 分析 ' + (d.ai_calls || []).length + ' 次');
    }).catch(function (e) {
      var box = document.getElementById('wb-pane-statement');
      if (box) box.innerHTML = '<div class="alert alert-error">' + QA.esc(QA.errMsg(e)) + '</div>';
      setStatus('加载失败');
    });
  }

  /* ------------------------------------------------------------ 题面面板 */
  function renderStatement(d) {
    var p = d.problem || {}, snaps = d.snapshots || [];
    var nodes = (p.knowledge || []).map(function (k) {
      return '<span class="chip">' + QA.esc(k.name) + (k.verified ? '' : '（待确认）') + '</span>';
    }).join('') || '<span class="muted small">暂无</span>';
    var tags = (p.external_tags || []).map(function (t) {
      return '<span class="chip">' + QA.esc(t) + '</span>';
    }).join('') || '<span class="muted small">—</span>';
    var html = abilityHtml(d.ability) +
      '<div class="alert alert-warn">题面快照<strong>仅本地保存、不参与任何导出</strong>。' +
      'AI 分析会把题面 / 代码发送给你在「设置 → AI」配置的 DeepSeek，请自行确认使用边界。</div>' +
      '<div class="wb-meta">' +
        '<div><span>来源 / 题号</span><strong>' + QA.esc(QA.sourceLabel(p.source)) + ' · ' + QA.esc(p.source_key || '') + '</strong></div>' +
        '<div><span>难度</span><strong>' + QA.esc(QA.difficultyLabel(p.difficulty_level)) + ' · ' + QA.esc(p.difficulty_raw || '—') + '</strong></div>' +
        '<div><span>知识点</span><div>' + nodes + '</div></div>' +
        '<div><span>标签</span><div>' + tags + '</div></div>' +
        '<div><span>摘要</span><div>' + QA.esc(p.summary || '—') + '</div></div>' +
        '<div><span>原题</span><div>' + (p.url ? QA.externalLink(p.url, '打开原题 ↗', 'small') : '—') + '</div></div>' +
      '</div>' +
      '<div class="section-title">题面内容</div>';
    if (snaps.length) {
      html += '<div class="wb-toolbar"><label class="small">快照版本 ' +
        '<select id="wb-snap-sel">' + snaps.map(function (s, i) {
          return '<option value="' + i + '">#' + s.id + ' · ' + QA.esc(QA.fmtDate(s.captured_at)) + '</option>';
        }).join('') + '</select></label>' +
        '<span class="small muted">' + QA.esc(p.snapshots_count || snaps.length) + ' 份快照，显示最近 ' + snaps.length + ' 份</span></div>' +
        '<div id="wb-snap-body" class="wb-md"></div>';
    } else {
      html += QA.empty('这题还没有题面快照', '可粘贴你自己整理的内容，或到「题号采集」下载公开题面。') +
        '<form id="wb-snap-form" class="wb-form">' +
          '<div class="field"><label>题面内容（Markdown / 纯文本，仅本地）</label>' +
          '<textarea name="content_md" rows="6" required placeholder="粘贴或写下题目描述、输入输出格式、样例…"></textarea></div>' +
          '<button class="btn btn-sm btn-primary" type="submit">保存为本地快照</button></form>';
    }
    var box = document.getElementById('wb-pane-statement');
    box.innerHTML = html;
    if (snaps.length) {
      var sel = document.getElementById('wb-snap-sel');
      sel.addEventListener('change', function () {
        state.snapIndex = Number(sel.value) || 0;
        showSnapshot();
      });
      showSnapshot();
    } else {
      document.getElementById('wb-snap-form').addEventListener('submit', function (e) {
        e.preventDefault();
        var fd = new FormData(e.target);
        QA.post('/api/problems/' + state.id + '/snapshots', {
          content_md: String(fd.get('content_md') || ''),
          source_url: (d.problem || {}).url || ''
        }).then(function () {
          QA.toast('题面快照已保存（仅本地）', 'success');
          reload();
        }).catch(function (err) { QA.toast(QA.errMsg(err), 'error'); });
      });
    }
  }

  function showSnapshot() {
    var snaps = (state.data && state.data.snapshots) || [];
    var s = snaps[state.snapIndex || 0];
    var body = document.getElementById('wb-snap-body');
    if (!body) return;
    body.innerHTML = s
      ? '<div class="hint">保存于 ' + QA.esc(QA.fmtDate(s.captured_at)) +
        (s.source_url ? ' · ' + QA.externalLink(s.source_url, '来源 ↗', 'small') : '') + '</div>' +
        QA.mdToHtml(s.content_md || '')
      : QA.empty('快照内容为空');
  }

  function currentSnapshotMd() {
    var snaps = (state.data && state.data.snapshots) || [];
    var s = snaps[state.snapIndex || 0];
    return s ? String(s.content_md || '') : '';
  }

  /* ------------------------------------------------------------ 代码面板 */
  function renderCode(d) {
    var draft = d.draft || {}, langs = d.languages || ['C++'];
    var box = document.getElementById('wb-pane-code');
    box.innerHTML =
      '<div class="wb-toolbar">' +
        '<label class="small">语言 <select id="wb-lang">' + langs.map(function (l) {
          return '<option value="' + QA.esc(l) + '"' + (l === draft.language ? ' selected' : '') + '>' + QA.esc(l) + '</option>';
        }).join('') + '</select></label>' +
        '<button class="btn btn-sm btn-primary" id="wb-code-save">保存草稿</button>' +
        '<button class="btn btn-sm" id="wb-code-copy">复制</button>' +
        '<button class="btn btn-sm" id="wb-code-to-ai">送去 AI 诊断</button>' +
        '<span id="wb-code-hint" class="small muted">' + (draft.updated_at ? '上次保存 ' + QA.esc(QA.fmtDate(draft.updated_at)) : '尚未保存') + '</span>' +
      '</div>' +
      '<div class="wb-editor"><div class="wb-gutter" id="wb-gutter"></div>' +
        '<textarea id="wb-code" spellcheck="false" wrap="off" placeholder="// 在这里写你的解法，Ctrl/⌘ + S 保存草稿"></textarea></div>' +
      '<div class="hint">Tab 插入两个空格；草稿按题目保存到本机，不参与导出；不会自动提交到任何在线评测。</div>';

    var ta = document.getElementById('wb-code');
    var gutter = document.getElementById('wb-gutter');
    ta.value = draft.code || '';

    function syncGutter() {
      var n = ta.value.split('\n').length;
      var lines = [];
      for (var i = 1; i <= n; i++) lines.push(i);
      gutter.textContent = lines.join('\n');
      gutter.scrollTop = ta.scrollTop;
    }
    function markDirty() {
      state.dirty = true;
      setStatus('代码有未保存的修改');
    }
    syncGutter();
    ta.addEventListener('input', function () { syncGutter(); markDirty(); });
    ta.addEventListener('scroll', function () { gutter.scrollTop = ta.scrollTop; });
    ta.addEventListener('keydown', function (e) {
      if (e.key === 'Tab') {
        e.preventDefault();
        var s = ta.selectionStart, en = ta.selectionEnd;
        ta.value = ta.value.slice(0, s) + '  ' + ta.value.slice(en);
        ta.selectionStart = ta.selectionEnd = s + 2;
        syncGutter(); markDirty();
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        e.preventDefault();
        saveCode();
      }
    });
    document.getElementById('wb-code-save').addEventListener('click', function () { saveCode(); });
    document.getElementById('wb-code-copy').addEventListener('click', function () {
      try {
        if (global.navigator && global.navigator.clipboard) {
          global.navigator.clipboard.writeText(ta.value).then(function () { QA.toast('代码已复制', 'success'); });
        } else {
          ta.select(); document.execCommand('copy'); QA.toast('代码已复制', 'success');
        }
      } catch (err) { QA.toast('复制失败，请手动选中', 'error'); }
    });
    document.getElementById('wb-code-to-ai').addEventListener('click', function () {
      activateTab('ai');
      QA.toast('已切换到 AI 分析，点「诊断当前代码」即可', 'info');
    });
  }

  function saveCode(silent) {
    var ta = document.getElementById('wb-code');
    var lang = document.getElementById('wb-lang');
    if (!ta || !lang) return Promise.resolve();
    return QA.put('/api/problems/' + state.id + '/code', {
      language: lang.value, code: ta.value
    }).then(function (r) {
      if (state.data) state.data.draft = r;
      state.dirty = false;
      var hint = document.getElementById('wb-code-hint');
      if (hint) hint.textContent = '已保存 ' + QA.fmtDate(r.updated_at);
      setStatus('代码草稿已保存');
      if (!silent) QA.toast('代码草稿已保存（仅本地）', 'success');
    }).catch(function (e) { QA.toast(QA.errMsg(e), 'error'); });
  }

  function currentCode() {
    var ta = document.getElementById('wb-code');
    return ta ? ta.value : ((state.data && state.data.draft && state.data.draft.code) || '');
  }

  function activateTab(name) {
    var tab = document.querySelector('.wb-tab[data-tab="' + name + '"]');
    if (tab) tab.click();
  }

  /* ------------------------------------------------------------ AI 面板 */
  function aiHistoryHtml(calls) {
    if (!calls || !calls.length) return '<div class="hint">还没有针对这道题的 AI 分析记录。</div>';
    return calls.map(function (c) {
      return '<details class="wb-ai-item"><summary>' + QA.esc(c.scene_label || c.scene) +
        ' · ' + QA.esc(QA.fmtDate(c.created_at)) + ' ' + QA.badge(QA.statusLabel(c.status), QA.statusClass(c.status)) +
        '</summary><div class="wb-md">' + (c.answer ? highlightNew(QA.mdToHtml(c.answer)) : '<div class="hint">本次未生成内容（可能已中断或失败）</div>') + '</div></details>';
    }).join('');
  }

  function refreshAIHistory() {
    return QA.get('/api/problems/' + state.id + '/workbench').then(function (d) {
      state.data.ai_calls = d.ai_calls;
      var box = document.getElementById('wb-ai-history');
      if (box) box.innerHTML = aiHistoryHtml(d.ai_calls);
    });
  }

  function renderAI(d) {
    var box = document.getElementById('wb-pane-ai');
    box.innerHTML =
      '<div class="alert alert-warn">点击下面的按钮才会发起 AI 请求。<strong>题面 / 代码会离开本机</strong>，' +
      '发送给你在「设置 → AI」里配置的 DeepSeek；题面快照原本只存本地，请自行确认是否符合使用边界。' +
      '<strong>分析会自动按你的「能力画像」调整讲解深度</strong>，涉及未掌握的知识点会用【新知识】标记并详细展开；' +
      '想改变难度请到左侧「能力画像」调整掌握度。' +
      '结果由现有 AI 调用记录自动落库，仅供个人学习参考。</div>' +
      '<div class="wb-toolbar">' +
        '<button class="btn btn-sm btn-primary" id="wb-ai-idea">生成思路引导</button>' +
        '<button class="btn btn-sm" id="wb-ai-debug">诊断当前代码</button>' +
        '<button class="btn btn-sm" id="wb-ai-stop" disabled>停止生成</button>' +
      '</div>' +
      '<div class="field"><label>错误现象（代码诊断可选）</label>' +
        '<input type="text" id="wb-ai-phenomenon" placeholder="如：样例 2 输出 0 / 超时 / 段错误"></div>' +
      '<div class="section-title">本次分析</div>' +
      '<div id="wb-ai-output" class="wb-md"><div class="hint">尚未生成。思路引导会包含考察知识点、分步提示、复杂度与折叠的参考代码。</div></div>' +
      '<div class="row" id="wb-ai-actions" hidden>' +
        '<button class="btn btn-sm" id="wb-ai-save-note">把本次分析存为思路笔记</button></div>' +
      '<div class="section-title">历史分析</div><div id="wb-ai-history">' + aiHistoryHtml(d.ai_calls) + '</div>';

    document.getElementById('wb-ai-idea').addEventListener('click', function () { runAI('analyze'); });
    document.getElementById('wb-ai-debug').addEventListener('click', function () { runAI('debug'); });
    document.getElementById('wb-ai-stop').addEventListener('click', function () {
      if (state.controller) state.controller.abort();
    });
    document.getElementById('wb-ai-save-note').addEventListener('click', function () {
      if (!state.aiText) return;
      var p = state.data.problem || {};
      QA.post('/api/notes', {
        problem_id: state.id,
        title: 'AI 思路分析 · ' + (p.source_key || ('#' + state.id)),
        content_md: state.aiText
      }).then(function () {
        QA.toast('已保存为思路笔记', 'success');
        refreshIdeas();
      }).catch(function (e) { QA.toast(QA.errMsg(e), 'error'); });
    });
  }

  function runAI(scene) {
    var p = state.data.problem || {};
    var snap = currentSnapshotMd();
    var code = currentCode();
    var content;
    if (scene === 'analyze') {
      var parts = ['题目：' + (p.title || '') + '（' + QA.sourceLabel(p.source) + ' ' + (p.source_key || '') + '）'];
      if (snap) parts.push('【题面（本地快照）】\n' + snap.slice(0, 8000));
      if (code.trim()) parts.push('【我的代码草稿】\n' + code.slice(0, 6000));
      if (parts.length === 1) {
        QA.toast('请先保存题面快照，或在代码面板写一点内容', 'warn');
        return;
      }
      content = parts.join('\n\n');
    } else {
      if (!code.trim()) {
        QA.toast('请先在代码面板写代码，再诊断', 'warn');
        return;
      }
      content = code.slice(0, 12000);
    }

    var out = document.getElementById('wb-ai-output');
    var stop = document.getElementById('wb-ai-stop');
    var actions = document.getElementById('wb-ai-actions');
    var controller = new AbortController();
    state.controller = controller;
    state.aiText = '';
    out.innerHTML = QA.loading('正在生成，请稍候…');
    stop.disabled = false;
    actions.hidden = true;
    setStatus('AI 正在生成（' + (scene === 'analyze' ? '思路引导' : '代码诊断') + '）…');

    var payload = {
      problem_id: state.id,
      content: content,
      phenomenon: (document.getElementById('wb-ai-phenomenon') || {}).value || '未说明'
    };
    return QA.stream('/api/ai/' + scene, payload, {
      signal: controller.signal,
      onDelta: function (_piece, _evt, full) {
        state.aiText = full;
        out.innerHTML = highlightNew(QA.mdToHtml(full));
        out.scrollTop = out.scrollHeight;
      },
      onDone: function () {
        if (!state.aiText) out.innerHTML = '<div class="hint">模型没有返回内容，可重试。</div>';
        actions.hidden = !state.aiText;
        setStatus('AI 分析完成');
        refreshAIHistory();
      },
      onError: function (e) {
        out.innerHTML += '<div class="alert alert-error" style="margin-top:10px">' + QA.esc(QA.errMsg(e)) + '</div>';
        QA.toast(QA.errMsg(e), 'error');
        setStatus('AI 分析失败');
        refreshAIHistory();
      }
    }).then(function () {
      stop.disabled = true;
      state.controller = null;
    });
  }

  /* ------------------------------------------------------------ 记录面板 */
  function recordsListHtml(rows) {
    if (!rows || !rows.length) return '<div class="hint">这道题还没有练习记录。</div>';
    return '<div class="wb-list">' + rows.map(function (r) {
      return '<div class="wb-list-item"><div class="row-between"><div><strong>' +
        QA.esc(QA.statusLabel(r.result)) + '</strong> <span class="small muted">' + QA.esc(QA.fmtDate(r.created_at)) +
        ' · ' + QA.esc(r.language || '—') + ' · ' + QA.esc(r.time_spent_min || 0) + ' 分钟 · 掌握度 ' + QA.esc(r.mastery || 3) + '/5</span></div>' +
        QA.badge(r.mastery >= 4 ? '掌握良好' : '需复习', r.mastery >= 4 ? 'badge-success' : 'badge-warn') + '</div>' +
        (r.memo ? '<div class="small">' + QA.esc(r.memo) + '</div>' : '') +
        (r.code_snippet ? '<details><summary>查看当时代码</summary><pre>' + QA.esc(r.code_snippet.slice(0, 4000)) + '</pre></details>' : '') +
        '</div>';
    }).join('') + '</div>';
  }

  function refreshRecords() {
    return QA.get('/api/problems/' + state.id + '/workbench').then(function (d) {
      state.data.records = d.records;
      var box = document.getElementById('wb-records-list');
      if (box) box.innerHTML = recordsListHtml(d.records);
    });
  }

  function renderRecords(d) {
    var plans = d.plan_items || [];
    var box = document.getElementById('wb-pane-records');
    box.innerHTML =
      '<div class="section-title">历史练习记录</div><div id="wb-records-list">' + recordsListHtml(d.records) + '</div>' +
      '<div class="hr"></div><div class="section-title">登记本次练习</div>' +
      '<form id="wb-record-form" class="wb-form">' +
        '<div class="form-row-3">' +
          '<div class="field"><label>结果</label><select name="result">' +
            '<option value="done">完成</option><option value="partial">部分完成</option><option value="failed">未做出</option></select></div>' +
          '<div class="field"><label>耗时（分钟）</label><input type="number" name="time_spent_min" min="0" value="0"></div>' +
          '<div class="field"><label>语言</label><input type="text" name="language" value="' + QA.esc((d.draft || {}).language || 'C++') + '"></div>' +
        '</div>' +
        '<div class="form-row-3">' +
          '<div class="field"><label>掌握度 1–5</label><select name="mastery">' +
            [1,2,3,4,5].map(function (n) { return '<option value="' + n + '"' + (n === 3 ? ' selected' : '') + '>' + n + '</option>'; }).join('') +
          '</select></div>' +
          '<div class="field"><label>关联任务（可选）</label><select name="plan_item_id"><option value="">不关联</option>' +
            plans.map(function (it) { return '<option value="' + it.id + '">#' + it.id + ' · ' + QA.esc(QA.statusLabel(it.status)) + '</option>'; }).join('') +
          '</select></div>' +
          '<div class="field"><label>备注</label><input type="text" name="memo" placeholder="卡在哪一步？"></div>' +
        '</div>' +
        '<div class="hint">保存时会把代码面板当前内容一并记入 code_snippet；留空则只登记文字信息。</div>' +
        '<button class="btn btn-sm btn-primary" type="submit">保存练习记录</button>' +
      '</form>';
    document.getElementById('wb-record-form').addEventListener('submit', function (e) {
      e.preventDefault();
      var fd = new FormData(e.target);
      var payload = {
        problem_id: state.id,
        result: String(fd.get('result') || 'done'),
        time_spent_min: Number(fd.get('time_spent_min') || 0),
        language: String(fd.get('language') || 'C++'),
        mastery: Number(fd.get('mastery') || 3),
        memo: String(fd.get('memo') || ''),
        code_snippet: currentCode()
      };
      if (fd.get('plan_item_id')) payload.plan_item_id = Number(fd.get('plan_item_id'));
      QA.post('/api/records', payload).then(function () {
        QA.toast('练习记录已保存', 'success');
        refreshRecords();
        setStatus('练习记录已保存');
      }).catch(function (err) { QA.toast(QA.errMsg(err), 'error'); });
    });
  }

  /* ------------------------------------------------------------ 思路面板 */
  function ideasListHtml(notes) {
    if (!notes || !notes.length) return '<div class="hint">还没有思路笔记。可以把 AI 分析的结果一键存进来。</div>';
    return notes.map(function (n) {
      return '<details class="wb-ai-item" open><summary>' + QA.esc(n.title) + ' <span class="small muted">' +
        QA.esc(QA.fmtDate(n.updated_at)) + '</span></summary><div class="wb-md">' + highlightNew(QA.mdToHtml(n.content_md || '')) + '</div></details>';
    }).join('');
  }

  function refreshIdeas() {
    return QA.get('/api/problems/' + state.id + '/workbench').then(function (d) {
      state.data.notes = d.notes;
      var box = document.getElementById('wb-ideas-list');
      if (box) box.innerHTML = ideasListHtml(d.notes);
    });
  }

  function renderIdeas(d) {
    var p = d.problem || {};
    var nodes = (p.knowledge || []).map(function (k) {
      return '<span class="chip">' + QA.esc(k.name) + ' · ' + QA.esc(QA.categoryLabel(k.category)) + '</span>';
    }).join('') || '<span class="muted small">尚未标注知识点，可在「知识点与标签」页维护</span>';
    var latest = (d.ai_calls || [])[0];
    var box = document.getElementById('wb-pane-ideas');
    box.innerHTML =
      '<div class="section-title">考察知识点（大致方向）</div><div class="wb-chips">' + nodes + '</div>' +
      (latest && latest.answer
        ? '<div class="section-title">最近一次 AI 思路摘要</div><div class="wb-md">' + highlightNew(QA.mdToHtml(latest.answer.slice(0, 3000))) + '</div>'
        : '<div class="hint">还没有 AI 思路分析。到「AI 分析」页点「生成思路引导」，即可看到分步思路、复杂度与参考代码。</div>') +
      '<div class="hr"></div><div class="section-title">我的思路笔记</div><div id="wb-ideas-list">' + ideasListHtml(d.notes) + '</div>' +
      '<form id="wb-note-form" class="wb-form">' +
        '<div class="field"><label>标题</label><input type="text" name="title" value="思路总结"></div>' +
        '<div class="field"><label>内容（Markdown）</label><textarea name="content_md" rows="5" placeholder="写下关键观察、状态定义、转移方程、易错点…"></textarea></div>' +
        '<button class="btn btn-sm btn-primary" type="submit">保存思路笔记</button>' +
      '</form>';
    document.getElementById('wb-note-form').addEventListener('submit', function (e) {
      e.preventDefault();
      var fd = new FormData(e.target);
      QA.post('/api/notes', {
        problem_id: state.id,
        title: String(fd.get('title') || '思路总结'),
        content_md: String(fd.get('content_md') || '')
      }).then(function () {
        QA.toast('思路笔记已保存', 'success');
        e.target.reset();
        refreshIdeas();
      }).catch(function (err) { QA.toast(QA.errMsg(err), 'error'); });
    });
  }

  /* ------------------------------------------------------------ 对外 API */
  QA.openWorkbench = function (problemId, opts) {
    opts = opts || {};
    var id = Number(problemId);
    if (!id) { QA.toast('题目 ID 无效', 'error'); return; }
    if (state && state.controller) state.controller.abort();
    state = {
      id: id, data: null, standalone: !!opts.standalone,
      controller: null, dirty: false, aiText: '', snapIndex: 0
    };
    var root = ensureRoot();
    document.body.classList.toggle('wb-standalone', !!opts.standalone);
    root.innerHTML = shellHtml(state.standalone);
    var win = document.getElementById('wb-window');
    bindChrome(win);
    reload();
  };

  QA.closeWorkbench = function () {
    if (state && state.controller) state.controller.abort();
    var root = document.getElementById(ROOT_ID);
    if (root) root.innerHTML = '';
    document.body.classList.remove('wb-standalone');
    state = null;
  };
})(window);
