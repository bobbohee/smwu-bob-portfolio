"""Bob Portfolio 일일 갱신 스크립트.

매매일지 -> 보유주식 집계 -> 시세 fetch -> 평가금액/수익 계산 ->
총자산 UPSERT -> 분류별 파이차트 생성 -> Notion 이미지 블록 URL 갱신.

GitHub Actions cron 또는 로컬 수동 실행.
"""

import base64
import os
import subprocess
import time
from collections import defaultdict
from datetime import date, timedelta

import matplotlib
matplotlib.use('Agg')
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
import requests
import yfinance as yf
from pykrx import stock

NOTION_TOKEN = os.environ['NOTION_TOKEN']
NOTION_VERSION = '2025-09-03'
API = 'https://api.notion.com/v1'

PAGE_ID    = '380940b8-828c-81a0-88f6-eadcff08e9c6'
DS_TRADE   = 'dae5f91b-fdbe-4787-90d9-f6966a727a2c'
DS_HOLDING = 'ae611b21-f20d-438a-a624-e81f34909de4'
DS_ASSET   = '77ab4124-838b-4ded-bef9-f10f337a86ec'
DS_WATCH   = 'cabfc928-42aa-4089-83cc-c58c1b13daa8'

INDEX_MAP = {
    '코스피200': {'src': 'krx_stock', 'code': '102110', 'img': 'images/idx_kospi200.png', 'heading': '코스피200 기준'},
    'S&P500':   {'src': 'yf',        'code': '^GSPC',  'img': 'images/idx_sp500.png',    'heading': 'SP500 기준'},
    '나스닥100': {'src': 'yf',        'code': '^NDX',   'img': 'images/idx_ndx.png',      'heading': '나스닥100 기준'},
}

TARGET_ANNUAL_RETURN = 0.15

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

GH_REPO   = os.environ.get('GH_REPO',   'bobbohee/smwu-bob-portfolio')
GH_BRANCH = os.environ.get('GH_BRANCH', 'main')
IMG_PATH  = 'images/pie.png'

H = {
    'Authorization': f'Bearer {NOTION_TOKEN}',
    'Notion-Version': NOTION_VERSION,
    'Content-Type': 'application/json',
}

KRX_CATS = {'국내종목', '국내ETF', '국내ETF-해외'}

COUNTRY_MAP = {
    '국내종목': '한국',
    '국내ETF': '한국',
    '국내ETF-해외': '한국 (해외추종)',
    '해외종목': '미국',
    '해외ETF': '미국',
}


def query_ds(ds_id):
    out, cursor = [], None
    while True:
        body = {'page_size': 100}
        if cursor:
            body['start_cursor'] = cursor
        r = requests.post(f'{API}/data_sources/{ds_id}/query', headers=H, json=body)
        r.raise_for_status()
        d = r.json()
        out.extend(d['results'])
        if not d.get('has_more'):
            break
        cursor = d['next_cursor']
    return out


def create_page(ds_id, props):
    r = requests.post(
        f'{API}/pages',
        headers=H,
        json={'parent': {'type': 'data_source_id', 'data_source_id': ds_id}, 'properties': props},
    )
    r.raise_for_status()
    return r.json()


def update_page(page_id, props):
    r = requests.patch(f'{API}/pages/{page_id}', headers=H, json={'properties': props})
    r.raise_for_status()
    return r.json()


def list_children(block_id):
    out, cursor = [], None
    while True:
        params = {'page_size': 100}
        if cursor:
            params['start_cursor'] = cursor
        r = requests.get(f'{API}/blocks/{block_id}/children', headers=H, params=params)
        r.raise_for_status()
        d = r.json()
        out.extend(d['results'])
        if not d.get('has_more'):
            break
        cursor = d['next_cursor']
    return out


def title(s):     return {'title': [{'text': {'content': str(s)}}]}
def rich(s):      return {'rich_text': [{'text': {'content': str(s)}}]}
def num(v):       return {'number': float(v) if v is not None else None}
def select(name): return {'select': {'name': name}} if name else {'select': None}


def get_text(prop):
    if not prop:
        return ''
    t = prop.get('type')
    if t == 'title':     return ''.join(x['plain_text'] for x in prop['title'])
    if t == 'rich_text': return ''.join(x['plain_text'] for x in prop['rich_text'])
    if t == 'select':    return prop['select']['name'] if prop['select'] else ''
    if t == 'number':    return prop['number']
    if t == 'date':      return prop['date']['start'] if prop['date'] else ''
    return ''


def last_krx_price(ticker):
    today = date.today()
    df = stock.get_market_ohlcv_by_date(
        (today - timedelta(days=14)).strftime('%Y%m%d'),
        today.strftime('%Y%m%d'),
        ticker,
    )
    return None if df.empty else float(df['종가'].iloc[-1])


