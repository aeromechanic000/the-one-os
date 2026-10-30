/* preview.js — in-system preview by type (IDEA.md §6.8):
   img / video+Range / audio / PDF.js / Markdown (marked + DOMPurify) /
   text <pre> / Office (LibreOffice→PDF, or simple frontend render) /
   archive entry list / unsupported card. SVG renders via <img> (XSS-safe). */
(function () {
  'use strict';
  var OS = window.OS;
  var $ = OS.$, $$ = OS.$$;

  var preview = OS.preview = {
    open: function (opts) {
      // single preview window; content swaps per file
      var existing = null;
      OS.wm.windows.forEach(function (w) { if (w.type === 'preview') existing = w; });
      var title = OS.t('pv_title') + ' — ' + opts.name;
      var win = OS.wm.openApp('preview', {
        title: title,
        update: function (w) { render(w, opts); }
      });
      if (win) win.setTitle(title);
      if (!existing) render(win, opts);
    }
  };

  function url(opts, download) {
    var u = '/api/preview?source=' + (opts.source === 'local' ? 'local' : 'remote') +
      '&path=' + encodeURIComponent(opts.path) +
      (opts.hostId ? '&host_id=' + opts.hostId : '') +
      (download ? '&download=1' : '');
    return u;
  }

  function render(win, opts) {
    var body = $('.preview-body', win.body);
    var banner = $('.preview-banner', win.body);
    var dl = $('.preview-download', win.body);
    dl.href = url(opts, true);
    dl.setAttribute('download', opts.name);
    $('.preview-name', win.body).textContent = opts.name;
    banner.hidden = true;
    body.innerHTML = '';
    body.appendChild(OS.el('div', 'preview-unsupported', OS.t('pv_loading')));

    fetch(url(opts), {redirect: 'follow'}).then(function (r) {
      var ct = (r.headers.get('Content-Type') || '').toLowerCase();
      if (ct.indexOf('application/json') === 0) {
        return r.json().then(function (data) {
          renderJson(body, banner, opts, data);
        });
      }
      return renderStream(body, opts, r, ct);
    }).catch(function () {
      body.innerHTML = '';
      body.appendChild(OS.el('div', 'preview-unsupported',
        OS.t('operation_failed', {msg: ''})));
    });
  }

  function renderStream(body, opts, resp, ct) {
    body.innerHTML = '';
    if (ct.indexOf('image/') === 0) {
      var img = OS.el('img');
      img.src = url(opts);
      img.alt = opts.name;
      body.appendChild(img);
    } else if (ct.indexOf('video/') === 0) {
      var v = OS.el('video');
      v.controls = true;
      v.src = url(opts);
      body.appendChild(v);
    } else if (ct.indexOf('audio/') === 0) {
      var a = OS.el('audio');
      a.controls = true;
      a.src = url(opts);
      body.appendChild(a);
    } else if (ct.indexOf('application/pdf') === 0) {
      renderPdf(body, url(opts));
    } else if (ct.indexOf('officedocument') >= 0 || ct.indexOf('msword') >= 0 ||
               ct.indexOf('ms-excel') >= 0 || ct.indexOf('ms-powerpoint') >= 0) {
      // office_preview_method = frontend: simple in-browser render
      renderOfficeFrontend(body, opts, ct);
    } else {
      body.appendChild(OS.el('div', 'preview-unsupported', OS.t('pv_unsupported')));
    }
  }

  function renderJson(body, banner, opts, data) {
    body.innerHTML = '';
    if (data.type === 'text') {
      if (data.truncated) showBanner(banner, data.truncated_hint || OS.t('pv_too_large'));
      var pre = OS.el('pre', 'preview-text');
      pre.textContent = data.content;
      body.appendChild(pre);
    } else if (data.type === 'markdown') {
      if (data.truncated) showBanner(banner, data.truncated_hint || OS.t('pv_too_large'));
      if (window.marked && window.DOMPurify) {
        var div = OS.el('div', 'preview-md');
        div.innerHTML = DOMPurify.sanitize(
          marked.parse(data.content || '', {breaks: true, mangle: false}));
        body.appendChild(div);
      } else {
        var pre2 = OS.el('pre', 'preview-text');
        pre2.textContent = data.content;
        body.appendChild(pre2);
      }
    } else if (data.type === 'archive') {
      if (data.error) {
        body.appendChild(OS.el('div', 'preview-unsupported', data.error));
        return;
      }
      var h = OS.el('h3', '', OS.t('pv_archive') + ' (' + data.entries.length + ')');
      body.appendChild(h);
      var tbl = OS.el('table', 'tbl archive-table');
      var tb = OS.el('tbody');
      data.entries.forEach(function (name) {
        var tr = OS.el('tr');
        var td = OS.el('td', 'mono', name);
        tr.appendChild(td);
        tb.appendChild(tr);
      });
      tbl.appendChild(tb);
      body.appendChild(tbl);
    } else if (data.type === 'office-frontend') {
      renderOfficeFrontend(body, opts, '');
    } else if (data.type === 'unsupported') {
      var card = OS.el('div', 'preview-unsupported');
      card.appendChild(OS.el('div', '', OS.t('pv_unsupported')));
      card.appendChild(OS.el('div', '', data.hint || OS.t('pv_unsupported_hint')));
      body.appendChild(card);
    } else if (data.type === 'error') {
      var card2 = OS.el('div', 'preview-unsupported');
      card2.appendChild(OS.el('div', '', data.error || OS.t('pv_conversion_failed')));
      body.appendChild(card2);
    }
  }

  function showBanner(banner, text) {
    banner.textContent = text;
    banner.hidden = false;
  }

  // ---- PDF.js -------------------------------------------------------------
  function renderPdf(body, src) {
    if (!window.pdfjsLib) {
      body.appendChild(OS.el('div', 'preview-unsupported', 'PDF.js unavailable'));
      return;
    }
    var task = pdfjsLib.getDocument(src);
    task.promise.then(function (pdf) {
      body.innerHTML = '';
      function page(n) {
        if (n > pdf.numPages) return;
        pdf.getPage(n).then(function (pg) {
          var canvas = OS.el('canvas', 'pdf-page');
          var scale = Math.min(1.6, (body.clientWidth - 24) / pg.getViewport({scale: 1}).width) || 1;
          var vp = pg.getViewport({scale: scale});
          canvas.width = vp.width;
          canvas.height = vp.height;
          pg.render({canvasContext: canvas.getContext('2d'), viewport: vp});
          body.appendChild(canvas);
          page(n + 1);
        });
      }
      page(1);
    }).catch(function () {
      body.innerHTML = '';
      body.appendChild(OS.el('div', 'preview-unsupported', 'PDF render failed'));
    });
  }

  // ---- Office simple frontend render (docx-preview / SheetJS) ----------------
  function renderOfficeFrontend(body, opts, ct) {
    var ext = (opts.name.split('.').pop() || '').toLowerCase();
    body.innerHTML = '';
    if ((ext === 'doc' || ext === 'docx') && window.docx) {
      fetch(url(opts)).then(function (r) { return r.blob(); }).then(function (blob) {
        var holder = OS.el('div');
        holder.style.background = '#fff';
        // docx body text has no color of its own and would inherit the
        // theme's light text on this white page — force dark-on-light
        holder.style.color = '#1a1a1a';
        holder.style.padding = '12px';
        body.appendChild(holder);
        window.docx.renderAsync(blob, holder).catch(function () {
          body.innerHTML = '';
          body.appendChild(OS.el('div', 'preview-unsupported', OS.t('pv_conversion_failed')));
        });
      });
      return;
    }
    if ((ext === 'xls' || ext === 'xlsx') && window.XLSX) {
      fetch(url(opts)).then(function (r) { return r.arrayBuffer(); }).then(function (buf) {
        try {
          var wb = XLSX.read(buf, {type: 'array'});
          var sheet = wb.Sheets[wb.SheetNames[0]];
          var html = XLSX.utils.sheet_to_html(sheet);
          var wrap = OS.el('div');
          wrap.className = 'sheet-wrap';
          wrap.innerHTML = html;
          // sanitize: SheetJS output is table markup only
          if (window.DOMPurify) wrap.innerHTML = DOMPurify.sanitize(html, {USE_PROFILES: {html: true}});
          body.appendChild(wrap);
        } catch (e) {
          body.appendChild(OS.el('div', 'preview-unsupported', OS.t('pv_conversion_failed')));
        }
      });
      return;
    }
    body.appendChild(OS.el('div', 'preview-unsupported',
      OS.t('pv_unsupported') + ' — ' + OS.t('pv_simple_render')));
  }

  OS.winClose.preview = function (win) {
    var body = $('.preview-body', win.body);
    if (body) {
      $$('video, audio', body).forEach(function (m) {
        m.pause();
        m.src = '';
      });
    }
  };
})();
