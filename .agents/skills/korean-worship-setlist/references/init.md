# Init Flow (`/korean-worship-setlist init`)

`init` captures durable church worship team context in `user_config.json` (gitignored per-church profile) and ensures `church_local.db` is ready without touching shared metadata in `$SKILL_DIR/data/worship.db`. Workspace-local installs keep state in `$SKILL_DIR/data/`; global skill/plugin installs (`~/.gemini/config/`, `~/.agents/`, `~/.claude/`, `node_modules/`) automatically isolate per-workspace state under `<workspace>/.worship-setlist/`.

## Step 1: Inspect Current State

Before asking questions, run `init_profile.py --inspect` from the workspace root (`uv run` or PowerShell fallback in [SKILL.md](../SKILL.md)):

```powershell
# $SKILL_DIR 예시 (전역 플러그인/스킬 설치 시 해당 설치 경로 지정):
$SKILL_DIR = ".agents/skills/korean-worship-setlist"

# 기본 실행:
uv run --project "$SKILL_DIR" python "$SKILL_DIR/scripts/init_profile.py" --inspect

# Windows App Control(os error 4551) 차단 시 PowerShell 실행:
$py = uv python find ">=3.11,<3.13"
$env:PYTHONPATH = "$SKILL_DIR/scripts;$SKILL_DIR/.venv/Lib/site-packages"
& $py "$SKILL_DIR/scripts/init_profile.py" --inspect
```

- **No `user_config.json` yet**: run the onboarding interview (Step 2) and write the profile.
- **`user_config.json` already exists**: show the current summary (`church_scale`, `congregation_age_group`, `vocal_ensemble`, `session_instruments`, `session_skill_tier`, `leader_gender` / `vocal_floor`~`vocal_ceiling`, `show_capo`, `lock_past_keys`, `local_sheet_dirs`) and ask only which settings to change; preserve untouched fields.
- Never silently overwrite an existing `user_config.json` without user confirmation.

## Step 2: Structured Church & Band Onboarding Interview

Use `ask_question` when available (or direct chat questions otherwise) in **at most 1–2 concise rounds** covering only missing or changed fields:

1. **교회 규모 (`church_scale`) & 보컬 편성 (`vocal_ensemble`)**
   - `small` — 소형/개척 교회 (기본 `familiar_ratio: 0.85`, `vocal_ensemble: solo_leader`, 로컬 무전조 악보 우선)
   - `medium` — 중형 교회 (기본 `familiar_ratio: 0.75`, `vocal_ensemble: small_team`, 봉사자 로테이션)
   - `large` — 대형 교회 (기본 `familiar_ratio: 0.70`, `vocal_ensemble: full_choir`, 다부제·프로세션)
2. **회중 기본 연령대 (`congregation_age_group`)**
   - `youth_young_adult` — 청소년·청년 중심 (`2010s~2020s` 모던워십 가중)
   - `intergenerational` — 전 세대 통합 (어른+청년·청소년 공용 찬양 가중)
   - `senior_adult` — 장년·어르신 중심 (새찬송가·복음성가 가중)
3. **보유 악기 세션 (`session_instruments`) & 숙련도 (`session_skill_tier`)**
   - 악기 선택: `piano`, `synth_pad`, `acoustic_guitar`, `electric_guitar`, `bass`, `drums`
   - 보유 악기에 따라 `default_session` (`full_band` / `acoustic` / `piano_only`)과 전환 안내(`synth_pad` 없으면 `피아노 잔잔한 코드 유지`, `drums`·기타 없으면 `피아노 리드`)가 자동 맞춤 적용됩니다.
   - 숙련도: `beginner_friendly` (어려운 플랫키 회피·기타 카포 이동 보호), `intermediate`, `advanced`
4. **인도자 보컬 · 카포 · 악보 폴더 (선택 확인)**
   - 인도자 성별(`male`/`female`) 및 음역(`vocal_floor`~`vocal_ceiling`: 기본 남성 `A2`~`F#4`, 여성 `G3`~`D5`)
   - 기타 카포 배너 표기 여부(`--show-capo` / `--no-show-capo`, 기본 `false`)
   - 과거 악보 Key 자동 고정 여부(`--lock-past-keys` / `--no-lock-past-keys`, 기본 `false`로 유동 선택 유지; 멀티트랙·브라스 등 특정 곡만 고정하려면 `manage_history.py set-repertoire --preferred-key` 사용)
   - 로컬 악보 폴더(기본 `sheet/`). 다부제·교대 인도자 교회는 주력 팀 기준 1세트를 저장하고, 예배 시 `--service-type`, `--scope`, `--session`으로 회차별 오버라이드를 사용하세요.

### What belongs in `user_config.json` vs per-setlist request

- **Persisted by `init`**: `team_name`, `church_scale`, `congregation_age_group`, `vocal_ensemble`, `session_instruments`, `default_session`, `session_skill_tier`, `leader_gender`, `vocal_floor`, `vocal_ceiling`, `cooldown_weeks`, `familiar_ratio`, `seeker_emphasis`, `show_capo`, `lock_past_keys`, `local_sheet_dirs`, `default_output_dir`.
- **Decided per service**: 이번 주 설교 본문/주제, 교회력 절기, 특정 예배만의 임시 템포·곡 수·분량·예배부(`--scope`).

## Step 3: Persist Profile (`init_profile.py --apply`)

Run `init_profile.py --apply` with confirmed answers (run `--help` for `--vocal-floor`, `--cooldown-weeks`, `--familiar-ratio`, `--no-show-capo`, `--no-lock-past-keys` 등 전체 옵션):

```powershell
& $py "$SKILL_DIR/scripts/init_profile.py" --apply `
  --team-name "청년부 찬양팀" `
  --church-scale medium `
  --age-group intergenerational `
  --vocal-ensemble small_team `
  --instruments "piano,synth_pad,acoustic_guitar,bass,drums" `
  --skill-tier intermediate `
  --leader-gender male `
  --vocal-ceiling F#4 `
  --no-show-capo `
  --no-lock-past-keys `
  --sheet-dir "sheet"
```

### Completion Gate

Confirm `user_config.json` exists (`$SKILL_DIR/data/user_config.json` for workspace-local installs or `<workspace>/.worship-setlist/user_config.json` for global installs) with `config_schema: 1`, and re-run `init_profile.py --inspect` if needed to verify saved settings and bootstrapped repertoire counts across all `local_sheet_dirs`.

## Step 4: Wrap Up or Resume

Present a concise summary of the saved profile (`교회 규모`, `회중 연령대`, `세션 악기·보컬 편성`, `악보 폴더`), confirm that past sheet Keys remain unlocked (`lock_past_keys: false`), and resume any pending setlist request immediately.
