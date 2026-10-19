"""
예제: 신규 권역 성장 기회
5주차 설계 예제의 쿼리를 SQLite로 바꾸고, 6주차 리포트의 KPI 카드 3개 · 차트 3장을
필터에 반응하도록 옮겨 둔 코드입니다. 

탭 3개(개요 · 탐색 · 액션) 뼈대가 들어 있습니다.
- ✏️표시: 내 5주차 · 6주차 결과물로 변경할 곳
- [블록 A] · [블록 B] · [블록 C]: Gemini에게 받은 블록으로 통째로 바꿀 곳
  (===== 시작 줄과 ===== 끝 줄 사이를 지우고 받은 블록을 붙임, 첫 줄은 맨 앞에 빈칸 없이)
"""
import sqlite3

import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(page_title="신규 권역 성장 기회 대시보드", layout="wide")
st.title("사용자 행동 분석 대시보드")                                  # ✏️ 6주차 메인 제목
st.caption("배달앱 신규 가입 고객 · 가입 후 30일 기준 · 2025.09~2026.07")     # ✏️ 6주차 부제

# ------------------------------------------------------------
# 0. 필요한 데이터만 추출 — 쓸 테이블과 컬럼만 메모리 DB에 올리고, 정제 테이블을 처음 한 번만 만듦
# ------------------------------------------------------------
DATA_DIR = "data"                          # 원본 CSV를 넣어 둔 폴더
NEEDED = {                                 # ✏️ 이번 대시보드에 필요한 테이블과 컬럼 (쿼리에 쓰는 것만)
    "app_events": ["event_datetime", "customer_id", "session_id", "event_name",
                   "platform", "traffic_source", "campaign_name"],
    "orders": ["customer_id", "order_datetime"],
}

@st.cache_resource
def get_db():                              # 필요한 컬럼만 메모리 DB에 올리고, 정제 테이블 events·odr을 생성(처음 한 번)
    con = sqlite3.connect(":memory:", check_same_thread=False)
    for name, cols in NEEDED.items():
        # dtype=str: 고객 ID의 앞자리 0(0000002)이 숫자로 바뀌지 않게 글자로 읽음
        pd.read_csv(f"{DATA_DIR}/{name}.csv", usecols=cols, dtype=str).to_sql(name, con, index=False)
    # ✏️ 5주차 제외 규칙: 테스트 계정(T로 시작) 제외 · 이벤트 100건 이상인 비정상 세션 제외
    con.execute("""
        CREATE TABLE events AS
        SELECT *
          FROM app_events
         WHERE customer_id NOT LIKE 'T%'
           AND session_id NOT IN (SELECT session_id FROM app_events
                                   GROUP BY session_id HAVING COUNT(*) >= 100)
    """)
    con.execute("CREATE TABLE odr AS SELECT * FROM orders WHERE customer_id NOT LIKE 'T%'")
    # 고객별로 묶는 집계가 빨라지도록 customer_id에 인덱스를 붙임
    con.execute("CREATE INDEX idx_events_customer ON events(customer_id)")
    con.execute("CREATE INDEX idx_odr_customer ON odr(customer_id)")
    return con

# ------------------------------------------------------------
# 1. 데이터 준비 — 정제된 테이블을 SQL로 집계 (비율이 아니라 분자 · 분모 건수만)
# ------------------------------------------------------------
@st.cache_data
def query(sql):                            # SQL을 실행해 표로 돌려줌
    return pd.read_sql(sql, get_db())

# 고객별 가입일 · 가입 채널 · 플랫폼 (가입 이벤트 기준) - 두 쿼리 모두 사용
SQL_SIGNUP = """
su AS (
    SELECT customer_id,
           DATE(MIN(event_datetime))                    AS 가입일,
           MIN(COALESCE(campaign_name, traffic_source)) AS 가입채널,
           MIN(platform)                                AS 플랫폼
      FROM events
     WHERE event_name = 'sign_up'
     GROUP BY customer_id
)
"""

# ✏️ 가입월 × 가입 채널 × 플랫폼별 가입 후 30일 퍼널 (예제 Q1 · Q3)
SQL_SIGNUP_FUNNEL = f"""
WITH {SQL_SIGNUP},
ev AS (                                     -- 가입 후 30일 안의 행동
    SELECT DISTINCT e.customer_id, e.event_name
      FROM events e
      JOIN su ON e.customer_id = su.customer_id
     WHERE julianday(DATE(e.event_datetime)) - julianday(su.가입일) BETWEEN 0 AND 30
),
fo AS (                                     -- 고객별 첫 주문일
    SELECT customer_id, DATE(MIN(order_datetime)) AS 첫주문일
      FROM odr
     GROUP BY customer_id
)
SELECT strftime('%Y-%m', su.가입일) AS 가입월,
       su.가입채널,
       su.플랫폼,
       COUNT(DISTINCT su.customer_id)                                                         AS 가입자,
       COUNT(DISTINCT CASE WHEN ev.event_name = 'view_restaurant' THEN su.customer_id END)    AS 식당조회,
       COUNT(DISTINCT CASE WHEN ev.event_name = 'add_to_cart'     THEN su.customer_id END)    AS 장바구니,
       COUNT(DISTINCT CASE WHEN ev.event_name = 'begin_checkout'  THEN su.customer_id END)    AS 결제시작,
       COUNT(DISTINCT CASE WHEN julianday(fo.첫주문일) - julianday(su.가입일) <= 30
                           THEN su.customer_id END)                                           AS 첫주문30일
  FROM su
  LEFT JOIN ev ON su.customer_id = ev.customer_id
  LEFT JOIN fo ON su.customer_id = fo.customer_id
 WHERE su.가입일 <= '2026-07-31'            -- 30일을 다 볼 수 있는 가입자만
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3
"""

