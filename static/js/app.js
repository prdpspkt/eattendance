/* E-Attendance UI behaviour.
 *
 * Progressive enhancement only: every feature here degrades to working
 * server-rendered HTML if the script fails to load. No build step, no
 * framework, matching how the rest of the project is served.
 */
(function () {
  'use strict';

  /* ---------------------------------------------------------------
   * Theme. Applied in <head> before paint to avoid a flash; this part
   * only wires up the toggle.
   * ------------------------------------------------------------- */
  function currentTheme() {
    return document.documentElement.getAttribute('data-bs-theme') || 'light';
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute('data-bs-theme', theme);
    try { localStorage.setItem('ea-theme', theme); } catch (e) { /* private mode */ }

    document.querySelectorAll('[data-theme-toggle]').forEach(function (button) {
      var isDark = theme === 'dark';
      var icon = button.querySelector('i');
      if (icon) { icon.className = isDark ? 'bi bi-sun' : 'bi bi-moon-stars'; }
      button.setAttribute('aria-label', isDark ? 'Switch to light theme' : 'Switch to dark theme');
      button.setAttribute('title', isDark ? 'Light theme' : 'Dark theme');
    });
  }

  document.querySelectorAll('[data-theme-toggle]').forEach(function (button) {
    button.addEventListener('click', function () {
      applyTheme(currentTheme() === 'dark' ? 'light' : 'dark');
    });
  });
  applyTheme(currentTheme());

  /* ---------------------------------------------------------------
   * Mobile sidebar. The old markup used .collapse.show with no trigger,
   * so on a phone the nav simply sat on top of the page with no way to
   * dismiss it.
   * ------------------------------------------------------------- */
  var sidebar = document.getElementById('appSidebar');
  var backdrop = document.getElementById('sidebarBackdrop');
  var toggles = document.querySelectorAll('[data-sidebar-toggle]');

  function setSidebar(open) {
    if (!sidebar) { return; }
    sidebar.classList.toggle('is-open', open);
    if (backdrop) { backdrop.classList.toggle('is-visible', open); }
    toggles.forEach(function (t) { t.setAttribute('aria-expanded', String(open)); });
    document.body.style.overflow = open ? 'hidden' : '';
    if (open) {
      var firstLink = sidebar.querySelector('.app-nav__link');
      if (firstLink) { firstLink.focus(); }
    }
  }

  toggles.forEach(function (toggle) {
    toggle.addEventListener('click', function () {
      setSidebar(!sidebar.classList.contains('is-open'));
    });
  });
  if (backdrop) { backdrop.addEventListener('click', function () { setSidebar(false); }); }

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && sidebar && sidebar.classList.contains('is-open')) {
      setSidebar(false);
      var toggle = document.querySelector('[data-sidebar-toggle]');
      if (toggle) { toggle.focus(); }
    }
  });

  /* ---------------------------------------------------------------
   * Submit feedback. A biometric sync or a device command can take
   * several seconds; without this the button looks inert and people
   * click it again.
   * ------------------------------------------------------------- */
  document.querySelectorAll('form[data-loading]').forEach(function (form) {
    form.addEventListener('submit', function () {
      var button = form.querySelector('[type="submit"]');
      if (!button || button.dataset.busy) { return; }
      button.dataset.busy = '1';
      button.disabled = true;
      var label = button.dataset.loadingText || 'Working...';
      button.innerHTML =
        '<span class="spinner-border spinner-border-sm" aria-hidden="true"></span> ' + label;
    });
  });

  /* ---------------------------------------------------------------
   * Client-side table filter. Narrows what is already on the page;
   * server-side filters still own the real querying.
   * ------------------------------------------------------------- */
  document.querySelectorAll('[data-table-filter]').forEach(function (input) {
    var table = document.querySelector(input.dataset.tableFilter);
    if (!table) { return; }
    var status = document.querySelector(input.dataset.filterStatus || '');

    input.addEventListener('input', function () {
      var term = input.value.trim().toLowerCase();
      var rows = table.querySelectorAll('tbody tr');
      var shown = 0;

      rows.forEach(function (row) {
        var match = !term || row.textContent.toLowerCase().indexOf(term) !== -1;
        row.hidden = !match;
        if (match) { shown += 1; }
      });

      if (status) {
        status.textContent = term
          ? shown + ' of ' + rows.length + ' shown'
          : rows.length + ' total';
      }
    });
  });

  /* ---------------------------------------------------------------
   * Password reveal.
   * ------------------------------------------------------------- */
  document.querySelectorAll('[data-password-toggle]').forEach(function (button) {
    button.addEventListener('click', function () {
      var field = document.querySelector(button.dataset.passwordToggle);
      if (!field) { return; }
      var reveal = field.type === 'password';
      field.type = reveal ? 'text' : 'password';
      button.setAttribute('aria-label', reveal ? 'Hide password' : 'Show password');
      button.setAttribute('aria-pressed', String(reveal));
      var icon = button.querySelector('i');
      if (icon) { icon.className = reveal ? 'bi bi-eye-slash' : 'bi bi-eye'; }
    });
  });

  /* ---------------------------------------------------------------
   * Auto-dismiss success messages. Errors stay until dismissed.
   * ------------------------------------------------------------- */
  window.setTimeout(function () {
    document.querySelectorAll('.alert-success[data-auto-dismiss]').forEach(function (alert) {
      if (window.bootstrap && window.bootstrap.Alert) {
        window.bootstrap.Alert.getOrCreateInstance(alert).close();
      }
    });
  }, 6000);
})();
