# 🎧 SoundReels

> AI가 **찾고 줄 세우기**까지 맡고, 사람은 **듣고 결정**만 한다. **Spotify 감성의 몰입형 플레이어 + 릴스식 초고속 넘기기**를 결합한 게임 SFX 큐레이션 도구.

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11-brightgreen.svg)](https://www.python.org/)

---

## 📖 개요

게임 개발 시 상황에 딱 맞는 효과음(SFX)을 찾기 위해 수많은 웹사이트를 돌아다니며 일일이 오디오를 재생하고 판단하는 극심한 피로를 덜어내기 위한 도구입니다.

- **자연어 시맨틱 검색**: LAION-CLAP 임베딩 모델을 활용해 파일명이 `SFX_001.wav`여도 파형 자체와 문맥을 분석해 검색.
- **Spotify 스타일 다크 플레이어**: 주파수/음색 기반 앰비언트 컬러 비주얼라이저와 키보드 단축키(`↑`, `↓`, `S`, `F`, `Enter`)로 릴스처럼 넘기며 빠른 의사결정.
- **적응형 순위 재정렬 (Rocchio)**: 넘기거나 찜하는 행동을 실시간 반영해 다음 후보를 취향에 맞게 수렴.
- **100% 안전한 상업적 라이선스 게이트**: CC0, CC-BY(출처 자동 명시) 필터링 및 게임 크레딧(`CREDITS.txt`) 자동 생성.
- **Unity 원클릭 익스포트**: 확정된 사운드를 Unity 프로젝트 폴더(`Assets/Audio/SFX/...`)로 바로 복사하고 GUID 메타 파일 보존.

상세 기획 및 아키텍처는 [docs/PLAN.md](docs/PLAN.md)를 참고하세요.

---

## 🛠️ 개발 로드맵

- [ ] **P0: 품질 검증 프로토타입** (CLAP 오디오 검색 정확도 검증, GTX 1060 환경 벤치마크)
- [ ] **P1: MVP** (FastAPI 백엔드, 로컬/LotsOfSounds CC0 커넥터, React Spotify 스타일 UI, Unity 내보내기)
- [ ] **P2: 확장** (Freesound OAuth 커넥터, Antigravity MCP 서버 연동, 절차적 사운드 생성기)