# ✏️ 가입월 × 가입 채널 × 플랫폼별 30일 재주문 (예제 Q2, 분모가 첫 주문 고객이라 표를 따로 만듦)
SQL_REORDER = f"""
WITH {SQL_SIGNUP},
seq AS (
    SELECT customer_id, order_datetime,
           ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY order_datetime) AS 순번,
           LEAD(order_datetime) OVER (PARTITION BY customer_id ORDER BY order_datetime) AS 다음주문
      FROM odr
)
SELECT strftime('%Y-%m', su.가입일) AS 가입월,
       su.가입채널,
       su.플랫폼,
       COUNT(*)                                                                          AS 첫주문고객,
       SUM(CASE WHEN julianday(DATE(seq.다음주문)) - julianday(DATE(seq.order_datetime)) <= 30
                THEN 1 ELSE 0 END)                                                       AS 재주문30일
  FROM seq
  JOIN su ON seq.customer_id = su.customer_id
 WHERE seq.순번 = 1
   AND seq.order_datetime < '2026-08-01'    -- 30일을 다 볼 수 있는 첫 주문만
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3
"""

@st.cache_data
def load_data():
    return query(SQL_SIGNUP_FUNNEL), query(SQL_REORDER)

df, rd = load_data()                       # df: 가입 후 30일 퍼널 / rd: 30일 재주문

# 📌 AI 해설: 아래 AI_NOTES = {} 한 줄을 Gemini가 준 AI_NOTES = { … } 로 바꾸기

AI_NOTES = {
    "전체": (
        "전체 가입자 2,774명의 30일 첫 주문 전환율은 69.9%, 조회 → 장바구니 전환율은 84.1%, 30일 재주문율은 67.4%이며, 가장 낮은 단계 전환율은 조회 → 장바구니(84.1%)입니다. "
        "첫 주문 전환율은 2025-12 가입월 76.6%에서 2026-01 가입월 67.8%로 하락했으며, 같은 달 INSTA를 제외한 고객의 전환율은 75.0%였습니다. "
        "INSTA 캠페인 유입 고객의 구성 변화가 전체 전환율 하락과 관련된 것으로 보이나, 구체적인 원인은 추가 확인이 필요합니다."
    ),

    "Android": (
        "Android 가입자 1,502명의 30일 첫 주문 전환율은 69.2%, 조회 → 장바구니 전환율은 83.8%, 30일 재주문율은 66.6%이며, 가장 낮은 단계 전환율은 조회 → 장바구니(83.8%)입니다. "
        "iOS 대비 첫 주문 전환율은 1.5%p, 재주문율은 1.6%p 낮습니다. "
        "두 플랫폼의 성과 차이는 상대적으로 작은 편으로 보이며, 플랫폼 자체가 전환율 하락의 원인인지는 추가 확인이 필요합니다."
    ),

    "iOS": (
        "iOS 가입자 1,272명의 30일 첫 주문 전환율은 70.7%, 조회 → 장바구니 전환율은 84.5%, 30일 재주문율은 68.2%이며, 가장 낮은 단계 전환율은 조회 → 장바구니(84.5%)입니다. "
        "Android 대비 첫 주문 전환율은 1.5%p, 재주문율은 1.6%p 높습니다. "
        "iOS의 전환 성과가 Android보다 소폭 높은 것으로 보이나, 이러한 차이가 발생한 원인은 추가 확인이 필요합니다."
    ),

    "organic": (
        "organic 가입자 847명의 30일 첫 주문 전환율은 75.2%, 조회 → 장바구니 전환율은 88.6%, 30일 재주문율은 73.5%이며, 가장 낮은 단계 전환율은 조회 → 장바구니(88.6%)입니다. "
        "전체 대비 첫 주문 전환율은 5.3%p, 재주문율은 6.1%p 높습니다. "
        "organic 고객은 전체 평균보다 첫 주문과 재주문 성과가 양호한 집단으로 보이나, 이러한 차이의 원인은 추가 확인이 필요합니다."
    ),

    "INSTA_첫주문쿠폰": (
        "INSTA_첫주문쿠폰 가입자 739명의 30일 첫 주문 전환율은 55.6%, 조회 → 장바구니 전환율은 72.4%, 30일 재주문율은 44.5%이며, 가장 낮은 단계 전환율은 조회 → 장바구니(72.4%)입니다. "
        "전체 대비 첫 주문 전환율은 14.3%p, 재주문율은 22.9%p 낮으며, INSTA 가입자가 처음 나타난 2026-01 가입월부터 전체 첫 주문 전환율이 하락했습니다. "
        "INSTA 고객의 낮은 전환 성과가 전체 성과 하락과 관련된 것으로 보이나, 캠페인의 구체적인 영향과 원인은 추가 확인이 필요합니다."
    ),

    "NAVER_검색광고": (
        "NAVER_검색광고 가입자 517명의 30일 첫 주문 전환율은 76.4%, 조회 → 장바구니 전환율은 87.7%, 30일 재주문율은 72.6%이며, 가장 낮은 단계 전환율은 조회 → 장바구니(87.7%)입니다. "
        "전체 대비 첫 주문 전환율은 6.5%p, 재주문율은 5.2%p 높습니다. "
        "NAVER 유입 고객은 전체 평균보다 첫 주문 및 재주문 성과가 높은 집단으로 보이며, 이러한 차이의 원인은 추가 확인이 필요합니다."
    ),

    "GOOGLE_앱설치": (
        "GOOGLE_앱설치 가입자 398명의 30일 첫 주문 전환율은 73.6%, 조회 → 장바구니 전환율은 89.5%, 30일 재주문율은 72.7%이며, 가장 낮은 단계 전환율은 장바구니 → 결제시작(88.0%)입니다. "
        "전체 대비 첫 주문 전환율은 3.7%p, 조회 → 장바구니 전환율은 5.4%p 높습니다. "
        "GOOGLE 고객은 조회 이후 장바구니 전환 성과가 양호한 것으로 보이며, 장바구니 이후 결제시작 단계의 상대적인 이탈 요인은 추가 확인이 필요합니다."
    ),

    "친구초대": (
        "친구초대 가입자 273명의 30일 첫 주문 전환율은 74.4%, 조회 → 장바구니 전환율은 87.1%, 30일 재주문율은 77.3%이며, 가장 낮은 단계 전환율은 조회 → 장바구니(87.1%)입니다. "
        "전체 대비 첫 주문 전환율은 4.5%p, 재주문율은 9.9%p 높지만, 가입자가 300명 미만인 비교적 작은 집단이라는 점에 유의해야 합니다. "
        "친구초대 고객은 상대적으로 높은 재주문 성과를 보이는 것으로 보이나, 집단 규모와 성과 차이의 원인을 추가 확인할 필요가 있습니다."
    )
}

