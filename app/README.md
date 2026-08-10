# App Package

이 폴더는 Streamlit 운영 콘솔입니다.

백엔드 batch process와 domain pipeline은 `src/smart_migrate` 아래에서 관리하고, 이 폴더는 화면, UI 전용 조회, 사용자 액션을 담당합니다.

주요 경로:

- `app.py`: Streamlit 운영 콘솔 진입점
- `pages/`: 화면 단위 모듈
- `utils/`: UI 전용 DB 조회, RAG 관리, 환경 설정, agent 제어 helper

현재 UI 코드는 `app/utils`를 직접 사용합니다. 이전에 만들었던 `app/services`는 실제 구현 없이 `app/utils`를 재노출하는 shim 폴더였기 때문에 제거했습니다.
