# Bob Portfolio 개선·고도화 Design

**작성일**: 2026-06-16
**목적**: AI코딩경제 기말 프로젝트 채점 기준 4개 모두 충족 (총 16점)

## Goal

기존 Bob Portfolio (Notion + GitHub Actions 자동화)에 8개 기능을 추가하여 대시보드 완성도·기술 난이도·창의성 강화.

### 채점 기준 매핑

| 기준 | 강화 방안 |
|---|---|
| 1. 개인 대시보드 | 메트릭 카드, 진척률, 매매 달력 뷰 |
| 2. 완성도 | columns 레이아웃, 색상 코딩, 시각 강조 |
| 3. 난이도 (차트/수익률) | 시간추이/국가비중/상관관계/지수비교, 변동성·MDD·Sharpe |
| 4. 창의성 | 환율 카드, 뉴스, 진척률 게이지, 상관관계 히트맵 |

## Architecture

### 데이터 흐름

기존 8단계 파이프라인 → 14단계 확장:

```
[1] 매매일지 fetch
[2] 보유주식 집계
[3] 시세 fetch
[4] 보유주식 UPSERT
[5] 총자산 UPSERT (오늘 row)
[6] 분류별 파이차트
[7] 관심종목 fetch + 6M 분석
[8] 지수 차트 3장
─ 신규 ─
[9]  환율 fetch (USD/KRW)
[10] 시간추이 차트 생성 (총자산 DB history)
[11] 국가별 비중 파이차트
[12] 리스크 지표 계산 (변동성/MDD/Sharpe) → 보유주식 UPSERT
[13] 상관관계 히트맵
[14] 뉴스 fetch (yfinance Ticker.news)
─ Notion 일괄 갱신 ─
[15] 메트릭 카드 callout 4개 patch
[16] 목표 진척률 callout patch
[17] 이미지 블록 6장 URL patch (pie/idx*3/asset_trend/country/correlation)
[18] 뉴스 paragraph 5개 replace
```

### 데이터 저장 위치

| 데이터 | 저장 위치 | 갱신 |
|---|---|---|
| 환율 | 페이지 메트릭 callout (4번째) | callout text patch |
| 목표 수익률 | 코드 상수 `TARGET_ANNUAL_RETURN = 0.15` | 코드 |
| 진척률 | 페이지 callout (계산값) | text patch |
| 시간추이 | 총자산 DB (이미 매일 row 누적) | 별도 저장 X |
| 국가별 비중 | 보유주식 분류에서 도출 | 차트만 |
| 리스크 지표 | 보유주식 DB 신규 컬럼 4개 | UPSERT |
| 상관관계 | matplotlib 차트만 | 차트만 |
| 뉴스 | 페이지 paragraph 블록 5개 | text replace |

### 의존성

기존 `requests, pandas, matplotlib, pykrx, yfinance` + `seaborn` 추가.

## 페이지 레이아웃

```
커버 + 타이틀 (Bob Portfolio)
헤더 (작성일자 / 새로고침 / 보호 경고)

[메트릭 카드 — 4 columns]
💰 총평가      📈 총수익      📊 수익률      💵 USD/KRW

🎯 목표 진척률
연간 +15% / 현재 +X% ████░░░░░░ XX%

📊 총자산
  [총자산 linked view]
  [시간추이 차트]

💼 보유주식
  [보유주식 linked view + 리스크 컬럼]

[🥧 분류별 비율 | 🌐 국가별 비중] (2 columns)

🔥 종목간 상관관계
  [히트맵]

📰 보유주식 뉴스
  • 헤드라인 1 (URL) — publisher
  • 헤드라인 2 ... (총 5건)

🔍 관심종목 분석
  [관심종목 linked view]
  [지수 차트 3장]

📅 매매 달력
  [매매일지 calendar view]

📝 최근 매매일지
  [매매일지 linked view]

---
원본 DB (관리용)
```

## 기능 상세

### A. 메트릭 카드 (4 columns)

페이지 상단 column_list + 4 column. 각 column 내 callout block.
- 카드 1: 💰 총평가금액 ₩X,XXX,XXX
- 카드 2: 📈 총수익 ±₩X,XXX (색: 빨강/파랑)
- 카드 3: 📊 수익률 ±X.XX%
- 카드 4: 💵 USD/KRW ₩X,XXX (전일 대비 ±X.X%)

매일 cron: `PATCH /blocks/{callout_id}` with new `callout.rich_text`.

### B. 목표 진척률

```python
TARGET = 0.15
ratio = current_total_return / TARGET
filled = max(0, min(10, int(ratio * 10)))
bar = '█' * filled + '░' * (10 - filled)
text = f'연간 목표 +15% / 현재 +{current*100:.1f}% {bar} {ratio*100:.0f}%'
```

callout 1개. 색상:
- ratio ≥ 0.8 → green
- 0.5 ≤ ratio < 0.8 → yellow
- ratio < 0.5 → red

### C. 시간추이 차트

- 데이터: 총자산 DB 전체 row (작성일자 ASC)
- 차트: matplotlib 이중축
  - X: 작성일자
  - Y1 (왼쪽 막대): 총평가금액 (₩)
  - Y2 (오른쪽 라인): 총수익률 (%)
- 파일: `images/asset_trend.png`

### D. 국가별 비중 파이차트

```python
COUNTRY_MAP = {
    '국내종목': '한국',
    '국내ETF': '한국',
    '국내ETF-해외': '한국 (해외추종)',
    '해외종목': '미국',
    '해외ETF': '미국',
}
```

