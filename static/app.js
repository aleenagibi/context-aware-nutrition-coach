/* Safe, lightweight Markdown rendering for Fuel Coach replies. */
function renderCoachMarkdown(root) {
  root = root || document;
  const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const inline = (s) => {
    let out = esc(s);
    out = out.replace(/`([^`]+)`/g, '<code>$1</code>');
    out = out.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/__(.+?)__/g, '<strong>$1</strong>');
    out = out.replace(/(?<!\*)\*([^*\n]+)\*(?!\*)/g, '<em>$1</em>');
    out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    return out;
  };
  root.querySelectorAll('[data-markdown]:not([data-markdown-ready])').forEach((node) => {
    const source = (node.textContent || '').replace(/\r/g, '').trim();
    const lines = source.split('\n');
    const blocks = []; let para = [], list = [], listType = '', table = [];
    const flushPara = () => { if (para.length) { blocks.push('<p>' + inline(para.join(' ')) + '</p>'); para = []; } };
    const flushList = () => { if (list.length) { blocks.push('<' + listType + '>' + list.map(x => '<li>' + inline(x) + '</li>').join('') + '</' + listType + '>'); list = []; listType = ''; } };
    const flushTable = () => {
      if (!table.length) return;
      const rows = table.filter(row => !/^\s*\|?\s*:?-{3,}/.test(row));
      if (rows.length) {
        const cells = row => row.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map(c => c.trim());
        const head = cells(rows[0]);
        let html = '<div class="coach-table-wrap"><table><thead><tr>' + head.map(c => '<th>' + inline(c) + '</th>').join('') + '</tr></thead><tbody>';
        rows.slice(1).forEach(row => { html += '<tr>' + cells(row).map(c => '<td>' + inline(c) + '</td>').join('') + '</tr>'; });
        blocks.push(html + '</tbody></table></div>');
      }
      table = [];
    };
    lines.forEach(line => {
      const t = line.trim();
      if (!t) { flushPara(); flushList(); flushTable(); return; }
      if (t.includes('|') && /^\|?\s*:?-{3,}/.test(t) === false && (table.length || /^\|/.test(t))) { flushPara(); flushList(); table.push(t); return; }
      if (table.length && t.includes('|')) { table.push(t); return; }
      flushTable();
      const h = t.match(/^(#{1,3})\s+(.+)$/);
      if (h) { flushPara(); flushList(); blocks.push('<h' + (h[1].length + 2) + '>' + inline(h[2]) + '</h' + (h[1].length + 2) + '>'); return; }
      const li = t.match(/^([-*•]|\d+\.)\s+(.+)$/);
      if (li) { flushPara(); const type = /^\d/.test(li[1]) ? 'ol' : 'ul'; if (list.length && listType !== type) flushList(); listType = type; list.push(li[2]); return; }
      flushList(); para.push(t);
    });
    flushPara(); flushList(); flushTable();
    node.innerHTML = blocks.join('');
    node.dataset.markdownReady = '1';
  });
}

/* Fuel: small, dependency-free UI glue. Forms post in place and the server returns refreshed HTML panels. */
(function () {
  'use strict';
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var csrf = ($('meta[name=csrf]') || {}).content || '';
  var wide = window.matchMedia('(min-width: 1024px)');
  var askFuelMobile = window.matchMedia('(max-width: 699px)');

  /* Responsive Ask Fuel controls + native desktop resize handle.
     Styles are applied here so the user's edited CSS files stay untouched. */
  function configureAskFuelLayout() {
    var nav = $('.tabbar');
    var navButton = $('#tab-ask-fuel');
    var launcher = $('#fuel-chat-launcher');
    var chatWindow = $('#fuel-chat-window');
    if (!navButton || !launcher || !chatWindow) return;

    if (askFuelMobile.matches) {
      if (nav) {
        nav.style.display = 'grid';
        nav.style.gridTemplateColumns = 'repeat(6, minmax(0, 1fr))';
      }
      navButton.style.display = 'flex';
      navButton.style.flexDirection = 'column';
      navButton.style.alignItems = 'center';
      navButton.style.justifyContent = 'center';
      navButton.style.minWidth = '0';
      navButton.style.width = '100%';
      launcher.style.display = 'none';
      chatWindow.style.resize = 'none';
      chatWindow.style.width = '100%';
      chatWindow.style.height = 'min(78dvh, 680px)';
      chatWindow.style.maxWidth = '100%';
    } else {
      if (nav) {
        nav.style.display = '';
        nav.style.gridTemplateColumns = '';
      }
      navButton.style.display = 'none';
      launcher.style.display = '';
      chatWindow.style.resize = 'both';
      chatWindow.style.overflow = 'hidden';
      chatWindow.style.minWidth = '320px';
      chatWindow.style.minHeight = '340px';
      chatWindow.style.maxWidth = 'min(720px, calc(100vw - 32px))';
      chatWindow.style.maxHeight = 'min(85dvh, 820px)';
      if (!chatWindow.style.width) chatWindow.style.width = '390px';
      if (!chatWindow.style.height) chatWindow.style.height = '560px';
    }
  }

  /* Keep one Coach instance in the floating chat, not inside the You page. */
  function mountCoach() {
    var mount = $('#fuel-chat-mount');
    var coach = $('.coach-shell', $('#panel-you') || document);
    if (mount && coach && coach.parentElement !== mount) mount.appendChild(coach);
  }
  function setCoachOpen(open) {
    var assistant = $('#fuel-assistant'), launcher = $('#fuel-chat-launcher'), windowEl = $('#fuel-chat-window');
    if (!assistant) return;
    assistant.classList.toggle('is-open', !!open);
    if (launcher) launcher.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (windowEl) { windowEl.setAttribute('aria-hidden', open ? 'false' : 'true'); windowEl.inert = !open; }
    if (open) {
      mountCoach();
      var input = $('#coach-question');
      if (input && !input.disabled) window.setTimeout(function(){ input.focus(); }, 80);
    }
  }
  configureAskFuelLayout();
  if (askFuelMobile.addEventListener) askFuelMobile.addEventListener('change', configureAskFuelLayout);
  else if (askFuelMobile.addListener) askFuelMobile.addListener(configureAskFuelLayout);
  window.addEventListener('resize', configureAskFuelLayout);

  var sheet = $('#detail'), body = $('#detail-body'), scrim = $('#scrim'), lastFocus = null;

  /* ---- tabs (hash routing; without JS every panel is simply visible) ---- */
  function show(id) {
    var panels = $$('[data-panel]');
    if (!panels.some(function (p) { return p.dataset.panel === id; })) id = 'today';
    panels.forEach(function (p) { p.hidden = p.dataset.panel !== id; });
    $$('[data-tab]').forEach(function (a) {
      var on = a.dataset.tab === id; a.classList.toggle('active', on);
      if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
    window.scrollTo(0, 0);
  }
  window.addEventListener('hashchange', function () { show(location.hash.slice(1)); });
  show(location.hash.slice(1) || 'today');

  /* ---- toast ---- */
  var toastTimer;
  function toast(msg, cat) {
    var t = $('#toast'); t.textContent = msg; t.className = 'show ' + (cat || '');
    clearTimeout(toastTimer); toastTimer = setTimeout(function () { t.className = ''; }, 4200);
  }

  /* ---- sheet / detail pane ---- */
  function openSheet(html) {
    lastFocus = document.activeElement;
    body.innerHTML = html;
    sheet.classList.add('open'); sheet.setAttribute('aria-hidden', 'false');
    scrim.hidden = false; document.body.classList.add('noscroll');
    sheet.scrollTop = 0; sheet.focus({ preventScroll: true });
  }
  function closeSheet() {
    sheet.classList.remove('open'); sheet.setAttribute('aria-hidden', 'true');
    scrim.hidden = true; document.body.classList.remove('noscroll');
    body.innerHTML = '';
    if (lastFocus && lastFocus.focus) lastFocus.focus({ preventScroll: true });
  }
  sheet.setAttribute('tabindex', '-1');
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && sheet.classList.contains('open')) closeSheet(); });

  /* ---- applying a server response ---- */
  function apply(d) {
    if (d.panels) Object.keys(d.panels).forEach(function (k) { var el = $('#panel-' + k); if (el) el.innerHTML = d.panels[k]; });
    mountCoach();
    renderCoachMarkdown(document);
    var chatMessages = $('.fuel-chat-mount .coach-messages');
    if (chatMessages) chatMessages.scrollTop = chatMessages.scrollHeight;
    loadFoodImages();
    if (d.sheet) openSheet(d.sheet);
    if (d.close) closeSheet();
    if (d.flash) toast(d.flash[1], d.flash[0]);
  }
  function post(form) {
    return fetch(form.action, { method: 'POST', body: new FormData(form), headers: { 'X-CSRF-Token': csrf, 'X-Requested-With': 'fetch' } })
      .then(function (r) {
        if (!r.ok && r.status === 400) throw new Error('Your session expired. Reload the page and try again.');
        return r.json();
      });
  }

  document.addEventListener('submit', function (e) {
    var form = e.target.closest('form[data-ajax]'); if (!form) return;
    e.preventDefault();
    var btn = form.querySelector('button[type=submit]'), label = btn && btn.textContent;
    if (btn) { btn.disabled = true; btn.textContent = 'Working…'; }
    post(form).then(apply).catch(function (err) { toast(err.message || 'Network error. Is the server running?', 'error'); })
      .then(function () { if (btn && document.contains(btn)) { btn.disabled = false; btn.textContent = label; } });
  });

  document.addEventListener('change', function (e) {
    var el = e.target.closest('[data-autosubmit]'); if (el && el.form) el.form.requestSubmit();
  });

  document.addEventListener('click', function (e) {
    var open = e.target.closest('[data-open]');
    if (open) {
      e.preventDefault();
      fetch(open.dataset.open, { headers: { 'X-Requested-With': 'fetch' } })
        .then(function (r) { if (!r.ok) throw new Error('Could not load that.'); return r.text(); })
        .then(openSheet).catch(function (err) { toast(err.message, 'error'); });
      return;
    }
    if (e.target.closest('[data-close]')) { closeSheet(); return; }
    var tab = e.target.closest('[data-subtab]');
    if (tab) {
      var root = tab.closest('.recipe');
      $$('[data-subtab]', root).forEach(function (b) { b.classList.toggle('on', b === tab); });
      $$('[data-subpanel]', root).forEach(function (p) { p.hidden = p.dataset.subpanel !== tab.dataset.subtab; });
    }
  });

  /* Ask Fuel launcher: desktop floating action and compact mobile nav action. */
  document.addEventListener('click', function(e) {
    if (e.target.closest('#fuel-chat-launcher, #tab-ask-fuel')) {
      e.preventDefault();
      setCoachOpen(!$('#fuel-assistant').classList.contains('is-open'));
      return;
    }
    if (e.target.closest('#fuel-chat-close')) { setCoachOpen(false); return; }
    if (e.target === $('#fuel-assistant') && $('#fuel-assistant').classList.contains('is-open')) {
      setCoachOpen(false);
    }
  });
  mountCoach();

  /* ---- background-generated recipe photos ---- */
  function loadFoodImages() {
    $$('[data-food-image]').forEach(function (holder) {
      if (holder.dataset.loading === '1' || holder.dataset.loaded === '1') return;
      holder.dataset.loading = '1';
      var url = holder.dataset.foodImage;
      var attempts = 0;
      function poll() {
        if (!document.contains(holder)) return;
        fetch(url, { headers: { 'X-Requested-With': 'fetch' }, cache: 'no-store' })
          .then(function (r) {
            if (r.status === 202) {
              attempts += 1;
              if (attempts < 90) window.setTimeout(poll, 2000);
              else holder.dataset.loading = '0';
              return null;
            }
            if (!r.ok) throw new Error('Image unavailable');
            return r.blob();
          })
          .then(function (blob) {
            if (!blob || !document.contains(holder)) return;
            var image = document.createElement('img');
            image.className = 'art-img' + (holder.classList.contains('big') ? ' big' : '');
            image.alt = '';
            image.loading = 'lazy';
            image.decoding = 'async';
            image.src = URL.createObjectURL(blob);
            image.onload = function () { holder.replaceChildren(image); holder.dataset.loaded = '1'; };
          })
          .catch(function () { holder.dataset.loading = '0'; });
      }
      poll();
    });
  }
  loadFoodImages();
  renderCoachMarkdown(document);

  /* ---- pantry search ---- */
  document.addEventListener('input', function (e) {
    var box = e.target.closest('[data-filter]'); if (!box) return;
    var q = box.value.trim().toLowerCase(), scope = $(box.dataset.filter);
    $$('.chip', scope).forEach(function (c) { c.hidden = q && c.dataset.text.indexOf(q) === -1; });
    $$('fieldset', scope).forEach(function (f) { f.hidden = !$$('.chip:not([hidden])', f).length; });
  });
})();


// AI Coach quick prompts: fill the composer so the user can review before sending.
document.addEventListener("click", (event) => {
  const prompt = event.target.closest("[data-coach-prompt]");
  if (!prompt) return;
  const panel = prompt.closest(".coach-chat");
  const input = panel && panel.querySelector("#coach-question");
  if (input) { input.value = prompt.dataset.coachPrompt || ""; input.focus(); }
});
