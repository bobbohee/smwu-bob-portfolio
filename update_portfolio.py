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

GH_REPO   = os.environ.get('GH_REPO',   'bobbohee/smwu-bob-portfolio')
GH_BRANCH = os.environ.get('GH_BRANCH', 'main')
IMG_PATH  = 'images/pie.png'

H = {
    'Authorization': f'Bearer {NOTION_TOKEN}',
    'Notion-Version': NOTION_VERSION,
    'Content-Type': 'application/json',
}

KRX_CATS = {'국내종목', '국내ETF', '국내ETF-해외'}


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

    print('done.')


if __name__ == '__main__':
    main()
