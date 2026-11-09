(() => {
  const agents = {
    migration: ['Migration Agent', '매핑룰과 원천·대상 DDL로 이관 SQL과 검증 SQL을 생성합니다. 실행 결과를 확인하고, 실패 위치에 따라 재처리합니다.', '선행 작업 확인 → DDL 조회 → SQL 생성 → 이관 실행 → 검증 → 상태·로그 저장'],
    conversion: ['Conversion Agent', 'AS-IS SQL에 테이블·컬럼 매핑을 반영해 TO-BE SQL로 변환합니다. 원본 구현은 SELECT SQL의 바인드 사례와 결과 건수를 확인합니다.', 'AS-IS SQL + 매핑룰 → TO-BE SQL → 바인드 구성 → SELECT 결과 건수 비교'],
    tuning: ['Tuning Agent', '변환된 SQL에 튜닝 규칙을 적용하고 SELECT 결과 건수를 비교합니다. 실제 성능 향상은 실행 계획과 실행 시간을 별도로 측정해야 합니다.', '검증된 TO-BE SQL → 튜닝 규칙 적용 → 전후 결과 건수 비교'],
    formatting: ['Formatting Agent', '처리된 SQL의 서식과 가독성을 정리합니다. 이관·변환·튜닝 결과를 검토하고 전달하기 쉬운 산출물로 연결합니다.', '처리된 SQL → 서식 정리 → 최종 SQL 산출물']
  };
  document.querySelectorAll('.architecture').forEach(root => {
    const buttons = [...root.querySelectorAll('[role=tab]')];
    const detail = root.querySelector('[role=tabpanel]');
    const choose = button => {
      buttons.forEach(b => { b.setAttribute('aria-selected', String(b === button)); b.tabIndex = b === button ? 0 : -1; });
      const data = agents[button.dataset.agent];
      detail.setAttribute('aria-labelledby', button.id);
      detail.querySelector('h3').textContent = data[0];
      detail.querySelector('.detail-copy').textContent = data[1];
      detail.querySelector('.detail-steps').textContent = data[2];
    };
    buttons.forEach((b,i) => {
      b.addEventListener('click', () => choose(b));
      b.addEventListener('keydown', e => {
        let target;
        if(e.key === 'ArrowRight') target = buttons[(i+1)%buttons.length];
        if(e.key === 'ArrowLeft') target = buttons[(i+buttons.length-1)%buttons.length];
        if(e.key === 'Home') target = buttons[0];
        if(e.key === 'End') target = buttons.at(-1);
        if(target){e.preventDefault();choose(target);target.focus();}
      });
    });
    choose(buttons[0]);
  });
  const links = [...document.querySelectorAll('.topbar nav a[href^="#"]')];
  if ('IntersectionObserver' in window) {
    const observer = new IntersectionObserver(entries => {
      const visible = entries.filter(e => e.isIntersecting).sort((a,b) => a.boundingClientRect.top - b.boundingClientRect.top);
      if (!visible.length) return;
      links.forEach(link => {if(link.hash === `#${visible[0].target.id}`) link.setAttribute('aria-current','location');else link.removeAttribute('aria-current');});
    }, {rootMargin:'-12% 0px -55% 0px',threshold:0});
    document.querySelectorAll('section[id]').forEach(s => observer.observe(s));
  }
})();
