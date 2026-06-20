# tg_value
TG's stock watchlist valuation

## 개요

`tg_stock_watchlist_value.py` — Google Sheets "태밸류 미주은 공유용" 문서의 Watchlist
종목들을 한 번에 순회하며 **연간 EPS 예상치(Y / Y+1 / Y+2)** 를 자동으로 채워 넣는
Python 스크립트.

- **대상 시트:** Google Sheets `GOOGLE_DOC_SHEET_FILE_TGVAL_KEY` (`태밸류` 탭)
- **인증:** Google Cloud 서비스 계정 JSON 키 (`gspread` + `oauth2client`)
- **갱신 대상 열:** O(EPS Y), R(EPS Y+1), V(EPS Y+2) — 이 3개 열만 자동으로 덮어씀
- **데이터 출처:**
  - **국장(KR):** `FinanceDataReader`(NAVER 재무 데이터) — 웹 크롤링 없음, 빠름
  - **미장(US):** Finviz 연간 Estimate 표 — selenium(headless Chrome)로 수집

### 동작 흐름

1. `.env` 로드 → 서비스 계정으로 Google Sheets 인증
2. `태밸류` 탭에서 `Stock` 헤더(B열) 행을 찾아, 그 다음 행부터 종목 순회
3. C열 티커로 시장 판별:
   - `KRX:` / `KOSPI:` / `KOSDAQ:` 접두사 → **국장**, 접두사 없음 → **미장**
4. 시장별 EPS 수집:
   - 국장: `fdr.SnapDataReader('NAVER/FINSTATE/<코드>')`의 `EPS(원)` 연도별 값(정수)
   - 미장: Finviz `quote.ashx?...&ty=ea` 에서 'Annual' 토글 클릭 후 non-GAAP
     'EPS Performance and Forecast' 표의 Estimate 행(달러, 소수)
5. 미장은 Chrome 드라이버 **1개를 재사용**(종목마다 URL만 변경), 요청 간 3초 간격
   - Finviz의 광고/트래커 스크립트가 렌더러를 점유해 selenium이 간헐 크래시하던 문제를
     `--host-resolver-rules`로 **finviz.com 외 도메인 DNS 차단**해 해결(로드도 빨라짐)
6. 조회 실패 시 드라이버 재생성 후 1회 재시도, 그래도 실패하면 해당 셀은 건너뜀(기존값 유지)
7. 전 종목 수집 후 `batch_update`(RAW)로 O/R/V 셀 일괄 기록 — 기존 셀 서식(콤마·소수) 보존
8. 종료 시 chromedriver 프로세스 트리를 PID 타겟으로 정리(사용자가 연 Chrome은 안 건드림)

> 적정PER(M열), ROE(J), 실적기준(N) 등 나머지 항목은 여전히 수동 입력. 이 스크립트는
> **EPS 3개년 값만** 자동화한다.

---

## 사용법

```bash
cd d:\TG\develop\tg_value
python tg_stock_watchlist_value.py              # 전체 Watchlist EPS 갱신 (INFO, headless)
python tg_stock_watchlist_value.py --name NVDA  # 지정 종목 한 개만 갱신 (테스트용)
python tg_stock_watchlist_value.py --debug      # DEBUG 로그 + selenium 창 표시(headed)

# 콘솔 출력 + 파일 저장 동시에 (PowerShell)
python tg_stock_watchlist_value.py 2>&1 | Tee-Object full_run.log
```

### 의존성 설치

```bash
pip install -r requirements.txt
```

| 인자 | 설명 |
|------|------|
| (없음) | 전체 종목 갱신. INFO 로그, selenium은 headless로 실행 |
| `--name <종목명\|티커>` | 해당 종목 한 개만 갱신 (값 검증·디버깅용) |
| `--debug` | DEBUG 레벨 로그 출력 + selenium 브라우저 창을 띄워 실제 동작 확인 |

