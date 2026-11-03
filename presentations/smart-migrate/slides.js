(() => {
  'use strict';
  const slides = [...document.querySelectorAll('.slide')];
  const byId = id => document.getElementById(id);
  const prev = byId('prev'), next = byId('next'), position = byId('position');
  let current = 0;
  function fromHash() {
    const match = /^#slide-(\d+)$/.exec(location.hash);
    return match ? Math.max(0, Math.min(slides.length - 1, Number(match[1]) - 1)) : 0;
  }
  function render(index, updateHash = true) {
    current = Math.max(0, Math.min(slides.length - 1, index));
    slides.forEach((slide, i) => {
      slide.classList.toggle('active', i === current);
      if (i === current) slide.setAttribute('aria-current', 'step');
      else slide.removeAttribute('aria-current');
    });
    position.textContent = `${current + 1} / ${slides.length}`;
    prev.disabled = current === 0;
    next.disabled = current === slides.length - 1;
    document.title = `${current + 1}/${slides.length} · SmartMigrate Agent`;
    if (updateHash) history.replaceState(null, '', `#slide-${current + 1}`);
    if (document.body.classList.contains('all')) slides[current].scrollIntoView({block:'start'});
    else window.scrollTo(0, 0);
  }
  function toggle(button, className) {
    const enabled = document.body.classList.toggle(className);
    button.setAttribute('aria-pressed', String(enabled));
    return enabled;
  }
  prev.addEventListener('click', () => render(current - 1));
  next.addEventListener('click', () => render(current + 1));
  byId('overview').addEventListener('click', () => {
    const enabled = toggle(byId('overview'), 'all');
    byId('overview').textContent = enabled ? '슬라이드 보기' : '전체 보기';
    render(current, false);
  });
  byId('notes').addEventListener('click', () => toggle(byId('notes'), 'show-notes'));
  byId('print').addEventListener('click', () => window.print());
  const fullscreen = byId('fullscreen');
  if (!document.documentElement.requestFullscreen) fullscreen.hidden = true;
  fullscreen.addEventListener('click', async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await document.documentElement.requestFullscreen();
    } catch (_) { position.textContent = '전체 화면을 사용할 수 없습니다.'; }
  });
  document.addEventListener('fullscreenchange', () => {
    fullscreen.textContent = document.fullscreenElement ? '화면 복원' : '전체 화면';
  });
  document.addEventListener('keydown', event => {
    if (event.ctrlKey || event.metaKey || event.altKey || /INPUT|TEXTAREA|SELECT|BUTTON|A/.test(event.target.tagName)) return;
    if (['ArrowRight','PageDown',' '].includes(event.key)) { event.preventDefault(); render(current + 1); }
    if (['ArrowLeft','PageUp'].includes(event.key)) { event.preventDefault(); render(current - 1); }
    if (event.key === 'Home') { event.preventDefault(); render(0); }
    if (event.key === 'End') { event.preventDefault(); render(slides.length - 1); }
    if (event.key.toLowerCase() === 'n') byId('notes').click();
    if (event.key.toLowerCase() === 'o') byId('overview').click();
  });
  window.addEventListener('hashchange', () => render(fromHash(), false));
  document.body.classList.add('present');
  render(fromHash(), false);
})();
