import os
import time
import logging
import argparse
import subprocess
from datetime import datetime

import pytz
import numpy as np
import pandas as pd

# Google spreadsheet
from oauth2client.service_account import ServiceAccountCredentials
import gspread
from gspread.exceptions import APIError

# 국장 EPS (크롤링 없이 NAVER/WiseReport 재무 데이터)
import FinanceDataReader as fdr

# 미장 EPS (Finviz 연간 Estimate, selenium)
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

from dotenv import load_dotenv

load_dotenv()

GOOGLE_CLOUD_KEY_PATH = os.getenv("GOOGLE_CLOUD_KEY_PATH")
GOOGLE_DOC_SHEET_FILE_TGVAL_KEY = os.getenv("GOOGLE_DOC_SHEET_FILE_TGVAL_KEY")

# 대상 워크시트(탭) 이름
WORKSHEET_NAME = "태밸류"
# B열 종목 목록 시작을 알리는 헤더 텍스트
STOCK_HEADER = "Stock"

logger = logging.getLogger("watchlist_eps")


# ---------------------------------------------------------------------------
# 로깅 설정: 기본 INFO, debug=True 시 DEBUG (selenium 상세 로그는 WARNING로 억제)
# ---------------------------------------------------------------------------
def setup_logging(debug=False):
    # set_timeout() DeprecationWarning 억제 (selenium 4.45에서 client_config kwarg
    # 미지원이라 set_timeout이 유일한 방법 — 경고만 끔)
    import warnings
    warnings.filterwarnings("ignore", message=r".*set_timeout.*deprecated.*")

    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    # selenium/urllib3 내부 디버그 로그는 과도하므로 억제
    logging.getLogger("selenium").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# 연도 변수: 올해 Y, 내년 Y1, 내후년 Y2
# ---------------------------------------------------------------------------
def get_year_vars():
    y = datetime.now().year
    return y, y + 1, y + 2


# 연도 → 엑셀 열 매핑 (1-based: A=1). O=15(Y), R=18(Y1), V=22(Y2)
def get_year_col_map(y, y1, y2):
    return {y: 15, y1: 18, y2: 22}


# ---------------------------------------------------------------------------
# C열 종목코드 파싱
#   'KRX:005930'    -> ('KR', '005930')
#   'KOSDAQ:214150' -> ('KR', '214150')
#   'NVDA'          -> ('US', 'NVDA')
#   ''              -> None
# ---------------------------------------------------------------------------
def parse_stock_code(c_value):
    c = (c_value or "").strip()
    if not c:
        return None
    upper = c.upper()
    for prefix in ("KRX:", "KRS:", "KOSPI:", "KOSDAQ:"):
        if upper.startswith(prefix):
            return ("KR", c.split(":", 1)[1].strip())
    return ("US", c)


# ---------------------------------------------------------------------------
# 국장 EPS 예상치 (원, 정수) — 웹 크롤링 없음
#   fdr.SnapDataReader('NAVER/FINSTATE/<code>')의 연도 인덱스 df에서 'EPS(원)' 추출
#   tg_stock/tg_stock_kor_value.py::get_kor_ticker_value 패턴 참고
#   return: {year: int or None}
# ---------------------------------------------------------------------------
def get_kor_eps_forecast(code, years):
    def get_snap(query, retries=3, delay=1):
        df = None
        for attempt in range(retries):
            try:
                df = fdr.SnapDataReader(query)
                if df is not None and not df.empty:
                    return df
            except Exception as e:
                logger.debug("SnapDataReader 오류(%s) 시도 %d: %s", code, attempt + 1, e)
            time.sleep(delay)
        return df

    result = {y: None for y in years}
    df = get_snap(f"NAVER/FINSTATE/{code}")
    if df is None or df.empty or "EPS(원)" not in df.columns:
        logger.warning("[KR %s] EPS 데이터 없음 -> skip", code)
        return result

    for y in years:
        s = df[df.index.year == y]["EPS(원)"]
        if s.empty:
            logger.debug("[KR %s] %d년 EPS 행 없음", code, y)
            continue
        val = pd.to_numeric(s.iloc[0], errors="coerce")
        if val is None or pd.isna(val) or np.isinf(val):
            logger.debug("[KR %s] %d년 EPS 값 무효: %r", code, y, s.iloc[0])
            continue
        result[y] = int(round(val))
    return result


