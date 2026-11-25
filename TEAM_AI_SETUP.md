# 🤝 SoundReels 팀원 · AI 에이전트 원클릭 세팅 및 작업 이어하기 가이드

이 문서는 **팀원이 자신의 PC와 AI 에이전트(Antigravity, Cursor, Claude, Codex 등)에게 그대로 넘겨, 현재 작업 상태(`DB · 오디오 캐시 · 확정 WAV · 남은 12개 슬롯`)를 100% 동일하게 재현하고 바로 작업을 이어갈 수 있도록** 만든 안내서입니다.

---

## 1. 팀원이 본인 AI에게 그대로 복사해서 넣을 프롬프트

아래 박스를 통째로 복사해 AI 채팅창에 입력하세요:

```text
https://github.com/sonminki07/SoundReels 저장소를 내 PC에 클론(이미 있으면 git pull)한 뒤,
레포 안의 `TEAM_AI_SETUP.md`와 `docs/SOUNDREELS_HANDOFF.md`를 읽고
1) 실행 환경(.venv 및 패키지)을 자동으로 설치하고,
2) SoundReels 데스크톱 앱(`run_desktop.py`)을 실행해서 현재 상태 그대로 띄워줘.
3) 남은 미추출 슬롯 목록과 사운드 조절/추출 방법을 요약해서 알려줘. 바로 이어서 작업할게.
```

---

## 2. AI 에이전트 자동 실행 체크리스트

이 문서를 읽은 AI 에이전트는 아래 순서대로 사용자 PC를 세팅하세요.

### Step 1. 저장소 클론 및 최신 상태 받기
```powershell
git clone https://github.com/sonminki07/SoundReels.git
cd SoundReels
```
이미 폴더가 있다면:
```powershell
git pull origin main
```

### Step 2. Python 환경(`.venv`) 확인 및 자동 설치
* Windows에서는 **`SoundReels.bat`** 를 실행하면 `.venv` 생성, PyTorch(CUDA/CPU 자동 선택), `backend/requirements.txt` 설치, 앱 실행까지 자동으로 처리됩니다.
* 직접 CLI로 설치할 경우 (Python 3.10~3.12 권장):
```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cu121
.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```
*(NVIDIA GPU가 없으면 CPU PyTorch로 설치해도 동일하게 동작합니다.)*

### Step 3. 현재 프로젝트 작업 컨텍스트 로드
아래 두 문서를 반드시 먼저 읽고 사용자에게 브리핑하세요:
1. [`docs/SOUNDREELS_HANDOFF.md`](docs/SOUNDREELS_HANDOFF.md) — **사운드 조절 파라미터(트림/템포 0.25x~2.5x/독립 피치 ±12st/서스테인 여운/역재생/레이어 믹싱), API, 남은 미추출 12개 슬롯 상세, 저작권 잠금 규칙**
2. [`exports/confirmed_sfx/확정/SOUNDREELS_확정_영수증_및_저작권안내.txt`](exports/confirmed_sfx/확정/SOUNDREELS_확정_영수증_및_저작권안내.txt) — **이미 확정 완료된 19개 WAV 파일과 원본 링크·주의사항**

### Step 4. 앱 실행 & 확인
```powershell
.venv\Scripts\python.exe run_desktop.py
```
* 서버 주소: `http://127.0.0.1:8000/`
* 앱이 열리면 우측 하단 **`📦 추출됨 숨김 중`** 필터가 기본 활성화되어 있어, 이미 추출된 슬롯들은 빠져 있고 **남은 미추출 슬롯 12개**만 바로 작업 목록에 보입니다.
* *(선택)* AI 추천/프롬프트 기능을 추가로 쓰려면 `.env.example`을 복사해 `.env`를 만들고 본인 `GEMINI_API_KEY`를 넣으면 됩니다. (없어도 오디오 편집·추출·로컬/Freesound/CLAP 검색은 모두 정상 동작합니다.)

---

## 3. 작업 완료 후 내보내기 & 공유 방법
* **단일 사운드 추출**: 중앙 **`📤 단일 추출`** → `exports/single_sfx/<슬롯ID>__<사운드ID>.wav`
* **확정 일괄 추출**: 하단 **`📦 확정 WAV 추출`** → `exports/confirmed_sfx/` (WAV + 영수증 + 주의문서)
* **두 사운드 레이어 합치기**: 하단 **`🎛️ 동시 재생`** → 볼륨/딜레이 조절 후 저장 또는 WAV 추출
* **상태 동기화(팀 Git 푸시 권한이 있을 때)**:
  ```powershell
  .venv\Scripts\python.exe scripts/sync_state.py
  git commit -m "update sound curation"
  git push origin main
  ```
