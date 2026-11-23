# 송폼(Song Form) 표기 규격 · PPT 매핑 · 비전 검수 체크리스트

## 1. 확장 송폼 표준 토큰 (`arrangement_song_form`)

| 토큰 | 의미 | 예시 및 설명 |
| :--- | :--- | :--- |
| `I`, `I(4m)`, `I(8m)` | Intro (괄호 안은 마디 수) | `I(4m)`: 소편성·일반 인트로 4마디 |
| `V1`, `V2`, `V3`, `V4` | Verse (절) | `[V1, V4]`: 찬송가 1절, 4절만 발췌 |
| `PC` | Pre-Chorus | DB `available_sections`에 `PC`가 있을 때만 사용 |
| `C`, `[C only]` | Chorus (후렴) / 후렴만 부르기 | `[C only]`: 메들리나 Reprise에서 후렴부터 바로 진입 |
| `Inter`, `Inter(8m)` | Interlude (간주) | 마디 수 병기로 세션 합주 길이 일치 |
| `B` | Bridge | DB `available_sections`에 `B`가 있을 때만 사용 |
| `Tag` | 후렴 끝 소절 반복 태그 | 클라이맥스 여운 반복 |
| `O` | Outro | 독립 종지(`pad_break`·마지막 곡) 시 표기; 직결 메들리(`segue`) 시 `-->(Medley)`로 대체 |
| `x2`, `x3` | 직전 섹션/묶음 반복 횟수 | `C(x2)`, `(B - C)(x2)` |
| `Vamp` | 기도회·멘트용 코드 루프 반복 | `C(x2) - Vamp(멘트/기도)` |
| `-->(Medley)` | Outro 없이 다음 곡으로 직결 | `... - C(x2) -->(Medley 2번곡 [C only])` |

## 2. 방송실 PPT 자막 큐시트 한글 변환 규칙 (`broadcast_cue.txt`)

음악 기호에 익숙하지 않은 방송실 오퍼레이터가 자막을 놓치지 않도록 아래 한글 레이블과 `first_line_incipit`(최대 15자 첫 구절)을 함께 생성합니다:
- `I(4m)` → `(전주 4마디)`
- `V1`, `V2` → `1절`, `2절`
- `PC` → `프리코러스`
- `C(x2)` → `후렴 (2번)`
- `Inter(8m)` → `(간주 8마디)`
- `B(x3)` → `브릿지 (3번)`
- `Vamp` → `반주 루프 (기도/멘트 대기)`
- `-->(Medley)` → `★ 무공백 바로 다음 곡 진입`

## 3. 에이전트 멀티모달 비전 검수 체크리스트 (`read_file`)

`find_sheets.py`로 매칭된 악보 이미지(PNG)를 `stamp_sheets.py`에 넘기기 전 반드시 시각적으로 확인합니다:

1. **실제 악보 Key 판독 & 2분기 처리**:
   - 조표(♯/♭ 개수)와 첫 코드·끝 코드를 읽어 목표 Key와 비교합니다.
   - 불일치 시:
     - **`[A] 악보 Key 기준 재계산`**: `--vision-file` JSON에 `"key_mismatch_action": "recalculate"`를 지정해 `find_sheets.py`가 `setlist_plan.json`의 Key·이조폭·전조 브릿지를 자동 재계산(`plan_transitions.py` 재실행 금지).
     - **`[B] 원본 악보 유지 + 이조/카포 경고 스탬핑`**: `"key_mismatch_action": "stamp_transpose_warning"` 지정 시 상단 배너에 `⚠️ 악보: G Key → 실연: A Key (+2)` 경고를 출력합니다(`--show-capo` 활성화 시 `/ 기타 Capo N` 병기).
2. **실제 구간 및 반복 기호 대조**:
   - 악보 위 섹션 표기(`Intro`, `Verse`, `Pre-Chorus`, `Chorus`, `Bridge`, `Outro`)와 도돌이표·세뇨(`D.S.`)·코다(`Coda`) 흐름을 확인합니다.
   - 실제 악보에 없는 `PC` 또는 `B`가 송폼에 들어갔다면 즉시 제거합니다.
3. **일회성 가사-설교 정합성 및 건전성 확인 (DB 미저장)**:
   - 악보 위 가사 텍스트를 눈으로만 판독하여 ① 설교 본문·주제와의 신학적 정합성(화자·뉘앙스 오류 없음), ② 번안곡의 가사 버전 일치 여부, ③ 이단·불건전 개사 여부를 확인합니다. (확인한 가사 원문은 어떤 파일이나 DB에도 저장하지 않습니다.)
4. **리듬(Groove)·박자·BPM 표기 판독 (`verified_groove`, `verified_bpm`, `verified_time_signature`)**:
   - 악보 상단의 템포·리듬 힌트(`Shuffle`, `Bounce`, `♫=♪³`, `Country`, `Go-Go`, `16 beat`, `8 beat`, `Moderato`, `BPM`)와 박자표를 읽고 필요 시 `vision_verifications.json`에 함께 기록합니다.
   - 표준 `verified_groove` 값: `shuffle_bounce` (셔플/바운스), `country_gogo` (고고/컨트리), `16th_upbeat` (16비트 스트레이트), `straight_8th` (8비트 스트레이트), `rock_upbeat` (업비트 록), `16th_ballad` (16비트 발라드), `6_8_worship` (6/8 워십).
   - 셔플/바운스 ↔ 스트레이트 또는 고고 ↔ 16비트처럼 리듬 분박이 충돌하면 `find_sheets.py`가 자동으로 `rhythm_reset`(앞곡 정박 엔딩 후 4박 카운트 재진입) 전환을 재계산하고 `church_local.db`에 학습 저장합니다.
