(() => {
  if (!('serviceWorker' in navigator)) return;
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/service-worker.js', { scope: '/' }).catch(error => {
      console.warn('Service Worker DrCloud OS non enregistré :', error);
    });
  });
})();