def last_yf_price(ticker):
    h = yf.Ticker(ticker).history(period='5d')
    return None if h.empty else float(h['Close'].iloc[-1])


def aggregate_trades():
    trades = query_ds(DS_TRADE)
    agg = defaultdict(lambda: {'name': '', 'category': '', 'qty': 0.0, 'buy_qty': 0.0, 'buy_amt': 0.0})
    for p in trades:
        pp = p['properties']
        ticker = get_text(pp.get('티커'))
        if not ticker:
            continue
        side = get_text(pp.get('매수/매도'))
        qty = float(get_text(pp.get('수량')) or 0)
        price = float(get_text(pp.get('단가')) or 0)
        a = agg[ticker]
        a['name'] = get_text(pp.get('종목이름'))
        a['category'] = get_text(pp.get('분류'))
        if side == '매수':
            a['qty'] += qty
            a['buy_qty'] += qty
            a['buy_amt'] += qty * price
        elif side == '매도':
            a['qty'] -= qty
    holdings = {}
    for ticker, a in agg.items():
        if a['qty'] <= 0:
            continue
        avg = a['buy_amt'] / a['buy_qty'] if a['buy_qty'] else 0
        holdings[ticker] = {**a, 'avg_price': avg}
    return holdings


def enrich_with_prices(holdings):
    for ticker, h in holdings.items():
        try:
            price = last_krx_price(ticker) if h['category'] in KRX_CATS else last_yf_price(ticker)
        except Exception as e:
            print(f'  WARN {ticker} 시세 실패: {e}')
            price = None
        h['current'] = price or h['avg_price']
        h['valuation'] = h['qty'] * h['current']
        h['profit'] = (h['current'] - h['avg_price']) * h['qty']
        h['profit_rate'] = h['profit'] / (h['avg_price'] * h['qty']) if h['avg_price'] else 0


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


def upsert_holdings(holdings):
    existing = query_ds(DS_HOLDING)
    by_ticker = {get_text(p['properties'].get('티커')): p['id'] for p in existing}
    for ticker, h in holdings.items():
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
        if h['category']:
            props['분류'] = select(h['category'])
        if ticker in by_ticker:
            update_page(by_ticker[ticker], props)
            print(f'  UPDATE {ticker} {h["name"]}')
        else:
            create_page(DS_HOLDING, props)
            print(f'  CREATE {ticker} {h["name"]}')


def upsert_asset(holdings):
    total_val = sum(h['valuation'] for h in holdings.values())
    total_buy = sum(h['avg_price'] * h['qty'] for h in holdings.values())
    total_pl = total_val - total_buy
    total_pr = total_pl / total_buy if total_buy else 0
    today_str = date.today().isoformat()
    asset_rows = query_ds(DS_ASSET)
    today_id = next(
        (p['id'] for p in asset_rows if get_text(p['properties'].get('작성일자')) == today_str),
        None,
    )
    props = {
        '작성일자':   title(today_str),
        '총평가금액': num(total_val),
        '총수익':     num(total_pl),
        '총수익률':   num(total_pr),
    }
    if today_id:
        update_page(today_id, props)
        print(f'  UPDATE 총자산 {today_str} W{int(total_val):,}')
    else:
        create_page(DS_ASSET, props)
        print(f'  CREATE 총자산 {today_str} W{int(total_val):,}')


def render_pie(holdings, out_path):
    font_candidates = [
        '/usr/share/fonts/truetype/nanum/NanumGothic.ttf',
        '/System/Library/Fonts/Supplemental/AppleGothic.ttf',
        '/Library/Fonts/AppleGothic.ttf',
    ]
    for fp in font_candidates:
        if os.path.exists(fp):
            fm.fontManager.addfont(fp)
            font_name = fm.FontProperties(fname=fp).get_name()
            plt.rcParams['font.family'] = font_name
            break
    plt.rcParams['axes.unicode_minus'] = False

    cat_total = defaultdict(float)
    for h in holdings.values():
        cat_total[h['category'] or '미분류'] += h['valuation']
    labels = list(cat_total.keys())
    sizes = list(cat_total.values())
    colors = ['#4C72B0', '#55A868', '#C44E52', '#8172B2', '#CCB974']

    fig, ax = plt.subplots(figsize=(8, 8))
    _, _, autotexts = ax.pie(
        sizes, labels=labels, autopct='%1.1f%%',
        startangle=90, colors=colors[: len(labels)],
        textprops={'fontsize': 13},
    )
    for at in autotexts:
        at.set_color('white')
        at.set_fontweight('bold')
    ax.set_title(f'보유주식 분류별 비율 ({date.today().isoformat()})', fontsize=16, pad=20)
    plt.tight_layout()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    plt.savefig(out_path, bbox_inches='tight', dpi=120, facecolor='white')
    plt.close(fig)
    for label, size in zip(labels, sizes):
        print(f'  {label}: W{int(size):,} ({size / sum(sizes) * 100:.1f}%)')


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


