# SoundReels 다음 컨버세션 작업 인수인계 문서 (Handoff Guide)

> **목적**: 새 대화(컨버세션) 또는 다른 작업자가 이 문서만 읽고 **SoundReels 사운드 조절·편집·믹싱·추출·검수 작업**을 즉시 이어서 진행할 수 있도록 프로젝트 구조, 편집 파라미터, API, 남은 슬롯, 저작권 규칙을 정리한 기준 문서입니다.

---

## 1. 프로젝트 기본 정보 & 실행 방법

* **로컬 경로**: `E:\SoundReels`
* **GitHub 저장소**: `https://github.com/sonminki07/SoundReels` (Private, `main` 브랜치)
* **Python 가상환경**: `E:\SoundReels\.venv\Scripts\python.exe`
* **앱 실행**:
  * 런처: `E:\SoundReels\SoundReels.bat`
  * 직접 실행: `E:\SoundReels\.venv\Scripts\python.exe E:\SoundReels\run_desktop.py` (FastAPI `http://127.0.0.1:8000/` + PyWebView2 데스크톱 창)
* **핵심 경로**:
  * SQLite DB: `E:\SoundReels\data\db\soundreels.db`
  * 오디오 캐시: `E:\SoundReels\data\audio_cache\`
  * 백엔드 API & DSP: `E:\SoundReels\backend\app\main.py`
  * DB 스키마 & 마이그레이션: `E:\SoundReels\backend\app\core\db.py`
  * 프론트엔드 UI: `E:\SoundReels\backend\app\static\index.html`, `E:\SoundReels\backend\app\static\app.js`
  * 확정 완료 보관 폴더: `E:\SoundReels\exports\confirmed_sfx\확정\` (19개 WAV + `SOUNDREELS_확정_영수증_및_저작권안내.txt`)
  * 단일 추출 폴더: `E:\SoundReels\exports\single_sfx\` (`<slot_id>__<sound_id>.wav`)
  * 일괄 추출 폴더: `E:\SoundReels\exports\confirmed_sfx\`

---

## 2. 사운드 조절·편집 파라미터 & 오디오 처리 파이프라인

### A. DB 편집 파라미터 (`slot_candidates` 테이블)
모든 편집은 원본 MP3를 훼손하지 않는 **비파괴(Non-Destructive)** 방식이며 `slot_candidates` 행에 저장됩니다.

| 컬럼명 | 타입 | 범위 / 기본값 | 설명 |
|---|---|---|---|
| `audio_in` | `REAL` | `>= 0.0` (초) | 시작 자르기 지점 |
| `audio_out` | `REAL` | `>= 0.0` (초, `0.0`=끝까지) | 끝 자르기 지점 |
| `playback_rate` | `REAL` | `0.25 ~ 4.0` (UI `0.25x ~ 2.5x`, 기본 `1.0`) | 템포(배속) |
| `preserve_pitch` | `INTEGER` | `1`(유지) / `0`(연동) | `1`이면 `time_stretch`(음정 유지), `0`이면 리샘플(속도 연동 피치 변화) |
| `pitch_semitones` | `REAL` | `-12.0 ~ +12.0` (반음, 기본 `0.0`) | 템포와 독립적인 반음 단위 피치 조절 |
| `tail_sec` | `REAL` | `0.0 ~ 5.0` (UI `0.0 ~ 3.0s`, 기본 `0.0`) | 끝부분 위상 정렬 서스테인 루프 + 지수 감쇠 페이드아웃 여운 길이 |
| `reverse` | `INTEGER` | `0`(정재생) / `1`(역재생) | 오디오 시간축 역재생 |

### B. DSP 처리 순서 (`_render_processed_audio_array` in `backend/app/main.py`)
추출(WAV) 시 항상 아래 순서로 `48kHz` 모노 24-bit PCM WAV를 생성합니다:
1. `librosa.load(..., sr=48000, mono=True)` 로드
2. `reverse == 1`이면 배열 반전 (`y[::-1]`)
3. `audio_in ~ audio_out` 구간 자르기 (둘 다 `0.0`이면 `-30dB` 앞뒤 무음 자동 제거)
4. `playback_rate != 1.0`: `preserve_pitch`에 따라 `time_stretch` 또는 `resample`
5. `abs(pitch_semitones) >= 0.1`: `librosa.effects.pitch_shift` 적용
6. `tail_sec >= 0.05`: `_apply_tail_reverb()` 적용 (끝 구간 자기상관 주기 탐지 → 위상 정렬 OLA 루프 연결 → 지수 감쇠 페이드아웃)
7. `-1.0 dBFS` 피크 정규화 후 `PCM_24` WAV 저장

### C. 핵심 API 엔드포인트
* **편집 저장**: `POST /api/actions/trim_audio` (`slot_id`, `sound_id`, `audio_in`, `audio_out`, `playback_rate`, `preserve_pitch`, `reverse`, `pitch_semitones`, `tail_sec`) — 저장 시 `slots.is_exported = 0` 자동 리셋
* **실시간 미리듣기 WAV**: `GET /api/sounds/{sound_id}/audio?reverse=0|1&pitch=0.0&out=0.0&tail=0.0`
* **두 사운드 레이어 믹싱**: `POST /api/actions/mix_layers` (`target_slot_id`, `slot_id_a`, `sound_id_a`, `gain_a`, `slot_id_b`, `sound_id_b`, `gain_b`, `delay_b_sec`, `save_to_slot`, `export_wav`)
* **즉석 피치 변형(+2st 후보 생성)**: `POST /api/actions/mutate`
* **후보 확정 / 취소**: `POST /api/actions/confirm`, `POST /api/actions/unconfirm`
* **단일 사운드 추출**: `POST /api/export/single` (`exports/single_sfx/<slot_id>__<sound_id>.wav`, `slots.is_exported = 1`)
* **확정 일괄 추출**: `POST /api/export/confirmed_bundle` (`exports/confirmed_sfx/`에 WAV + 영수증 + 주의사항 생성)
* **Unity 내보내기**: `POST /api/export/batch_unity` (`Assets/Audio/SFX/<slot_id>/<slot_id>_01.wav`)
* **슬롯 추가 / 삭제**: `POST /api/slots`, `DELETE /api/slots/{slot_id}`

---

## 3. 남은 작업 슬롯 현황 (미추출 슬롯 12개 + 확인 필요)

현재 앱 우측 하단 **`📦 추출됨 숨김`** 토글이 기본 켜져 있어, `E:\SoundReels\exports\confirmed_sfx\확정\`에 이미 추출된 17개 슬롯은 가려지고 아래 미추출 슬롯들만 작업 대상으로 표시됩니다.

| 슬롯 ID | 슬롯명 | 카테고리 | 진행상태 / 메모 | 후보 수 | 현재 찜(Liked) 후보 |
|---|---|---|---:|---:|---|
| `sfx_paintball_cannon` | 페인트볼 캐논 발사 | `weapon` | `needs_review` (성범이 짬처리) · 묵직한데 깔끔한 발포음 | 9 | (찜 없음 — 후보 탐색/청취 필요) |
| `sfx_paintball_impact` | 페인트볼 폭발 | `weapon` | `needs_review` (성범이 짬처리) · 펑 + 페인트 찰팍 겹침 | 12 | `fs-445109` (Mud Splat Squelch, 0.36s), `fs-174637` (Water Spilling On Floor, 6.35s) |
| `sfx_melee_swing_combo_1` | 평타 1타 휘두르기 (가벼움) | `combat` | `ready` · 메모: `2타` | 7 | `fs-263595` (Sword Slash Swoosh, 1.83s), `fs-59992` (Sword Swing Swoosh, 0.42s) |
| `sfx_shield_bubble_activate` | 버블 방어막(파랑) 활성화 | `skill` | `needs_review` (성범이 짬처리) · 미래지향적 에너지음 | 5 | `fs-880775` (Magic Portal Hum, 8.32s), `fs-238373` (CP_Power_Down03_mid, 6.53s) |
| `sfx_shield_bubble_pop` | 버블 방어막 터지는 소리 | `skill` | `needs_review` (성범이 짬처리) · 비눗방울/깨짐 디스펠음 | 5 | `fs-221528` (Glass Sheet Shatter, 0.42s), `fs-156578` (Picture Frame Shatter, 1.60s) |
| `sfx_mob_death` | 잡몹 사망 (좀비 쓰러짐) | `enemy` | `in_progress` · 메모: `보류` | 4 | `fs-417994` (Body Fall Compiled, 1.14s) |
| `sfx_gimmick_mushroom` | 버섯 점프대 (트램펄린) | `gimmick` | `needs_review` (성범이 짬처리) · 통/보잉 느낌 | 6 | `fs-383240` (Bouncy Cartoon Bounce), `fs-518645` (Jaw Harp Boing), `fs-540790` |
| `sfx_gimmick_bush_trap` | 덤불 함정 (수풀 바스락) | `gimmick` | `ready` · **메모: "소리를 작게 희미한 느낌으로"** | 18 | `fs-59315` (looking_in_bushes), `fs-560261` (Bush Rustle), `fs-106114` (bushmovement) |
| `sfx_target_hit` | 타겟 명중 (과녁 박힘) | `gimmick` | `needs_review` (성범이 짬처리) · 과녁에 탁! 맞는 소리 | 13 | `fs-259542` (Knife Stabbing), `fs-495645` (mug_wood1), `fs-418530` (Darts), `fs-527211` |
| `sfx_midboss_move` | 중간보스 움직이는 소리 | `boss` | 신규 추가 · 거대 로봇 이동/기계 구동음 | 17 | (신규 탐색 완료 — 청취 후 선별 필요) |
| `sfx_slime_move` | 슬라임 움직이는 소리 | `enemy` | 신규 추가 · 액체 괴물 바닥에 내던지는 소리 | 18 | (신규 탐색 완료 — 청취 후 선별 필요) |
| `sfx_ui_tab_loading` | 탭을 눌렀을 때 로딩음 | `ui` | 신규 추가 · 삐이ㅣ이익~ 띠링 UI 로딩 효과음 | 20 | (신규 탐색 완료 — 청취 후 선별 필요) |

*(참고: `sfx_ui_message_alert`는 `확정` 폴더에 `sfx_ui_message_alert__fs-328243.wav`로 이미 추출되어 있으나 DB상 `is_exported` 플래그 확인 필요 시 `1`로 맞춰주면 됩니다.)*

---

## 4. 저작권·라이선스 잠금 및 주의 규칙

백엔드 `_get_license_permissions()`(`backend/app/main.py`) 및 프론트엔드 `applyCandidateLicenseLock()`(`app.js`)이 자동 보호합니다:

* **자동 잠금 (`HTTP 403` + UI 비활성화)**:
  * `ND` (변경금지) → 자르기·피치·배속·여운·역재생·즉석변형 모두 잠금
  * `NC` (비상업) → 최종 채택(`confirm`)·추출(`export`)·믹싱(`mix_layers`) 모두 잠금
  * `fs-125384` (Apple iPhone 잠금해제 원음) → 사용 및 수정 전면 잠금 (사용 금지)
* **크레딧 표기 필수**:
  * `CC BY` (예: `fs-423831` `closing elevator doors 1`) → 수정/상업 이용 가능하나 게임 크레딧에 원작자 표기 필수 (`⚠️표기필수` 배너)
* **상표명 노출 주의 (CC0)**:
  * `fs-182365` (`Samsung`), `fs-328243` / `fs-732816` (`Yamaha`) → 음원 사용·편집은 자유로우나 인게임 UI/크레딧에 제조사 상표명 노출 지양

---

## 5. 작업 마무리 & GitHub 동기화 절차

사운드 조절·선별·추출을 마친 뒤 다른 PC와 상태를 맞추려면 아래 명령을 순서대로 실행하세요:
1. DB 및 활성 오디오 캐시 스테이징: `E:\SoundReels\.venv\Scripts\python.exe E:\SoundReels\scripts\sync_state.py`
2. 커밋 & 푸시: `git -C E:\SoundReels commit -m "..." && git -C E:\SoundReels push origin main`
3. 필요 시 `E:\SoundReels\WorkLog.txt` 및 `E:\SoundReels\exports\confirmed_sfx\확정\SOUNDREELS_확정_영수증_및_저작권안내.txt` 갱신
