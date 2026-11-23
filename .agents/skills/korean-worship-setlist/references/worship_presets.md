# 예배 유형 · 교회력 절기 · 2블록 프리셋 가이드

## 1. 예배 유형별 기본 프리셋

| 예배 ID | 예배명 | 권장 시간 | 곡 수 | 기본 서사·템포 구조 | 세대·신곡·편성 정책 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `sunday_main` | 주일 대예배 | 15~20분 | 3~4곡 | 입례/경배(미들·빠른 찬양/장중 찬송가 허용) → 선포(미들·빠른) → 고백·결단(발라드·미들) + 선택적 설교 후 결단 1곡 | 전 세대 친숙도 최우선, 찬송가/80-90s 복음성가 1곡+ 권장, **신곡 월 1~2회 제한**, `seeker_emphasis` 반영 |
| `youth` | 청년부/다음세대 | 25~35분 | 4~5곡 | **[Block A: 말씀 전]** 빠른곡 1~2 → 미들 1 → 발라드 1 / **[Block B: 말씀 후]** 결단·기도회(본 콘티 핵심곡 `Reprise` 또는 동일/온음 Key 기도회 루프) | 모던 워십 중심, 신곡 1곡(20~25%) 반영 허용 |
| `friday_revival` | 금요성령집회/수련회 | 40분+ | 5~7곡 | 빠른곡 메들리 2~3 → 미들 1 → 깊은 발라드/기도회 2~3 + 결단송 | `B` 반복·고조·`Vamp` 적합 곡 우선, 풀밴드 에너지 |
| `small_group` | 수요/새벽/소그룹 | 10~15분 | 2~3곡 | 미들 1 → 발라드 1~2 (또는 발라드 2~3) | `band_dependency='low'` / `piano_only`·`acoustic` 우선, 인트로 `I(8m)→I(4m)` 축소 권장 |

## 2. 말씀 전(`pre_sermon`) / 말씀 후(`post_sermon`) 2블록 구조

- **Block A (`pre_sermon`)**: 부르심(`opener`) → 선포(`declaration`) → 묵상·고백(`meditation`/`confession`) 순서로 회중의 마음을 말씀 앞으로 모읍니다.
- **Block B (`post_sermon`)**: 설교 직후 헌신·결단(`dedication`)과 기도회(`Vamp`)를 담당합니다.
  - **1순위 추천**: Block A의 마지막 발라드 또는 핵심 고백곡의 후렴/브릿지를 재연하는 **`Reprise`** (회중 학습 효과 및 즉흥 전환 극대화).
  - **2순위 추천**: Block A 마지막 곡과 동일 키 또는 온음 상향 키의 결단송 1곡 + 기도회 코드 루프(`IV - V - iii - vi`, `ii7 - V7sus4 - Imaj7`).

## 3. 교회력 절기(`church_seasons`) 가중 및 배제 규칙

| 절기 코드 | 절기/예식명 | 우선 가중(`theological_tone` / 태그) | 자동 감점 또는 배제 규칙 |
| :--- | :--- | :--- | :--- |
| `ordinary` | 일반 주일(평년) | 전체 균형 선곡 | 특별 제한 없음 |
| `advent` | 대림절 | `comfort_trust`, `adoration`, 오실 메시아 대망·기다림 | 성탄 전야 이전의 성탄 축하 캐럴(`성탄절 축제`) 조기 배치 감점 |
| `christmas` | 성탄절 | `adoration`, `resurrection_victory` 제외 성탄 찬양·캐럴 | 성탄 메시지 외 비관련 곡 후순위 |
| `epiphany` | 주현절 | 빛·영광·열방 선교(`mission_sending`) | - |
| `lent` | 사순절 | `penitence`, `cross_sacrifice`, 회개·자기부인 | 축제형·환호형 `fast` 곡 및 `resurrection_victory` 톤 자동 감점/배제 |
| `holy_week` | 고난주간/종려주일 | `cross_sacrifice`, `penitence`, 대속·십자가 묵상 | 경쾌한 `fast` 곡 및 부활 승리 톤 **강제 배제** |
| `easter` | 부활절 | `resurrection_victory`, `adoration`, 부활·생명·소망 | 애통/참회 전용 곡 후순위 |
| `pentecost` | 성령강림절 | 성령 임재·부흥·증인·선교 | - |
| `thanksgiving` | 추수감사절 | 감사·열매·섭리·공급 | - |
| `watchnight` | 송구영신 | 인도하심·새 일·소명·헌신(`mission_sending`) | - |
| `communion` | 성찬식 | `cross_sacrifice`, 주님의 몸과 피·연합·기념 | 축제형 빠른 곡 배제 |
| `baptism` | 세례/입교 | `confession`, 새 생명·구원·거듭남·고백 | - |

## 4. 단일 예배 순서 슬롯(`service_slots`) 선곡 규칙

전체 콘티 외에 특정 순서 1곡만 요청할 때 `search_candidates.py --slot <슬롯ID>` 옵션(DB `service_slots` 필드 매칭)으로 정밀 필터링합니다:
- `pre_service`: 준비찬양 (회중 모임 중 친숙한 선포/감사곡)
- `introit_doxology`: 입례·송영 (새찬송가 1장 등 장중하고 짧은 고백, 쿨타임 `pinned_ritual` 면제 가능)
- `worship_set`: 본 예배 경배와 찬양
- `offertory`: 봉헌송 (감사·헌신·드림 톤의 미들/발라드, 1~2절 중심)
- `sermon_response`: 설교 후 결단송
- `sending_benediction`: 축복·파송송 (파송과 사명 톤, 고정 예식곡 `pinned_ritual` 우선 지원)
