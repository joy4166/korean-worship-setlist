---
name: korean-worship-setlist
description: >-
  Use when planning or revising a Korean church worship setlist (찬양 콘티, 예배 선곡,
  세대통합 예배, 봉헌·결단 특송), running team/band/vocal onboarding (init, 초기 설정,
  찬양팀 세팅), swapping a song or Key in rehearsal (update, 부분 수정), tracking
  setlist history or 6-week cooldowns (history, 쿨타임, 신곡 정착), or verifying,
  transposing, and stamping worship sheet music.
---

# Korean Worship Setlist (`korean-worship-setlist`)

한국 교회의 예배 규모·절기·설교 주제·찬양팀 세션에 맞춰 **찬양 콘티 선곡 → Key/박자 전조 설계 → 로컬 악보 매칭·비전 검수 → 송폼 스탬핑 및 4종 산출물(통합 PDF·PNG·카톡·방송실 큐시트)** 을 생성하는 100% 로컬 실행 스킬입니다.

> **규칙의 문언(letter)을 어기는 것은 곧 규칙의 정신(spirit)을 어기는 것입니다.** 시간 압박이나 일괄 실행 요청이 있어도 아래 안전·검수 게이트는 생략하지 않습니다.

## 실행 환경 & 핵심 원칙

- **스킬 디렉터리(`$SKILL_DIR`) & 명시적 인터프리터 실행**: `SKILL.md`가 위치한 디렉터리를 `$SKILL_DIR`로 잡고(워크스페이스 설치 시 `.agents/skills/korean-worship-setlist`, 전역 스킬/플러그인 설치 시 `~/.gemini/config/plugins/.../skills/korean-worship-setlist` 또는 `~/.agents/skills/korean-worship-setlist`), 항상 **현재 워크스페이스 루트(`cwd`)** 에서 인터프리터와 함께 실행합니다(bare 경로 금지, `--help`로 상세 플래그 확인).
  ```powershell
  # $SKILL_DIR 예시 (설치 위치에 맞게 지정):
  $SKILL_DIR = ".agents/skills/korean-worship-setlist"

  # 기본 실행:
  uv run --project "$SKILL_DIR" python "$SKILL_DIR/scripts/init_profile.py" --inspect

  # Windows App Control이 .venv\Scripts\python.exe를 차단(os error 4551)할 때 검증된 PowerShell 실행:
  $py = uv python find ">=3.11,<3.13"
  $env:PYTHONPATH = "$SKILL_DIR/scripts;$SKILL_DIR/.venv/Lib/site-packages"
  & $py "$SKILL_DIR/scripts/<name>.py" --help
  ```
- **DB 2원화 & 전역 설치 상태 격리**: 공용 메타(`$SKILL_DIR/data/worship.db`: 고유 3,538곡 / 팀별 편곡 5,463개)는 읽기 전용으로 공유하고, 교회 전용 상태(`user_config.json`, `church_local.db`, `_raster_cache`)는 워크스페이스 로컬 설치 시 `$SKILL_DIR/data/`에, 전역 스킬·플러그인(`~/.gemini/config/`, `~/.agents/`, `~/.claude/`, `node_modules/`) 설치 시 현재 워크스페이스 루트의 `<워크스페이스>/.worship-setlist/`에 자동 격리 저장됩니다.
- **저작권·신학 안전**: ① 15자(`first_line_incipit`/`chorus_incipit`)를 초과하는 **가사 원문은 DB·JSON·Markdown·TXT·응답 어디에도 저장·출력하지 않습니다.** ② `data/blocked_sources.json`(이단·논란 단체) 통과 필수, 유료/로그인/워터마크 악보 무단 수집 금지.
- **카탈로그 구축·오디오·수집 보조 스크립트**: DB 재초기화·유튜브 챕터 수집·오디오 BPM/Key 분석·E5 임베딩은 `init_db.py`, `build_bulk_catalog.py`, `collect_youtube.py`, `analyze_audio.py`, `enrich_metadata.py` 각 `--help`를 참조하세요.

