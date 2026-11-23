# 한국 찬양 콘티 에이전트 Skill (`korean-worship-setlist`)

한국 교회의 예배 유형과 교회력 절기, 설교 주제·본문, 찬양팀 음역대와 세션 편성에 맞춰 **찬양 콘티 기획 → Key·박자·카포·전조 최적화 → 로컬 악보 매칭 및 비전 검수 → 상단 송폼(`I - V - PC - C...`) 스탬핑 및 4종 산출물 생성**을 자동화하는 100% 로컬 구동 Antigravity Skill입니다.

자세한 아키텍처와 스키마 설계는 [DESIGN.md](./DESIGN.md)를 참고하세요.

---

## 주요 특징

- **Zero API Key · 100% 로컬 실행**: 외부 유료 API 없이 `uv` + Python(3.11/3.12) 로컬 환경에서 동작하며, 임베딩 모델(`intfloat/multilingual-e5-small`) 미설치/오프라인 환경에서도 SQLite `trigram` FTS5 + `LIKE` 폴백으로 중단 없이 검색됩니다.
- **저작권 안전 & DB 2원화**: 공용 DB([worship.db](./.agents/skills/korean-worship-setlist/data/worship.db))에는 가사 원문이나 악보 이미지를 저장하지 않고 구조화된 메타데이터·신학적 핵심·태그·표준 성경 코드·15자 이내 식별용 첫 구절만 저장하며, 교회별 레퍼토리·쿨타임 이력·악보 캐시는 `.gitignore` 대상인 `church_local.db`로 분리합니다.
- **전 세대·교회력 절기·이단 차단 게이트**: 모던 워십팀(1,443곡)·80~2000년대 복음성가(1,450곡)·새찬송가 전곡(1~645장, 통일찬송가 매핑)을 아우르는 **총 3,538곡 / 5,470편곡 규모의 공용 DB**를 내장하며, 사순절·고난주간·부활절·성찬 등 절기 필터와 [blocked_sources.json](./.agents/skills/korean-worship-setlist/data/blocked_sources.json) 기반 차단 게이트를 적용합니다.
- **실전 콘티 & 합주 지능**: 예배 서사·템포 고정 제약 하 자동 이조(`-3~+2` 반음), `V7sus4 (IV/V)` / `IVm6` / `V/IV` 전조 브릿지 코드, `4/4↔6/8`·BPM 낙차 시 `pad_break`/`segue` 큐, 기타 카포 매핑, 고정 예식곡(`pinned_ritual`) 면제 및 신곡 정착(`learning` 2~3주 연속 허용 후 승격)을 지원합니다.
- **비파괴 상단 송폼 스탬퍼 & 4종 산출물**: EXIF·투명 배경을 보정한 A4 안전 마진 악보 위에 1페이지 2행 풀배너 / 2페이지+ 1줄 컴팩트 헤더 / 이조 경고 배지를 합성하여 ① 1페이지 총괄 커버·곡별 북마크 포함 통합 PDF, ② 개별 PNG, ③ 카톡 공지(이조 경고·`?t=` 딥링크) 및 인도자 큐시트(`.md`), ④ 방송실 자막/PPT 큐시트(`broadcast_cue.txt`)를 출력합니다.

---

## 디렉터리 구조

```text
.agents/skills/korean-worship-setlist/
├── pyproject.toml                  # runtime / builder / embeddings / dev 의존성 분리
├── SKILL.md                        # 에이전트용 6단계 실행 런북
├── scripts/
│   ├── common.py                   # DB 연결(ATTACH), 조성/음역 수학, 성경 코드 정규화, 이단 게이트
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
├── resources/fonts/                # 내장 OFL 한글 고딕 폰트 (NotoSansKR-VF.ttf)
├── data/                           # seed_songs.json, worship.db, blocked_sources.json, user_config.example.json
└── tests/                          # Phase 1~5 단위 및 End-to-End 테스트 스위트
```

---

## 빠른 시작

### 0. npm (`npx`)으로 스킬 설치
```powershell
# 현재 워크스페이스에 설치 (.agents/skills/korean-worship-setlist)
npx korean-worship-setlist install

# 전역 스킬로 설치 (~/.gemini/config/skills/korean-worship-setlist)
npx korean-worship-setlist install --global

# Antigravity 전역 플러그인으로 설치 (~/.gemini/config/plugins/korean-worship-setlist)
npx korean-worship-setlist install --plugin

# Claude Code 워크스페이스 스킬로 설치 (.claude/skills/korean-worship-setlist)
npx korean-worship-setlist install --claude
```

### 1. 환경 설치 (`uv`)
```powershell
$SKILL_DIR = ".agents/skills/korean-worship-setlist"  # 전역 설치 시 해당 스킬/플러그인 경로 지정
uv sync --project "$SKILL_DIR" --extra dev
```
*(데이터 수집·오디오 분석 기능을 쓸 경우 `--extra builder`, 로컬 임베딩 모델을 쓸 경우 `--extra embeddings`를 추가합니다.)*

### 2. DB 초기화 및 시드 적재
```powershell
uv run --project "$SKILL_DIR" python "$SKILL_DIR/scripts/init_db.py" --seed
```
- **전역 스킬·플러그인 설치 시 상태 격리**: `~/.gemini/config/`, `~/.agents/`, `~/.claude/`, `node_modules/` 아래에 전역 설치된 상태에서 워크스페이스 루트(`cwd`)에서 실행하면, 공용 카탈로그(`$SKILL_DIR/data/worship.db`)는 읽기 전용으로 공유하고 교회별 설정·이력(`user_config.json`, `church_local.db`, `_raster_cache`)은 현재 워크스페이스 `<workspace>/.worship-setlist/`에 자동 분리 저장됩니다.
- *(Windows 애플리케이션 제어 정책이 `.venv\Scripts\python.exe`를 차단하는 환경에서는 PowerShell에서 `$env:PYTHONPATH = "$SKILL_DIR/scripts;$SKILL_DIR/.venv/Lib/site-packages"` 설정 후 `& (uv python find ">=3.11,<3.13") "$SKILL_DIR/scripts/init_db.py" --seed`로 실행하세요.)*

### 3. 전체 테스트 실행
```powershell
$env:PYTHONPATH = "$SKILL_DIR/scripts;$SKILL_DIR/.venv/Lib/site-packages"
& (uv python find ">=3.11,<3.13") -m pytest "$SKILL_DIR/tests" -v
```

---

## 라이선스 (License)

- **코드·스크립트·문서**: [MIT License](./LICENSE) (스킬 패키지 단독 배포용 전문은 [.agents/skills/korean-worship-setlist/LICENSE](./.agents/skills/korean-worship-setlist/LICENSE) 참조)
- **내장 한글 폰트 (`NotoSansKR-VF.ttf`)**: [SIL Open Font License 1.1 (OFL-1.1)](./.agents/skills/korean-worship-setlist/resources/fonts/OFL.txt) — Copyright 2014-2023 The Noto Project Authors (Reserved Font Name: `"Noto"`)
- **공용 곡 메타데이터 & 사용자 악보**: `worship.db` 및 `seed_songs.json`에는 곡 제목·작곡가·BPM·박자·원키·성경 코드 등 사실적 메타데이터만 포함되며 가사 전문이나 악보 이미지는 포함되지 않습니다. 원곡·가사 및 로컬 `sheet/` 내 악보 파일의 저작권은 각 권리자(KOMCA, CCLI, 한국찬송가공회 등)에게 있습니다.