- 파일: `images/country_pie.png`

### E. 리스크 지표

각 보유 종목 90일 일별 종가 fetch → 지표 계산:

```python
returns = close.pct_change().dropna()
vol_30d = returns.tail(30).std() * (252 ** 0.5)
vol_90d = returns.std() * (252 ** 0.5)

cum = (1 + returns).cumprod()
peak = cum.cummax()
mdd = ((cum - peak) / peak).min()  # 음수

sharpe = (returns.mean() * 252) / (returns.std() * (252 ** 0.5))
```

보유주식 DB 신규 컬럼 (NUMBER):
- `30일변동성` (percent)
- `90일변동성` (percent)
- `MDD` (percent)
- `Sharpe` (소수)

### F. 상관관계 히트맵

```python
import seaborn as sns
df = pd.DataFrame({name: returns for name, returns in stock_returns.items()})
corr = df.corr()
sns.heatmap(corr, annot=True, fmt='.2f', cmap='RdYlGn_r',
            vmin=-1, vmax=1, square=True)
```

- 파일: `images/correlation.png`
- 데이터 < 30일이면 차트 skip + warning

### G. 뉴스 fetch

```python
def fetch_news(holdings):
    news = []
    seen = set()
    for ticker in holdings:
        if not ticker.isalpha():  # 한국 6자리 skip
            continue
        try:
            for item in yf.Ticker(ticker).news[:3]:
                title = item.get('title')
                if not title or title in seen: continue
                seen.add(title)
                news.append({...})
        except: pass
    return sorted(news, key=lambda x: x['time'] or 0, reverse=True)[:5]
```

페이지 paragraph 5개에 `[헤드라인](URL) — publisher` 형식.
뉴스 0건이면 "최근 뉴스 없음" 메시지.

### H. 매매 달력 뷰

코드 0줄. MCP `notion-create-view` 1회:

```python
create_view(
    parent_page_id=PAGE_ID,
    data_source_id=DS_TRADE,
    name='매매 달력',
    type='calendar',
    configure='CALENDAR BY "날짜"'
)
```

### I. 환율 fetch

```python
def fetch_usdkrw():
    h = yf.Ticker('KRW=X').history(period='5d')
    if h.empty: return None, None
    cur = float(h['Close'].iloc[-1])
    prev = float(h['Close'].iloc[-2]) if len(h) > 1 else cur
    return cur, (cur - prev) / prev
```

fallback: None 반환 시 카드에 "—" 표시.

## 구현 Phases

### Phase 1: 안전한 신규 추가
- P1a. 매매 달력 view 생성
- P1b. 보유주식 DB 컬럼 4개 추가
- P1c. requirements.txt: seaborn 추가

### Phase 2: 백엔드 함수 (단위 가능)
- P2a. `fetch_usdkrw()`
- P2b. `compute_risk_metrics(holdings)`
- P2c. `render_asset_trend_chart()`
- P2d. `render_country_pie()`
- P2e. `render_correlation_heatmap()`
- P2f. `fetch_news_for_holdings()`

### Phase 3: 페이지 구조 변경
- P3a. `replace_content`로 새 레이아웃 적용 (메트릭 카드 4 columns, callout, 이미지 placeholder, 뉴스 paragraph placeholder, 분류별+국가별 2 columns, 달력 뷰 inline)
- P3b. 신규 callout/이미지/paragraph block ID 식별 helper

### Phase 4: 갱신 로직
- P4a. `update_metric_cards(total_val, total_pl, total_pr, fx)`
- P4b. `update_goal_progress(current_pr)`
- P4c. 이미지 블록 6장 URL patch (기존 패턴 확장)
- P4d. 뉴스 paragraph 5개 텍스트 patch

### Phase 5: 통합 + 검증
- P5a. main()에 신규 단계 통합 (총 18단계: 기존 8 + 데이터 6 + Notion 갱신 4)
- P5b. 로컬 dry-run
- P5c. commit + push → Actions trigger
- P5d. Notion 시각 확인

## 위험 + 완화

| 위험 | 영향 | 완화 |
|---|---|---|
| Notion column_list block 생성 실패 | 메트릭 카드 안 됨 | column markdown 실험. 안 되면 callout 세로 4개로 fallback |
| seaborn pip install 실패 | Actions error | 명시 버전 pin. matplotlib `imshow`로 fallback |
| 환율 휴장일 빈 응답 | NoneType error | try/except + "—" 표시 |
| 한국 종목 뉴스 빈 결과 | 뉴스 섹션 비어보임 | "최근 뉴스 없음" 메시지 |
| 90일 데이터 부족 | 상관계수 NaN | < 30일이면 차트 skip + 경고 |
| 신규 callout block ID 매번 변동 | block 못 찾음 | heading 매칭 패턴 확장 (분류별 비율 패턴 재사용) |
| Actions race | push 충돌 | 이미 retry 5회 적용됨 |

## 작업 분량

- 코드: ~400줄 추가 (현재 473 → ~870)
- DB: 보유주식 4컬럼 + 매매일지 calendar view
- 페이지 콘텐츠: 전면 재구성
- 시간 추정: 1세션

## 절대 규칙 (유지)

- Notion 페이지 **블록 절대 삭제 금지**
- Row UPSERT + callout/image/paragraph block PATCH만 허용
- 재실행 시 idempotent (같은 row 중복 X, 같은 block 갱신)
