# 설치한 디자인 스킬

2026-10-25 공식 GitHub 저장소에서 skill-installer로 사용자 스킬 폴더에 설치했다. 디렉터리는 C:/Users/11824/.codex/skills 아래다. 다음 턴부터 자동 탐색 가능하다.

| 스킬 | 원본 | 고정 리비전 | 사용 역할 |
|---|---|---|---|
| emil-design-eng | https://github.com/emilkowalski/skills/tree/main/skills/emil-design-eng | 2d228630ba1446365f614a108cb8252f1feda202 | 접근성·조작 피드백·전환 |
| impeccable | https://github.com/pbakaus/impeccable/tree/main/.agents/skills/impeccable | 936867543ac2576920d663cc9803597f6044f3ac | 내용 계층·프로젝트 맥락·검토 |
| design-taste-frontend | https://github.com/Leonxlnx/taste-skill/tree/main/skills/taste-skill | 12af471f627f866411b4bd785b19646d3672784c | 시각 방향·구성·문구 |

Emil의 실제 배포처는 사용자가 기억한 emilkowalski/skill이 아니라 확인 시점의 emilkowalski/skills다. Taste Skill은 설치 폴더가 taste-skill이고 manifest의 이름은 design-taste-frontend다.

자료는 발표용 문서로 제작한다. 웹사이트 마케팅 페이지의 사진·전환·랜딩 페이지 관례보다 사용자가 지정한 슬라이드형 아키텍처 설명을 우선한다. Impeccable context를 실행해 기존 디자인 문서가 없음을 확인했다. 자동 hook은 활성화하지 않았다.

## 스크롤 시안 후속 적용
현재는 사용자가 A 리포트형을 확정했다. B·C 파일과 비교 UI는 제거했으며, 아래 기록은 시안 제작 이력이다. 확정본은 개발자 대상 20~25분이며 사람·Agent 역할, DBA·B/E 활용, LangGraph Workflow, Dify 7개 메인 노드·5개 Tool 노드와 실패 분석으로 확장했다.
후속 요청으로 페이지 넘김을 제거하고 데스크톱 스크롤 시안 3개를 만들었다. 현재 기준은 DESIGN.md다.
- Taste: A quiet-instrument(seed 96556807), B product-performs(seed 1603318430), C type-only-statement(seed 356890023). 방향 구성과 실제 SQL·아키텍처를 중심으로 적용했다.
- Impeccable: context·craft-floor·detect와 별도 최종 검토를 수행했다. 세 접근성 개선을 반영하고 후속 검토를 완료했다.
- Emil: 명시적 150ms 버튼 전환, 즉시 반응하는 키보드 탭, 포커스·aria-selected·roving tabindex, reduced-motion을 적용했다.
- 사진·자동 움직임 요구보다 사용자의 장식 없는 웹페이지 요청을 우선했다. 휴대폰·태블릿 검사는 하지 않았다.