# ---------------------------------------------------------------------------
# selenium Chrome 드라이버 생성
#   headless=True  : INFO 레벨 기본 — 창 없이 실행
#   headless=False : DEBUG 레벨 — selenium 윈도우 표시
#   Finviz가 headless 봇을 감지·차단하므로 anti-detection 옵션 항상 적용
# ---------------------------------------------------------------------------
def make_chrome_driver(headless=True):
    o = Options()
    flags = [
        "--no-sandbox",  # CI(GitHub Actions Linux) headless chrome 구동에 필요
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--disable-software-rasterizer",
        "--disable-extensions",
        "--disable-background-timer-throttling",
        "--blink-settings=imagesEnabled=false",
        "--window-size=1400,1000",
        "--disable-blink-features=AutomationControlled",
        # Finviz 광고/트래커(prebid·pubmatic·yieldmo·googletag 등) 차단.
        #   finviz.com 외 모든 호스트 DNS 해석 실패 -> 광고 스크립트 0.
        #   이 ad-tech가 렌더러를 점유해 navigate가 멈추면 chromedriver 명령이
        #   stall/reset 되며 세션이 죽는 것이 간헐적 크래시(ReadTimeout/10054)의 원인.
        #   부수효과: 페이지 로드/파싱 속도 향상.
        "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE finviz.com",
    ]
    if headless:
        flags.insert(0, "--headless=new")
    for f in flags:
        o.add_argument(f)
    o.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    )
    o.add_experimental_option("excludeSwitches", ["enable-automation"])
    o.add_experimental_option("useAutomationExtension", False)
    o.page_load_strategy = "eager"

    d = webdriver.Chrome(options=o)
    # page_load_timeout(20) < client/command timeout(60): chromedriver가 자체 page
    # timeout으로 깔끔히 반환하도록 둘이 같지 않게 함(같으면 client read가 먼저 터져
    # ReadTimeout으로 세션이 깨질 수 있음). 광고 차단으로 로드는 보통 <1s.
    d.set_page_load_timeout(20)
    try:
        d.command_executor.set_timeout(60)
    except Exception:
        pass
    # navigator.webdriver 흔적 제거 (봇 탐지 우회)
    d.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"},
    )
    logger.debug("Chrome 드라이버 생성 (headless=%s, pid=%s)", headless, _driver_pid(d))
    return d


# 드라이버의 chromedriver 프로세스 PID
def _driver_pid(driver):
    try:
        return driver.service.process.pid
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 드라이버 완전 종료 — driver.quit() 후 chromedriver 프로세스 트리를 강제 kill.
#   crash/hang 시 quit()만으로는 chrome 자식 프로세스가 orphan으로 남으므로,
#   Windows taskkill /T 로 chromedriver(부모) + chrome(자식) 전부 종료.
# ---------------------------------------------------------------------------
def kill_driver(driver):
    if driver is None:
        return
    pid = _driver_pid(driver)
    # 1) chromedriver 프로세스 트리를 먼저 강제 kill (PID 타겟 — 사용자가 연 chrome은 안 건드림).
    #    포트가 즉시 닫혀 이후 quit()이 30초×재시도로 행되지 않고 곧장 실패한다.
    if pid:
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass
    # 2) python 측 세션 객체 정리 (chromedriver 이미 죽어 ConnectionRefused로 즉시 반환).
    #    포트가 닫혀 quit()의 HTTP 호출이 urllib3 재시도 경고를 3줄 뱉으므로 잠시 억제.
    _pool_log = logging.getLogger("urllib3.connectionpool")
    _prev = _pool_log.level
    _pool_log.setLevel(logging.ERROR)
    try:
        driver.quit()
    except Exception:
        pass
    finally:
        _pool_log.setLevel(_prev)
    logger.debug("드라이버 종료 (chromedriver pid=%s 트리 kill)", pid)