AI_SOURCE = "🤖 Gemini가 미리 만든 해설 · 전체 기간 기준"

# ------------------------------------------------------------
# 2. 사이드바 필터 — 두 표에 같은 조건을 적용
# ------------------------------------------------------------
SMALL = 100                                # ✏️ 이보다 가입자가 적은 집단은 "작은 집단"

if "reset" not in st.session_state:
    st.session_state.reset = 0

def reset_filters():                       # 번호가 바뀌면 위젯이 처음 상태로 새로 만들어짐
    st.session_state.reset += 1

n = st.session_state.reset
months = sorted(df["가입월"].unique())
channels = sorted(df["가입채널"].unique())
platforms = sorted(df["플랫폼"].unique())

st.sidebar.header("필터")
start, end = st.sidebar.select_slider("가입월", options=months,
                                      value=(months[0], months[-1]), key=f"기간_{n}")
sel_channels = st.sidebar.multiselect("가입 채널", channels, default=channels, key=f"채널_{n}")
sel_platforms = st.sidebar.multiselect("플랫폼", platforms, default=platforms, key=f"플랫폼_{n}")
with st.sidebar.expander("고급 설정"):
    base_start, base_end = st.select_slider(
        "평소 범위 기준 기간", options=months, value=(months[0], months[3]), key=f"기준_{n}",   # ✏️ 문제가 없던 기간
        help="문제가 없던 기간을 고르세요. 이 기간의 월별 값 범위를 '평소 범위'로 씁니다.")
    hide_small = st.checkbox(f"가입자 {SMALL}명 미만 집단 제외 (집단 비교 · 드릴다운)", key=f"작은집단_{n}")
st.sidebar.button("필터 초기화", on_click=reset_filters)

# 집단 조건(가입 채널 · 플랫폼)만 적용한 데이터 g · rg, 기간까지 적용한 데이터 f · rf
g = df[df["가입채널"].isin(sel_channels) & df["플랫폼"].isin(sel_platforms)]
f = g[(g["가입월"] >= start) & (g["가입월"] <= end)]
rg = rd[rd["가입채널"].isin(sel_channels) & rd["플랫폼"].isin(sel_platforms)]
rf = rg[(rg["가입월"] >= start) & (rg["가입월"] <= end)]

def label(selected, all_values):           # 조건 줄에 쓸 말: 모두 고르면 "전체"
    if len(selected) == len(all_values):
        return "전체"
    return ", ".join(selected) if selected else "선택 없음"

period = "전체" if (start, end) == (months[0], months[-1]) else f"{start} ~ {end}"
st.caption(f"🔎 보고 있는 조건: 가입월 {period} · 가입 채널 {label(sel_channels, channels)} · "
           f"플랫폼 {label(sel_platforms, platforms)}")

if f.empty:
    st.warning("선택한 조건에 해당하는 데이터가 없습니다. 필터를 바꿔 주세요.")
    st.stop()

def rate(a, b):
    return a / b * 100 if b else 0

# ------------------------------------------------------------
# 3. 화면 — 탭 3개
# ------------------------------------------------------------
tab_overview, tab_explore, tab_action = st.tabs(["📊 개요", "🔍 탐색", "✅ 액션"])