## Commands & Routing

| Command | Category | Purpose | Guide |
|---|---|---|---|
| `init` (별칭: `teach`) | Setup | 교회 규모·회중 연령대·세션 악기·보컬 음역·로컬 악보 경로 인터뷰 후 `user_config.json` 저장 | [init.md](./references/init.md) |
| `plan` | Build | 예배 컨텍스트 → 선곡 → 전조 설계 → 초안 컨펌 → 비전 검수 → 4종 산출물 생성 | 아래 6단계 런북 |
| `update <슬롯>` | Refine | 리허설 중 곡/Key/송폼 수정 후 `stamp_sheets.py --update-slot`으로 변경분 Diff·직전 전환 포함 재생성 | 6단계 + `--update-slot` |
| `history` | Track | `manage_history.py` (`record` \| `list` \| `set-repertoire` \| `bootstrap`)로 이력·쿨타임·곡별 `pinned_key` 관리 | `--help` 참조 |

- **라우팅 규칙**:
  - 사용자가 `init` / `초기 설정` / `찬양팀 세팅`을 요청하면 [init.md](./references/init.md)를 먼저 읽고 `init_profile.py --inspect`로 현재 상태를 확인한 뒤 진행합니다.
  - `plan` 요청 시 `user_config.json`(`init_profile.py --inspect`로 로컬/전역 경로 확인)이 없고 사용자가 "기본값으로 바로 진행"을 명시했으면 기본 설정으로 실행하고, 명시하지 않았으면 진행 전 1회 질문으로 `init` 설정 여부를 확인합니다.
  - 다부제·로테이션 사역팀에서 회차별 예외가 있을 때는 `user_config.json`을 덮어쓰지 말고 `--service-type`, `--scope`, `--session`, `manage_history.py set-repertoire --preferred-key` 등 CLI 오버라이드를 우선 적용합니다.

## Reference Guides (필요 시 로드)

- 초기 팀/세션 프로파일 (`init`): [init.md](./references/init.md)
- 예배 유형·교회력 절기·단일 슬롯(`--slot`): [worship_presets.md](./references/worship_presets.md)
- 전조 공식·박자/BPM 전환·카포 가이드: [key_transition_guide.md](./references/key_transition_guide.md)
- 송폼 규격·PPT 한글 변환·비전 검수 체크리스트: [song_form_conventions.md](./references/song_form_conventions.md)

---

## 6단계 실행 런북

### 1~2단계: 컨텍스트 접수 & 후보 선곡 (`search_candidates.py`)
`references/worship_presets.md` 기준으로 조건을 확인하고 후보를 추출합니다(미지정 값은 `user_config.json` 자동 반영, 상세 옵션은 `--help`).
```powershell
& $py "$SKILL_DIR/scripts/search_candidates.py" `
  --service-type youth --season ordinary --theme "광야를 통과하는 은혜" --scripture "PSA.23" `
  --output candidates.json
```
- `pinned_ritual` 쿨타임 면제, `learning` 신곡 2~3주 정착, `cooldown_scope`별 쿨타임, `vocal_ceiling` 기반 `-3~+2` 자동 이조, `congregation_age_group` 및 `church_scale` 친숙도 가중치가 적용됩니다.
- 과거 악보 Key를 자동 잠그지 않으며(`lock_past_keys: false` 기본값), 멀티트랙/브라스 등으로 특정 곡의 Key만 고정하려면 `manage_history.py set-repertoire --slug <slug> --preferred-key <Key>`를 사용하세요.

### 3단계: 서사 고정 하 전조·박자 최적화 (`plan_transitions.py`)
```powershell
& $py "$SKILL_DIR/scripts/plan_transitions.py" `
  --input candidates.json --output setlist_plan.json
