# Bob Portfolio 개선·고도화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bob Portfolio Notion 대시보드에 메트릭 카드 / 목표 진척률 / 시간추이·국가비중·상관관계 차트 / 환율 / 뉴스 / 리스크 지표 / 매매 달력 8개 기능을 추가하여 AI코딩경제 기말 프로젝트 채점 기준 4개 모두 충족.

**Architecture:** 기존 `update_portfolio.py` 8단계 cron 파이프라인을 18단계로 확장. 신규 데이터(환율/뉴스)는 Notion 페이지 callout·paragraph block에 직접 patch. 신규 지표(변동성/MDD/Sharpe)는 보유주식 DB 컬럼 4개 추가. 신규 차트 3장(asset_trend/country/correlation)은 `images/` 저장 + raw URL로 Notion 이미지 블록 갱신. 매매 달력은 Notion native calendar view (코드 0줄).

**Tech Stack:** Python 3.11, requests, pandas, matplotlib, seaborn (신규), pykrx, yfinance, Notion API 2025-09-03, GitHub Actions cron.

**Spec:** `docs/superpowers/specs/2026-06-16-portfolio-enhancement-design.md`

---

## File Structure

```
update_portfolio.py             # 모든 신규 함수 + main 통합 (single source of truth)
requirements.txt                 # seaborn 추가
images/                          # asset_trend.png / country_pie.png / correlation.png 신규
.github/workflows/daily.yml      # 변경 없음 (기존 retry loop + images/ 전체 add 유지)
```

신규 파일 없음. 모두 기존 파일 수정.

Notion 변경:
- 보유주식 DB: 컬럼 4개 추가 (30일변동성/90일변동성/MDD/Sharpe)
- 매매일지 DB: calendar view 추가
- Bob Portfolio 페이지: 콘텐츠 전면 재구성

---

## Task 1: Notion DB·View 변경 (수동, MCP)

**Files:** Notion workspace 직접 변경 (코드 변경 없음)

- [ ] **Step 1: 보유주식 DB 컬럼 4개 추가**

Notion 보유주식 DB 열기 → `+` 컬럼 추가 4회:
- `30일변동성` (Number, format: Percent)
- `90일변동성` (Number, format: Percent)
- `MDD` (Number, format: Percent)
- `Sharpe` (Number, format: Number)

또는 Claude에 요청: "보유주식 DB에 컬럼 4개 추가해줘".

- [ ] **Step 2: 매매일지 DB calendar view 추가**

Notion 매매일지 DB → 우상단 `+` view → `Calendar` 선택 → name `매매 달력` → `Calendar by: 날짜` 설정.

또는 Claude에 요청.

- [ ] **Step 3: 검증**

보유주식 DB linked view에서 신규 컬럼 4개 보임 확인.
매매일지 DB에 calendar 탭 추가됨 확인.

- [ ] **Step 4: Commit (변경 없음 — Notion 작업이라 skip)**

---

## Task 2: requirements.txt seaborn 추가

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: seaborn 라인 추가**

```diff
 requests>=2.31
 pandas>=2.2
 matplotlib>=3.8
+seaborn>=0.13
 pykrx>=1.0.45
 yfinance>=0.2.40
```

- [ ] **Step 2: 로컬 설치 확인**

```bash
cd ~/Documents/bobbohee/smwu-bob-portfolio
pip install seaborn>=0.13
python3 -c "import seaborn; print(seaborn.__version__)"
```

Expected: 버전 출력 (예: `0.13.2`).

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git -c commit.gpgsign=false commit -m "deps: seaborn 추가 (상관관계 히트맵용)"
```

---

## Task 3: 환율 fetch 함수

**Files:**
- Modify: `update_portfolio.py` (도메인 헬퍼 영역에 함수 추가)

- [ ] **Step 1: 함수 추가**

`update_portfolio.py`의 `fetch_6m_close_yf` 정의 직후에 추가:

```python
def fetch_usdkrw():
    """USD/KRW 환율 현재가 + 전일 대비 변화율."""
    try:
        h = yf.Ticker('KRW=X').history(period='5d')
        if h.empty:
            return None, None
        cur = float(h['Close'].iloc[-1])
        prev = float(h['Close'].iloc[-2]) if len(h) > 1 else cur
        return cur, (cur - prev) / prev if prev else 0.0
    except Exception as e:
        print(f'  WARN 환율 fetch 실패: {e}')
        return None, None