with tab_overview:
    st.caption(
        "이 탭에서 볼 수 있는 것: 핵심 숫자와 그 숫자를 읽는 법, "
        "6주차 리포트의 차트 3장 (필터 적용)"
    )

    # ==========================================
    # 3-1. KPI 카드 (기존 코드 유지)
    # ==========================================
    t = f[["가입자", "식당조회", "장바구니", "첫주문30일"]].sum()
    tr = rf[["첫주문고객", "재주문30일"]].sum()

    c1, c2, c3 = st.columns(3)

    c1.metric(
        "30일 첫 주문 전환율",
        f"{rate(t['첫주문30일'], t['가입자']):.1f}%"
    )

    c2.metric(
        "식당 조회 → 장바구니 전환율",
        f"{rate(t['장바구니'], t['식당조회']):.1f}%"
    )

    c3.metric(
        "30일 재주문율",
        f"{rate(tr['재주문30일'], tr['첫주문고객']):.1f}%"
    )

    # ==========================================
    # 3-2. 지금 숫자 읽기
    # ==========================================

    # 비교할 KPI 정의
    kpi_definitions = [
        {
            "이름": "30일 첫 주문 전환율",
            "분자": "첫주문30일",
            "분모": "가입자"
        },
        {
            "이름": "식당 조회 → 장바구니 전환율",
            "분자": "장바구니",
            "분모": "식당조회"
        }
    ]

    # ------------------------------------------
    # 이번 값: f의 마지막 3개월
    # ------------------------------------------
    selected_months = sorted(
        f["가입월"].dropna().unique().tolist()
    )

    if len(selected_months) >= 3:
        recent_months = selected_months[-3:]
        recent_data = f[
            f["가입월"].isin(recent_months)
        ].copy()

        recent_start = recent_months[0]
    else:
        recent_months = []
        recent_data = f.iloc[0:0].copy()
        recent_start = None

    # ------------------------------------------
    # 평소 범위: g에서 기준 기간 추출
    # ------------------------------------------
    baseline_data = g[
        (g["가입월"] >= base_start)
        & (g["가입월"] <= base_end)
    ].copy()

    # ------------------------------------------
    # KPI별 비교 문장 생성
    # ------------------------------------------
    reading_messages = []

    for kpi_info in kpi_definitions:
        kpi_name = kpi_info["이름"]
        numerator = kpi_info["분자"]
        denominator = kpi_info["분모"]

        # 선택 기간이 3개월 미만
        if len(selected_months) < 3:
            reading_messages.append(
                f"{kpi_name}: "
                "비교하려면 선택 기간을 3개월 이상 골라 주세요"
            )
            continue

        # 이번 값: 최근 3개월의 분자/분모 합계
        recent_num = recent_data[numerator].sum()
        recent_den = recent_data[denominator].sum()

        if recent_den == 0:
            reading_messages.append(
                f"{kpi_name}: "
                "최근 3개월의 분모가 0이어서 "
                "전환율을 계산할 수 없습니다."
            )
            continue

        recent_rate = recent_num / recent_den * 100

        # 평소 범위: 가입월별로 먼저 합산
        baseline_monthly = (
            baseline_data
            .groupby("가입월", as_index=False)[
                [numerator, denominator]
            ]
            .sum()
        )

        # 분모가 0인 월 제외
        baseline_monthly = baseline_monthly[
            baseline_monthly[denominator] > 0
        ].copy()

        # 기준 기간의 유효한 데이터가 3개월 미만
        if len(baseline_monthly) < 3:
            reading_messages.append(
                f"{kpi_name}: "
                "기준 기간을 이 집단의 데이터가 "
                "있는 시기로 옮겨 주세요"
            )
            continue

        # 월별 전환율 계산
        baseline_monthly["월별 전환율"] = (
            baseline_monthly[numerator]
            / baseline_monthly[denominator]
            * 100
        )

        # 평소 범위 최솟값 / 최댓값
        baseline_min = baseline_monthly[
            "월별 전환율"
        ].min()

        baseline_max = baseline_monthly[
            "월별 전환율"
        ].max()

        # 공통 문장
        message = (
            f"{recent_start} 가입월부터 3개월 "
            f"{kpi_name}은 {recent_rate:.1f}%로, "
            f"평소 범위({baseline_min:.1f}"
            f"–{baseline_max:.1f}%)"
        )

        # --------------------------------------
        # 낮음 / 높음 / 범위 안
        # --------------------------------------
        if recent_rate < baseline_min:
            gap = baseline_min - recent_rate

            message += (
                f"보다 {gap:.1f}%p 낮습니다."
            )

        elif recent_rate > baseline_max:
            gap = recent_rate - baseline_max

            message += (
                f"보다 {gap:.1f}%p 높습니다."
            )

        else:
            message += " 안에 있습니다."

        reading_messages.append(message)

    # ==========================================
    # 지금 숫자 읽기 화면
    # ==========================================
    with st.container(border=True):

        # 1. 제목
        st.markdown("**📏 지금 숫자 읽기**")

        # 2. 규칙 문장 2개
        st.markdown(
            "\n".join(
                f"- {message}"
                for message in reading_messages
            )
        )

        # 3. 평소 범위 기준 기간
        st.caption(
            "📏 자동 계산 · 평소 범위 기준 기간 "
            f"{base_start}–{base_end} (월별 값)"
        )

        # 4. AI 해설
        with st.expander(
            "🤖 AI 해설 · 전체",
            expanded=False
        ):
            st.write(
                AI_NOTES.get(
                    "전체",
                    "아직 해설이 없습니다."
                )
            )

            st.caption(AI_SOURCE)


with tab_overview:
    st.divider()

    # ✏️ 3-2. 차트 - 6주차 차트 ① 문제 · ② 원인 · ③ 제안
    left, right = st.columns(2)

    # ① 문제: 가입월별 30일 첫 주문 전환율
    m = f.groupby("가입월")[["가입자", "첫주문30일"]].sum().reset_index()
    m["첫주문전환율"] = (m["첫주문30일"] / m["가입자"] * 100).round(1)
    fig1 = px.line(m, x="가입월", y="첫주문전환율", markers=True, hover_data=["가입자"],
                   labels={"가입월": "가입월", "첫주문전환율": "30일 첫 주문 전환율(%)", "가입자": "가입자(명)"},
                   title="① 가입월별 30일 첫 주문 전환율")
    fig1.update_layout(xaxis_title="")
    fig1.update_xaxes(type="category")       # 가입월(2025-09)을 날짜가 아니라 글자 그대로 표시
    left.plotly_chart(fig1, width="stretch")

    # ② 원인: 가입 채널별 식당 조회 → 장바구니 전환율
    ch = f.groupby("가입채널")[["식당조회", "장바구니"]].sum().reset_index()
    ch["장바구니전환율"] = (ch["장바구니"] / ch["식당조회"] * 100).round(1)
    ch = ch.sort_values("장바구니전환율")
    fig2 = px.bar(ch, x="장바구니전환율", y="가입채널", orientation="h", text_auto=".1f",
                  hover_data=["식당조회"],
                  labels={"장바구니전환율": "식당 조회 → 장바구니 전환율(%)", "가입채널": "가입 채널",
                          "식당조회": "식당 조회 가입자(명)"},
                  title="② 가입 채널별 식당 조회 → 장바구니 전환율")
    fig2.update_xaxes(range=[0, ch["장바구니전환율"].max() * 1.2])
    fig2.update_layout(yaxis_title="")
    right.plotly_chart(fig2, width="stretch")

    # ③ 제안: 가입 채널별 30일 재주문율
    rc = rf.groupby("가입채널")[["첫주문고객", "재주문30일"]].sum().reset_index()
    rc["재주문율"] = (rc["재주문30일"] / rc["첫주문고객"] * 100).round(1)
    rc = rc.sort_values("재주문율")
    fig3 = px.bar(rc, x="재주문율", y="가입채널", orientation="h", text_auto=".1f",
                  hover_data=["첫주문고객"],
                  labels={"재주문율": "30일 재주문율(%)", "가입채널": "가입 채널", "첫주문고객": "첫 주문 고객(명)"},
                  title="③ 가입 채널별 30일 재주문율")
    fig3.update_xaxes(range=[0, rc["재주문율"].max() * 1.2])
    fig3.update_layout(yaxis_title="")
    st.plotly_chart(fig3, width="stretch")

    # AI 해설을 만들 때 Gemini에 올릴 표 (필터와 관계없이 전체)
    with st.expander("📥 AI 해설용 데이터 내려받기"):
        st.download_button("가입 후 30일 퍼널 (가입월 × 가입 채널 × 플랫폼)",
                           df.to_csv(index=False).encode("utf-8-sig"), "가입월_채널_플랫폼_퍼널.csv")
        st.download_button("30일 재주문 (가입월 × 가입 채널 × 플랫폼)",
                           rd.to_csv(index=False).encode("utf-8-sig"), "가입월_채널_플랫폼_재주문.csv")