def fetch_6m_close_krx_stock(ticker):
    end = date.today()
    start = end - timedelta(days=210)
    df = stock.get_market_ohlcv_by_date(start.strftime('%Y%m%d'), end.strftime('%Y%m%d'), ticker)
    return df['종가']


def fetch_6m_close_krx_index(code):
    end = date.today()
    start = end - timedelta(days=210)
    df = stock.get_index_ohlcv_by_date(start.strftime('%Y%m%d'), end.strftime('%Y%m%d'), code)
    return df['종가']


def fetch_6m_close_yf(ticker):
    return yf.Ticker(ticker).history(period='7mo')['Close']


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


def fetch_news_for_holdings(holdings):
    """보유 종목 중 미국 ticker만 yfinance.news fetch. 최대 5건."""
    news = []
    seen = set()
    for ticker, h in holdings.items():
        if not ticker.isalpha():
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


def month_end_series(series):
    s = series.copy()
    s.index = pd.to_datetime(s.index)
    return s.resample('ME').last().dropna().tail(7)


def cum_returns(monthly_close):
    base = monthly_close.iloc[0]
    return [(v / base - 1) for v in monthly_close]


def render_idx_chart(idx_name, stocks_monthly, idx_monthly, out_path):
    fig, ax = plt.subplots(figsize=(11, 6))
    months = [d.strftime('%Y-%m') for d in idx_monthly.index]
    idx_cum = [r * 100 for r in cum_returns(idx_monthly)]
    ax.plot(months, idx_cum, 'k-', linewidth=3.5, marker='o',
            markersize=8, label=f'[지수] {idx_name}')
    for stock_name, series in stocks_monthly.items():
        cum = [r * 100 for r in cum_returns(series)]
        ax.plot(months, cum, '-o', label=stock_name, linewidth=1.8, markersize=5)
    ax.set_title(f'{idx_name} 기준 6개월 누적 수익률 ({date.today().isoformat()})',
                 fontsize=15, pad=15)
    ax.set_xlabel('월')
    ax.set_ylabel('누적 수익률 (%)')
    ax.legend(loc='best', fontsize=9, ncol=2)
    ax.grid(True, alpha=0.3)
    ax.axhline(0, color='gray', linewidth=0.8, linestyle='--')
    plt.xticks(rotation=45)
    plt.tight_layout()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    plt.savefig(out_path, dpi=120, bbox_inches='tight', facecolor='white')
    plt.close(fig)


def analyze_watchlist():
    rows = query_ds(DS_WATCH)
    by_index = defaultdict(list)
    for p in rows:
        pp = p['properties']
        name = get_text(pp.get('종목이름'))
        ticker = get_text(pp.get('티커'))
        idx = get_text(pp.get('기준지수'))
        if not (name and ticker and idx):
            continue
        by_index[idx].append({'page_id': p['id'], 'name': name, 'ticker': ticker})
    return by_index


def run_index_analysis(by_index):
    results = []
    for idx_name, stocks in by_index.items():
        if idx_name not in INDEX_MAP:
            continue
        meta = INDEX_MAP[idx_name]
        try:
            if meta['src'] == 'krx_stock':
                idx_series = fetch_6m_close_krx_stock(meta['code'])
            elif meta['src'] == 'krx_index':
                idx_series = fetch_6m_close_krx_index(meta['code'])
            else:
                idx_series = fetch_6m_close_yf(meta['code'])
        except Exception as e:
            print(f'  WARN 지수 {idx_name} fetch 실패: {e}')
            continue
        idx_monthly = month_end_series(idx_series)
        if len(idx_monthly) < 2:
            print(f'  WARN 지수 {idx_name} 월별 데이터 부족')
            continue
        idx_6m = cum_returns(idx_monthly)[-1]
        stocks_monthly = {}
        for s in stocks:
            try:
                if idx_name == '코스피200':
                    series = fetch_6m_close_krx_stock(s['ticker'])
                else:
                    series = fetch_6m_close_yf(s['ticker'])
                monthly = month_end_series(series)
                if len(monthly) < 2:
                    continue
                stocks_monthly[s['name']] = monthly
                stock_6m = cum_returns(monthly)[-1]
                diff = stock_6m - idx_6m
                judgement = '손절 검토' if diff <= -0.10 else '유지'
                results.append({
                    'page_id': s['page_id'],
                    'name': s['name'],
                    'ticker': s['ticker'],
                    'stock_6m': stock_6m,
                    'idx_6m': idx_6m,
                    'diff': diff,
                    'judgement': judgement,
                })
            except Exception as e:
                print(f'  WARN {s["ticker"]} 분석 실패: {e}')
        render_idx_chart(idx_name, stocks_monthly, idx_monthly, meta['img'])
    return results