```

- [ ] **Step 2: 로컬 검증**

```bash
NOTION_TOKEN=dummy python3 -c "
from update_portfolio import fetch_usdkrw
cur, chg = fetch_usdkrw()
print(f'USD/KRW: {cur:.2f}, change: {chg*100:.2f}%')
assert cur is None or 1000 < cur < 2000, '환율 범위 비정상'
"
```

Expected: 현재 환율(약 ₩1,300~1,500) 출력.

- [ ] **Step 3: Commit**

```bash
git add update_portfolio.py
git -c commit.gpgsign=false commit -m "feat: fetch_usdkrw — yfinance KRW=X 환율 fetch"
```

---

## Task 4: 리스크 지표 계산 함수

**Files:**
- Modify: `update_portfolio.py`

- [ ] **Step 1: 함수 추가**

`enrich_with_prices` 다음에 추가:

```python
def compute_risk_metrics(holdings):
    """각 보유 종목 90일 일별 종가 → 30일/90일 변동성, MDD, Sharpe."""
    for ticker, h in holdings.items():
        try:
            if h['category'] in KRX_CATS:
                close = fetch_6m_close_krx_stock(ticker).tail(90)
            else:
                close = fetch_6m_close_yf(ticker).tail(90)
            returns = close.pct_change().dropna()
            if len(returns) < 5:
                h['vol_30d'] = h['vol_90d'] = h['mdd'] = h['sharpe'] = None
                continue
            ann = 252 ** 0.5
            h['vol_30d'] = float(returns.tail(30).std() * ann) if len(returns) >= 30 else None
            h['vol_90d'] = float(returns.std() * ann)
            cum = (1 + returns).cumprod()
            peak = cum.cummax()
            h['mdd'] = float(((cum - peak) / peak).min())
            std = returns.std()
            h['sharpe'] = float((returns.mean() * 252) / (std * ann)) if std > 0 else None
        except Exception as e:
            print(f'  WARN 리스크 {ticker}: {e}')
            h['vol_30d'] = h['vol_90d'] = h['mdd'] = h['sharpe'] = None
```

- [ ] **Step 2: `upsert_holdings`에 신규 컬럼 4개 추가**

`update_portfolio.py`의 `upsert_holdings` 함수 내 `props` dict에 추가:

```python
        props = {
            '종목이름': title(h['name']),
            '티커':     rich(ticker),
            '보유수량': num(h['qty']),
            '매입가':   num(h['avg_price']),
            '평가금액': num(h['valuation']),
            '수익':     num(h['profit']),
            '수익률':   num(h['profit_rate']),
            '30일변동성': num(h.get('vol_30d')),
            '90일변동성': num(h.get('vol_90d')),
            'MDD':       num(h.get('mdd')),
            'Sharpe':    num(h.get('sharpe')),
        }
