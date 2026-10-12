document.addEventListener('DOMContentLoaded', () => {
  const mobileButton = document.getElementById('mobile-menu-button');
  const mobileMenu = document.getElementById('mobile-menu');
  const dropdowns = [...document.querySelectorAll('.nav-menu')];

  const closeDropdowns = (except = null) => {
    dropdowns.forEach((menu) => {
      if (menu === except) return;
      menu.classList.remove('is-open');
      menu.querySelector('.nav-menu-trigger').setAttribute('aria-expanded', 'false');
    });
  };

  dropdowns.forEach((menu) => {
    const trigger = menu.querySelector('.nav-menu-trigger');
    trigger.addEventListener('click', (event) => {
      event.stopPropagation();
      const opening = !menu.classList.contains('is-open');
      closeDropdowns(menu);
      menu.classList.toggle('is-open', opening);
      trigger.setAttribute('aria-expanded', String(opening));
    });
  });

  if (mobileButton && mobileMenu) {
    mobileButton.addEventListener('click', () => {
      const opening = mobileMenu.hidden;
      mobileMenu.hidden = !opening;
      mobileButton.setAttribute('aria-expanded', String(opening));
      mobileButton.querySelector('.sr-only').textContent = opening ? 'Close navigation' : 'Open navigation';
    });
  }

  document.addEventListener('click', () => closeDropdowns());
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    closeDropdowns();
    if (mobileMenu && !mobileMenu.hidden) {
      mobileMenu.hidden = true;
      mobileButton.setAttribute('aria-expanded', 'false');
      mobileButton.focus();
    }
  });

  window.addEventListener('resize', () => {
    if (window.innerWidth > 980 && mobileMenu && !mobileMenu.hidden) {
      mobileMenu.hidden = true;
      mobileButton.setAttribute('aria-expanded', 'false');
    }
  });
});
