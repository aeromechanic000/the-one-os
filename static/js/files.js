/* files.js — file browser window: Storage + remote hosts, upload, ops,
   preview trigger, Send to Server (IDEA.md §6.7/§6.8). */
(function () {
  'use strict';
  var OS = window.OS;
  var $ = OS.$, $$ = OS.$$;

  var FILE_ICONS = {
    dir: 'folder', img: 'image', video: 'film', audio: 'music',
    pdf: 'docText', code: 'code', text: 'docText', archive: 'archive',
    office: 'table', unknown: 'doc'
  };
  var EXT_KIND = {
    img: ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp'],
    video: ['mp4', 'webm', 'ogg', 'mov'],
    audio: ['mp3', 'wav', 'ogg', 'flac'],
    pdf: ['pdf'],
    code: ['py', 'js', 'ts', 'sh', 'json', 'yaml', 'yml', 'go', 'rs', 'c',
      'cpp', 'h', 'java', 'rb', 'sql', 'conf', 'ini', 'toml', 'service'],
    text: ['txt', 'log', 'md', 'markdown', 'csv', 'xml', 'html'],
    archive: ['zip', 'tar', 'gz', 'tgz', 'bz2', 'xz'],
    office: ['doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx']
  };

  function kindOf(entry) {
    if (entry.type === 'dir') return 'dir';
    var ext = (entry.ext || '').toLowerCase();
    for (var k in EXT_KIND) {
      if (EXT_KIND[k].indexOf(ext) >= 0) return k;
    }
    return 'unknown';
  }

  var files = OS.files = {
    // window state: {source: 'storage'|host_id, path: string, entries: []}
    state: null,

    open: function (opts) {
      opts = opts || {};
      OS.wm.openApp('files', {
        update: function () {
          if (opts.hostId) {
            var sel = $('.files-source');
            if (sel) {
              sel.value = 'host:' + opts.hostId;
              sel.dispatchEvent(new Event('change'));
            }
          }
        }
      });
    }
  };

  OS.winInit.files = function (win) {
    var sel = $('.files-source', win.body);
    var crumb = $('.files-breadcrumb', win.body);
    var list = $('.files-list', win.body);
    var uploadInput = $('.files-upload-input', win.body);
    files.state = {source: 'storage', path: '', entries: []};

    function renderSource() {
      sel.innerHTML = '';
      var optStorage = OS.el('option', '', OS.t('storage'));
      optStorage.value = 'storage';
      sel.appendChild(optStorage);
      OS.state.hosts.forEach(function (h) {
        var opt = OS.el('option', '', h.name);
        opt.value = 'host:' + h.id;
        sel.appendChild(opt);
      });
      if (files.state) {
        sel.value = files.state.source === 'storage'
          ? 'storage' : 'host:' + files.state.source;
      }
    }

    sel.addEventListener('change', function () {
      if (sel.value === 'storage') {
        files.state = {source: 'storage', path: ''};
      } else {
        files.state = {source: parseInt(sel.value.split(':')[1], 10), path: ''};
      }
      load();
    });

    function apiBase() {
      return files.state.source === 'storage'
        ? '/api/files/local'
        : '/api/files/remote?host_id=' + files.state.source;
    }

    function load() {
      list.innerHTML = '';
      list.appendChild(OS.el('div', 'actions-empty', OS.t('loading')));
      var url = apiBase() + '&path=' + encodeURIComponent(files.state.path)
        .replace(/%2F/g, '/');
      if (files.state.source === 'storage') {
        url = '/api/files/local?path=' + encodeURIComponent(files.state.path);
      }
      OS.api(url).then(function (data) {
        if (data.error) {
          list.innerHTML = '';
          list.appendChild(OS.el('div', 'actions-empty',
            OS.t('operation_failed', {msg: data.error})));
          return;
        }
        files.state.path = data.path !== undefined ? data.path : files.state.path;
        files.state.entries = data.entries || [];
        renderCrumb();
        renderEntries();
      });
    }

    function renderCrumb() {
      crumb.innerHTML = '';
      var base = files.state.source === 'storage'
        ? {label: OS.t('storage'), path: ''}
        : {label: (OS.hostById(files.state.source) || {}).name || 'remote', path: ''};
      var btn = OS.el('button', 'crumb', base.label);
      btn.addEventListener('click', function () {
        files.state.path = files.state.source === 'storage' ? '' : '.';
        load();
      });
      crumb.appendChild(btn);
      var sep = files.state.source === 'storage' ? '/' : '/';
      var parts = files.state.path.split(sep).filter(Boolean);
      var acc = '';
      parts.forEach(function (part) {
        acc = acc ? acc + sep + part : part;
        crumb.appendChild(OS.el('span', 'crumb-sep', '/'));
        var p = acc;
        var b = OS.el('button', 'crumb', part);
        b.addEventListener('click', function () {
          files.state.path = p;
          load();
        });
        crumb.appendChild(b);
      });
    }

    function renderEntries() {
      list.innerHTML = '';
      if (!files.state.entries.length) {
        list.appendChild(OS.el('div', 'actions-empty', OS.t('empty_dir')));
        return;
      }
      files.state.entries.forEach(function (entry) {
        var kind = kindOf(entry);
        var row = OS.el('div', 'file-row');
        var ico = OS.el('span', 'file-ico');
        ico.innerHTML = OS.iconSvg(FILE_ICONS[kind] || 'doc');
        row.appendChild(ico);
        var name = OS.el('span', 'file-name mono', entry.name);
        row.appendChild(name);
        if (kind !== 'dir' && kind !== 'unknown' && entry.ext) {
          var chip = OS.el('span', 'ext-chip', entry.ext);
          row.appendChild(chip);
        }
        if (entry.type === 'file') {
          row.appendChild(OS.el('span', 'file-size', OS.fmtSize(entry.size)));
          row.appendChild(OS.el('span', 'file-mtime', OS.fmtDateTime(entry.mtime)));
        }
        row.addEventListener('click', function () {
          $$('.file-row', list).forEach(function (r) { r.classList.remove('selected'); });
          row.classList.add('selected');
        });
        row.addEventListener('dblclick', function () {
          if (entry.type === 'dir') {
            files.state.path = files.state.path
              ? joinPath(files.state.path, entry.name)
              : (files.state.source === 'storage' ? entry.name : entry.name);
            load();
          } else {
            previewEntry(entry);
          }
        });
        row.addEventListener('contextmenu', function (e) {
          e.preventDefault();
          entryMenu(entry, e.clientX, e.clientY);
        });
        // phone: single tap opens dirs / previews files (dblclick unreliable)
        row.addEventListener('touchend', function (e) {
          if (OS.layout.isPhone && e.detail === 0) {
            if (entry.type === 'dir') {
              files.state.path = files.state.path
                ? joinPath(files.state.path, entry.name) : entry.name;
              load();
            } else {
              previewEntry(entry);
            }
          }
        });
        list.appendChild(row);
      });
    }

    function joinPath(base, name) {
      var j = base === '' ? name : base.replace(/\/+$/, '') + '/' + name;
      return j;
    }

    function entryPath(entry) {
      return joinPath(files.state.path, entry.name);
    }

    function previewEntry(entry) {
      OS.preview.open({
        source: files.state.source === 'storage' ? 'local' : 'remote',
        hostId: files.state.source === 'storage' ? null : files.state.source,
        path: entryPath(entry),
        name: entry.name
      });
    }

    function entryMenu(entry, x, y) {
      var items = [];
      if (entry.type === 'file') {
        items.push({icon: 'eye', label: OS.t('preview'),
          onClick: function () { previewEntry(entry); }});
        items.push({icon: 'download', label: OS.t('download'),
          onClick: function () { downloadEntry(entry); }});
        items.push('-');
        items.push({icon: 'send', label: OS.t('send_to_server'),
          onClick: function () { sendDialog(entry); }});
      }
      items.push({icon: 'edit', label: OS.t('rename'),
        onClick: function () { renameEntry(entry); }});
      items.push({icon: 'close', label: OS.t('delete'), danger: true,
        onClick: function () { deleteEntry(entry); }});
      OS.ctxMenu(items, x, y);
    }

    function downloadEntry(entry) {
      var url = '/api/files/download?target=' +
        (files.state.source === 'storage' ? 'local' : 'remote') +
        (files.state.source === 'storage'
          ? '' : '&host_id=' + files.state.source) +
        '&path=' + encodeURIComponent(entryPath(entry));
      var a = document.createElement('a');
      a.href = url;
      a.download = entry.name;
      document.body.appendChild(a);
      a.click();
      a.remove();
    }

    function op(payload) {
      return OS.api('/api/files/op', {method: 'POST', body: payload})
        .then(function (res) {
          if (res.error) OS.toast(res.error, true);
          else load();
        });
    }

    function renameEntry(entry) {
      OS.prompt(OS.t('new_name'), entry.name).then(function (newName) {
        if (!newName || newName === entry.name) return;
        op({
          op: 'rename', target: files.state.source === 'storage' ? 'local' : 'remote',
          host_id: files.state.source === 'storage' ? null : files.state.source,
          path: entryPath(entry),
          new_path: joinPath(files.state.path, newName)
        });
      });
    }

    function deleteEntry(entry) {
      var msg = entry.type === 'dir'
        ? OS.t('confirm_delete_dir') : OS.t('confirm_delete');
      OS.confirm(msg + ' (' + entry.name + ')', true).then(function (yes) {
        if (!yes) return;
        var go = function (recursive) {
          op({
            op: 'delete',
            target: files.state.source === 'storage' ? 'local' : 'remote',
            host_id: files.state.source === 'storage' ? null : files.state.source,
            path: entryPath(entry), recursive: recursive
          });
        };
        if (entry.type === 'dir') {
          // second confirm for recursive delete
          OS.confirm(OS.t('confirm_delete_dir') + ' (' + entry.name + ')', true)
            .then(function (yes2) { if (yes2) go(true); });
        } else {
          go(false);
        }
      });
    }

    function sendDialog(entry) {
      var body = OS.el('div');
      var srcIsLocal = files.state.source === 'storage';
      var lbl1 = OS.el('label', 'field-label', OS.t('target_host'));
      var hostSel = OS.el('select');
      var noneOpt = OS.el('option', '', OS.t('storage'));
      noneOpt.value = 'storage';
      hostSel.appendChild(noneOpt);
      OS.state.hosts.forEach(function (h) {
        var o = OS.el('option', '', h.name);
        o.value = h.id;
        hostSel.appendChild(o);
      });
      var lbl2 = OS.el('label', 'field-label', OS.t('target_path'));
      var pathInput = OS.el('input');
      pathInput.value = '/tmp/';
      var lbl3 = OS.el('label', 'field-label', OS.t('transfer_mode'));
      var modeRow = OS.el('div', 'radio-row');
      var modes = [['A', OS.t('mode_a')], ['B', OS.t('mode_b')], ['C', OS.t('mode_c')]];
      var modeVal = 'A';
      modes.forEach(function (m, i) {
        var pill = OS.el('button', 'radio-pill' + (i === 0 ? ' active' : ''), m[1]);
        pill.type = 'button';
        pill.addEventListener('click', function () {
          modeVal = m[0];
          modeRow.querySelectorAll('.radio-pill').forEach(function (p) {
            p.classList.remove('active');
          });
          pill.classList.add('active');
        });
        modeRow.appendChild(pill);
      });
      body.appendChild(lbl1);
      body.appendChild(hostSel);
      body.appendChild(lbl2);
      body.appendChild(pathInput);
      body.appendChild(lbl3);
      body.appendChild(modeRow);
      OS.modal({
        title: OS.t('send_to_server') + ' — ' + entry.name,
        body: body,
        buttons: [
          {label: OS.t('cancel')},
          {
            label: OS.t('run_transfer'), cls: 'btn-primary',
            onClick: function () {
              var dst = hostSel.value === 'storage'
                ? {kind: 'local', path: pathInput.value + entry.name}
                : {kind: 'remote', host_id: parseInt(hostSel.value, 10),
                   path: pathInput.value + entry.name};
              var src = srcIsLocal
                ? {kind: 'local', path: entryPath(entry)}
                : {kind: 'remote', host_id: files.state.source,
                   path: entryPath(entry)};
              OS.api('/api/transfer/run',
                {method: 'POST', body: {src: src, dst: dst, mode: modeVal}})
                .then(function (res) {
                  if (res.error) OS.toast(res.error, true);
                  else OS.toast(OS.t('q_queued') + ': ' + entry.name);
                });
            }
          }
        ]
      });
    }

    // toolbar
    $('.files-toolbar', win.body).addEventListener('click', function (e) {
      var btn = e.target.closest('button');
      if (!btn) return;
      if (btn.dataset.act === 'refresh') load();
      if (btn.dataset.act === 'upload') uploadInput.click();
      if (btn.dataset.act === 'mkdir') {
        OS.prompt(OS.t('folder_name'), '').then(function (name) {
          if (!name) return;
          op({
            op: 'mkdir',
            target: files.state.source === 'storage' ? 'local' : 'remote',
            host_id: files.state.source === 'storage' ? null : files.state.source,
            path: files.state.path, new_path: name
          });
        });
      }
    });

    uploadInput.addEventListener('change', function () {
      var filesArr = Array.prototype.slice.call(uploadInput.files || []);
      if (!filesArr.length) return;
      var done = 0;
      filesArr.forEach(function (f) {
        var fd = new FormData();
        fd.append('file', f);
        if (files.state.source === 'storage') {
          fd.append('target', 'local');
          fd.append('path', files.state.path);
        } else {
          fd.append('target', 'remote');
          fd.append('host_id', files.state.source);
          fd.append('path', files.state.path);
        }
        fetch('/api/files/upload', {method: 'POST', body: fd})
          .then(function (r) { return r.json(); })
          .then(function (res) {
            if (res.error) OS.toast(res.error, true);
            if (++done === filesArr.length) {
              uploadInput.value = '';
              load();
            }
          });
      });
    });

    // drag & drop upload into storage
    list.addEventListener('dragover', function (e) { e.preventDefault(); });
    list.addEventListener('drop', function (e) {
      e.preventDefault();
      if (files.state.source !== 'storage') return;
      var dropped = Array.prototype.slice.call(e.dataTransfer.files || []);
      dropped.forEach(function (f) {
        var fd = new FormData();
        fd.append('file', f);
        fd.append('target', 'local');
        fd.append('path', files.state.path);
        fetch('/api/files/upload', {method: 'POST', body: fd})
          .then(function () { load(); });
      });
    });

    var unsubHosts = OS.bus.on('hosts-changed', renderSource);
    OS.winClose.files = function () { unsubHosts(); };

    renderSource();
    load();
    OS.transfer.bind($('.transfer-queue', win.body));
  };
})();