def upsert_watchlist_results(results):
    for r in results:
        props = {
            '6M수익률':     num(r['stock_6m']),
            '지수6M수익률': num(r['idx_6m']),
            '차이':         num(r['diff']),
            '판정':         select(r['judgement']),
        }
        update_page(r['page_id'], props)
        print(f"  {r['ticker']:8} {r['name']:12} 6M={r['stock_6m']*100:+6.1f}% "
              f"diff={r['diff']*100:+6.1f}% [{r['judgement']}]")


def update_notion_idx_images(timestamp):
    children = list_children(PAGE_ID)
    matched = None
    targets = {}
    for b in children:
        if b['type'] == 'heading_3':
            text = ''.join(t['plain_text'] for t in b['heading_3']['rich_text'])
            matched = None
            for idx_name, meta in INDEX_MAP.items():
                if meta['heading'] in text:
                    matched = idx_name
                    break
        elif matched and b['type'] == 'image':
            targets[matched] = b['id']
            matched = None
    for idx_name, block_id in targets.items():
        url = (f'https://raw.githubusercontent.com/{GH_REPO}/{GH_BRANCH}/'
               f'{INDEX_MAP[idx_name]["img"]}?t={timestamp}')
        r = requests.patch(
            f'{API}/blocks/{block_id}', headers=H,
            json={'image': {'external': {'url': url}}},
        )
        r.raise_for_status()
        print(f'  {idx_name} image patched: {block_id}')


def list_children_recursive(block_id, max_depth=3, _depth=0):
    """페이지 children 재귀 fetch — columns/column 안 children 평탄화."""
    out = []
    for b in list_children(block_id):
        out.append(b)
        if _depth < max_depth and b.get('has_children') and b.get('type') in ('column_list', 'column'):
            out.extend(list_children_recursive(b['id'], max_depth, _depth + 1))
    return out


def find_blocks_by_keyword(children):
    """children 리스트 → 키워드 매칭으로 callout/image/news paragraph block ID 추출."""
    metrics = {}
    goal_id = None
    images = {}
    news_para_ids = []
    cur_heading = None
    in_news = False
    for b in children:
        t = b['type']
        if t == 'callout':
            text = ''.join(x['plain_text'] for x in b['callout']['rich_text'])
            matched = False
            for kw, key in METRIC_KEYS.items():
                if kw in text:
                    metrics[key] = b['id']
                    matched = True
                    break
            if not matched and GOAL_KEY in text:
                goal_id = b['id']
        elif t in ('heading_2', 'heading_3'):
            txt = ''.join(x['plain_text'] for x in b[t]['rich_text'])
            cur_heading = next((p for kw, p in IMAGE_HEADINGS.items() if kw in txt), None)
            in_news = NEWS_HEADING in txt
        elif t == 'image' and cur_heading:
            images[cur_heading] = b['id']
            cur_heading = None
        elif t == 'paragraph' and in_news:
            news_para_ids.append(b['id'])
    return metrics, goal_id, images, news_para_ids


def update_notion_image(img_url):
    children = list_children(PAGE_ID)
    target = None
    after_heading = False
    for b in children:
        if b['type'] == 'heading_2':
            text = ''.join(t['plain_text'] for t in b['heading_2']['rich_text'])
            after_heading = '분류별 비율' in text
        elif after_heading and b['type'] == 'image':
            target = b['id']
            break
    if not target:
        raise RuntimeError('"분류별 비율" 헤딩 다음 image 블록 없음.')
    r = requests.patch(
        f'{API}/blocks/{target}',
        headers=H,
        json={'image': {'external': {'url': img_url}}},
    )
    r.raise_for_status()
    print(f'  Notion image patched: {target}')


def main():
    print('[1] 매매일지 집계')
    holdings = aggregate_trades()
    print(f'  종목 {len(holdings)}개')

    print('[2] 시세 fetch')
    enrich_with_prices(holdings)

    print('[3] 보유주식 UPSERT')
    upsert_holdings(holdings)

    print('[4] 총자산 UPSERT')
    upsert_asset(holdings)

    print('[5] 파이차트 생성')
    render_pie(holdings, IMG_PATH)

    print('[6] Notion 이미지 URL 갱신')
    ts = int(time.time())
    img_url = f'https://raw.githubusercontent.com/{GH_REPO}/{GH_BRANCH}/{IMG_PATH}?t={ts}'
    update_notion_image(img_url)

    print('[7] 관심종목 6개월 분석 + 지수 차트 생성')
    by_index = analyze_watchlist()
    results = run_index_analysis(by_index)
    upsert_watchlist_results(results)

    print('[8] 지수 차트 Notion 갱신')
    update_notion_idx_images(ts)

    print('done.')


if __name__ == '__main__':
    main()
