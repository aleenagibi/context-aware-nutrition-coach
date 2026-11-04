/* Safe, lightweight Markdown rendering for Fuel Coach replies. */
function renderCoachMarkdown(root) {
  root = root || document;

  const esc = (s) => String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');

  const inline = (source) => {
    let out = esc(source);

    // Protect inline code while formatting the rest.
    const codeParts = [];
    out = out.replace(/`([^`]+)`/g, (_, value) => {
      const token = `@@CODE${codeParts.length}@@`;
      codeParts.push('<code>' + value + '</code>');
      return token;
    });

    out = out.replace(
      /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>'
    );
    out = out.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    out = out.replace(/__(.+?)__/g, '<strong>$1</strong>');
    out = out.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>');
    out = out.replace(/(^|[^_])_([^_\n]+)_(?!_)/g, '$1<em>$2</em>');

    codeParts.forEach((html, index) => {
      out = out.replace(`@@CODE${index}@@`, html);
    });

    return out;
  };

  root.querySelectorAll('[data-markdown]:not([data-markdown-ready])').forEach((node) => {
    const source = (node.textContent || '').replace(/\r/g, '').trim();
    const lines = source.split('\n');

    const blocks = [];
    let para = [];
    let list = [];
    let listType = '';
    let table = [];

    const flushPara = () => {
      if (para.length) {
        blocks.push('<p>' + inline(para.join(' ')) + '</p>');
        para = [];
      }
    };

    const flushList = () => {
      if (list.length) {
        blocks.push(
          '<' + listType + '>' +
          list.map(item => '<li>' + inline(item) + '</li>').join('') +
          '</' + listType + '>'
        );
        list = [];
        listType = '';
      }
    };

    const splitCells = (row) => row.trim()
      .replace(/^\|/, '')
      .replace(/\|$/, '')
      .split('|')
      .map(cell => cell.trim());

    const flushTable = () => {
      if (!table.length) return;

      const rows = table.filter(row =>
        !/^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(row)
      );

      if (rows.length) {
        const head = splitCells(rows[0]);

        let html =
          '<div class="coach-table-wrap"><table><thead><tr>' +
          head.map(cell => '<th>' + inline(cell) + '</th>').join('') +
          '</tr></thead><tbody>';

        rows.slice(1).forEach(row => {
          html += '<tr>' +
            splitCells(row).map(cell => '<td>' + inline(cell) + '</td>').join('') +
            '</tr>';
        });

        blocks.push(html + '</tbody></table></div>');
      }

      table = [];
    };

    lines.forEach(line => {
      const t = line.trim();

      if (!t) {
        flushPara();
        flushList();
        flushTable();
        return;
      }

      // Detect Markdown tables.
      if (t.includes('|') && (table.length || (t.startsWith('|') && t.endsWith('|')))) {
        flushPara();
        flushList();
        table.push(t);
        return;
      }

      flushTable();

      const heading = t.match(/^(#{1,3})\s+(.+)$/);

      if (heading) {
        flushPara();
        flushList();

        const level = heading[1].length + 2;

        blocks.push(
          '<h' + level + '>' +
          inline(heading[2]) +
          '</h' + level + '>'
        );

        return;
      }

      const item = t.match(/^([-*•]|\d+\.)\s+(.+)$/);

      if (item) {
        flushPara();

        const type = /^\d/.test(item[1]) ? 'ol' : 'ul';

        if (list.length && listType !== type) {
          flushList();
        }

        listType = type;
        list.push(item[2]);
        return;
      }

      flushList();
      para.push(t);
    });

    flushPara();
    flushList();
    flushTable();

    node.innerHTML = blocks.join('');
    node.dataset.markdownReady = '1';
  });
}


/* Fuel: dependency-free UI glue. */
(function () {
  'use strict';

  var $ = function (selector, root) {
    return (root || document).querySelector(selector);
  };

  var $$ = function (selector, root) {
    return Array.prototype.slice.call(
      (root || document).querySelectorAll(selector)
    );
  };

  var csrf = ($('meta[name=csrf]') || {}).content || '';

  var askFuelMobile = window.matchMedia('(max-width: 699px)');


  /* Responsive Ask Fuel controls and desktop resize handle. */
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

      if (!chatWindow.style.width) {
        chatWindow.style.width = '390px';
      }

      if (!chatWindow.style.height) {
        chatWindow.style.height = '560px';
      }
    }
  }


  /*
   * Keep exactly one Coach instance in the floating chat.
   * Prefer the freshly-rendered Coach from #panel-you after AJAX updates.
   */
  function mountCoach() {
    var mount = $('#fuel-chat-mount');
    var panel = $('#panel-you');

    if (!mount || !panel) return;

    var freshCoach = $('.coach-shell', panel);
    var mountedCoach = $('.coach-shell', mount);

    if (freshCoach) {
      if (mountedCoach && mountedCoach !== freshCoach) {
        mountedCoach.remove();
      }

      if (freshCoach.parentElement !== mount) {
        mount.appendChild(freshCoach);
      }
    } else if (mountedCoach) {
      // Preserve the current Coach if the refreshed panel has no Coach.
      return;
    }
  }


  function setCoachOpen(open) {
    var assistant = $('#fuel-assistant');
    var launcher = $('#fuel-chat-launcher');
    var windowEl = $('#fuel-chat-window');

    if (!assistant) return;

    // Move focus out before hiding the chat window.
    if (!open && windowEl && windowEl.contains(document.activeElement)) {
      if (launcher) {
        launcher.focus();
      } else if (document.activeElement.blur) {
        document.activeElement.blur();
      }
    }

    assistant.classList.toggle('is-open', !!open);

    if (launcher) {
      launcher.setAttribute('aria-expanded', open ? 'true' : 'false');
    }

    if (windowEl) {
      windowEl.setAttribute('aria-hidden', open ? 'false' : 'true');
      windowEl.inert = !open;
    }

    if (open) {
      mountCoach();

      var input = $('#coach-question');

      if (input && !input.disabled) {
        window.setTimeout(function () {
          if (assistant.classList.contains('is-open') && !input.disabled) {
            input.focus();
          }
        }, 80);
      }
    }
  }


  configureAskFuelLayout();

  if (askFuelMobile.addEventListener) {
    askFuelMobile.addEventListener('change', configureAskFuelLayout);
  } else if (askFuelMobile.addListener) {
    askFuelMobile.addListener(configureAskFuelLayout);
  }

  window.addEventListener('resize', configureAskFuelLayout);


  /* ---- Detail sheet references ---- */

  var sheet = $('#detail');
  var body = $('#detail-body');
  var scrim = $('#scrim');
  var lastFocus = null;


  /* ---- Tabs ---- */

  function show(id) {
    var panels = $$('[data-panel]');

    if (!panels.some(function (panel) {
      return panel.dataset.panel === id;
    })) {
      id = 'today';
    }

    panels.forEach(function (panel) {
      panel.hidden = panel.dataset.panel !== id;
    });

    $$('[data-tab]').forEach(function (tab) {
      var active = tab.dataset.tab === id;

      tab.classList.toggle('active', active);

      if (active) {
        tab.setAttribute('aria-current', 'page');
      } else {
        tab.removeAttribute('aria-current');
      }
    });

    window.scrollTo(0, 0);
  }

  window.addEventListener('hashchange', function () {
    show(location.hash.slice(1));
  });

  show(location.hash.slice(1) || 'today');


  /* ---- Toast notifications ---- */

  var toastTimer;

  function toast(msg, category) {
    var toastEl = $('#toast');

    if (!toastEl) return;

    toastEl.textContent = msg;
    toastEl.className = 'show ' + (category || '');

    clearTimeout(toastTimer);

    toastTimer = setTimeout(function () {
      toastEl.className = '';
    }, 4200);
  }


  /* ---- Detail sheet ---- */

  function openSheet(html) {
    if (!sheet || !body || !scrim) return;

    lastFocus = document.activeElement;

    body.innerHTML = html;

    sheet.classList.add('open');
    sheet.setAttribute('aria-hidden', 'false');

    scrim.hidden = false;
    document.body.classList.add('noscroll');

    sheet.scrollTop = 0;
    sheet.focus({ preventScroll: true });
  }

  function closeSheet() {
    if (!sheet || !body || !scrim) return;

    sheet.classList.remove('open');
    sheet.setAttribute('aria-hidden', 'true');

    scrim.hidden = true;
    document.body.classList.remove('noscroll');

    body.innerHTML = '';

    if (lastFocus && lastFocus.focus && document.contains(lastFocus)) {
      lastFocus.focus({ preventScroll: true });
    }
  }

  if (sheet) {
    sheet.setAttribute('tabindex', '-1');
  }

  document.addEventListener('keydown', function (event) {
    if (
      event.key === 'Escape' &&
      sheet &&
      sheet.classList.contains('open')
    ) {
      closeSheet();
    }
  });


  /* ---- Apply server response ---- */

  function apply(data) {
    if (!data || typeof data !== 'object') {
      throw new Error('The server returned an invalid response.');
    }

    if (data.panels) {
      Object.keys(data.panels).forEach(function (key) {
        var panel = $('#panel-' + key);

        if (panel && typeof data.panels[key] === 'string') {
          panel.innerHTML = data.panels[key];
        }
      });
    }

    // Reconcile the newly-rendered Coach with the floating chat.
    mountCoach();

    renderCoachMarkdown(document);

    var chatMessages = $('.fuel-chat-mount .coach-messages');

    if (chatMessages) {
      chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    loadFoodImages();

    if (data.sheet) {
      openSheet(data.sheet);
    }

    if (data.close) {
      closeSheet();
    }

    if (data.flash && Array.isArray(data.flash)) {
      toast(data.flash[1], data.flash[0]);
    }
  }


  /* ---- AJAX form submission ---- */

  function post(form) {
    return fetch(form.action, {
      method: 'POST',
      body: new FormData(form),
      headers: {
        'X-CSRF-Token': csrf,
        'X-Requested-With': 'fetch'
      }
    }).then(function (response) {
      if (!response.ok && response.status === 400) {
        throw new Error(
          'Your session expired. Reload the page and try again.'
        );
      }

      if (!response.ok) {
        throw new Error(
          'Request failed (' + response.status + '). Please try again.'
        );
      }

      return response.json();
    });
  }


  document.addEventListener('submit', function (event) {
    var form = event.target.closest('form[data-ajax]');

    if (!form) return;

    event.preventDefault();

    var btn = form.querySelector('button[type=submit]');
    var label = btn && btn.textContent;

    if (btn) {
      btn.disabled = true;
      btn.textContent = 'Working…';
    }

    post(form)
      .then(apply)
      .catch(function (error) {
        toast(
          error.message || 'Network error. Is the server running?',
          'error'
        );
      })
      .then(function () {
        if (btn && document.contains(btn)) {
          btn.disabled = false;
          btn.textContent = label;
        }
      });
  });


  /* ---- Auto-submit controls ---- */

  document.addEventListener('change', function (event) {
    var element = event.target.closest('[data-autosubmit]');

    if (element && element.form) {
      element.form.requestSubmit();
    }
  });


  /* ---- General click handlers ---- */

  document.addEventListener('click', function (event) {
    var open = event.target.closest('[data-open]');

    if (open) {
      event.preventDefault();

      fetch(open.dataset.open, {
        headers: {
          'X-Requested-With': 'fetch'
        }
      })
        .then(function (response) {
          if (!response.ok) {
            throw new Error('Could not load that.');
          }

          return response.text();
        })
        .then(openSheet)
        .catch(function (error) {
          toast(error.message, 'error');
        });

      return;
    }

    if (event.target.closest('[data-close]')) {
      closeSheet();
      return;
    }

    var tab = event.target.closest('[data-subtab]');

    if (tab) {
      var root = tab.closest('.recipe');

      if (!root) return;

      $$('[data-subtab]', root).forEach(function (button) {
        button.classList.toggle('on', button === tab);
      });

      $$('[data-subpanel]', root).forEach(function (panel) {
        panel.hidden = panel.dataset.subpanel !== tab.dataset.subtab;
      });
    }
  });


  /* ---- Ask Fuel launcher ---- */

  document.addEventListener('click', function (event) {
    if (event.target.closest('#fuel-chat-launcher, #tab-ask-fuel')) {
      event.preventDefault();

      var assistant = $('#fuel-assistant');

      if (assistant) {
        setCoachOpen(!assistant.classList.contains('is-open'));
      }

      return;
    }

    if (event.target.closest('#fuel-chat-close')) {
      setCoachOpen(false);
      return;
    }

    var assistantEl = $('#fuel-assistant');

    if (
      assistantEl &&
      event.target === assistantEl &&
      assistantEl.classList.contains('is-open')
    ) {
      setCoachOpen(false);
    }
  });


  // Move the initial Coach into the floating chat.
  mountCoach();


  /* ---- Background-generated recipe photos ---- */

  function loadFoodImages() {
    $$('[data-food-image]').forEach(function (holder) {
      if (
        holder.dataset.loading === '1' ||
        holder.dataset.loaded === '1'
      ) {
        return;
      }

      holder.dataset.loading = '1';

      var url = holder.dataset.foodImage;
      var attempts = 0;

      function poll() {
        if (!document.contains(holder)) return;

        fetch(url, {
          headers: {
            'X-Requested-With': 'fetch'
          },
          cache: 'no-store'
        })
          .then(function (response) {
            if (response.status === 202) {
              attempts += 1;

              if (attempts < 90) {
                window.setTimeout(poll, 2000);
              } else {
                holder.dataset.loading = '0';
              }

              return null;
            }

            if (!response.ok) {
              throw new Error('Image unavailable');
            }

            return response.blob();
          })
          .then(function (blob) {
            if (!blob || !document.contains(holder)) return;

            var image = document.createElement('img');

            image.className =
              'art-img' +
              (holder.classList.contains('big') ? ' big' : '');

            image.alt = '';
            image.loading = 'lazy';
            image.decoding = 'async';
            image.src = URL.createObjectURL(blob);

            image.onload = function () {
              holder.replaceChildren(image);
              holder.dataset.loaded = '1';
              holder.dataset.loading = '0';
            };
          })
          .catch(function () {
            holder.dataset.loading = '0';
          });
      }

      poll();
    });
  }


  loadFoodImages();
  renderCoachMarkdown(document);


  /* ---- Pantry search ---- */

  document.addEventListener('input', function (event) {
    var box = event.target.closest('[data-filter]');

    if (!box) return;

    var query = box.value.trim().toLowerCase();
    var scope = $(box.dataset.filter);

    if (!scope) return;

    $$('.chip', scope).forEach(function (chip) {
      chip.hidden =
        !!query &&
        (chip.dataset.text || '').indexOf(query) === -1;
    });

    $$('fieldset', scope).forEach(function (fieldset) {
      fieldset.hidden = !$$('.chip:not([hidden])', fieldset).length;
    });
  });

})();


/* ---- AI Coach quick prompts ---- */

document.addEventListener('click', function (event) {
  var prompt = event.target.closest('[data-coach-prompt]');

  if (!prompt) return;

  var panel = prompt.closest('.coach-chat');
  var input = panel && panel.querySelector('#coach-question');

  if (input) {
    input.value = prompt.dataset.coachPrompt || '';
    input.focus();
  }
});