with tab_explore:
    st.caption(
        "이 탭에서 볼 수 있는 것: 집단 비교와 드릴다운 — "
        "어디서, 언제부터인가"
    )

    # ==========================================
    # 3-1. 집단 비교
    # ==========================================
    st.subheader("집단 비교")

    compare_axis = st.radio(
        "비교 기준",
        options=["가입채널", "플랫폼"],
        horizontal=True,
        key="compare_axis"
    )

    # 퍼널 5단계
    stages = [
        "가입자",
        "식당조회",
        "장바구니",
        "결제시작",
        "첫주문30일"
    ]

    stage_labels = {
        "가입자": "가입자",
        "식당조회": "식당 조회",
        "장바구니": "장바구니",
        "결제시작": "결제 시작",
        "첫주문30일": "첫 주문(30일)"
    }

    # 필터 적용 데이터 f에서 집단별 합계
    compare_data = f.copy()
    compare_data[compare_axis] = (
        compare_data[compare_axis]
        .fillna("미분류")
        .astype(str)
    )

    group_data = (
        compare_data
        .groupby(compare_axis, as_index=False)[stages]
        .sum()
        .sort_values("가입자", ascending=False)
        .reset_index(drop=True)
    )

    # ==========================================
    # 작은 집단 처리
    # ==========================================
    small_groups = group_data.loc[
        group_data["가입자"] < SMALL,
        compare_axis
    ].tolist()

    if hide_small:
        group_data = group_data.loc[
            group_data["가입자"] >= SMALL
        ].copy()

        if small_groups:
            st.caption(
                f"제외한 집단: {', '.join(small_groups)} "
                f"(가입자 {SMALL:,}명 미만)"
            )

    elif small_groups:
        st.caption(
            f"⚠️ {', '.join(small_groups)}: "
            f"가입자 {SMALL:,}명 미만으로 "
            "값이 크게 흔들릴 수 있다."
        )

    if group_data.empty:
        st.warning(
            "비교할 집단이 없습니다. "
            "필터 또는 작은 집단 제외 설정을 변경해 주세요."
        )

    else:
        # ==========================================
        # 그래프 데이터 생성
        # ==========================================
        funnel_rows = []
        step_rows = []

        groups = group_data[compare_axis].tolist()

        step_labels = [
            "가입 → 조회",
            "조회 → 장바구니",
            "장바구니 → 결제",
            "결제 → 첫 주문"
        ]

        for _, row in group_data.iterrows():
            group_name = row[compare_axis]
            signup_count = int(row["가입자"])

            # 누적 전환율 (가입자 대비)
            for stage in stages:
                count = int(row[stage])

                cumulative_rate = (
                    count / signup_count * 100
                    if signup_count > 0
                    else None
                )

                funnel_rows.append({
                    "집단": group_name,
                    "퍼널 단계": stage_labels[stage],
                    "누적 전환율(%)": cumulative_rate,
                    "단계 고객 수": count,
                    "가입자 수": signup_count
                })

            # 단계 전환율 (직전 단계 대비)
            for i in range(1, len(stages)):
                prev_count = int(row[stages[i - 1]])
                curr_count = int(row[stages[i]])

                step_rate = (
                    curr_count / prev_count * 100
                    if prev_count > 0
                    else None
                )

                step_rows.append({
                    "집단": group_name,
                    "전환 단계": step_labels[i - 1],
                    "단계 전환율(%)": step_rate,
                    "직전 단계 사람 수": prev_count,
                    "다음 단계 사람 수": curr_count,
                    "표시값": (
                        f"{step_rate:.1f}%"
                        if step_rate is not None
                        else ""
                    )
                })

        funnel_df = pd.DataFrame(funnel_rows)
        step_df = pd.DataFrame(step_rows)

        # ==========================================
        # 공통 색상 및 범례 설정
        # ==========================================
        INSTA = "INSTA_첫주문쿠폰"
        RED = "#B42332"

        gray_palette = [
            "#64748B",
            "#94A3B8",
            "#B0B8C4",
            "#CDD2D9",
            "#808B98",
            "#A0A7B1"
        ]

        color_map = {}
        gray_idx = 0

        for group_name in groups:
            if group_name == INSTA:
                color_map[group_name] = RED
            else:
                color_map[group_name] = gray_palette[
                    gray_idx % len(gray_palette)
                ]
                gray_idx += 1

        # 범례 이름 간소화
        legend_aliases = {
            "INSTA_첫주문쿠폰": "INSTA",
            "GOOGLE_앱설치": "GOOGLE",
            "NAVER_검색광고": "NAVER",
            "organic": "ORGANIC",
            "ORGANIC": "ORGANIC"
        }

        legend_style = dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
            font=dict(size=11),
            title_text=""
        )

        # ==========================================
        # 좌우 2단 배치 유지
        # ==========================================
        left, right = st.columns(
            [1, 1.1],
            gap="large"
        )

        # ==========================================
        # 왼쪽: 누적 전환율 퍼널
        # ==========================================
        with left:
            st.markdown("**① 집단별 누적 전환율**")

            fig = px.funnel(
                funnel_df,
                x="누적 전환율(%)",
                y="퍼널 단계",
                color="집단",
                category_orders={
                    "퍼널 단계": [
                        stage_labels[s] for s in stages
                    ],
                    "집단": groups
                },
                color_discrete_map=color_map,
                custom_data=[
                    "집단",
                    "단계 고객 수",
                    "가입자 수"
                ]
            )

            # 겹치는 라벨 제거
            fig.update_traces(
                textinfo="none",
                opacity=0.95,
                connector=dict(
                    line=dict(
                        color="rgba(0,0,0,0)"
                    )
                ),
                hovertemplate=(
                    "집단: %{customdata[0]}<br>"
                    "퍼널 단계: %{y}<br>"
                    "누적 전환율: %{x:.1f}%<br>"
                    "단계 고객 수: "
                    "%{customdata[1]:,.0f}명<br>"
                    "가입자 수: "
                    "%{customdata[2]:,.0f}명"
                    "<extra></extra>"
                )
            )

            # INSTA만 수치 직접 표시
            if INSTA in groups:
                fig.update_traces(
                    selector=dict(name=INSTA),
                    texttemplate="%{x:.1f}%",
                    textposition="inside",
                    textfont=dict(
                        size=11,
                        color="white"
                    )
                )

            # 범례 이름 간소화
            for trace in fig.data:
                trace.name = legend_aliases.get(
                    trace.name, trace.name
                )

            fig.update_layout(
                template="plotly_white",
                funnelmode="group",
                funnelgap=0.18,
                height=620,
                margin=dict(
                    l=105, r=15, t=110, b=55
                ),
                legend=legend_style,
                plot_bgcolor="white",
                font=dict(size=12)
            )

            fig.update_xaxes(
                title_text="가입자 대비 누적 전환율(%)",
                range=[0, 105],
                ticksuffix="%",
                tickformat=".1f",
                dtick=20,
                showgrid=True,
                gridcolor="#E9ECEF",
                zeroline=False
            )

            fig.update_yaxes(
                title_text="",
                categoryorder="array",
                categoryarray=[
                    stage_labels[s] for s in stages
                ],
                autorange="reversed",
                showgrid=False
            )

            st.plotly_chart(fig, width="stretch")

            st.caption(
                "각 단계의 고객 수를 전체 가입자 수로 "
                "나눈 비율입니다. 상세 수치는 마우스를 "
                "올려 확인할 수 있습니다."
            )

        # ==========================================
        # 오른쪽: 단계 전환율 비교
        # ==========================================
        with right:
            st.markdown("**② 집단별 단계 전환율**")

            fig = px.bar(
                step_df,
                x="단계 전환율(%)",
                y="전환 단계",
                color="집단",
                orientation="h",
                barmode="group",
                text="표시값",
                category_orders={
                    "전환 단계": step_labels,
                    "집단": groups
                },
                color_discrete_map=color_map,
                custom_data=[
                    "집단",
                    "직전 단계 사람 수",
                    "다음 단계 사람 수"
                ]
            )

            fig.update_traces(
                textposition="outside",
                textfont=dict(size=10),
                cliponaxis=False,
                hovertemplate=(
                    "집단: %{customdata[0]}<br>"
                    "전환 단계: %{y}<br>"
                    "단계 전환율: %{x:.1f}%<br>"
                    "직전 단계 사람 수: "
                    "%{customdata[1]:,.0f}명<br>"
                    "다음 단계 사람 수: "
                    "%{customdata[2]:,.0f}명"
                    "<extra></extra>"
                )
            )

            # 범례 이름 간소화
            for trace in fig.data:
                trace.name = legend_aliases.get(
                    trace.name, trace.name
                )

            # x축은 0부터 시작
            valid_rates = step_df[
                "단계 전환율(%)"
            ].dropna()

            max_rate = (
                valid_rates.max()
                if not valid_rates.empty
                else 100
            )

            x_max = max(110, max_rate * 1.2)

            fig.update_layout(
                template="plotly_white",
                barmode="group",
                bargap=0.28,
                height=620,
                margin=dict(
                    l=120, r=25, t=110, b=55
                ),
                legend=legend_style,
                plot_bgcolor="white",
                font=dict(size=12),
                uniformtext=dict(
                    minsize=9,
                    mode="hide"
                )
            )

            fig.update_xaxes(
                title_text="단계 전환율(%)",
                range=[0, x_max],
                ticksuffix="%",
                tickformat=".1f",
                dtick=20,
                showgrid=True,
                gridcolor="#E9ECEF",
                zeroline=False
            )

            fig.update_yaxes(
                title_text="",
                categoryorder="array",
                categoryarray=step_labels,
                autorange="reversed",
                showgrid=False
            )

            st.plotly_chart(fig, width="stretch")

            st.caption(
                "단계 전환율은 다음 단계 고객 수 ÷ "
                "직전 단계 고객 수로 계산합니다. "
                "툴팁에서 각 집단의 고객 수를 확인할 수 있습니다."
            )

        # ==========================================
        # 3-2. 드릴다운
        # ==========================================
        st.divider()
        st.subheader("드릴다운: 언제부터, 무엇 때문일까")

        # 위의 집단 비교에서 선택한 축의 집단만 사용
        # hide_small이 True이면 제외 후의 groups 사용
        other_axis = (
            "플랫폼"
            if compare_axis == "가입채널"
            else "가입채널"
        )

        drill_stages = {
            "가입 → 30일 첫 주문": (
                "가입자", "첫주문30일"
            ),
            "가입 → 식당조회": (
                "가입자", "식당조회"
            ),
            "식당조회 → 장바구니": (
                "식당조회", "장바구니"
            ),
            "장바구니 → 결제시작": (
                "장바구니", "결제시작"
            ),
            "결제시작 → 30일 첫 주문": (
                "결제시작", "첫주문30일"
            )
        }

        # 선택 기본값은 INSTA 캠페인 (존재할 때)
        default_group_idx = (
            groups.index(INSTA)
            if INSTA in groups
            else 0
        )

        select_col1, select_col2 = st.columns(2)

        with select_col1:
            selected_group = st.selectbox(
                "자세히 볼 집단",
                options=groups,
                index=default_group_idx,
                key=f"drill_group_{compare_axis}"
            )

        with select_col2:
            selected_stage = st.selectbox(
                "자세히 볼 전환 단계",
                options=list(drill_stages.keys()),
                index=2,
                key="drill_stage"
            )

        denominator, numerator = drill_stages[
            selected_stage
        ]

        # 선택한 가입월 범위
        month_order = sorted(
            compare_data["가입월"].unique().tolist()
        )

        # ==========================================
        # 드릴다운 차트 ①
        # 선택 집단의 월별 전환율을 다른 축으로 비교
        # ==========================================
        st.markdown("**월별 전환율 상세 비교**")

        drill_data = compare_data.loc[
            compare_data[compare_axis] == selected_group
        ].copy()

        drill_data[other_axis] = (
            drill_data[other_axis]
            .fillna("미분류")
            .astype(str)
        )

        other_groups = sorted(
            drill_data[other_axis].unique().tolist()
        )

        # 가입월 × 다른 축으로 분자/분모 합계
        drill_monthly = (
            drill_data
            .groupby(["가입월", other_axis])[
                [denominator, numerator]
            ]
            .sum()
        )

        # 데이터가 없는 월도 유지
        # 분모가 0이면 전환율을 NaN으로 처리
        full_index = pd.MultiIndex.from_product(
            [month_order, other_groups],
            names=["가입월", other_axis]
        )

        drill_monthly = (
            drill_monthly
            .reindex(full_index, fill_value=0)
            .reset_index()
        )

        drill_monthly["전환율(%)"] = (
            drill_monthly[numerator]
            .div(
                drill_monthly[denominator].where(
                    drill_monthly[denominator] > 0
                )
            )
            * 100
        )

        # 드릴다운 차트 색상
        # 한 집단만 진한 남색, 나머지는 회색
        NAVY = "#253B5B"
        DRILL_GRAY = "#A0A7B1"

        drill_colors = {}

        for idx, group_name in enumerate(other_groups):
            drill_colors[group_name] = (
                NAVY if idx == 0
                else gray_palette[
                    (idx - 1) % len(gray_palette)
                ]
            )

        fig_drill1 = px.line(
            drill_monthly,
            x="가입월",
            y="전환율(%)",
            color=other_axis,
            markers=True,
            category_orders={
                "가입월": month_order,
                other_axis: other_groups
            },
            color_discrete_map=drill_colors,
            custom_data=[
                denominator,
                numerator
            ],
            title=(
                f"① {selected_group}의 가입월별 "
                f"{selected_stage} 전환율 — "
                f"{other_axis}별로 쪼개 보기"
            )
        )

        fig_drill1.update_traces(
            line=dict(width=2.5),
            marker=dict(size=8),
            connectgaps=False,
            hovertemplate=(
                "가입월: %{x}<br>"
                "전환율: %{y:.1f}%<br>"
                "직전 단계 사람 수: "
                "%{customdata[0]:,.0f}명<br>"
                "다음 단계 사람 수: "
                "%{customdata[1]:,.0f}명"
                "<extra></extra>"
            )
        )

        # 실제 전환율 범위에 맞춰 y축 설정
        valid_drill_rates = drill_monthly[
            "전환율(%)"
        ].dropna()

        max_drill_rate = (
            valid_drill_rates.max()
            if not valid_drill_rates.empty
            else 100
        )

        fig_drill1.update_layout(
            template="plotly_white",
            height=460,
            margin=dict(
                l=50, r=30, t=90, b=60
            ),
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="left",
                x=0,
                title_text=""
            ),
            plot_bgcolor="white",
            hovermode="closest"
        )

        fig_drill1.update_xaxes(
            title_text="가입월",
            type="category",
            categoryorder="array",
            categoryarray=month_order,
            showgrid=False
        )

        fig_drill1.update_yaxes(
            title_text=f"{selected_stage} 전환율(%)",
            range=[0, max(105, max_drill_rate * 1.1)],
            ticksuffix="%",
            tickformat=".1f",
            showgrid=True,
            gridcolor="#E9ECEF",
            zeroline=False
        )

        st.plotly_chart(
            fig_drill1,
            width="stretch"
        )

        st.caption(
            "모든 갈래가 함께 움직였다면, "
            "그 축은 원인이 아닙니다."
        )

        # ==========================================
        # 드릴다운 차트 ②
        # 월별 가입자 구성 변화
        # 선택 집단 vs 그 외
        # ==========================================
        st.markdown("**월별 가입자 구성 변화**")

        # 선택 집단과 나머지 집단 구분
        mix_data = compare_data.copy()

        mix_data["집단 구분"] = mix_data[
            compare_axis
        ].where(
            mix_data[compare_axis] == selected_group,
            "그 외"
        )

        # 월별 가입자 합계
        mix_monthly = (
            mix_data
            .groupby(
                ["가입월", "집단 구분"],
                as_index=False
            )["가입자"]
            .sum()
        )

        # 월별 선택 집단과 그 외의 가입자 수
        mix_pivot = (
            mix_monthly
            .pivot(
                index="가입월",
                columns="집단 구분",
                values="가입자"
            )
            .reindex(month_order)
            .fillna(0)
        )

        # 해당 기간에 특정 구분이 없어도 0명으로 표시
        if selected_group not in mix_pivot.columns:
            mix_pivot[selected_group] = 0

        if "그 외" not in mix_pivot.columns:
            mix_pivot["그 외"] = 0

        mix_chart = (
            mix_pivot[[selected_group, "그 외"]]
            .reset_index()
            .melt(
                id_vars="가입월",
                value_vars=[selected_group, "그 외"],
                var_name="집단 구분",
                value_name="가입자 수(명)"
            )
        )

        # 쌓은 막대그래프
        fig_drill2 = px.bar(
            mix_chart,
            x="가입월",
            y="가입자 수(명)",
            color="집단 구분",
            barmode="stack",
            category_orders={
                "가입월": month_order,
                "집단 구분": [selected_group, "그 외"]
            },
            color_discrete_map={
                selected_group: NAVY,
                "그 외": DRILL_GRAY
            },
            title=(
                f"② 가입월별 가입자 구성 변화 "
                f"— {selected_group} vs 그 외"
            )
        )

        fig_drill2.update_traces(
            hovertemplate=(
                "가입월: %{x}<br>"
                "가입자 수: %{y:,.0f}명"
                "<extra></extra>"
            )
        )

        fig_drill2.update_layout(
            template="plotly_white",
            barmode="stack",
            height=440,
            margin=dict(
                l=50, r=30, t=90, b=60
            ),
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="left",
                x=0,
                title_text=""
            ),
            plot_bgcolor="white",
            bargap=0.3
        )

        fig_drill2.update_xaxes(
            title_text="가입월",
            type="category",
            categoryorder="array",
            categoryarray=month_order,
            showgrid=False
        )

        fig_drill2.update_yaxes(
            title_text="가입자 수(명)",
            rangemode="tozero",
            tickformat=",.0f",
            showgrid=True,
            gridcolor="#E9ECEF",
            zeroline=False
        )

        st.plotly_chart(
            fig_drill2,
            width="stretch"
        )

        st.caption(
            "전체 전환율이 움직인 시점에 이 집단의 비중이 "
            "함께 바뀌었다면 구성 변화가 원인 후보입니다. "
            "함께 움직였다고 원인이 확정되지는 않습니다."
        )

        # ==========================================
        # 3-3. 선택 집단 AI 해설
        # ==========================================
        with st.expander(
            f"🤖 AI 해설 · {selected_group}",
            expanded=False
        ):
            st.write(
                AI_NOTES.get(
                    selected_group,
                    "이 집단의 해설은 아직 없습니다."
                )
            )

            st.caption(AI_SOURCE)



