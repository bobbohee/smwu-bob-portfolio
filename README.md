# Bob Portfolio

Notion 기반 개인 주식 포트폴리오 자동화. 매일 1회 GitHub Actions가 매매일지를 읽어 보유주식·총자산·관심종목·차트·메트릭 카드·뉴스를 갱신한다.

## 구조

```
.
├── update_portfolio.py        # 메인 스크립트 (15단계 파이프라인)
├── requirements.txt           # requests / pandas / matplotlib / seaborn / pykrx / yfinance
├── images/
│   ├── asset_trend.png        # 총자산 시간추이 (이중축)
│   ├── correlation.png        # 보유 종목간 90일 상관관계 히트맵
│   ├── idx_kospi200.png       # 코스피200 기준 6M 누적 수익률
│   ├── idx_sp500.png          # S&P500 기준 6M 누적 수익률
│   ├── idx_ndx.png            # NASDAQ100 기준 6M 누적 수익률
│   ├── pie.png                # 분류별 비율 (현재 페이지 미사용)
│   └── country_pie.png        # 국가별 비중 (현재 페이지 미사용)
├── .github/workflows/daily.yml
├── docs/superpowers/          # spec / plan 문서
└── bob_portfolio.ipynb        # 수동 디버깅용 Colab 노트북 (선택)
```

## 데이터 흐름

```
[매매일지 DB]
   │ 티커별 집계 (수량/가중평균 매입가)
   ▼
[보유주식 DB] ← 시세 갱신 (pykrx / yfinance) + 리스크 (변동성/MDD/Sharpe)
   │
   │ 합계 + 환율
   ▼
[총자산 DB] (작성일자 title + 날짜 date + 총평가금액/총수익/총수익률)
   │
   │ 분석 + 차트
   ▼
[관심종목 DB] ← 지수 대비 6M 수익률 비교 → 판정 (유지/손절 검토)

[차트 6장] → images/ → git push → GitHub raw URL → Notion image block PATCH
[메트릭 카드 3개 + 진척률 1개 + 뉴스 paragraph 5개] → callout/paragraph PATCH
[Notion native chart view 4개] → DB 데이터 변경시 자동 갱신
```

## 파이프라인 (main 15단계)

| # | 단계 | 출력 |
|---|---|---|
| 1 | 매매일지 집계 | holdings dict |
| 2 | 시세 fetch | 현재가/평가금액/수익/수익률 |
| 3 | 리스크 지표 | 30/90일 변동성, MDD, Sharpe |
| 4 | 보유주식 UPSERT | DB row 갱신 |
| 5 | 총자산 UPSERT + 날짜/총수익률 backfill | 오늘 row + 과거 row 일괄 backfill |
| 6 | 환율 fetch | USD/KRW (현재 카드 미사용, 로그만) |
| 7 | 시간추이 차트 | asset_trend.png |
| 8 | 상관관계 히트맵 | correlation.png |
| 9 | 뉴스 fetch (보유 + 관심종목 통합) | 5건 |
| 10 | 관심종목 6개월 분석 | KOSPI200/S&P500/NASDAQ100 chart 3장 + 판정 |
| 11 | Notion 페이지 블록 매핑 | callout/heading/image/paragraph ID 수집 |
| 12 | 메트릭 카드 갱신 (총평가금액/총수익/수익률) | callout PATCH × 3 |
| 13 | 목표 진척률 갱신 | callout PATCH × 1 |
| 14 | 이미지 블록 갱신 | image PATCH × 5 |
| 15 | 뉴스 paragraph 갱신 | paragraph PATCH × 5 |

## 사전 준비

### 1. Notion Integration

1. https://www.notion.so/profile/integrations → `New integration` (Internal)
2. Capabilities: Read/Update/Insert content 모두 체크
3. Secret token 복사 (`ntn_...`)
4. Bob Portfolio 페이지 우상단 `⋯` → `Connections` → 만든 integration 추가

### 2. GitHub Repo

1. 이 폴더를 GitHub repo로 push (예: `bobbohee/smwu-bob-portfolio`)
2. 비공개 추천 (포트폴리오 정보가 raw URL에 노출됨)
3. Repo Settings → Secrets and variables → Actions → `New repository secret`
   - Name: `NOTION_TOKEN`
   - Value: 위 1.3에서 받은 토큰
4. Settings → Actions → General → Workflow permissions → `Read and write permissions` 체크 (git push 허용)

### 3. update_portfolio.py 상수 확인

`PAGE_ID`, `DS_TRADE`, `DS_HOLDING`, `DS_ASSET`, `DS_WATCH`는 이미 Bob Portfolio용으로 하드코딩됨. 다른 워크스페이스 사용 시 교체.

주요 설정:
```python
TARGET_ANNUAL_RETURN = 0.30  # 연간 목표 수익률
CHART_STOCKS_FILTER = {       # 지수별 chart 표시 종목 whitelist (옵션)
    '코스피200': {'현대자동차', '삼성전자', 'SK하이닉스'},
}
```

## 실행

### 자동 (GitHub Actions cron)