```
- `user_config.json`의 `session_skill_tier`, `session_instruments`, `vocal_ceiling`을 자동 읽어 보컬 음역 상한을 넘지 않는 선에서 `V7sus4 (IV/V)`·`IVm6`·`V/IV` 브릿지 코드와 `segue`/`pad_break` 큐를 설계합니다.

### 4단계: 콘티 초안 사용자 컨펌 (<HARD-GATE>)
**사용자 확정 전 5~6단계(PDF 스탬핑·`manage_history.py record`)로 진행하지 마세요.** 아래 3개 블록 형식으로 초안을 제시하고 응답을 기다립니다:
1. **Block A (`pre_sermon`) 요약표**: `순번 | 곡명 | 실연 Key (원키 대비 이조) | BPM·박자 | 추천 송폼 | 전조 브릿지·세션 큐`
2. **Block B (`post_sermon`)**: 1순위 `Reprise` 곡·구간 또는 결단송 + 기도회 코드 루프
3. **슬롯별 Plan B**: 각 슬롯 대체 후보 1~2곡

### 5단계: 악보 매칭(`find_sheets.py`) · 수집(`fetch_sheets.py`) · 비전 검수 (<HARD-GATE>)
```powershell
& $py "$SKILL_DIR/scripts/find_sheets.py" `
  --setlist setlist_plan.json --output matched_sheets.json
```
- 누락 곡(`missing_local`)은 사용자가 원할 때만 공개 악보 URL을 찾아 `fetch_sheets.py --url <URL> --title <곡명> --key <Key>`로 저장한 뒤, **`find_sheets.py`를 다시 실행**해 PNG 목록(`rasterized_pages`)에 반영합니다.
- **필수 비전 검수 (`read_file`)**: `matched_sheets.json`에 매칭된 **모든 `rasterized_pages` PNG 전 페이지**를 `read_file`로 직접 열어 [song_form_conventions.md](./references/song_form_conventions.md) 3대 항목(실제 Key · 구간/도돌이 · 일회성 가사-설교/이단 확인)을 검수합니다.
- 검수 결과를 JSON 배열(`vision_verifications.json`)로 저장해 `find_sheets.py`에 반영합니다:
  ```json
  [
    {
      "slot_index": 1,
      "verified_key": "G",
      "verified_time_signature": "4/4",
      "verified_bpm": 124,
      "verified_groove": "shuffle_bounce",
      "verified_sections": ["I", "V1", "PC", "C"],
      "confirmed_song_form": "I(4m) - V1 - PC - C(x2)",
      "key_mismatch_action": "ok",
      "lyrics_theology_ok": true,
      "meter_note": "",
      "chorus_incipit": "후렴첫구절",
      "score_annotations": [{"page": 1, "rx": 0.41, "ry": 0.59, "text": "메모", "style": "cue"}]
    }
  ]
  ```
  ```powershell
  & $py "$SKILL_DIR/scripts/find_sheets.py" `
    --setlist setlist_plan.json --vision-file vision_verifications.json --output matched_sheets.json
  ```
- **Key · 리듬 불일치 2분기 & 자가 학습**: `[A]` 악보 실제 Key를 따르려면 `key_mismatch_action`을 `"recalculate"`로 지정합니다(`plan_transitions.py` 재실행 금지 — `find_sheets.py`가 `setlist_plan.json`의 Key·이조폭·세션 전조를 자동 재계산). 악보 상단에 `Shuffle`/`Bounce`/`♫=♪³`/`16beat`/`Go-Go` 등 리듬이나 BPM·박자 표기가 보이면 `verified_groove`, `verified_bpm`, `verified_time_signature`를 함께 기록하세요(`find_sheets.py`가 `rhythm_reset` 등 전환을 즉시 재계산하고 `church_local.db`에 저장해 이후 콘티에 자가 학습 반영). `[B]` 기존 목표 Key를 유지하려면 `"stamp_transpose_warning"`을 지정합니다. `lyrics_theology_ok: false`인 악보는 PDF 생성이 자동 차단됩니다.

### 6단계: 배너 스탬핑(`stamp_sheets.py`) & 확정 이력 저장(`manage_history.py`)
```powershell
& $py "$SKILL_DIR/scripts/stamp_sheets.py" `
  --setlist setlist_plan.json --sheets matched_sheets.json --candidates candidates.json `
  --output-dir "output/2026-11-29_청년부"