with tab_action:
    st.caption(
        "측정 KPI의 현재 값은 선택한 기간의 마지막 3개월로 계산합니다"
    )

    # ==========================================
    # 4-1. 측정 기간 설정
    # 가입월 필터만 적용
    # 가입채널 및 플랫폼 필터는 적용하지 않음
    # ==========================================

    selected_months = sorted(
        df.loc[
            (df["가입월"] >= start)
            & (df["가입월"] <= end),
            "가입월"
        ].dropna().unique().tolist()
    )

    recent_months = selected_months[-3:]

    INSTA = "INSTA_첫주문쿠폰"

    # ==========================================
    # 카드 1: INSTA 첫 주문 전환율
    # ==========================================

    first_order_data = df.loc[
        (df["가입월"].isin(recent_months))
        & (df["가입채널"] == INSTA)
    ]

    first_order_num = first_order_data["첫주문30일"].sum()
    first_order_den = first_order_data["가입자"].sum()

    if first_order_den > 0:
        first_order_value = (
            f"{first_order_num / first_order_den * 100:.1f}%"
        )
    else:
        first_order_value = "데이터 없음"

    # ==========================================
    # 카드 2: INSTA 재주문율
    # ==========================================

    reorder_data = rd.loc[
        (rd["가입월"].isin(recent_months))
        & (rd["가입채널"] == INSTA)
    ]

    reorder_num = reorder_data["재주문30일"].sum()
    reorder_den = reorder_data["첫주문고객"].sum()

    if reorder_den > 0:
        reorder_value = (
            f"{reorder_num / reorder_den * 100:.1f}%"
        )
    else:
        reorder_value = "데이터 없음"

    # ==========================================
    # 4-2. 액션 카드 2개
    # ==========================================

    col1, col2 = st.columns(2, gap="large")

    # ------------------------------------------
    # 카드 1: 첫 주문 전환율 개선
    # ------------------------------------------
    with col1:
        with st.container(border=True):

            st.markdown(
                "### 카드 1 · INSTA 30일 첫 주문 전환율"
            )

            st.markdown(
                "- **문제:** INSTA 첫주문쿠폰 가입자의 "
                "첫 주문 전환율이 전체 평균보다 낮음.\n"
                "- **근거:** 전체 기간 INSTA 첫 주문 전환율은 "
                "55.6%로 전체 69.9%보다 14.3%p 낮으며, "
                "조회 → 장바구니 전환율도 72.4%로 "
                "전체 84.1%보다 낮음.\n"
                "- **성격:** 데이터로 확인된 전환율 차이이며, "
                "구체적인 이탈 원인은 추가 검증이 필요한 가설임.\n"
                "- **액션:** INSTA 가입자의 식당 조회 → "
                "장바구니 단계 이탈 요인을 분석하고, "
                "첫 주문 유도 개선안을 설계하여 A/B 테스트 진행.\n"
                "- **우선순위:** 높음 — 전체 대비 첫 주문 "
                "전환율 차이가 크고, 해당 캠페인 가입자가 "
                "739명으로 개선 대상 규모가 큼."
            )

            st.divider()

            st.metric(
                label="측정 KPI · 30일 첫 주문 전환율",
                value=first_order_value
            )

            st.caption(
                "대시보드에서: 개요 탭 → 월별 첫 주문 전환율 / "
                "탐색 탭 → 가입채널별 단계 전환율"
            )

    # ------------------------------------------
    # 카드 2: 재주문율 개선
    # ------------------------------------------
    with col2:
        with st.container(border=True):

            st.markdown(
                "### 카드 2 · INSTA 30일 재주문율"
            )

            st.markdown(
                "- **문제:** INSTA 첫주문쿠폰 가입자의 "
                "첫 주문 후 30일 이내 재주문율이 낮아 "
                "고객 유지 성과가 상대적으로 저조함.\n"
                "- **근거:** 전체 기간 INSTA 재주문율은 "
                "44.5%로 전체 평균 67.4%보다 "
                "22.9%p 낮으며, 첫 주문 고객 456명 중 "
                "203명이 30일 이내 재주문함.\n"
                "- **성격:** 재주문율 차이는 확인된 사실이나, "
                "쿠폰이나 고객 특성이 원인인지는 추가 검증이 필요함.\n"
                "- **액션:** INSTA 첫 주문 고객의 재주문 행동을 "
                "분석하고, 재주문 유도 메시지 및 혜택 제공 "
                "방식에 대한 실험을 설계하여 효과 검증.\n"
                "- **우선순위:** 높음 — 전체 대비 재주문율 "
                "차이가 22.9%p로 크고, "
                "신규 고객의 지속적인 이용을 유도할 "
                "필요가 있음."
            )

            st.divider()

            st.metric(
                label="측정 KPI · 30일 재주문율",
                value=reorder_value
            )

            st.caption(
                "대시보드에서: 개요 탭 → "
                "가입 채널별 30일 재주문율 비교"
            )
