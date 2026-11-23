# 한국 찬양 콘티 에이전트 Skill (`korean-worship-setlist`)

[![npm version](https://img.shields.io/npm/v/korean-worship-setlist.svg)](https://www.npmjs.com/package/korean-worship-setlist)
[![License: MIT AND OFL-1.1](https://img.shields.io/badge/license-MIT%20AND%20OFL--1.1-blue.svg)](./LICENSE)
[![Python 3.11 | 3.12](https://img.shields.io/badge/python-3.11%20%7C%203.12-3776AB.svg)](./.agents/skills/korean-worship-setlist/pyproject.toml)

한국 교회의 예배 유형과 교회력 절기, 설교 주제·본문, 찬양팀 음역대와 세션 편성에 맞춰 **찬양 콘티 기획 → Key·박자·카포·전조 최적화 → 로컬 악보 매칭 및 비전 검수 → 상단 송폼(`I - V - PC - C...`) 스탬핑 및 4종 산출물 생성**을 자동화하는 100% 로컬 구동 AI 에이전트 Skill입니다.

- **npm 패키지**: [https://www.npmjs.com/package/korean-worship-setlist](https://www.npmjs.com/package/korean-worship-setlist) (`korean-worship-setlist`)
- **아키텍처 설계 문서**: [DESIGN.md](./DESIGN.md)

---

## 주요 특징

- **Zero API Key · 100% 로컬 실행**: 외부 유료 API 없이 `uv` + Python(3.11/3.12) 로컬 환경에서 동작하며, 임베딩 모델(`intfloat/multilingual-e5-small`) 미설치/오프라인 환경에서도 SQLite `trigram` FTS5 + `LIKE` 폴백으로 중단 없이 검색됩니다.
- **저작권 안전 & DB 2원화**: 공용 DB([worship.db](./.agents/skills/korean-worship-setlist/data/worship.db))에는 가사 원문이나 악보 이미지를 저장하지 않고 구조화된 메타데이터·신학적 핵심·태그·표준 성경 코드·15자 이내 식별용 첫 구절만 저장하며, 교회별 레퍼토리·쿨타임 이력·악보 캐시는 `.gitignore` 대상인 `church_local.db`로 분리합니다.
- **전 세대·교회력 절기·이단 차단 게이트**: 모던 워십팀·80~2000년대 복음성가·새찬송가 전곡(1~645장, 통일찬송가 매핑)을 아우르는 **고유 곡 3,498곡 / 워십팀별 편곡 4,881개 규모의 공용 DB**를 내장하며, 사순절·고난주간·부활절·성찬 등 절기 필터와 [blocked_sources.json](./.agents/skills/korean-worship-setlist/data/blocked_sources.json) 기반 차단 게이트를 적용합니다.
- **실전 콘티 & 합주 지능**: 예배 서사·템포 고정 제약 하 자동 이조(`-3~+2` 반음), `V7sus4 (IV/V)` / `IVm6` / `V/IV` 전조 브릿지 코드, `4/4↔6/8`·BPM 낙차 시 `pad_break`/`segue` 큐, 기타 카포 매핑, 고정 예식곡(`pinned_ritual`) 면제 및 신곡 정착(`learning` 2~3주 연속 허용 후 승격)을 지원합니다.
- **비파괴 상단 송폼 스탬퍼 & 4종 산출물**: EXIF·투명 배경을 보정한 A4 안전 마진 악보 위에 1페이지 2행 풀배너 / 2페이지+ 1줄 컴팩트 헤더 / 이조 경고 배지를 합성하여 ① 1페이지 총괄 커버·곡별 북마크 포함 통합 PDF, ② 개별 PNG, ③ 카톡 공지(이조 경고·`?t=` 딥링크) 및 인도자 큐시트(`.md`), ④ 방송실 자막/PPT 큐시트(`broadcast_cue.txt`)를 출력합니다.

---

## 디렉터리 구조

```text
.
├── package.json                        # npm 패키지 명세 (`korean-worship-setlist`)
├── plugin.json                         # Antigravity 플러그인 매니페스트 (`--plugin` 설치용)
├── bin/
│   └── korean-worship-setlist.mjs      # 무의존 Node ESM CLI 인스톨러 (`npx korean-worship-setlist`)
├── LICENSE                             # 루트 MIT 라이선스 및 서드파티·카탈로그 저작권 고지
├── DESIGN.md                           # 전체 아키텍처·DB 스키마 설계 문서
└── .agents/skills/korean-worship-setlist/
    ├── LICENSE                         # 스킬 단독 배포용 라이선스 (MIT + SIL OFL-1.1 + 저작권 고지)
    ├── pyproject.toml                  # runtime / builder / embeddings / dev 의존성 분리
    ├── uv.lock                         # Python 의존성 잠금 파일
    ├── SKILL.md                        # 에이전트용 6단계 실행 런북
    ├── scripts/
    │   ├── common.py                   # DB 연결(ATTACH), 로컬/전역 상태 경로 격리, 조성/음역 수학, 이단 게이트
    │   ├── init_db.py                  # worship.db & church_local.db 초기화 및 시드 적재
    │   ├── init_profile.py             # `/korean-worship-setlist init` 팀·세션·음역 온보딩
    │   ├── build_bulk_catalog.py       # 4대 공개 소스 통합(시드+새찬송가645+KCCM+워십디스코그래피) 대량 DB 구축
    │   ├── collect_youtube.py          # 워십팀 유튜브 flat 차분 수집 및 챕터 분할 파서
    │   ├── analyze_audio.py            # 3구간 오디오 BPM(6/8·하프타임 보정) 및 Key 추정
    │   ├── enrich_metadata.py          # 메타 정규화, trigram FTS5 동기화, E5 passage 임베딩
    │   ├── manage_history.py           # bootstrap 초기 시딩, 1~3부 중복 방지 이력/레퍼토리 관리
    │   ├── search_candidates.py        # 하이브리드 선곡, 자동 이조, 절기/쿨타임/정착/친숙도 밸런스
    │   ├── plan_transitions.py         # Key·박자·카포·전조 브릿지 코드 최적화
    │   ├── find_sheets.py              # 로컬 악보 매칭(원본 Key 폴백), pypdfium2 래스터화, 비전 검수 기록
    │   ├── fetch_sheets.py             # 공개 악보 URL 안전 수집(SSRF·차단·해상도 검증)
    │   └── stamp_sheets.py             # A4 배너 스탬핑, 1p 커버·북마크 통합 PDF, 4종 산출물, 부분 Diff 갱신
    ├── references/                     # init 온보딩, 예배 프리셋, 전조 공식, 송폼/비전 검수 가이드
    ├── resources/fonts/                # 내장 OFL 한글 고딕 폰트 (`NotoSansKR-VF.ttf`, `OFL.txt`)
    ├── data/                           # seed_songs.json, worship.db, blocked_sources.json, user_config.example.json
    └── tests/                          # Phase 1~5 단위 및 End-to-End 테스트 스위트
```

---

## 설치 및 빠른 시작

### 1. npm (`npx`)으로 설치 (권장)

[korean-worship-setlist](https://www.npmjs.com/package/korean-worship-setlist) npm 패키지의 zero-dependency CLI로 사용 중인 AI 에이전트 환경에 맞게 바로 설치할 수 있습니다. *(요구 사항: Node.js 18+, Python 패키지 관리용 `uv`)*

```powershell
# 현재 워크스페이스에 스킬 설치 (.agents/skills/korean-worship-setlist)
npx korean-worship-setlist install

# 스킬 설치 + Python 의존성(`uv sync`) 한 번에 완료
npx korean-worship-setlist install --sync

# Antigravity 전역 스킬로 설치 (~/.gemini/config/skills/korean-worship-setlist)
npx korean-worship-setlist install --global

# Antigravity 전역 플러그인으로 설치 (~/.gemini/config/plugins/korean-worship-setlist)
npx korean-worship-setlist install --plugin

# Claude Code 워크스페이스 / 전역 스킬로 설치 (.claude/skills/... 또는 ~/.claude/skills/...)
npx korean-worship-setlist install --claude
npx korean-worship-setlist install --claude --global

# 공용 ~/.agents/skills/ 전역 설치 및 사용자 지정 경로(`--dest`) 설치
npx korean-worship-setlist install --agents --global
npx korean-worship-setlist install --dest ./custom-path

# 최신 버전으로 업데이트 (교회별 user_config.json · church_local.db 유지)
npx korean-worship-setlist@latest update          # 워크스페이스 스킬
npx korean-worship-setlist@latest update --global # 전역 스킬 (--plugin / --claude 동일 지원)
npx korean-worship-setlist check                  # 설치 버전과 npm 최신 버전 비교 확인
```

- **업그레이드 안전성 · 자동 업데이트 알림**: 기존 설치 경로에 다시 `npx korean-worship-setlist install` 또는 `update`를 실행해도 교회별 `user_config.json` 및 `church_local.db`는 덮어쓰지 않고 보존합니다. 또한 `init_profile.py --inspect`·`search_candidates.py` 실행 시 24시간 주기로 npm 최신 버전을 비차단(오프라인 시 조용히 생략) 확인해 새 버전이 있으면 `UPDATE_AVAILABLE`로 알려줍니다(`WORSHIP_NO_UPDATE_CHECK=1`로 비활성화 가능).
- **전역 스킬·플러그인 상태 격리**: `--global`·`--plugin`·`--agents --global` 등으로 전역 설치한 뒤 워크스페이스 루트(`cwd`)에서 실행하면, 공용 카탈로그(`$SKILL_DIR/data/worship.db`)는 읽기 전용으로 공유하고 교회별 상태(`user_config.json`, `church_local.db`, `_raster_cache`)는 현재 워크스페이스의 `<workspace>/.worship-setlist/`에 자동 분리 저장됩니다.

### 2. Python 런타임 환경 설치 (`uv`)

`--sync` 옵션을 쓰지 않고 설치했다면 설치된 스킬 경로(`$SKILL_DIR`)에 대해 아래 명령을 1회 실행합니다.

```powershell
$SKILL_DIR = ".agents/skills/korean-worship-setlist"  # 전역·Claude 설치 시 해당 스킬 경로 지정
uv sync --project "$SKILL_DIR"
```

- **선택 의존성**: 테스트 실행 시 `--extra dev`, 데이터 수집·오디오 분석 시 `--extra builder`, 로컬 E5 임베딩 모델 사용 시 `--extra embeddings`를 추가합니다.
- **Windows App Control (`os error 4551`) 대응**: 보안 정책이 `.venv\Scripts\python.exe` 직접 실행을 차단하는 환경에서는 PowerShell에서 `$env:PYTHONPATH = "$SKILL_DIR/scripts;$SKILL_DIR/.venv/Lib/site-packages"`를 지정한 뒤 `& (uv python find ">=3.11,<3.13") "$SKILL_DIR/scripts/<script>.py"`로 실행할 수 있습니다.

### 3. 에이전트에서 바로 사용하기

npm 패키지 및 GitHub 저장소에 **공용 곡 DB(`data/worship.db`: 고유 3,498곡 / 팀별 편곡 4,881개)가 이미 포함되어 있으므로** 사용자가 별도로 DB를 재구축할 필요 없이 바로 콘티 작업을 시작할 수 있습니다.

| 명령 / 요청 예시 | 동작 요약 |
|---|---|
| `/korean-worship-setlist init` (또는 *"찬양팀 초기 설정해줘"*) | 교회 규모·회중 연령대·세션 악기·보컬 음역대·로컬 악보 폴더(`sheet/`) 인터뷰 후 `user_config.json` 저장 |
| *"이번 주 청년부 예배 시편 23편 주제로 찬양 콘티 짜줘"* (`plan`) | 후보 선곡 → Key·박자·전조 브릿지 설계 → 초안 컨펌 → 악보 비전 검수 → 통합 PDF·PNG·카톡·방송실 큐시트 생성 |
| *"콘티 2번 곡 Key를 A로 바꾸고 수정해줘"* (`update <슬롯>`) | 해당 슬롯과 직전 곡(`k-1`) 전환 배너·`kakao_diff_notice.txt`·`broadcast_cue.txt` 부분 갱신 |
| *"지난 주 확정 콘티 이력 조회/등록해줘"* (`history`) | `manage_history.py`로 1~3부 예배 이력, 6주 쿨타임, 신곡 정착 상태(`learning`), 곡별 `pinned_key` 관리 |

### 4. 개발자용: DB 재초기화 및 테스트 실행

기여자·개발자가 시드 데이터에서 DB를 재생성하거나 테스트 스위트를 검증할 때 사용합니다.

```powershell
# 시드 기반 DB 초기화
uv run --project "$SKILL_DIR" python "$SKILL_DIR/scripts/init_db.py" --seed

# 개발 의존성 포함 동기화 및 전체 pytest 실행
uv sync --project "$SKILL_DIR" --extra dev
$env:PYTHONPATH = "$SKILL_DIR/scripts;$SKILL_DIR/.venv/Lib/site-packages"
& (uv python find ">=3.11,<3.13") -m pytest "$SKILL_DIR/tests" -v
```

---

## 라이선스 (License)

- **코드·스크립트·문서**: [MIT License](./LICENSE) (스킬 패키지 단독 배포용 전문은 [.agents/skills/korean-worship-setlist/LICENSE](./.agents/skills/korean-worship-setlist/LICENSE) 참조)
- **내장 한글 폰트 (`NotoSansKR-VF.ttf`)**: [SIL Open Font License 1.1 (OFL-1.1)](./.agents/skills/korean-worship-setlist/resources/fonts/OFL.txt) — Copyright 2014-2023 The Noto Project Authors (Reserved Font Name: `"Noto"`)
- **공용 곡 메타데이터 & 사용자 악보**: `worship.db` 및 `seed_songs.json`에는 곡 제목·작곡가·BPM·박자·원키·성경 코드 등 사실적 메타데이터만 포함되며 가사 전문이나 악보 이미지는 포함되지 않습니다. 원곡·가사 및 로컬 `sheet/` 내 악보 파일의 저작권은 각 권리자(KOMCA, CCLI, 한국찬송가공회 등)에게 있습니다.
