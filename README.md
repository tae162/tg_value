# tg_value
TG's stock watchlist valuation

## 개요

`tg_stock_watchlist_value.py` — Google Sheets "태밸류 미주은 공유용" 문서를 읽어  
주시 종목의 밸류에이션 항목(EPS, 적정PER 등)을 자동으로 업데이트하는 Python 스크립트.

- **대상 시트:** Google Sheets `GOOGLE_DOC_SHEET_FILE_TGVAL_KEY` (`태밸류` 탭)
- **인증:** Google Cloud 서비스 계정 JSON 키 (`gspread` + `oauth2client`)
- **주요 동작:** 종목별 실적 EPS(Y0/Y+1/Y+2)와 적정PER을 naver/finviz 등에서 수집해 시트에 업데이트

---

## 태밸류 시트 구조

### 메타 정보 (1~2행)
| 행 | 내용 |
|----|------|
| 1행 | 시트명("태밸류"), 지표 출처("지표: naver, finviz") |
| 2행 | 총 종목 수, 최종 업데이트 날짜 |

### 컬럼 구성 (3행 헤더, 4행~: 종목 데이터)

| 열 | 헤더 | 입력 방식 | 설명 |
|----|------|-----------|------|
| A | (순위) | 수동 | Y+2 기대수익률 기준 정렬 순위 |
| B | Stock | 수동 | 종목명 |
| C | (ticker) | 수동 | GOOGLEFINANCE 티커 (예: `KRX:066575`, `APP`) |
| D | 주가 | 자동 | `=GOOGLEFINANCE(C, "price")` — 실시간 현재 주가 |
| E | 하루 | 자동 | `=GOOGLEFINANCE(C, "changepct")/100` — 당일 등락률 |
| F | 한달 | 자동 | `=SPARKLINE(GOOGLEFINANCE(..., TODAY()-30, TODAY()))` — 30일 스파크라인 |
| G | 고점대비 | 자동 | `=(D - GOOGLEFINANCE(C,"high52")) / GOOGLEFINANCE(C,"high52")` — 52주 고점 대비 하락률 |
| H | Y2 | 자동 | `=X` — Y+2 기대수익률 참조 (정렬 기준 키) |
| I | Y1 > Y2 | 자동 | EPS 성장률 가속/감속 비교 (`U+5%< Y` 이면 `<`, 아니면 `>`) |
| J | ROE | 수동 | 자기자본이익률 (%) |
| K | PEG | 수동 | PEG ratio (선택적 입력) |
| L | PER | 자동 | `=GOOGLEFINANCE(C, "pe")` — 실시간 PER (참고용) |
| M | PER | **수동** | **적정 PER** — 이하 목표주가 계산의 기준 |
| N | 실적 | 수동 | EPS 기준 최신 실적 분기 (예: `26.Q1`) |
| O | EPS(Y0) | **수동** | 현재 기준 연간 EPS |
| P | 주가(Y0) | 자동 | `=M*O` — 목표주가 (적정PER × EPS Y0) |
| Q | 수익률(Y0) | 자동 | `=P/D-1` — Y0 목표주가 기준 기대수익률 |
| R | EPS(Y+1) | **수동** | 내년 EPS 예상치 |
| S | 주가(Y+1) | 자동 | `=M*R` — Y+1 목표주가 |
| T | 수익률(Y+1) | 자동 | `=S/D-1` — Y+1 목표주가 기준 기대수익률 |
| U | Y+1성장 | 자동 | `=R/O-1` — EPS 성장률 (Y0→Y+1) |
| V | EPS(Y+2) | **수동** | 내후년 EPS 예상치 |
| W | 주가(Y+2) | 자동 | `=M*V` — Y+2 목표주가 |
| X | 수익률(Y+2) | 자동 | `=W/D-1` — Y+2 목표주가 기준 기대수익률 |
| Y | Y+2성장 | 자동 | `=V/R-1` — EPS 성장률 (Y+1→Y+2) |

### 밸류에이션 핵심 공식

```
목표주가 = 적정PER(M열, 수동) × EPS(수동)

예) LG전자: 적정PER 11 × EPS 10,858 = 119,438원  → 현재 72,000 대비 +65.9%
           적정PER 11 × EPS 13,311 = 146,421원  → 현재 72,000 대비 +103.4%  (Y+1)
           적정PER 11 × EPS 17,239 = 189,629원  → 현재 72,000 대비 +163.4%  (Y+2)
```

### 자동 계산 vs 수동 입력 요약

- **자동 (GOOGLEFINANCE):** 현재 주가, 당일 등락, 30일 추이, 52주 고점, 실시간 PER
- **수동 입력 필요:** 티커(C), 적정PER(M), 실적기준(N), EPS Y0/Y+1/Y+2(O/R/V), ROE(J), PEG(K)
- **자동 계산 (수식):** 목표주가 3개년(P/S/W), 기대수익률 3개년(Q/T/X), EPS 성장률(U/Y), 성장가속비교(I)

---

## 환경 설정

`.env` 참고 (실제 값은 `.env` 파일에만 기재):
```
GOOGLE_CLOUD_KEY_PATH=<서비스계정 JSON 키 파일>
GOOGLE_DOC_SHEET_FILE_TGVAL_KEY=<Google Sheets 파일 ID>
OPENDART_API_KEY=<OpenDART API 키>
```