# ---------------------------------------------------------------------------
# 미장 EPS 예상치 (USD, float) — Finviz 연간 Estimate
#   https://finviz.com/quote.ashx?t=<ticker>&p=d&ty=ea
#   'Annual' 토글 클릭 후 non-GAAP 'EPS Performance and Forecast' 표의 Estimate 행 파싱
#   주의: Estimate 값 행에 선행 빈 셀이 있어 years[k] <-> est[k+1].
#         -> est[-len(years):] 로 정렬
#   return: {year: float or None}
# ---------------------------------------------------------------------------
def get_us_eps_forecast(ticker, years, driver):
    result = {y: None for y in years}
    driver.get(f"https://finviz.com/quote.ashx?t={ticker}&p=d&ty=ea")
    time.sleep(3)

    # 첫 'Annual' 토글(non-GAAP 섹션) 클릭
    spans = driver.find_elements("xpath", "//span[text()='Annual']")
    if not spans:
        logger.warning("[US %s] Annual 토글 없음(차단 가능) -> skip", ticker)
        return result
    driver.execute_script("arguments[0].scrollIntoView(true); arguments[0].click();", spans[0])
    time.sleep(3)

    txt = driver.execute_script("return document.body.innerText;")

    # non-GAAP 섹션만: 첫 'EPS Performance and Forecast' ~ 'GAAP EPS Performance and Forecast'
    a = txt.find("EPS Performance and Forecast")
    gaap = txt.find("GAAP EPS Performance and Forecast")
    if a < 0:
        logger.warning("[US %s] EPS 표 없음 -> skip", ticker)
        return result
    section = txt[a:gaap] if gaap > a else txt[a:]
    lines = [l for l in section.split("\n") if l.strip()]

    year_list = None
    est_list = None
    for j, line in enumerate(lines):
        if line.startswith("Currency"):
            year_list = line.split("\t")[1:]
        if line.strip() == "Estimate" and j + 1 < len(lines):
            est_list = lines[j + 1].split("\t")

    if not year_list or not est_list:
        logger.warning("[US %s] 연도/Estimate 행 파싱 실패 -> skip", ticker)
        return result

    # 선행 빈 셀 보정: 뒤에서 len(year_list)개를 연도와 정렬
    est_aligned = est_list[-len(year_list):]
    logger.debug("[US %s] years=%s est=%s", ticker, year_list, est_aligned)
    year_to_est = {}
    for yr_str, ev in zip(year_list, est_aligned):
        yr_str = yr_str.strip()
        if not yr_str.isdigit():
            continue
        v = pd.to_numeric((ev or "").replace(",", "").strip(), errors="coerce")
        if v is None or pd.isna(v):
            continue
        year_to_est[int(yr_str)] = float(v)

    for y in years:
        if y in year_to_est:
            result[y] = year_to_est[y]
    return result


# ---------------------------------------------------------------------------
# gspread 인증 + 대상 워크시트 핸들
# ---------------------------------------------------------------------------
def open_watchlist_ws():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name(GOOGLE_CLOUD_KEY_PATH, scope)
    client = gspread.authorize(creds)

    spreadsheet = None
    for i in range(3):
        try:
            spreadsheet = client.open_by_key(GOOGLE_DOC_SHEET_FILE_TGVAL_KEY)
            break
        except APIError as e:
            if i < 2:
                time.sleep(2 ** i)
            else:
                raise e
    return spreadsheet.worksheet(WORKSHEET_NAME)


