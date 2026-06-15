# Bob Portfolio

Notion 기반 개인 주식 포트폴리오 자동화. 매일 1회 GitHub Actions가 매매일지를 읽어 보유주식·총자산·분류 파이차트를 갱신한다.

## 구조

```
.
├── update_portfolio.py        # 메인 스크립트
├── requirements.txt
├── images/pie.png             # 매일 갱신되는 분류별 비율 차트
├── .github/workflows/daily.yml
└── bob_portfolio.ipynb        # 수동 디버깅용 Colab 노트북 (선택)
```

## 데이터 흐름

```
[매매일지 DB]
   │ 티커별 집계 (수량/가중평균 매입가)
   ▼
[보유주식 DB]  ←──┐ 시세 갱신 (pykrx / yfinance)
   │              │
   │ 합계
   ▼              │
[총자산 DB]       │
   │              │
   └──── 분류별 평가금액 ──→ pie chart → images/pie.png
                                          │
                                          ▼
                              git push → GitHub raw URL
                                          │
                                          ▼
                              Notion 이미지 블록 PATCH
                              (URL 끝에 ?t={ts} 캐시 무효)
```

## 사전 준비

### 1. Notion Integration

1. https://www.notion.so/profile/integrations → `New integration` (Internal)
2. Capabilities: Read/Update/Insert content 모두 체크
3. Secret token 복사 (`ntn_...`)
4. Bob Portfolio 페이지 우상단 `⋯` → `Connections` → 만든 integration 추가

### 2. GitHub Repo

1. 이 폴더를 GitHub repo로 push (예: `bobbohee/bob-portfolio`)
2. 비공개 추천 (포트폴리오 정보가 raw URL에 노출됨)
3. Repo Settings → Secrets and variables → Actions → `New repository secret`
   - Name: `NOTION_TOKEN`
   - Value: 위 1.3에서 받은 토큰
4. Settings → Actions → General → Workflow permissions → `Read and write permissions` 체크 (git push 허용)

### 3. update_portfolio.py 상수 확인

`PAGE_ID`, `DS_TRADE`, `DS_HOLDING`, `DS_ASSET`은 이미 Bob Portfolio용으로 하드코딩됨. 다른 워크스페이스 사용 시 교체.

## 실행

### 자동 (GitHub Actions)

`.github/workflows/daily.yml`이 매일 09:00 UTC (18:00 KST)에 cron 트리거. 한국장 마감(15:30) + 미국장 직전 종가 기준.

### 수동

GitHub Actions 페이지에서 `Daily Portfolio Update` → `Run workflow` 클릭.

또는 로컬에서:

```bash
export NOTION_TOKEN=ntn_...
python update_portfolio.py
```

## 절대 규칙

- Notion 페이지 **블록은 절대 삭제 금지**. 스크립트는 row UPSERT + image 블록 URL PATCH만 수행.
- 보유주식 / 총자산 row는 (티커 / 작성일자) 키로 idempotent UPSERT → 재실행해도 중복 없음.

## 데이터 모델

| DB | 키 | 자동 갱신 컬럼 |
|---|---|---|
| 매매일지 | 사용자 직접 등록 | (없음 — input) |
| 보유주식 | 티커 | 평가금액, 수익, 수익률, 보유수량, 매입가, 분류 |
| 총자산 | 작성일자 | 총평가금액, 총수익, 총수익률 |

## 트러블슈팅

- **404 query 실패** → integration이 Bob Portfolio 페이지에 connect 안 됨. 페이지 ⋯ → Connections 확인.
- **이미지 안 바뀜** → raw URL 캐시. `?t={timestamp}` 쿼리로 강제 무효화하지만 Notion 클라이언트 캐시 30초 정도 잔존 가능.
- **Actions cron 미실행** → repo가 60일 무활동 시 GitHub cron 일시정지. 수동 `Run workflow` 1회로 복구.
- **pykrx 시세 None** → 휴장일 또는 데이터 미반영. 매입가로 fallback.
