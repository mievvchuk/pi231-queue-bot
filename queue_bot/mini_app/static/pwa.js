"use strict";
(() => {
  let prompt = null;
  window.addEventListener('beforeinstallprompt', event => {
    event.preventDefault();
    prompt = event;
  });
  window.addEventListener('appinstalled', () => { prompt = null; });
  window.PI231PWA = {
    async install() {
      const tg = window.Telegram?.WebApp;
      if (tg?.initData) {
        if (tg.addToHomeScreen) {
          tg.addToHomeScreen();
          return 'Підтвердь додавання ярлика Telegram, якщо пристрій підтримує цю дію.';
        }
        return 'Відкрий адресу застосунку в Safari або Chrome для встановлення.';
      }
      if (matchMedia('(display-mode: standalone)').matches || navigator.standalone)
        return 'Застосунок уже встановлено.';
      if (prompt) {
        const pending = prompt;
        prompt = null;
        await pending.prompt();
        const choice = await pending.userChoice;
        return choice.outcome === 'accepted' ? 'Встановлення підтверджено.' : 'Встановлення скасовано.';
      }
      if (/iPhone|iPad|iPod/.test(navigator.userAgent))
        return 'У Safari відкрий «Поділитися» → «На початковий екран» → «Додати».';
      return 'У меню Chrome або Edge вибери «Встановити застосунок» чи «Додати на головний екран».';
    },
  };
  function connection() {
    document.getElementById('connection-status')?.remove();
    if (!navigator.onLine) {
      const message = document.createElement('div');
      message.id = 'connection-status';
      message.className = 'connection-status';
      message.setAttribute('role', 'status');
      message.textContent = 'Немає інтернету. Для входу й актуальних черг потрібне з’єднання.';
      document.body.append(message);
    }
  }
  window.addEventListener('online', connection);
  window.addEventListener('offline', connection);
  connection();
  // Telegram already supplies a container and updates the shell directly.
  if (!window.Telegram?.WebApp?.initData && 'serviceWorker' in navigator)
    navigator.serviceWorker.register('/app/sw.js', {scope: '/app/'}).catch(() => {});
})();