# ---------------------------------------------------------------------------
# Watchlist 전체 종목 Y/Y1/Y2 EPS 자동 갱신
#   - 'Stock' 헤더 다음 행부터 순회
#   - 국장: get_kor_eps_forecast, 미장: get_us_eps_forecast(selenium)
#   - O/R/V 셀에 batch_update(RAW)로 일괄 쓰기 (서식 변경 안 함)
#   - 조회 실패 셀은 건너뜀(기존값 유지)
# ---------------------------------------------------------------------------
def update_watchlist_eps(only_name=None, headless=True):
    y, y1, y2 = get_year_vars()
    col_map = get_year_col_map(y, y1, y2)
    logger.info("연도: Y=%d(O) Y1=%d(R) Y2=%d(V)", y, y1, y2)

    ws = open_watchlist_ws()
    rows = ws.get_all_values()

    # 'Stock' 헤더 행 탐색 (B열)
    header_idx = None
    for i, r in enumerate(rows):
        if len(r) > 1 and r[1].strip() == STOCK_HEADER:
            header_idx = i
            break
    if header_idx is None:
        logger.error("'%s' 헤더(B열)를 찾지 못함 -> 중단", STOCK_HEADER)
        return
    logger.info("'%s' 헤더 행: %d, 데이터 시작 행: %d", STOCK_HEADER, header_idx + 1, header_idx + 2)

    updates = []          # batch_update 값 요청 목록
    fmt_updates = []      # batch_format 서식 요청 목록
    driver = None         # 미장용 selenium 드라이버(재사용)
    us_pause = 3.0        # Finviz 요청 간 간격(초) — 봇 탐지/rate-limit 완화
    kr_cnt = us_cnt = skip_cnt = 0

    # 숫자 서식: 미장(소수 EPS)은 소수 2자리, 국장(원 단위 정수)은 소수 없음. 둘 다 천단위 콤마.
    def num_pattern(market):
        return "#,##0.00" if market == "US" else "#,##0"

    def queue_cell(sheet_row, year, value, pattern):
        if value is None:
            return
        a1 = gspread.utils.rowcol_to_a1(sheet_row, col_map[year])
        updates.append({"range": a1, "values": [[value]]})
        fmt_updates.append({
            "range": a1,
            "format": {"numberFormat": {"type": "NUMBER", "pattern": pattern}},
        })

    try:
        for i in range(header_idx + 1, len(rows)):
            r = rows[i]
            sheet_row = i + 1
            name = r[1].strip() if len(r) > 1 else ""
            c_val = r[2].strip() if len(r) > 2 else ""

            parsed = parse_stock_code(c_val)
            if parsed is None:
                logger.debug("[%d] %s: C열 빈값/미해석 -> skip", sheet_row, name)
                continue
            market, code = parsed

            if only_name and name != only_name and code != only_name:
                continue

            if market == "KR":
                eps = get_kor_eps_forecast(code, [y, y1, y2])
                kr_cnt += 1
            else:
                if driver is None:
                    driver = make_chrome_driver(headless=headless)
                try:
                    eps = get_us_eps_forecast(code, [y, y1, y2], driver)
                except Exception as e:
                    logger.warning("[US %s] 조회 실패(%s) -> 드라이버 재생성 후 1회 재시도",
                                   code, type(e).__name__)
                    kill_driver(driver)
                    driver = make_chrome_driver(headless=headless)
                    try:
                        eps = get_us_eps_forecast(code, [y, y1, y2], driver)
                    except Exception as e2:
                        logger.error("[US %s] 재시도 실패(%s) -> skip", code, type(e2).__name__)
                        eps = {y: None, y1: None, y2: None}
                us_cnt += 1
                time.sleep(us_pause)

            vy, vy1, vy2 = eps.get(y), eps.get(y1), eps.get(y2)
            if vy is None and vy1 is None and vy2 is None:
                skip_cnt += 1
            logger.info("[%d] %-12s (%s:%s)  Y=%s Y1=%s Y2=%s",
                        sheet_row, name, market, code, vy, vy1, vy2)
            pat = num_pattern(market)
            queue_cell(sheet_row, y, vy, pat)
            queue_cell(sheet_row, y1, vy1, pat)
            queue_cell(sheet_row, y2, vy2, pat)
    finally:
        kill_driver(driver)

    logger.info("처리 종목: KR=%d US=%d, 전체실패(skip)=%d", kr_cnt, us_cnt, skip_cnt)
    if not updates:
        logger.warning("갱신할 셀 없음")
        return

    # 값 RAW로 일괄 쓰기
    ws.batch_update(updates, value_input_option="RAW")
    # 숫자 서식 일괄 적용 (US=#,##0.00, KR=#,##0). 값과 별개 API라 따로 호출.
    if fmt_updates:
        try:
            ws.batch_format(fmt_updates)
        except Exception as e:
            logger.warning("서식 적용 실패(%s) -> 값은 갱신됨", type(e).__name__)
    kst = pytz.timezone("Asia/Seoul")
    logger.info("%d개 셀 갱신 완료 (Updated on %s KST)",
                len(updates), datetime.now(kst).strftime("%Y-%m-%d %H:%M:%S"))


def main():
    parser = argparse.ArgumentParser(
        description="Watchlist(태밸류) 종목 Y/Y1/Y2 EPS 자동 업데이트",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="사용 예시:\n"
               "  script.py            : 전체 Watchlist EPS 갱신 (INFO, selenium headless)\n"
               "  script.py --name NVDA: 지정 종목 한개만 갱신(테스트)\n"
               "  script.py --debug    : DEBUG 로그 + selenium 윈도우 표시\n",
    )
    parser.add_argument("--name", type=str, help="지정 종목명/티커 한개만 갱신")
    parser.add_argument("--debug", action="store_true",
                        help="DEBUG 로그 출력 + selenium 윈도우 표시(headed)")
    args = parser.parse_args()

    setup_logging(debug=args.debug)
    # DEBUG 레벨: selenium 창 표시(headed) / INFO 레벨: headless
    headless = not args.debug

    logger.info("Key path: %s", GOOGLE_CLOUD_KEY_PATH)
    logger.info("Sheet key set: %s | selenium headless: %s",
                "yes" if GOOGLE_DOC_SHEET_FILE_TGVAL_KEY else "no", headless)

    start = time.time()
    update_watchlist_eps(only_name=args.name, headless=headless)
    logger.info("...수행 시간: %.2f 분", (time.time() - start) / 60)


if __name__ == "__main__":
    main()