### 자동 실행 (GitHub Actions)

`.github/workflows/weekly-eps.yml` — **매주 일요일 06:00 KST**(=토 21:00 UTC, cron `0 21 * * 6`)
자동 실행. Actions 탭에서 수동 실행(`workflow_dispatch`)도 가능.

실행 전 GitHub 리포 **Settings → Secrets and variables → Actions** 에 아래 3개 secret 등록 필요:

| Secret 이름 | 값 |
|------------|----|
| `GOOGLE_CLOUD_KEY_JSON` | 서비스 계정 JSON 키 **파일 전체 내용** (`*.json`) |
| `GOOGLE_DOC_SHEET_FILE_TGVAL_KEY` | Google Sheets 파일 ID |
| `OPENDART_API_KEY` | OpenDART API 키 |

> 워크플로가 `GOOGLE_CLOUD_KEY_JSON`을 `keyfile.json`으로 복원하고, 실행 후 삭제한다.
> `.json` 키 파일과 `.env`는 `.gitignore`로 리포에 올라가지 않으므로 secret으로 주입한다.

### 콘솔 처리 현황

실행하면 종목마다 한 줄씩 실시간으로 진행 상황이 콘솔(stderr)에 출력된다:

```
13:49:38 [INFO] 'Stock' 헤더 행: 3, 데이터 시작 행: 4
13:49:38 [INFO] [4] LG전자        (KR:066575)  Y=10858 Y1=13311 Y2=17239
13:53:11 [INFO] [26] PLTR         (US:PLTR)  Y=1.46 Y1=2.08 Y2=3.06
...
13:57:08 [INFO] 처리 종목: KR=.. US=.., 전체실패(skip)=..
13:57:08 [INFO] N개 셀 갱신 완료 (Updated on YYYY-MM-DD HH:MM:SS KST)
```

- 형식: `[행번호] 종목명 (시장:코드)  Y=.. Y1=.. Y2=..`
- 조회 실패·재시도는 `[WARNING]`, 마지막에 처리 요약과 갱신 셀 수 출력
- selenium/urllib3 내부 로그는 WARNING으로 억제돼 화면이 깔끔함

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
| O | EPS(Y0) | **자동** | 현재 연도(Y) 연간 EPS — 스크립트가 갱신 |
| P | 주가(Y0) | 자동 | `=M*O` — 목표주가 (적정PER × EPS Y0) |
| Q | 수익률(Y0) | 자동 | `=P/D-1` — Y0 목표주가 기준 기대수익률 |
| R | EPS(Y+1) | **자동** | 내년(Y+1) EPS 예상치 — 스크립트가 갱신 |
| S | 주가(Y+1) | 자동 | `=M*R` — Y+1 목표주가 |
| T | 수익률(Y+1) | 자동 | `=S/D-1` — Y+1 목표주가 기준 기대수익률 |
| U | Y+1성장 | 자동 | `=R/O-1` — EPS 성장률 (Y0→Y+1) |
| V | EPS(Y+2) | **자동** | 내후년(Y+2) EPS 예상치 — 스크립트가 갱신 |
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
- **자동 (이 스크립트):** EPS Y0/Y+1/Y+2 (O/R/V) — KR=FinanceDataReader, US=Finviz
- **수동 입력 필요:** 티커(C), 적정PER(M), 실적기준(N), ROE(J), PEG(K)
- **자동 계산 (수식):** 목표주가 3개년(P/S/W), 기대수익률 3개년(Q/T/X), EPS 성장률(U/Y), 성장가속비교(I)

---

## 환경 설정

`.env` 참고 (실제 값은 `.env` 파일에만 기재):
```
GOOGLE_CLOUD_KEY_PATH=<서비스계정 JSON 키 파일>
GOOGLE_DOC_SHEET_FILE_TGVAL_KEY=<Google Sheets 파일 ID>
OPENDART_API_KEY=<OpenDART API 키>
```
