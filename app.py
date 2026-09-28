# app.py
import streamlit as st
import pandas as pd
import requests
import base64
import json
from datetime import datetime, timedelta

# 1. 페이지 기본 설정 (항상 최상단)
st.set_page_config(page_title="나라장터 용역 검색", layout="centered")

# 2. 보안 (비밀번호 폼 로그인) - 토시 하나 바꾸지 않고 적용
if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False
if not st.session_state["authenticated"]:
    st.warning("🔒 보안을 위해 비밀번호를 입력해주세요.")
    with st.form("login_form"):
        # 📌 st.secrets["APP_PASSWORD"] 설정 필요
        pwd = st.text_input("비밀번호", type="password")
        submitted = st.form_submit_button("확인")
        if submitted:
            if pwd == st.secrets.get("APP_PASSWORD", "1234"):
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("비밀번호가 일치하지 않습니다.")
    st.stop()

# 3. UI 최적화 CSS (기본 안내 문구 숨김 및 커스텀 로고 고정)
# 📌 모바일 환경에서도 우측 상단에 잘 보이도록 반응형(media query) 적용
st.markdown("""
    <style>
        /* Streamlit 기본 입력 안내 문구 숨김 */
        [data-testid="InputInstructions"] {display: none !important;}
        
        /* 우측 상단 회사 로고 고정 CSS */
        .company-logo {
            position: fixed;
            top: 70px;
            right: 30px;
            width: 120px;
            z-index: 99999;
        }
        @media (max-width: 768px) {
            .company-logo {
                top: 60px;
                right: 15px;
                width: 80px;
            }
        }
    </style>
""", unsafe_allow_html=True)

# 4. 회사 로고 (우측 상단 고정) 함수
def get_base64_of_bin_file(bin_file):
    with open(bin_file, 'rb') as f:
        data = f.read()
    return base64.b64encode(data).decode()

# 📌 로고 파일이 앱과 동일한 경로에 있어야 합니다. 파일명을 변경하려면 아래 문자열을 수정하세요.
try:
    logo_base64 = get_base64_of_bin_file("company_logo.png")
    st.markdown(
        f'<img src="data:image/png;base64,{logo_base64}" class="company-logo">',
        unsafe_allow_html=True
    )
except FileNotFoundError:
    pass # 파일이 없을 경우 에러 방지

# 얇은 여백 구분선 공통 변수
THIN_DIVIDER = '<hr style="margin-top: 15px; margin-bottom: 15px; border: 0; border-top: 1px solid rgba(49, 51, 63, 0.2);">'

# 5. 커스텀 복사 버튼 함수 (Base64 인코딩 및 JS 활용)
def create_copy_button(text_to_copy, button_label="📋 텍스트 복사"):
    text_b64 = base64.b64encode(text_to_copy.encode('utf-8')).decode('utf-8')
    button_uuid = base64.b64encode(text_to_copy.encode('utf-8')[:10]).decode('utf-8') + str(datetime.now().timestamp())
    
    custom_html = f"""
        <button id="btn-{button_uuid}" style="background-color: transparent; border: 1px solid rgba(49, 51, 63, 0.2); 
        color: inherit; padding: 0.25rem 0.75rem; font-size: 14px; border-radius: 0.25rem; cursor: pointer; transition: all 0.2s;"
        onclick="
            const text = decodeURIComponent(escape(window.atob('{text_b64}')));
            navigator.clipboard.writeText(text).then(function() {{
                const btn = document.getElementById('btn-{button_uuid}');
                const originalText = btn.innerHTML;
                btn.innerHTML = '✅ 복사 완료!';
                btn.style.borderColor = '#4CAF50';
                btn.style.color = '#4CAF50';
                setTimeout(function() {{
                    btn.innerHTML = originalText;
                    btn.style.borderColor = 'rgba(49, 51, 63, 0.2)';
                    btn.style.color = 'inherit';
                }}, 2000);
            }});
        ">
            {button_label}
        </button>
    """
    return custom_html


