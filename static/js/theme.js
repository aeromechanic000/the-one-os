/* Color themes: registry, persistence and terminal palettes.
   main.css holds the html[data-theme] variable blocks; the snippet in
   base.html applies the localStorage cache before first paint; this module
   owns switching (OS.theme.apply), persists the choice to the backend
   settings store (OS.theme.select) so it follows the account across
   machines, re-syncs from the server at boot (syncFromServer), and
   notifies listeners on the OS.bus 'theme' event so open terminals can
   restyle themselves. */
(function () {
  'use strict';
  var OS = window.OS;

  var THEMES = [
    { id: 'midnight',      nameKey: 'theme_midnight' },
    { id: 'dracula',       nameKey: 'theme_dracula' },
    { id: 'nord',          nameKey: 'theme_nord' },
    { id: 'tokyo',         nameKey: 'theme_tokyo' },
    { id: 'solar-dark',    nameKey: 'theme_solar_dark' },
    { id: 'gruvbox',       nameKey: 'theme_gruvbox' },
    { id: 'solar-light',   nameKey: 'theme_solar_light' },
    { id: 'daylight',      nameKey: 'theme_daylight' }
  ];

  /* Swatches for the Settings picker: [bg, win, accent] per theme. */
  var SWATCH = {
    midnight:    ['#1e1e2e', '#2a2a3c', '#4fc3f7'],
    dracula:     ['#282a36', '#44475a', '#bd93f9'],
    nord:        ['#2e3440', '#3b4252', '#88c0d0'],
    tokyo:       ['#1a1b26', '#24283b', '#7aa2f7'],
    'solar-dark':  ['#002b36', '#073642', '#268bd2'],
    gruvbox:     ['#282828', '#3c3836', '#83a598'],
    'solar-light': ['#eee8d5', '#fdf6e3', '#268bd2'],
    daylight:    ['#f4f5f7', '#ffffff', '#0288d1']
  };

  /* xterm.js palettes (background must match the CSS --console tone). */
  var XTERM = {
    midnight: {
      background: '#16161f', foreground: '#e6e6f0', cursor: '#4fc3f7',
      selectionBackground: 'rgba(79,195,247,0.28)'
    },
    dracula: {
      background: '#1e1f29', foreground: '#f8f8f2', cursor: '#bd93f9',
      selectionBackground: 'rgba(189,147,249,0.30)',
      black: '#282a36', red: '#ff5555', green: '#50fa7b', yellow: '#f1fa8c',
      blue: '#bd93f9', magenta: '#ff79c6', cyan: '#8be9fd', white: '#f8f8f2',
      brightBlack: '#6272a4', brightRed: '#ff6e6e', brightGreen: '#69ff94',
      brightYellow: '#ffffa5', brightBlue: '#d6acff', brightMagenta: '#ff92df',
      brightCyan: '#a4ffff', brightWhite: '#ffffff'
    },
    nord: {
      background: '#252b36', foreground: '#eceff4', cursor: '#88c0d0',
      selectionBackground: 'rgba(136,192,208,0.28)',
      black: '#3b4252', red: '#bf616a', green: '#a3be8c', yellow: '#ebcb8b',
      blue: '#81a1c1', magenta: '#b48ead', cyan: '#8fbcbb', white: '#e5e9f0',
      brightBlack: '#4c566a', brightRed: '#d08770', brightGreen: '#a3be8c',
      brightYellow: '#ebcb8b', brightBlue: '#81a1c1', brightMagenta: '#b48ead',
      brightCyan: '#8fbcbb', brightWhite: '#eceff4'
    },
    tokyo: {
      background: '#14151d', foreground: '#c0caf5', cursor: '#7aa2f7',
      selectionBackground: 'rgba(122,162,247,0.30)',
      black: '#15161e', red: '#f7768e', green: '#9ece6a', yellow: '#e0af68',
      blue: '#7aa2f7', magenta: '#bb9af7', cyan: '#7dcfff', white: '#a9b1d6',
      brightBlack: '#414868', brightRed: '#ff7a93', brightGreen: '#b9f00c',
      brightYellow: '#ff9e64', brightBlue: '#7da6ff', brightMagenta: '#bb9af7',
      brightCyan: '#0db9d7', brightWhite: '#c0caf5'
    },
    'solar-dark': {
      background: '#001e26', foreground: '#93a1a1', cursor: '#268bd2',
      selectionBackground: 'rgba(38,139,210,0.28)',
      black: '#073642', red: '#dc322f', green: '#859900', yellow: '#b58900',
      blue: '#268bd2', magenta: '#d33682', cyan: '#2aa198', white: '#eee8d5',
      brightBlack: '#586e75', brightRed: '#cb4b16', brightGreen: '#586e75',
      brightYellow: '#657b83', brightBlue: '#839496', brightMagenta: '#6c71c4',
      brightCyan: '#93a1a1', brightWhite: '#fdf6e3'
    },
    gruvbox: {
      background: '#1d2021', foreground: '#ebdbb2', cursor: '#83a598',
      selectionBackground: 'rgba(131,165,152,0.28)',
      black: '#282828', red: '#fb4934', green: '#b8bb26', yellow: '#fabd2f',
      blue: '#83a598', magenta: '#d3869b', cyan: '#8ec07c', white: '#ebdbb2',
      brightBlack: '#928374', brightRed: '#fb4934', brightGreen: '#b8bb26',
      brightYellow: '#fabd2f', brightBlue: '#83a598', brightMagenta: '#d3869b',
      brightCyan: '#8ec07c', brightWhite: '#fbf1c7'
    },
    'solar-light': {
      background: '#eee8d5', foreground: '#073642', cursor: '#268bd2',
      selectionBackground: 'rgba(38,139,210,0.22)',
      black: '#073642', red: '#dc322f', green: '#859900', yellow: '#b58900',
      blue: '#268bd2', magenta: '#d33682', cyan: '#2aa198', white: '#fdf6e3',
      brightBlack: '#93a1a1', brightRed: '#cb4b16', brightGreen: '#586e75',
      brightYellow: '#657b83', brightBlue: '#839496', brightMagenta: '#6c71c4',
      brightCyan: '#93a1a1', brightWhite: '#142d35'
    },
    daylight: {
      background: '#f7f8fa', foreground: '#1f2430', cursor: '#0288d1',
      selectionBackground: 'rgba(2,136,209,0.20)',
      black: '#1f2430', red: '#c62828', green: '#2e7d32', yellow: '#f9a825',
      blue: '#1565c0', magenta: '#8e24aa', cyan: '#00838f', white: '#424652',
      brightBlack: '#6a7181', brightRed: '#d32f2f', brightGreen: '#388e3c',
      brightYellow: '#fbc02d', brightBlue: '#1976d2', brightMagenta: '#9c27b0',
      brightCyan: '#0097a7', brightWhite: '#1f2430'
    }
  };

  var current = 'midnight';

  function isKnown(id) {
    for (var i = 0; i < THEMES.length; i++) {
      if (THEMES[i].id === id) { return true; }
    }
    return false;
  }

  function apply(id, silent) {
    if (!isKnown(id)) { id = 'midnight'; }
    current = id;
    if (id === 'midnight') {
      document.documentElement.removeAttribute('data-theme');
    } else {
      document.documentElement.setAttribute('data-theme', id);
    }
    OS.store.set('theme', id);   // pre-paint cache; server copy is the truth
    if (!silent) { OS.bus.emit('theme', id); }
  }

  /* User picked a theme: apply locally and persist to the backend so the
     choice follows the account to every machine. */
  function select(id) {
    apply(id);
    OS.api('/api/settings', {method: 'PUT', body: {theme: id}})
      .then(function (res) {
        if (res.error) OS.toast(res.error, true);
      });
  }

  /* The server theme wins over the localStorage cache (another machine
     may have chosen it). Called once at boot after the pre-paint apply. */
  function syncFromServer() {
    OS.api('/api/settings').then(function (s) {
      if (s && s.theme && isKnown(s.theme) && s.theme !== current) {
        apply(s.theme);
      }
    });
  }

  function get() {
    return current;
  }

  function xtermTheme() {
    return XTERM[current] || XTERM.midnight;
  }

  OS.theme = {
    THEMES: THEMES,
    SWATCH: SWATCH,
    apply: apply,
    select: select,
    syncFromServer: syncFromServer,
    get: get,
    xtermTheme: xtermTheme
  };

  // adopt the theme the pre-paint snippet in base.html already applied
  var saved = OS.store.get('theme', 'midnight');
  apply(isKnown(saved) ? saved : 'midnight', true);
})();