`.github/workflows/daily.yml`이 매일 09:00 UTC (18:00 KST)에 cron 트리거. 한국장 마감(15:30) + 미국장 직전 종가 기준.

### 수동 (workflow_dispatch)

- GitHub UI: Actions → `Daily Portfolio Update` → `Run workflow` 클릭
- CLI (admin): `gh workflow run "Daily Portfolio Update" --repo bobbohee/smwu-bob-portfolio`

### 로컬 실행

```bash
pip install -r requirements.txt
export NOTION_TOKEN=ntn_...
python update_portfolio.py
```

## 절대 규칙

- Notion 페이지 **블록은 절대 삭제 금지**. 스크립트는 row UPSERT + callout/image/paragraph URL PATCH만 수행.
- 보유주식 / 총자산 row는 (티커 / 작성일자) 키로 idempotent UPSERT → 재실행해도 중복 없음.
- 페이지 layout 변경 시 update_portfolio.py 상수 (`METRIC_KEYS`, `IMAGE_HEADINGS`, `NEWS_HEADING`) 매칭 확인 필요.

## 데이터 모델

| DB | 키 | 자동 갱신 컬럼 |
|---|---|---|
| 매매일지 | 사용자 직접 등록 | (없음 — input) |
| 보유주식 | 티커 | 평가금액, 수익, 수익률, 보유수량, 매입가, 분류, 30일변동성, 90일변동성, MDD, Sharpe |
| 총자산 | 작성일자 (title) | 날짜 (date), 총평가금액, 총수익, 총수익률 |
| 관심종목 | 티커 + 기준지수 | 6M수익률, 지수6M수익률, 차이, 판정 |

### 정밀도 helper

| helper | round | 용도 |
|---|---|---|
| `num(v)` | (없음) | 원본 number |
| `num1(v)` | `round(v, 1)` | 1자리 number (Sharpe) |
| `pct(v)` | `round(v, 2)` | 0자리 percent (보유주식 수익률) |
| `pct1(v)` | `round(v, 3)` | 1자리 percent (총수익률, 변동성, MDD, 6M수익률) |

## Notion 페이지 구조

페이지 = `380940b8-828c-81a0-88f6-eadcff08e9c6`

```
📅 작성일자 callout / 🔄 새로고침 link callout
columns: [💰 총평가금액] [📈 총수익] [📊 수익률]
🎯 연간 목표 +30% / 현재 / 진척률 bar
─────────────────
# 💼 보유주식
columns: [donut 분류별 평가금액] [donut 분류별 개수]
inline DB (table)
# 🔥 종목간 상관관계
image (correlation.png)
# 📰 보유주식 뉴스
5 × paragraph (linked)
# 📝 최근 매매일지 (inline DB)
# 📊 총자산
image (asset_trend.png) + inline DB
# 🔍 관심종목 분석 (inline DB)
### 📈 KOSPI200 기준 → idx_kospi200.png
### 📈 S&P500 기준 → idx_sp500.png
### 📈 NASDAQ100 기준 → idx_ndx.png
# 📂 원본 데이터베이스 (관리용) — 총자산/보유주식/매매일지/관심종목
```

### Notion native chart view (코드 무관, DB 변경 시 자동 갱신)

1. 분류별 평가금액 — donut, group=분류 select, sum 평가금액
2. 분류별 개수 — donut, group=분류 select, count
3. 총자산 시간추이 — line, group=날짜 date, sum 총평가금액
4. (선택) 매매일지 시간추이 — line, group=날짜 date, count

## 트러블슈팅

- **404 query 실패** → integration이 Bob Portfolio 페이지에 connect 안 됨. 페이지 ⋯ → Connections 확인.
- **이미지 안 바뀜** → raw URL 캐시. `?t={timestamp}` 쿼리로 강제 무효화하지만 Notion 클라이언트 캐시 30초 정도 잔존 가능.
- **Actions cron 미실행** → repo가 60일 무활동 시 GitHub cron 일시정지. 수동 `Run workflow` 1회로 복구.
- **pykrx 시세 None** → 휴장일 또는 데이터 미반영. 매입가로 fallback.
- **메트릭 카드 갱신 안됨** → 페이지 callout 텍스트의 키워드가 `METRIC_KEYS` 매칭 조건 만족하는지 확인. 페이지 layout 변경시 keyword 매칭 fail 가능.
- **이미지 갱신 안됨** → heading 텍스트가 `IMAGE_HEADINGS` keyword 포함하는지 + heading_1/2/3 어느 level이든 OK.
- **소수점 자릿수 변경 안 반영** → 과거 row는 옛 정밀도. `upsert_asset`에서 호출되는 `backfill_asset_pct`로 일괄 backfill.
- **chart view 빈 박스** → groupBy property type이 select가 아니면 sum/count 렌더 fail. title/text type은 사용 X.

## 관련 문서

- `docs/superpowers/specs/2026-06-16-portfolio-enhancement-design.md` — spec
- `docs/superpowers/plans/2026-06-16-portfolio-enhancement.md` — 14 task plan