# 최종 확정된 콘티만 쿨타임 DB에 기록:
& $py "$SKILL_DIR/scripts/manage_history.py" record `
  --setlist setlist_plan.json --output-dir "output/2026-11-29_청년부"
```
- **산출물 4종**: ① 1p 총괄 커버·북마크 통합 PDF, ② 개별 PNG, ③ `kakao_notice.txt` + `leader_cuesheet.md`, ④ 방송실 `broadcast_cue.txt`.
- **리허설 부분 수정 (`update <슬롯>`)**: `setlist_plan.json` 수정 및 해당 슬롯 악보 `find_sheets.py --vision-file` 재검수 후 `--update-slot <번호>`로 실행하면 대상 슬롯과 직전 슬롯(`k-1`) 전환 배너·`kakao_diff_notice.txt`·`broadcast_cue.txt`까지 일괄 갱신됩니다.

---

## Red Flags — 즉시 멈추고 절차로 복귀할 신호

- `rasterized_pages` PNG 전 페이지를 `read_file`로 직접 보지 않고 `stamp_sheets.py`를 돌리려 할 때
- "파일명에 `_G.png`라고 적혀 있으니 실제 악보도 G키겠지"라고 추정할 때
- 4단계 사용자 초안 컨펌 전에 5~6단계(PDF 병합·`manage_history.py record`)를 한 번에 끝내려 할 때
- `fetch_sheets.py` 대신 `Invoke-WebRequest`/`curl`로 블로그 이미지를 받거나 유료/워터마크 악보를 넣으려 할 때
- 악보에서 읽은 15자 초과 가사 원문을 DB·JSON·큐시트·채팅 응답에 적으려 할 때

## 합리화 차단표 (Rationalization Table)

| 흔한 변명 (Excuse) | 실제 현실 (Reality) |
|---|---|
| "파일명이 `곡명_G.png`이고 `matched_exact_key`라 비전 검수는 빼도 된다" | 로컬 악보는 파일명과 실제 조표·도돌이·마디가 다른 경우가 흔합니다. 반드시 `read_file`로 직접 확인하세요. |
| "이미 `sheet_cache`에 있는 악보니 이번엔 `read_file`을 생략하자" | 캐시에 섹션이 있어도 **이번 주 설교 주제와의 가사/신학 정합성**은 매 예배 눈으로 확인해야 합니다. |
| "바쁘니까 4단계 컨펌 없이 PDF 생성과 `history record`까지 한 번에 하자" | 미확정 초안을 기록하면 탈락 곡이 6주 쿨타임에 묶입니다. 4단계에서 반드시 멈추세요. |
| "DB에만 안 넣으면 되니 큐시트나 JSON에 가사를 적어두자" | 저작권 보호 규칙은 DB·파일(`.json`, `.md`, `.txt`)·채팅 응답 모두에 동일하게 적용됩니다. |
| "Key 재계산을 위해 `plan_transitions.py`를 다시 돌리자" | `plan_transitions.py`는 `candidates.json` 스키마 전용이라 `setlist_plan.json`을 덮어씁니다. `find_sheets.py --vision-file`의 `"recalculate"`를 쓰세요. |

## Common Mistakes

- `stamp_sheets.py`에서 `--candidates candidates.json`을 빼먹어 `leader_cuesheet.md`에서 Plan B가 누락됨
- `stamp_sheets.py`를 `--show-capo` / `--no-show-capo` 없이 실행할 때 `user_config.json`의 `show_capo`가 기본 적용됨을 놓침
- 단일 순서 검색 시 `--service-slots` 대신 `--slot` 플래그를 사용해야 함
- 악보 파일을 디스크에서 이름 변경한 뒤 `find_sheets.py`를 재실행하지 않아 빈 악보 박스가 출력됨
