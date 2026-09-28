import base64
import hmac
import html
from datetime import datetime, time, timedelta
from pathlib import Path
from threading import Lock

import pandas as pd
import streamlit as st

import g2b_core as core

_LOGO = Path(__file__).with_name("company_logo.png")
st.set_page_config(
    page_title="나라장터 용역 추천",
    page_icon=str(_LOGO) if _LOGO.exists() else "📋",
    layout="wide",
)

# --- 🎨 통합 CSS (사내 도구 공통 스타일) ---
st.markdown(
    """
    <style>
    /* 'Press Enter to apply' 안내 문구 숨기기 */
    div[data-testid="InputInstructions"] { display: none !important; }

    /* 회사 로고: 우측 상단 고정 */
    .company-logo {
        position: fixed; top: 70px; right: 30px;
        width: 110px; z-index: 1000; cursor: pointer;
    }
    @media (max-width: 640px) {
        .company-logo { width: 80px; top: 60px; right: 10px; }
    }

    /* 조회 중 안내 창 */
    .busy-back {
        position: fixed; inset: 0; z-index: 999999;
        background: rgba(0, 0, 0, .35); backdrop-filter: blur(2px);
        display: flex; align-items: center; justify-content: center;
    }
    .busy-card {
        width: min(420px, calc(100vw - 32px));
        background: #fff; color: #31333F; border-radius: 14px;
        padding: 28px 28px 22px; text-align: center;
        box-shadow: 0 12px 40px rgba(0, 0, 0, .25);
    }
    .busy-spin {
        width: 38px; height: 38px; margin: 0 auto 14px;
        border: 4px solid rgba(49, 51, 63, .15);
        border-top-color: #FF4B4B; border-radius: 50%;
        animation: busy-rot .9s linear infinite;
    }
    @keyframes busy-rot { to { transform: rotate(360deg); } }
    .busy-title { font-size: 1.1em; font-weight: 700; margin-bottom: 6px; }
    .busy-step { font-size: .95em; opacity: .8; min-height: 1.4em; }
    .busy-track {
        height: 8px; margin: 14px 0 4px; border-radius: 4px;
        background: rgba(49, 51, 63, .12); overflow: hidden;
    }
    .busy-fill { height: 100%; background: #FF4B4B; transition: width .3s; }
    .busy-pct { font-size: .85em; opacity: .7; }
    .busy-note { margin-top: 14px; font-size: .8em; opacity: .6; }

    /* 결과 표: 새 공고·마감 임박 표시 */
    .tag-new {
        display: inline-block; margin-right: 6px; padding: 0 6px;
        border-radius: 4px; background: #FF4B4B; color: #fff;
        font-size: 11px; font-weight: 700; line-height: 18px;
        vertical-align: 1px;
    }
    .soon { color: #E03131; font-weight: 700; }
    .tag-rgn {
        display: inline-block; margin-left: 6px; padding: 0 6px;
        border-radius: 4px; border: 1px solid #F08C00; color: #E67700;
        font-size: 11px; font-weight: 700; line-height: 16px;
        vertical-align: 1px; cursor: help;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# --- 🖼️ 회사 로고 (클릭 시 홈페이지로 이동) ---
if _LOGO.exists():
    st.markdown(
        '<a href="http://www.iptob.co.kr/" target="_blank" '
        'title="(주)아이피투비 홈페이지로 이동">'
        '<img class="company-logo" alt="(주)아이피투비 로고" '
        'src="data:image/png;base64,'
        f'{base64.b64encode(_LOGO.read_bytes()).decode()}"></a>',
        unsafe_allow_html=True,
    )


# 관련도 점수별 표시(화면에는 색만 보임)
BADGE = {5: "🟢", 4: "🟡", 3: "⚪"}
SOON_DAYS = 3  # 마감까지 이 일수 이내면 빨간색으로 표시
# 참가가능지역 표시용 줄임말
SHORT_REGION = {
    "서울": "서울", "부산": "부산", "대구": "대구", "인천": "인천",
    "광주": "광주", "대전": "대전", "울산": "울산", "세종": "세종",
    "경기": "경기", "강원": "강원", "충청북": "충북", "충북": "충북",
    "충청남": "충남", "충남": "충남", "전라북": "전북", "전북": "전북",
    "전라남": "전남", "전남": "전남", "경상북": "경북", "경북": "경북",
    "경상남": "경남", "경남": "경남", "제주": "제주",
}


def short_regions(text):
    names = []
    for part in str(text).split(","):
        part = part.strip()
        name = next(
            (v for k, v in SHORT_REGION.items() if part.startswith(k)), part
        )
        if name not in names:
            names.append(name)
    return "·".join(names)
# 추정가격 필터 눈금(원)
PRICE_STEPS = [
    0, 20_000_000, 50_000_000, 100_000_000, 300_000_000,
    500_000_000, 1_000_000_000, float("inf"),
]


@st.cache_resource(show_spinner=False)
def shared():
    """직원 간 공유 저장소와 갱신 잠금."""
    return {"snapshot": None, "cache": {}}, Lock()


@st.cache_data(ttl=300, show_spinner=False)
def sheet_stamp():
    """시트의 마지막 저장 시각(5분마다 확인)."""
    return core.sheet_stamp()


def restore():
    """시트에 더 새로운 목록(아침 자동 조회 등)이 있으면 불러온다.

    앱이 새로 켜졌을 때와, 켜져 있는 동안 자동 조회가 시트를 갱신했을 때
    (최대 5분 뒤) 불러온다.
    """
    state, lock = shared()
    if not core.sheets_enabled():
        return
    try:
        stamp = sheet_stamp()
        current = state["snapshot"]["at"] if state["snapshot"] else ""
        if not stamp or stamp <= current:
            return
        if not lock.acquire(blocking=False):  # 조회 중이면 다음에
            return
        try:
            snap = core.load_sheet()
        finally:
            lock.release()
    except Exception as exc:
        st.warning(
            "구글 시트에서 저장 목록을 읽지 못했습니다. "
            + core.sheet_error(exc)
        )
        return
    if snap:
        state["snapshot"] = snap
        state["cache"] = {
            row["_key"]: {"score": row["score"]}
            for row in snap["rows"]
            if row.get("_key") and row["score"] is not None
        }


# ---------------------------------------------------------------- 설정·로그인

try:
    CFG = {
        key: str(st.secrets.get(key, "")).strip()
        for key in ["APP_PASSWORD", "G2B_API_KEY", "GOOGLE_API_KEY"]
    }
except FileNotFoundError:
    st.error("Streamlit Secrets를 먼저 설정해주세요.")
    st.stop()

if not all(CFG.values()):
    st.error(
        "Secrets 설정 누락: "
        + ", ".join(k for k, v in CFG.items() if not v)
    )
    st.stop()

DAYS = max(1, min(365, int(st.secrets.get("LOOKBACK_DAYS", 7))))
# 이 점수 이상인 공고만 결과에 표시(3~5, 기본 4)
MIN_SCORE = max(3, min(5, int(st.secrets.get("MIN_SCORE", 4))))

if not st.session_state.get("authenticated"):
    st.warning("🔒 보안을 위해 비밀번호를 입력해주세요.")
    with st.form("login_form"):
        pwd = st.text_input("비밀번호", type="password")
        if st.form_submit_button("확인"):
            if hmac.compare_digest(
                pwd.encode(), CFG["APP_PASSWORD"].encode()
            ):
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("비밀번호가 일치하지 않습니다.")
    st.stop()

HR = (
    '<hr style="margin-top: 15px; margin-bottom: 15px; border: 0; '
    'border-top: 1px solid rgba(128, 128, 128, 0.3);">'
)

with st.sidebar:
    st.markdown(
        """
        <div style="margin-top: 5px;">
            <a href="https://ip2b-work-tools.streamlit.app/" target="_blank"
               style="text-decoration: none; color: inherit;
                      font-size: 15px; font-weight: 600;">
                🏠 홈으로
            </a>
        </div>
        """ + HR.replace("margin-top: 15px", "margin-top: 10px"),
        unsafe_allow_html=True,
    )
    st.markdown("### 🔎 조회 기준")
    st.caption(f"공고 등록일 기준 최근 {DAYS}일을 조회합니다.")
    st.caption("수의계약 공고는 제외합니다.")
    st.markdown(HR, unsafe_allow_html=True)
    if st.button("🚪 로그아웃", use_container_width=True):
        st.session_state.clear()
        st.rerun()

try:
    PROFILE_TEXT = core.load_profile(
        Path(__file__).with_name("experience.xlsx")
    )
except Exception:
    st.error(
        "app.py와 같은 폴더에 experience.xlsx를 올려주세요. "
        "시트·열 이름은 원본을 유지하세요."
    )
    st.stop()

core.configure(
    G2B_API_KEY=CFG["G2B_API_KEY"],
    GOOGLE_API_KEY=CFG["GOOGLE_API_KEY"],
    MODEL=str(st.secrets.get("GEMINI_MODEL", "gemini-3.5-flash-lite")),
    DAYS=DAYS,
    BATCHES=max(1, min(30, int(st.secrets.get("AI_BATCHES_PER_CLICK", 20)))),
    # 동시에 보내는 AI 요청 수(1~8, 기본 4)
    WORKERS=max(1, min(8, int(st.secrets.get("AI_WORKERS", 4)))),
    PROFILE_TEXT=PROFILE_TEXT,
    CREDS=(
        dict(st.secrets["gcp_service_account"])
        if "gcp_service_account" in st.secrets else None
    ),
    SHEET_ID=str(st.secrets.get("SHEET_ID", "")).strip(),
)
PROFILE_HASH = core.PROFILE_HASH

# --- 📝 헤더 영역 ---
st.markdown(
    f"""
    <div style="text-align: center; width: 100%;">
        <h1 style="margin: 0; padding: 0; opacity: 0.85;">
            나라장터 용역 추천
        </h1>
        <p style="margin-top: 10px; font-size: 1.05em; opacity: 0.75;">
            기존 수행실적 {PROFILE_TEXT.count(chr(10)) + 1}건을 기준으로
            나라장터 용역 입찰공고를 검토합니다.
        </p>
    </div>
    <br>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------- 조회·갱신

class Busy:
    """조회 중 화면 가운데에 띄우는 작업 안내 창.

    refresh()가 쓰는 status.caption()·bar.progress()·empty()를 그대로
    받아 안내 창 안에 표시한다(처리 방식은 바꾸지 않음).
    """

    def __init__(self):
        self.box = st.empty()
        self.step = "나라장터에 접속하고 있습니다..."
        self.pct = None
        self.show()

    def show(self):
        bar = ""
        if self.pct is not None:
            pct = max(0, min(100, round(self.pct * 100)))
            bar = (
                f'<div class="busy-track"><div class="busy-fill" '
                f'style="width:{pct}%"></div></div>'
                f'<div class="busy-pct">{pct}%</div>'
            )
        self.box.markdown(
            '<div class="busy-back"><div class="busy-card">'
            '<div class="busy-spin"></div>'
            '<div class="busy-title">나라장터 공고를 조회하고 있습니다</div>'
            f'<div class="busy-step">{html.escape(self.step)}</div>'
            f"{bar}"
            '<div class="busy-note">창을 닫거나 새로고침하지 마세요.<br>'
            "새 공고가 많으면 몇 분 걸릴 수 있습니다.</div>"
            "</div></div>",
            unsafe_allow_html=True,
        )

    def caption(self, text):
        self.step, self.pct = text, None
        self.show()

    def progress(self, value, text=""):
        self.step, self.pct = text or self.step, value
        self.show()

    def empty(self):
        self.box.empty()


restore()

if st.button("🔄 나라장터 입찰공고 조회", type="primary"):
    state, lock = shared()
    if not lock.acquire(blocking=False):
        st.error("다른 직원이 갱신 중입니다. 잠시 후 다시 확인해주세요.")
    else:
        status = bar = Busy()
        try:
            snap, cache, error = core.refresh(state["cache"], status, bar)
            state["snapshot"], state["cache"] = snap, cache
            if core.sheets_enabled():
                status.caption("조회 결과를 구글 시트에 저장하고 있습니다...")
                try:
                    core.save_sheet(snap)
                except Exception as exc:
                    st.warning(
                        "구글 시트 저장에 실패했습니다. 이번 결과는 앱이 "
                        "켜져 있는 동안만 유지됩니다. " + core.sheet_error(exc)
                    )
            if error:
                st.error(error + " 여기까지 분석한 결과를 표시합니다.")
            else:
                st.success("공유 목록을 갱신했습니다.")
            if core.RATE_LIMITED[0]:
                st.info(
                    f"AI 요청 한도에 {core.RATE_LIMITED[0]}번 걸려 기다렸다가 "
                    "다시 보냈습니다. 자주 보이면 Secrets의 AI_WORKERS를 "
                    "줄여주세요."
                )
        except RuntimeError as exc:
            st.error(f"{exc} 마지막 저장 목록을 표시합니다.")
        except Exception:
            st.error("처리 오류. 설정과 입력 형식을 확인해주세요.")
        finally:
            lock.release()
            status.empty()
            bar.empty()

# ---------------------------------------------------------------- 결과

snapshot = shared()[0]["snapshot"]
if not snapshot:
    st.info("아직 저장된 목록이 없습니다. 조회 버튼을 눌러주세요.")
    st.stop()

st.caption(
    f"마지막 저장: {snapshot['at'][:16].replace('T', ' ')} (한국시간) · "
    f"수집 기간: {' ~ '.join(snapshot['scope'])}"
)
if snapshot.get("region_error"):
    st.caption(
        "⚠️ 참가가능지역 정보를 불러오지 못해 지역제한 표시가 빠졌습니다. "
        f"({snapshot['region_error']})"
    )
if snapshot["profile"] != PROFILE_HASH:
    st.warning(
        "실적 자료나 판단 기준이 바뀌었습니다. "
        "조회 버튼으로 갱신해주세요."
    )

rows = snapshot["rows"]
counts = {"미분석": sum(r.get("score") is None for r in rows)}
if counts["미분석"]:
    st.warning(
        f"아직 분석하지 않은 공고가 {counts['미분석']}건 남았습니다. "
        "조회 버튼을 다시 눌러 이어서 분석하세요."
    )


def new_since():
    """'새 공고' 기준: 직전 영업일 0시(월요일이면 금요일 0시)."""
    day = core.now().date() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return datetime.combine(day, time.min, tzinfo=core.KST)


cutoff, since = core.now(), new_since()
table = []
for r in rows:
    close = core.deadline(r["bidClseDt"])
    if (r.get("score") or 0) < MIN_SCORE or (close and close <= cutoff):
        continue
    posted = core.deadline(r.get("bidNtceDt", ""))
    left = (close.date() - cutoff.date()).days if close else None
    region = str(r.get("rgnLmt") or "")
    table.append({
        "공고명": r["bidNtceNm"],
        "공고기관": r["ntceInsttNm"],
        "입찰마감": r["bidClseDt"] if close else "확인 필요",
        "추정가격(원)": pd.to_numeric(r["presmptPrce"], errors="coerce"),
        "공고 링크": (
            r["bidNtceDtlUrl"]
            if str(r["bidNtceDtlUrl"]).startswith(("https://", "http://"))
            else ""
        ),
        "비고": BADGE[r["score"]],
        "공고번호": f"{r['bidNtceNo']}-{r['bidNtceOrd']}",
        "_마감": close,
        "_새공고": bool(posted and posted >= since),
        # 본점(서울)이 참가가능지역에 없으면 지역제한
        "_지역제한": (
            region if region and core.HOME_REGION not in region else ""
        ),
        "_남은일": left if left is not None and left <= SOON_DAYS else None,
    })
table = pd.DataFrame(table)


def render(frame):
    """내용 길이에 딱 맞는 HTML 표(엑셀 열 너비 자동 맞춤과 같은 방식)."""
    cols = ["공고명", "공고기관", "입찰마감", "추정가격(원)", "공고 링크", "비고"]
    head = "".join(f"<th>{c}</th>" for c in cols)
    body = []
    for _, r in frame.iterrows():
        price = r["추정가격(원)"]
        link = r["공고 링크"]
        name = html.escape(str(r["공고명"]))
        if r["_새공고"]:
            name = '<span class="tag-new">NEW</span>' + name
        if r["_지역제한"]:
            name += (
                '<span class="tag-rgn" title="'
                f'{html.escape(r["_지역제한"], quote=True)}">'
                f'지역제한</span>'
            )
        close = html.escape(str(r["입찰마감"]))
        if pd.notna(r["_남은일"]):
            left = int(r["_남은일"])
            close = (
                f'<span class="soon">{close} '
                f'({"오늘 마감" if left == 0 else f"D-{left}"})</span>'
            )
        cells = [
            name,
            html.escape(str(r["공고기관"])),
            close,
            f"{int(price):,}" if pd.notna(price) else "",
            (
                f'<a href="{html.escape(link, quote=True)}" '
                'target="_blank">열기</a>' if link else ""
            ),
            r["비고"],
        ]
        body.append(
            "<tr>" + "".join(
                f'<td class="c{i}">{v}</td>' for i, v in enumerate(cells)
            ) + "</tr>"
        )
    return (
        "<style>"
        ".g2b{max-height:640px;overflow:auto;margin-bottom:1rem;}"
        ".g2b table{border-collapse:collapse;font-size:14px;}"
        ".g2b th,.g2b td{white-space:nowrap;padding:6px 12px;"
        "border-bottom:1px solid rgba(128,128,128,.25);}"
        ".g2b th{position:sticky;top:0;text-align:left;"
        "background:var(--background-color,#fff);"
        "border-bottom:2px solid rgba(128,128,128,.5);}"
        ".g2b .c3{text-align:right;}"
        ".g2b .c4,.g2b .c5{text-align:center;}"
        "</style>"
        f'<div class="g2b"><table><thead><tr>{head}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table></div>'
    )


def won(value):
    if value == float("inf"):
        return "제한 없음"
    if value >= 100_000_000:
        return f"{value / 100_000_000:g}억원"
    if value >= 10_000_000:
        return f"{value / 10_000_000:g}천만원"
    return "0원"


if table.empty:
    st.info("검색 결과 0건")
    st.stop()

# 입찰마감이 빠른 순, 마감일을 알 수 없는 공고('확인 필요')는 맨 끝
table["_정렬"] = pd.to_datetime(table["_마감"], utc=True)
table = table.sort_values(
    "_정렬", na_position="last", kind="stable"
).drop(columns="_정렬")

# --- 🔍 검색·필터 ---
c1, c2, _ = st.columns([2.2, 2, 3.8], gap="small")
query = c1.text_input(
    "🔍 검색", placeholder="공고명, 공고기관 등"
)
low, high = c2.select_slider(
    "💰 추정가격",
    options=PRICE_STEPS,
    value=(PRICE_STEPS[0], PRICE_STEPS[-1]),
    format_func=won,
)
r1, r2, _ = st.columns([0.8, 1, 6])
only_new = r2.toggle("🆕 새 공고만")

shown = table
for word in query.split():
    text = shown["공고명"].astype(str) + " " + shown["공고기관"].astype(str)
    shown = shown[text.str.contains(word, case=False, regex=False)]
if (low, high) != (PRICE_STEPS[0], PRICE_STEPS[-1]):
    # 가격 범위를 좁히면 추정가격이 없는 공고는 제외
    price = shown["추정가격(원)"]
    shown = shown[price.notna() & (price >= low) & (price <= high)]
if only_new:
    shown = shown[shown["_새공고"]]

r1.caption(f"검색 결과 {len(shown)}건")
if shown.empty:
    st.info("조건에 맞는 공고가 없습니다.")
else:
    st.markdown(render(shown), unsafe_allow_html=True)

    csv = shown.assign(지역제한=shown["_지역제한"])
    csv = csv.drop(columns=[c for c in csv.columns if c.startswith("_")])
    csv["추정가격(원)"] = csv["추정가격(원)"].map(
        lambda v: f"{int(v):,}" if pd.notna(v) else ""
    )
    st.download_button(
        "📥 결과 CSV 저장",
        csv.to_csv(index=False).encode("utf-8-sig"),
        "용역추천.csv",
        "text/csv",
    )
