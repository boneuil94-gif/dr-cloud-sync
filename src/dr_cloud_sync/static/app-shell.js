(() => {
  const breakpoint = window.matchMedia('(max-width: 800px)');
  const body = document.body;
  const drawer = document.querySelector('.dc-sidebar');
  const toggle = document.querySelector('.dc-menu-button');
  const overlay = document.querySelector('.dc-drawer-overlay');
  if (!drawer || !toggle || !overlay) return;

  const setOpen = open => {
    const mobileOpen = breakpoint.matches && open;
    body.classList.toggle('dc-drawer-open', mobileOpen);
    toggle.setAttribute('aria-expanded', String(mobileOpen));
    toggle.setAttribute('aria-label', mobileOpen ? 'Fermer le menu' : 'Ouvrir le menu');
    drawer.setAttribute('aria-hidden', String(breakpoint.matches && !mobileOpen));
    if (mobileOpen) drawer.querySelector('a')?.focus();
  };
  const close = ({ restoreFocus = true } = {}) => {
    const wasOpen = body.classList.contains('dc-drawer-open');
    setOpen(false);
    if (wasOpen && restoreFocus) toggle.focus();
  };

  toggle.addEventListener('click', () => setOpen(!body.classList.contains('dc-drawer-open')));
  overlay.addEventListener('click', () => close());
  drawer.querySelectorAll('a').forEach(link => link.addEventListener('click', () => close({ restoreFocus: false })));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && body.classList.contains('dc-drawer-open')) close();
  });
  breakpoint.addEventListener?.('change', () => setOpen(false));
  setOpen(false);
})();
