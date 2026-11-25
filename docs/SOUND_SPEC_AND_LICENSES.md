# SoundReels 최종 사운드 명세서 및 저작권 검증 보고서 (v2.5)

> **기준 시점**: 팀 회의 후 1차 정리 및 신규 슬롯·URL 반영 (총 **29개 슬롯**)  
> **라이선스 실사 기준**: `https://freesound.org` 직접 접속(`HTTP 200`) 기반 20개 확정/신규 음원 라이브 검증 완료 (`2026-11-25`)

---

## 1. 핵심 요약 및 저작권 경보

| 구분 | 수량 | 비고 |
|---|---:|---|
| 전체 유지/신규 슬롯 | **29개** | 불필요한 미확정·미할당 7개 슬롯 제거 완료 |
| 확정 완료 슬롯 | **17개** (20음원) | 기존 확정 14슬롯 + 신규 URL 확정 3슬롯 + `fs-395368` 임포트 |
| 진행 중·인수인계 슬롯 | **9개** | `ready` / `needs_review` / `in_progress` 작업 슬롯 |
| 신규 추가·탐색 슬롯 | **3개** | `중간보스 움직이는 소리`, `슬라임 움직이는 소리`, `탭을 눌렀을 때 로딩음` |
| 라이브 저작권 검증 | **20 / 20건** | 전원 Freesound `CC0 1.0` 확인 (**단, 1건 고위험 사용 중단 권고**) |

> [!NOTE]
> **고위험 음원 교체 완료 — `#125384` (`Iphone.wav`) → `#732816` (`Soft Button/Switch Press`)**  
> 기존 `sfx_elevator_button`에 확정되어 있던 Apple iPhone 잠금 해제 원음 `#125384`는 확정 목록에서 제외되었고, **`#732816` (`Soft Button/Switch Press` by `SirBagel`, 순수 CC0 1.0)** 으로 교체 확정되었습니다. (라이브 HTTP 200 검증 완료)