# 6. 사이드바 구성
with st.sidebar:
    # 홈 버튼 (포털 복귀) 및 얇은 여백 구분선
    st.markdown(
        '''
        <div style="margin-top: 5px;">
            <a href="https://ip2b-work-tools.streamlit.app/" target="_blank" style="text-decoration: none; color: #31333F; font-size: 15px; font-weight: 600;">
                🏠 홈으로
            </a>
        </div>
        <hr style="margin-top: 10px; margin-bottom: 15px; border: 0; border-top: 1px solid rgba(49, 51, 63, 0.2);">
        ''', 
        unsafe_allow_html=True
    )
    
    st.header("🔍 검색 설정")
    # 📌 공공데이터포털 API Key (일반적으로 st.secrets로 관리 권장)
    api_key = st.secrets.get("G2B_API_KEY", "").strip()

    if not api_key:
        st.error("Streamlit Secrets에 G2B_API_KEY를 등록해주세요.")
        st.stop()
    
    keyword = st.text_input("검색 키워드 (공고명)", placeholder="예: 데이터, AI, 시스템")
    
    today = datetime.now()
    default_start = today - timedelta(days=30)
    
    date_range = st.date_input(
        "조회 기간",
        value=(default_start, today),
        max_value=today
    )
    
    st.markdown(THIN_DIVIDER, unsafe_allow_html=True)
    search_btn = st.button("조회하기", use_container_width=True)

# 7. 메인 화면 구성
st.title("🏛️ 나라장터 입찰공고 검색")
st.markdown("공공데이터포털 조달청 나라장터 API를 활용하여 용역 공고를 검색합니다.")
st.markdown(THIN_DIVIDER, unsafe_allow_html=True)

def fetch_g2b_data(api_key, keyword, start_date, end_date):
    # 📌 조달청_나라장터 공공데이터포털 API 엔드포인트 (버전에 따라 URL 변경 가능성 있음)
    url = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService/getBidPblancListInfoServcPPSSrch"
    
    params = {
        "serviceKey": api_key,
        "numOfRows": 50,
        "pageNo": 1,
        "inqryDiv": 1,
        "inqryBgnDt": start_date.strftime("%Y%m%d0000"),
        "inqryEndDt": end_date.strftime("%Y%m%d2359"),
        "bidNtceNm": keyword,
        "type": "json"
    }
    
    response = requests.get(url, params=params)
    if response.status_code == 200:
        try:
            data = response.json()
            items = data.get('response', {}).get('body', {}).get('items', [])
            if not items:
                return []
            return items
        except json.JSONDecodeError:
            st.error("API 응답을 해석할 수 없습니다. API Key나 엔드포인트를 확인해주세요.")
            return None
    else:
        st.error(f"API 호출 실패 (상태 코드: {response.status_code})")
        return None

if search_btn:
    if not api_key:
        st.warning("사이드바에서 API Key를 입력해주세요.")
    elif not keyword:
        st.warning("검색 키워드를 입력해주세요.")
    elif len(date_range) != 2:
        st.warning("조회 시작일과 종료일을 모두 선택해주세요.")
    else:
        with st.spinner("나라장터에서 데이터를 불러오는 중입니다..."):
            start_date, end_date = date_range
            results = fetch_g2b_data(api_key, keyword, start_date, end_date)
            
            if results is not None:
                if len(results) == 0:
                    st.info("해당 조건에 맞는 입찰공고가 없습니다.")
                else:
                    # 데이터 정제
                    df = pd.DataFrame(results)
                    # 필요한 컬럼만 추출 (API 응답 명세에 맞춰 수정 가능)
                    cols_to_keep = {
                        'bidNtceNo': '공고번호',
                        'bidNtceNm': '공고명',
                        'ntceInsttNm': '공고기관',
                        'dminsttNm': '수요기관',
                        'bidNtceDt': '공고일시',
                        'presmptPrce': '추정가격(원)'
                    }
                    
                    available_cols = [c for c in cols_to_keep.keys() if c in df.columns]
                    df_display = df[available_cols].rename(columns=cols_to_keep)
                    
                    st.success(f"총 {len(df_display)}건의 공고를 찾았습니다.")
                    
                    # 표 출력
                    st.dataframe(df_display, use_container_width=True, hide_index=True)
                    
                    st.markdown(THIN_DIVIDER, unsafe_allow_html=True)
                    
                    col1, col2 = st.columns([1, 1])
                    with col1:
                        # CSV 다운로드 기능
                        csv = df_display.to_csv(index=False).encode('utf-8-sig')
                        st.download_button(
                            label="📥 CSV로 저장",
                            data=csv,
                            file_name=f"입찰공고_{keyword}_{datetime.now().strftime('%Y%m%d')}.csv",
                            mime="text/csv",
                            use_container_width=True
                        )
                    with col2:
                        # 주요 공고명 리스트 텍스트 복사 버튼 (규칙 8번 커스텀 복사 기능 활용)
                        summary_text = "\n".join([f"- {row['공고명']} ({row.get('공고기관', '')})" for _, row in df_display.iterrows()])
                        st.markdown(create_copy_button(summary_text, "📋 공고 목록 복사"), unsafe_allow_html=True)