```

- [ ] **Step 3: Commit**

```bash
git add update_portfolio.py
git -c commit.gpgsign=false commit -m "feat: compute_risk_metrics — 변동성/MDD/Sharpe"
```

---

## Task 5: 시간추이 차트 렌더

**Files:**
- Modify: `update_portfolio.py`

- [ ] **Step 1: 함수 추가**

`render_pie` 다음에 추가:

```python
def render_asset_trend_chart(out_path):
    """총자산 DB 전체 history → 이중축 차트 (막대=평가금액, 라인=수익률)."""
    rows = query_ds(DS_ASSET)
    points = []
    for p in rows:
        pp = p['properties']
        d = get_text(pp.get('작성일자'))
        val = get_text(pp.get('총평가금액'))
        ret = get_text(pp.get('총수익률'))
        if d and val is not None:
            points.append((d, float(val), float(ret or 0)))
    if len(points) < 2:
        print('  WARN 시간추이 데이터 부족 (< 2일)')
        return
    points.sort(key=lambda x: x[0])
    dates  = [p[0] for p in points]
    values = [p[1] for p in points]
    rets   = [p[2] * 100 for p in points]

    fig, ax1 = plt.subplots(figsize=(12, 5))
    ax1.bar(dates, values, color='#4C72B0', alpha=0.55, label='총평가금액')
    ax1.set_ylabel('총평가금액 (₩)')
    ax1.tick_params(axis='x', rotation=45)
    ax2 = ax1.twinx()
    ax2.plot(dates, rets, color='#C44E52', linewidth=2.5, marker='o', markersize=7, label='총수익률')
    ax2.set_ylabel('총수익률 (%)')
    ax2.axhline(0, color='gray', linewidth=0.8, linestyle='--')
    ax1.set_title(f'총자산 시간추이 ({date.today().isoformat()})', fontsize=15, pad=15)
    fig.tight_layout()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    plt.savefig(out_path, dpi=120, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    print(f'  asset_trend 저장: {out_path} ({len(points)} points)')
```

- [ ] **Step 2: Commit**

```bash
git add update_portfolio.py
git -c commit.gpgsign=false commit -m "feat: render_asset_trend_chart — 이중축 시간추이"
```

---

## Task 6: 국가별 파이차트 렌더

**Files:**
- Modify: `update_portfolio.py`

- [ ] **Step 1: COUNTRY_MAP 상수 + 함수 추가**

`KRX_CATS` 정의 다음에:

```python
COUNTRY_MAP = {
    '국내종목': '한국',
    '국내ETF': '한국',
    '국내ETF-해외': '한국 (해외추종)',
    '해외종목': '미국',
    '해외ETF': '미국',
}
```

`render_asset_trend_chart` 다음에:

```python
def render_country_pie(holdings, out_path):
    """국가별 평가금액 비율 파이차트."""
    country_total = defaultdict(float)
    for h in holdings.values():
        country = COUNTRY_MAP.get(h['category'], '기타')
        country_total[country] += h['valuation']
    labels = list(country_total.keys())
    sizes  = list(country_total.values())
    colors = ['#1f77b4', '#2ca02c', '#ff7f0e', '#d62728']
    fig, ax = plt.subplots(figsize=(8, 8))
    _, _, autotexts = ax.pie(
        sizes, labels=labels, autopct='%1.1f%%',
        startangle=90, colors=colors[:len(labels)],
        textprops={'fontsize': 13},
    )
    for at in autotexts:
        at.set_color('white'); at.set_fontweight('bold')
    ax.set_title(f'국가별 비중 ({date.today().isoformat()})', fontsize=16, pad=20)
    plt.tight_layout()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    plt.savefig(out_path, dpi=120, bbox_inches='tight', facecolor='white')
    plt.close(fig)
```

- [ ] **Step 2: Commit**

```bash
git add update_portfolio.py
git -c commit.gpgsign=false commit -m "feat: render_country_pie — 국가별 비중 파이차트"
```

---

## Task 7: 상관관계 히트맵 렌더

**Files:**
- Modify: `update_portfolio.py`

- [ ] **Step 1: import seaborn 추가 + 함수 추가**

상단 import 영역에:

```python
import seaborn as sns
```

`render_country_pie` 다음에:

```python
def render_correlation_heatmap(holdings, out_path):
    """보유 종목간 90일 수익률 상관계수 히트맵."""
    stock_returns = {}
    for ticker, h in holdings.items():
        try:
            if h['category'] in KRX_CATS:
                close = fetch_6m_close_krx_stock(ticker).tail(90)
            else:
                close = fetch_6m_close_yf(ticker).tail(90)
            r = close.pct_change().dropna()
            if len(r) >= 30:
                stock_returns[h['name']] = r
        except Exception as e:
            print(f'  WARN heatmap {ticker}: {e}')
    if len(stock_returns) < 2:
        print('  WARN 상관관계 데이터 부족 (< 2종목)')
        return
    df = pd.DataFrame({n: s.reset_index(drop=True) for n, s in stock_returns.items()})
    corr = df.corr()
    fig, ax = plt.subplots(figsize=(9, 7))
    sns.heatmap(corr, annot=True, fmt='.2f', cmap='RdYlGn_r',
                vmin=-1, vmax=1, square=True, ax=ax,
                cbar_kws={'label': '상관계수'}, annot_kws={'size': 11})
    ax.set_title(f'보유주식 상관관계 (90일, {date.today().isoformat()})', fontsize=15, pad=15)
    plt.xticks(rotation=30, ha='right')
    plt.yticks(rotation=0)
    plt.tight_layout()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    plt.savefig(out_path, dpi=120, bbox_inches='tight', facecolor='white')
    plt.close(fig)
```

- [ ] **Step 2: Commit**

```bash
git add update_portfolio.py
git -c commit.gpgsign=false commit -m "feat: render_correlation_heatmap — seaborn 히트맵"
```

---

## Task 8: 뉴스 fetch 함수

**Files:**
- Modify: `update_portfolio.py`

- [ ] **Step 1: 함수 추가**

`fetch_usdkrw` 다음에:

```python
def fetch_news_for_holdings(holdings):
    """보유 종목 중 미국 ticker만 yfinance.news fetch. 최대 5건."""
    news = []
    seen = set()
    for ticker, h in holdings.items():
        if not ticker.isalpha():  # 한국 6자리 코드 skip
            continue
        try:
            items = yf.Ticker(ticker).news[:3] or []
            for it in items:
                title = (it.get('title') or '').strip()
                if not title or title in seen:
                    continue
                seen.add(title)
                news.append({
                    'title': title,
                    'url': it.get('link') or '',
                    'publisher': it.get('publisher') or '',
                    'time': it.get('providerPublishTime') or 0,
                })
        except Exception as e:
            print(f'  WARN news {ticker}: {e}')
    news.sort(key=lambda x: x['time'], reverse=True)
    return news[:5]
```

- [ ] **Step 2: Commit**

```bash
git add update_portfolio.py
git -c commit.gpgsign=false commit -m "feat: fetch_news_for_holdings — 미국 보유주식 뉴스 5건"
```

---

## Task 9: Notion 페이지 레이아웃 재구성

**Files:**
- Manual via Claude/MCP (Notion 페이지 콘텐츠 변경)

- [ ] **Step 1: replace_content 실행 (Claude에 요청)**

요청 프롬프트: "Bob Portfolio 페이지를 spec 레이아웃대로 재구성해줘 (메트릭 카드 4 columns + 목표 진척률 callout + 시간추이/국가비중 columns + 상관관계 + 뉴스 + 달력)."

페이지 markdown은 다음 골격:

```markdown
> 📅 작성일자 / 🔄 새로고침 / ⚠️ 보호 경고

<column_list>
<column>
> 💰 **총평가금액**
> ₩—
</column>
<column>
> 📈 **총수익**
> —
</column>
<column>
> 📊 **수익률**
> —
</column>
<column>
> 💵 **USD/KRW**
> —
</column>
</column_list>

> 🎯 **연간 목표 +15%** / 현재 — ░░░░░░░░░░ 0%

## 📊 총자산
<linked view 총자산>
![시간추이](placeholder)

## 💼 보유주식
<linked view 보유주식>

<column_list>
<column>
### 🥧 분류별 비율
![](pie placeholder)
</column>
<column>
### 🌐 국가별 비중
![](country placeholder)
</column>
</column_list>

## 🔥 종목간 상관관계
![](correlation placeholder)

## 📰 보유주식 뉴스

뉴스 1 placeholder

뉴스 2 placeholder

뉴스 3 placeholder

뉴스 4 placeholder

뉴스 5 placeholder

## 🔍 관심종목 분석
<linked view 관심종목>

### 📈 코스피200 기준
![](idx_kospi placeholder)

### 📈 SP500 기준
![](idx_sp500 placeholder)

### 📈 나스닥100 기준
![](idx_ndx placeholder)

## 📅 매매 달력
<linked view 매매일지 calendar>

## 📝 최근 매매일지
<linked view 매매일지 table>

---
### 📂 원본 데이터베이스 (관리용)
<child DB 4개>
```

**중요 검증**: Notion enhanced markdown spec에서 `<column_list>` `<column>` 문법 지원 여부 확인. 미지원 시 fallback = 메트릭 카드 4개를 callout 세로 배치.

- [ ] **Step 2: 레이아웃 적용 후 페이지 fetch로 block 구조 확인**

`notion-fetch`로 페이지 콘텐츠 조회 → callout block ID, 이미지 block ID, paragraph block ID 확인.

- [ ] **Step 3: Commit (변경 없음 — Notion 작업)**

---

## Task 10: callout/paragraph block 매칭 helper

**Files:**
- Modify: `update_portfolio.py`

- [ ] **Step 1: 매칭 키워드 상수 추가**

`INDEX_MAP` 다음에:

```python
METRIC_KEYS = {
    '총평가금액': 'eval',
    '총수익':     'pl',
    '수익률':     'pr',
    'USD/KRW':   'fx',
}
GOAL_KEY = '연간 목표'
IMAGE_HEADINGS = {
    '시간추이':         'images/asset_trend.png',
    '분류별 비율':      'images/pie.png',
    '국가별 비중':      'images/country_pie.png',
    '종목간 상관관계':  'images/correlation.png',
    '코스피200 기준':   'images/idx_kospi200.png',
    'SP500 기준':       'images/idx_sp500.png',
    '나스닥100 기준':   'images/idx_ndx.png',
}
NEWS_HEADING = '보유주식 뉴스'
```

- [ ] **Step 2: callout/paragraph 찾기 helper 추가**

`update_notion_idx_images` 다음에:

```python
def find_blocks_by_keyword(children):
    """페이지 children → 키워드 기반 블록 ID 매핑."""
    metrics = {}      # 'eval'/'pl'/'pr'/'fx' → callout block id
    goal_id = None
    images = {}       # img_path → image block id
    news_para_ids = []
    cur_heading = None
    cur_h2 = None
    for b in children:
        t = b['type']
        if t == 'callout':
            text = ''.join(x['plain_text'] for x in b['callout']['rich_text'])
            for kw, key in METRIC_KEYS.items():
                if kw in text:
                    metrics[key] = b['id']
                    break
            else:
                if GOAL_KEY in text:
                    goal_id = b['id']
        elif t in ('heading_2', 'heading_3'):
            txt = ''.join(x['plain_text'] for x in b[t]['rich_text'])
            cur_heading = next((p for kw, p in IMAGE_HEADINGS.items() if kw in txt), None)
            cur_h2 = NEWS_HEADING if NEWS_HEADING in txt else None
        elif t == 'image' and cur_heading:
            images[cur_heading] = b['id']
            cur_heading = None
        elif t == 'paragraph' and cur_h2 == NEWS_HEADING:
            news_para_ids.append(b['id'])
    return metrics, goal_id, images, news_para_ids
```

- [ ] **Step 3: Commit**

```bash
git add update_portfolio.py
git -c commit.gpgsign=false commit -m "feat: find_blocks_by_keyword — callout/이미지/뉴스 블록 매핑 helper"
```

---

## Task 11: 메트릭 카드 + 진척률 + 뉴스 갱신 함수

**Files:**
- Modify: `update_portfolio.py`

- [ ] **Step 1: callout 갱신 helper 추가**

`find_blocks_by_keyword` 다음에:

```python
def patch_callout(block_id, text, color='default'):
    body = {
        'callout': {
            'rich_text': [{'type': 'text', 'text': {'content': text}}],
            'color': color,
        }
    }
    r = requests.patch(f'{API}/blocks/{block_id}', headers=H, json=body)
    r.raise_for_status()


def patch_paragraph_link(block_id, title, url, publisher):
    body = {
        'paragraph': {
            'rich_text': [
                {'type': 'text', 'text': {'content': '• '}},
                {'type': 'text', 'text': {'content': title, 'link': {'url': url}}},
                {'type': 'text', 'text': {'content': f'  —  {publisher}'},
                 'annotations': {'italic': True, 'color': 'gray'}},
            ]
        }
    }
    r = requests.patch(f'{API}/blocks/{block_id}', headers=H, json=body)
    r.raise_for_status()


def update_metric_cards(metrics_ids, total_val, total_pl, total_pr, fx, fx_chg):
    if 'eval' in metrics_ids:
        patch_callout(metrics_ids['eval'],
                      f'💰 총평가금액  ₩{int(total_val):,}', color='blue_background')
    if 'pl' in metrics_ids:
        sign = '+' if total_pl >= 0 else ''
        color = 'red_background' if total_pl >= 0 else 'blue_background'  # 한국: 빨강=상승
        patch_callout(metrics_ids['pl'],
                      f'📈 총수익  {sign}₩{int(total_pl):,}', color=color)
    if 'pr' in metrics_ids:
        sign = '+' if total_pr >= 0 else ''
        color = 'red_background' if total_pr >= 0 else 'blue_background'
        patch_callout(metrics_ids['pr'],
                      f'📊 수익률  {sign}{total_pr*100:.2f}%', color=color)
    if 'fx' in metrics_ids:
        if fx is None:
            patch_callout(metrics_ids['fx'], '💵 USD/KRW  —', color='gray_background')
        else:
            chg_pct = (fx_chg or 0) * 100
            sign = '+' if chg_pct >= 0 else ''
            patch_callout(metrics_ids['fx'],
                          f'💵 USD/KRW  ₩{fx:.0f} ({sign}{chg_pct:.2f}%)',
                          color='yellow_background')


def update_goal_progress(goal_id, current_pr, target=0.15):
    if not goal_id:
        return
    ratio = max(0.0, min(2.0, current_pr / target if target > 0 else 0))
    filled = max(0, min(10, int(ratio * 10)))
    bar = '█' * filled + '░' * (10 - filled)
    sign = '+' if current_pr >= 0 else ''
    text = (f'🎯 연간 목표 +{int(target*100)}%  /  현재 {sign}{current_pr*100:.1f}%  '
            f'{bar}  {ratio*100:.0f}%')
    color = 'green_background' if ratio >= 0.8 else (
        'yellow_background' if ratio >= 0.5 else 'red_background')
    patch_callout(goal_id, text, color=color)


def update_news_paragraphs(news_para_ids, news_items):
    """뉴스 paragraph 블록 N개에 헤드라인 patch. 부족하면 '뉴스 없음'."""
    for i, pid in enumerate(news_para_ids):
        if i < len(news_items):
            n = news_items[i]
            patch_paragraph_link(pid, n['title'], n['url'], n['publisher'])
        else:
            body = {'paragraph': {'rich_text': [
                {'type': 'text', 'text': {'content': '(뉴스 없음)'},
                 'annotations': {'italic': True, 'color': 'gray'}}
            ]}}
            r = requests.patch(f'{API}/blocks/{pid}', headers=H, json=body)
            r.raise_for_status()
```

- [ ] **Step 2: Commit**

```bash
git add update_portfolio.py
git -c commit.gpgsign=false commit -m "feat: 메트릭 카드/진척률/뉴스 paragraph 갱신 함수"
```

---

## Task 12: 이미지 블록 일괄 patch helper

**Files:**
- Modify: `update_portfolio.py`

- [ ] **Step 1: 일괄 이미지 갱신 함수 추가**

기존 `update_notion_image` 및 `update_notion_idx_images` 옆에 추가 (기존 함수는 호환 유지):

```python
def update_image_blocks(image_ids, timestamp):
    """{img_path: block_id} 매핑 → 모든 이미지 URL patch."""
    for img_path, block_id in image_ids.items():
        url = (f'https://raw.githubusercontent.com/{GH_REPO}/{GH_BRANCH}/'
               f'{img_path}?t={timestamp}')
        r = requests.patch(
            f'{API}/blocks/{block_id}', headers=H,
            json={'image': {'external': {'url': url}}},
        )
        r.raise_for_status()
        print(f'  image patched: {img_path} → {block_id}')
```

- [ ] **Step 2: Commit**

```bash
git add update_portfolio.py
git -c commit.gpgsign=false commit -m "feat: update_image_blocks — 이미지 블록 일괄 patch"
```

---

## Task 13: main() 통합 (18단계)

**Files:**
- Modify: `update_portfolio.py` (main 함수 교체)

- [ ] **Step 1: main 함수 재작성**

기존 main():

```python
def main():
    print('[1] 매매일지 집계')
    holdings = aggregate_trades()
    print(f'  종목 {len(holdings)}개')

    print('[2] 시세 fetch')
    enrich_with_prices(holdings)

    print('[3] 리스크 지표')
    compute_risk_metrics(holdings)

    print('[4] 보유주식 UPSERT')
    upsert_holdings(holdings)

    print('[5] 총자산 UPSERT')
    upsert_asset(holdings)
    total_val = sum(h['valuation'] for h in holdings.values())
    total_buy = sum(h['avg_price'] * h['qty'] for h in holdings.values())
    total_pl  = total_val - total_buy
    total_pr  = (total_pl / total_buy) if total_buy else 0.0

    print('[6] 분류 파이차트')
    render_pie(holdings, IMG_PATH)

    print('[7] 환율 fetch')
    fx, fx_chg = fetch_usdkrw()
    print(f'  USD/KRW: {fx} ({(fx_chg or 0)*100:+.2f}%)' if fx else '  USD/KRW: —')

    print('[8] 시간추이 차트')
    render_asset_trend_chart('images/asset_trend.png')

    print('[9] 국가별 파이차트')
    render_country_pie(holdings, 'images/country_pie.png')

    print('[10] 상관관계 히트맵')
    render_correlation_heatmap(holdings, 'images/correlation.png')

    print('[11] 뉴스 fetch')
    news_items = fetch_news_for_holdings(holdings)
    print(f'  뉴스 {len(news_items)}건')

    print('[12] 관심종목 6개월 분석')
    by_index = analyze_watchlist()
    results = run_index_analysis(by_index)
    upsert_watchlist_results(results)

    print('[13] Notion 페이지 블록 매핑')
    ts = int(time.time())
    children = list_children(PAGE_ID)
    metrics_ids, goal_id, image_ids, news_para_ids = find_blocks_by_keyword(children)

    print('[14] 메트릭 카드 갱신')
    update_metric_cards(metrics_ids, total_val, total_pl, total_pr, fx, fx_chg)

    print('[15] 목표 진척률 갱신')
    update_goal_progress(goal_id, total_pr, target=TARGET_ANNUAL_RETURN)

    print('[16] 이미지 블록 갱신')
    update_image_blocks(image_ids, ts)

    print('[17] 뉴스 paragraph 갱신')
    update_news_paragraphs(news_para_ids, news_items)

    print('done.')
```

- [ ] **Step 2: 상수 추가 (파일 상단)**

`DS_WATCH` 정의 다음에:

```python
TARGET_ANNUAL_RETURN = 0.15
```

- [ ] **Step 3: Commit**

```bash
git add update_portfolio.py
git -c commit.gpgsign=false commit -m "feat: main() 18단계 통합 + TARGET_ANNUAL_RETURN 상수"
```

---

## Task 14: 로컬 dry-run + push + 시각 검증

**Files:** 검증만

- [ ] **Step 1: 로컬 dry-run (NOTION_TOKEN 필요)**

```bash
cd ~/Documents/bobbohee/smwu-bob-portfolio
export NOTION_TOKEN=ntn_...
python3 update_portfolio.py 2>&1 | tee /tmp/portfolio_run.log
```

Expected: 18단계 모두 출력. 마지막 `done.` 도달. images/ 폴더에 신규 PNG 3장.

- [ ] **Step 2: 검증 항목**

| 항목 | 검증 |
|---|---|
| 환율 | `[7] USD/KRW: 1380.xx` 형식 출력 |
| 시간추이 PNG | `images/asset_trend.png` 존재 (총자산 row ≥ 2건일 때) |
| 국가별 PNG | `images/country_pie.png` 존재 |
| 상관관계 PNG | `images/correlation.png` 존재 (90일 데이터 OK일 때) |
| 뉴스 | `[11] 뉴스 N건` 출력 (보유주식에 US ticker 있을 때) |
| 메트릭 카드 | Notion 페이지 상단 4 callout 갱신됨 |
| 진척률 callout | 진척률 bar 갱신됨 |
| 이미지 6장 | Notion 모든 차트 자리 새 URL 적용 |

- [ ] **Step 3: Push**

```bash
git push
```

- [ ] **Step 4: Actions 수동 trigger**

```bash
gh workflow run "Daily Portfolio Update" --repo bobbohee/smwu-bob-portfolio
```

- [ ] **Step 5: Actions 로그 확인**

```bash
sleep 60
gh run list --limit 1 --repo bobbohee/smwu-bob-portfolio
gh run view <run_id> --log --repo bobbohee/smwu-bob-portfolio | tail -50
```

Expected: 모든 step success. images/ 자동 commit + push 성공.

- [ ] **Step 6: Notion 페이지 브라우저 시각 확인**

Playwright 또는 직접 페이지 열기. 8개 신규 요소 모두 렌더링 확인.

- [ ] **Step 7: 최종 commit (변경 없으면 skip)**

---

## Risk Notes

| 리스크 | 완화 |
|---|---|
| Notion `<column_list>` markdown 미지원 | callout 4개 세로 배치로 fallback. Task 9 결과로 결정. |
| 첫 실행 시 총자산 DB row 1건뿐 | `render_asset_trend_chart`에서 `< 2` 시 skip + 경고. Notion 이미지 placeholder 그대로 유지. |
| 한국 종목 yfinance 뉴스 빈 결과 | 미국 ticker만 fetch (한국 6자리 코드 skip). 뉴스 0건이면 paragraph에 "뉴스 없음" 표시. |
| 90일 데이터 부족 (신규 상장 등) | 상관관계 차트 skip, 리스크 지표 None. linked view에서 빈칸으로 표시. |
| Actions seaborn 설치 실패 | requirements.txt에 명시. Linux ubuntu-latest는 wheel 제공. 만약 실패 시 `pip install seaborn --no-build-isolation`. |
| Race condition push | 기존 retry 5회 (workflow daily.yml) 그대로. |
| Notion API rate limit | 18단계에서 API 호출 ~20회. 분당 한도 3회/초 = 180회/분 충분 여유. |

---

## Completion Criteria

- [ ] Task 1~14 모두 완료
- [ ] Notion Bob Portfolio 페이지에 8개 신규 요소 모두 시각 노출
- [ ] GitHub Actions run 1회 성공 (errors 0, retry 0~1회)
- [ ] images/ 폴더에 PNG 6장 (pie + asset_trend + country + correlation + idx_*×3)
- [ ] 보유주식 DB linked view에 신규 컬럼 4개 표시
- [ ] 매매일지 calendar view 페이지 inline 렌더

다음 cron (UTC 09:00) 자동 트리거 시도 무인 통과.