> [!WARNING]
> **주의 표기가 필요한 음원 목록 (확정 및 찜 포함)**  
> 1. **`#423831` (`closing elevator doors 1`, crazymonke9 — `sfx_elevator_door_close` `⭐ 찜` #1번)**: 현재 DB 내 유일한 **`CC BY 4.0`** 음원입니다. 상업 사용/편집은 가능하나 게임 내 **원작자·출처 크레딧 표기 필수**입니다.  
> 2. **`#351256` (`Deep Whoosh #1`, Kinoton — `sfx_mob_female_projectile` 확정 #2번)**: `CC0 1.0`이나 프로필 약관(`https://freesound.org/people/Kinoton/`)상 게임 내 SFX 사용·변조는 허용, **원본 파일 단독 재배포/재판매 금지**입니다. (메모 지정 순수 CC0 `#395368` 사용 권장)  
> 3. **`#636578` (`Cutting Power`, swag1773 — `sfx_midboss_death_shutdown` 확정 #2번)**: `#243170` 파생음이며 원작자 프로필에 생성형 AI 학습 금지 안내가 있습니다. 일반 상업 게임 SFX 사용·편집은 가능.  
> 4. **`#182365` (`Samsung V40 - 5400rpm - BB`, Klerrp — `sfx_env_platform_move` 확정 #5번)**: 하드웨어 녹음으로 인게임/크레딧에 **`Samsung` 상표명 노출 지양**.  
> 5. **`#328243` (`Message Notification`, UnderlinedDesigns — `sfx_ui_message_alert` 확정 #1번)**: 악기 프리셋 녹음으로 인게임/크레딧에 **`Yamaha` 상표명 노출 지양**.  
> 6. **`#732816` (`Soft Button/Switch Press`, SirBagel — `sfx_elevator_button` 확정 #1번)**: 물리 버튼 녹음(순수 `CC0 1.0`). 설명상 `Yamaha` 건반 버튼 언급만 있으므로 상표명 노출만 지양.  
> 7. **`#238958` (`DSLR Camera Shots`, qubodup — `sfx_item_tracker` 확정 #1번)**: `2024-06-11`부로 `CC BY` → `CC0 1.0` 전환 확인 완료.

---

## 2. 최종 확정 사운드 목록 및 직접 링크 (17슬롯 / 20음원)

*공통 허용 범위*: Freesound 라이브 `CC0 1.0` (`http://creativecommons.org/publicdomain/zero/1.0/`) 기준 **상업적 이용 가능 / 수정·변조·피치·배속 편집 가능 / 저작자 표기 의무 없음** (위 1번 경고 1건 제외).

| No | 슬롯 ID | 슬롯명 | 확정 음원명 (작성자) | Freesound 직접 링크 | 편집 스펙 (Trim / 템포 / 피치 / 역재생) | 인수인계/회의 메모 | 라이브 라이선스 및 안전도 |
|---:|---|---|---|---|---|---|---|
| 1 | `sfx_cookie_crunch` | 쿠키 사용 시 바삭거리는 소리 | `Munching Food` (`Kodack`) | https://freesound.org/s/256311/ | `3.89s ~ 5.34s` · `1.25x` (피치유지) · `0.0st` | `ready` · 확정 소리에서 두번만 씹기 | 🟢 **CC0 1.0** (자체 녹음, 안전) |
| 2 | `sfx_dash_wind` | 대시 소리 | `fast simple chop 5.aif` (`dave.des`) | https://freesound.org/s/127197/ | 전체 (`0.50s`) · `1.00x` · `0.0st` | - | 🟢 **CC0 1.0** (안전) |
| 3 | `sfx_dash_wind` | 대시 소리 | `fast simple chop 1.aif` (`dave.des`) | https://freesound.org/s/127196/ | 전체 (`0.39s`) · `1.00x` · `0.0st` | - | 🟢 **CC0 1.0** (안전) |
| 4 | `sfx_elevator_bell` | 엘리베이터 도착음 (띵~) | `Door Bell Rings` (`LanDub`) | https://freesound.org/s/182879/ | `0.07s ~ 0.76s` · `1.00x` · `0.0st` | `ready` · 조금 길게 하고 한번만 띵 | 🟢 **CC0 1.0** (캠코더 녹음, 안전) |
| 5 | `sfx_elevator_button` | 엘리베이터 층 버튼 누름음 | `Soft Button/Switch Press` (`SirBagel`) | https://freesound.org/s/732816/ | `1.58s ~ 1.99s` · `1.00x` · `0.0st` | 교체 확정 (`#125384` 제외) | 🟢 **CC0 1.0** (물리 버튼 녹음, 상표명 미노출 권장) |
| 6 | `sfx_elevator_button` | 엘리베이터 층 버튼 누름음 | `Button press.wav` (`ZvinbergsA`) | https://freesound.org/s/273143/ | 전체 (`0.93s`) · `1.00x` · `0.0st` | - | 🟢 **CC0 1.0** (FL Studio 합성음, 대체 권장) |
| 7 | `sfx_elevator_door_close` | 엘리베이터 문 닫히는 소리 | `ElevatorDoor2.wav` (`tiptoe84`) | https://freesound.org/s/155897/ | 전체 (`6.02s`) · `1.00x` · `0.0st` | - | 🟢 **CC0 1.0** (원 설명은 문 열림 녹음, 안전) |
| 8 | `sfx_elevator_door_open` | 엘리베이터 문 열리는 소리 | `Elevator Door opens and closes` (`NachtmahrTV`) | https://freesound.org/s/556699/ | `0.00s ~ 4.20s` · `1.00x` · `0.0st` | - | 🟢 **CC0 1.0** (안전) |
| 9 | `sfx_elevator_move` | 엘리베이터 움직이는 소리 | `Refridgerator hum.wav` (`NHumphrey`) | https://freesound.org/s/200979/ | 전체 (`21.42s`) · `1.00x` · `0.0st` | - | 🟢 **CC0 1.0** (Zoom H4n 녹음, 안전) |
| 10 | `sfx_env_fan` | 환풍기 바람/팬 소리 | `Industrial ceiling fan 2` (`pushkin`) | https://freesound.org/s/319410/ | 전체 (`34.08s`) · `1.00x` · `0.0st` | `ready` · 여기에서 약한 바람 소리를 추가 | 🟢 **CC0 1.0** (안전) |
| 11 | `sfx_env_platform_move` | 움직이는 플랫폼 기계음 | `Samsung V40 - 5400rpm - BB` (`Klerrp`) | https://freesound.org/s/182365/ | `3.51s ~ 7.68s` · `1.00x` · `0.0st` | - | 🟡 **CC0 1.0** (하드웨어 브랜드명 노출 지양) |
| 12 | `sfx_midboss_death_shutdown` | 중간보스 거대 로봇 사망 셧다운 | `Cutting Power` (`swag1773`) | https://freesound.org/s/636578/ | `0.00s ~ 3.92s` · `1.25x` (피치유지) · `0.0st` | - | 🟡 **CC0 1.0** (`#243170` 파생 / 게임 사용 안전) |
| 13 | `sfx_mob_female_projectile` | 잡몹(여) 투사체 비행음 | `Swish 5` (`ihitokage`) | https://freesound.org/s/395368/ | 전체 (`0.30s`) · `1.00x` · `0.0st` | `ready` · `https://freesound.org/s/395368/ 이걸로 확정` | 🟢 **CC0 1.0** (안전) |
| 14 | `sfx_mob_female_projectile` | 잡몹(여) 투사체 비행음 | `Deep Whoosh #1` (`Kinoton`) | https://freesound.org/s/351256/ | `0.00s ~ 1.59s` · `1.00x` · `0.0st` | 기존 확정분 (메모 지정본은 `#395368`) | 🟡 **CC0 1.0** (Kinoton 프로필 약관: 원본 재배포 금지) |
| 15 | `sfx_mob_male_swing` | 잡몹(남) 휘두르는 공격음 | `Wooden Stick Swing 1_2` (`Joao_Janz`) | https://freesound.org/s/485277/ | `0.00s ~ 0.23s` · `0.75x` (피치유지) · `0.0st` | `ready` · 피치를 높더 높혀서 | 🟢 **CC0 1.0** (안전) |
| 16 | `sfx_spray_fkill` | 스프레이 발사 소리 | `Graffiti Spraycan` (`1histori`) | https://freesound.org/s/412530/ | `1.42s ~ 2.57s` · `0.75x` (피치유지) · `0.0st` | - | 🟢 **CC0 1.0** (자체 녹음, 안전) |
| 17 | `sfx_ui_tab_open` | Tab BUT UI 활성화 | `Synth whip sound` (`flood-mix`) | https://freesound.org/s/413393/ | `0.00s ~ 0.83s` · `1.00x` · `0.0st` | `ready` · 정재생이 열리는 / 역재생이 닫히는 | 🟢 **CC0 1.0** (신스 합성음, 안전) |
| 18 | `sfx_player_fall_high` | 플레이어 높은 곳에서 떨어질 때 | `woosh.wav` (`freakinbehemoth`) | https://freesound.org/s/243400/ | 전체 (`0.56s`) · `1.00x` · `0.0st` | **추가 URL** · 고공 추락 풍절/낙하 사운드 | 🟢 **CC0 1.0** (Blue Yeti 녹음, 안전) |
| 19 | `sfx_item_tracker` | 위치 추적기 | `DSLR Camera Shots` (`qubodup`) | https://freesound.org/s/238958/ | 전체 (`2.44s`) · `1.00x` · `0.0st` | **추가 URL** · 위치 추적기 작동/신호 사운드 | 🟡 **CC0 1.0** (2024-06-11 CC BY→CC0 전환) |
| 20 | `sfx_ui_message_alert` | 메세지 알림 | `Message Notification` (`UnderlinedDesigns`) | https://freesound.org/s/328243/ | 전체 (`2.60s`) · `1.00x` · `0.0st` | **추가 URL** · 메세지 수신 알림음 | 🟡 **CC0 1.0** (Yamaha P95 상표명 노출 지양) |

---

## 3. 진행 중·인수인계 및 신규 탐색 슬롯 (12슬롯)

### A. 인수인계 / 작업 중 유지 슬롯 (9개)

| 슬롯 ID | 카테고리 | 슬롯명 | 진행 상태 | 담당자 | 메모 / 인수인계 내용 | 현재 후보/찜 |
|---|---|---|---|---|---|---|
| `sfx_gimmick_bush_trap` | `gimmick` | 덤불 함정 (수풀 바스락) | `ready` (인수완료) | - | 소리를 작게 희미한 느낌으로 | 18후보 · 찜 3 |
| `sfx_gimmick_mushroom` | `gimmick` | 버섯 점프대 (트램펄린) | `needs_review` (인수요청) | 성범이 짬처리 | 버섯 밟으면 튀는 소리 트램펄린 같이 통/보잉 | 6후보 · 찜 3 |
| `sfx_melee_swing_combo_1` | `combat` | 평타 1타 휘두르기 (가벼움) | `ready` (인수완료) | - | `2타` | 7후보 · 찜 2 |
| `sfx_mob_death` | `enemy` | 잡몹 사망 (좀비 쓰러짐) | `in_progress` (작업중) | - | `보류` | 4후보 · 찜 1 |
| `sfx_paintball_cannon` | `weapon` | 페인트볼 캐논 발사 | `needs_review` (인수요청) | 성범이 짬처리 | 묵직한데 깔끔한 발포음 | 9후보 · 찜 0 |
| `sfx_paintball_impact` | `weapon` | 페인트볼 폭발 | `needs_review` (인수요청) | 성범이 짬처리 | 펑 터지는 소리 + 페인트 흩뿌려지는 소리 | 12후보 · 찜 2 |
| `sfx_shield_bubble_activate` | `skill` | 버블 방어막(파랑) 활성화 | `needs_review` (인수요청) | 성범이 짬처리 | 파란색 보호막/버블 쉴드 에너지음 | 5후보 · 찜 2 |
| `sfx_shield_bubble_pop` | `skill` | 버블 방어막 터지는 소리 | `needs_review` (인수요청) | 성범이 짬처리 | 비눗방울 터지는 소리 / 디스펠음 | 5후보 · 찜 2 |
| `sfx_target_hit` | `gimmick` | 타겟 명중 (과녁 박힘) | `needs_review` (인수요청) | 성범이 짬처리 | 화살이나 투사체가 과녁에 탁! 맞았을 때 | 13후보 · 찜 4 |

### B. 신규 추가 및 자동 탐색 완료 슬롯 (3개)

| 슬롯 ID | 카테고리 | 슬롯명 | 설명 / 톤 가이드 | 탐색된 후보 수 |
|---|---|---|---|---:|
| `sfx_midboss_move` | `boss` | 중간보스 움직이는 소리 | 중간보스 거대 로봇 이동/기계 구동음 | **17개** |
| `sfx_slime_move` | `enemy` | 슬라임 움직이는 소리 | 액체 괴물 바닥에 내던지는 소리 | **18개** |
| `sfx_ui_tab_loading` | `ui` | 탭을 눌렀을 때 로딩음 | 삐이ㅣ이익~ 띠링 느낌의 UI 로딩 효과음 | **20개** |

---

## 4. 회의 정리 후 제거된 불필요 슬롯 (7슬롯)

미확정 상태이며 작업중/인수요청/인수완료 표기가 없어 DB에서 정리한 슬롯:
1. `sfx_melee_hit_machine` — 평타 맞추는 소리 (기계/쇳소리) *(기존 메모: 폐기)*
2. `sfx_melee_swing_combo_2` — 평타 2타 휘두르기 (중간)
3. `sfx_melee_swing_combo_3` — 평타 3타 휘두르기 (묵직함)
4. `sfx_mob_female_proj_hit` — 투사체 적중 퍽 소리 *(기존 메모: 폐기)*
5. `sfx_mob_footstep` — 잡몹 걸어다니는 발소리 (하리)
6. `sfx_player_hurt` — 플레이어 피격음 (보류)
7. `sfx_ui_tab_close` — Tab BUT UI 꺼짐 *(활성화 음원 `#413393` 역재생으로 통합)*

---

## 5. SoundReels v2.5 신규 기능 안내

- **독립 피치(`🎵 피치 -12 ~ +12st`) & 템포(`⚡ 템포 0.5x ~ 2.5x`) 조절**: 하단 파형 편집기에서 템포와 반음(semitone) 피치를 독립 조절해 실시간 미리듣기(`librosa.effects.pitch_shift`) 및 Unity WAV 내보내기(`export_processed_audio_to_unity`)에 동일 반영됩니다. (`🔊 0st` 버튼으로 원음 피치 즉시 복구 가능)
- **확정 슬롯 가리기(`🙈 확정 숨김`)**: 우측 `사운드 슬롯` 상단 필터에서 이미 확정된 슬롯을 숨기고 남은 미확정/인수인계 슬롯만 집중 작업할 수 있습니다.
- **슬롯 자유 추가(`➕ 슬롯`) & 삭제(`🗑️ 슬롯 삭제`)**: 우측 `사운드 슬롯` 상단 `➕ 슬롯` 버튼으로 새 슬롯과 자동 AI 탐색을 즉시 생성할 수 있으며, 슬롯 우클릭 인수인계 모달 하단 `🗑️ 슬롯 삭제` 버튼으로 확정 슬롯을 포함한 불필요한 슬롯을 안전하게 제거할 수 있습니다.
