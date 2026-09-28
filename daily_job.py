"""나라장터 용역 추천 — 매일 아침 자동 조회.

GitHub Actions가 정해진 시간에 실행한다(.github/workflows/daily_refresh.yml).
Streamlit 앱과 같은 방식으로 공고를 수집·분석해 구글 시트에 저장하면,
앱은 다음 접속 때(켜져 있으면 5분 안에) 시트의 새 목록을 불러온다.

필요한 GitHub Secrets
  G2B_API_KEY, GOOGLE_API_KEY, SHEET_ID
  GCP_SERVICE_ACCOUNT  서비스 계정 JSON 파일 내용 전체
선택(앱의 Streamlit Secrets와 같은 값으로 맞출 것)
  GEMINI_MODEL, LOOKBACK_DAYS, AI_WORKERS
"""
import json
import os
import sys
from pathlib import Path

import g2b_core as core


class Log:
    """앱의 진행 표시 대신 실행 기록(로그)에 한 줄씩 남긴다."""

    def caption(self, text):
        print(text, flush=True)

    def progress(self, value, text=""):
        print(text, flush=True)

    def empty(self):
        pass


def env(name, default=""):
    return os.environ.get(name, "").strip() or default


def main():
    missing = [
        name for name in (
            "G2B_API_KEY", "GOOGLE_API_KEY", "SHEET_ID", "GCP_SERVICE_ACCOUNT"
        )
        if not env(name)
    ]
    if missing:
        sys.exit("GitHub Secrets 설정 누락: " + ", ".join(missing))

    core.configure(
        G2B_API_KEY=env("G2B_API_KEY"),
        GOOGLE_API_KEY=env("GOOGLE_API_KEY"),
        MODEL=env("GEMINI_MODEL", "gemini-3.5-flash-lite"),
        DAYS=max(1, min(365, int(env("LOOKBACK_DAYS", "7")))),
        BATCHES=30,  # 자동 조회는 한 번에 최대한 많이(최대 1,800건)
        WORKERS=max(1, min(8, int(env("AI_WORKERS", "4")))),
        PROFILE_TEXT=core.load_profile(
            Path(__file__).with_name("experience.xlsx")
        ),
        CREDS=json.loads(env("GCP_SERVICE_ACCOUNT"), strict=False),
        SHEET_ID=env("SHEET_ID"),
    )

    # 이미 분석한 공고는 다시 분석하지 않도록 시트의 결과를 불러온다
    try:
        previous = core.load_sheet()
    except Exception as exc:
        sys.exit("구글 시트 읽기 실패: " + core.sheet_error(exc))
    cache = {
        row["_key"]: {"score": row["score"]}
        for row in (previous or {}).get("rows", [])
        if row.get("_key") and row["score"] is not None
    }

    # AI 응답 일부가 빠지면 최대 3번까지 이어서 분석
    for attempt in range(3):
        snapshot, cache, error = core.refresh(cache, Log(), Log())
        left = sum(row["score"] is None for row in snapshot["rows"])
        if not left:
            break
        print(f"미분석 {left}건 남음. 다시 분석합니다. ({error or ''})")

    try:
        core.save_sheet(snapshot)
    except Exception as exc:
        sys.exit("구글 시트 저장 실패: " + core.sheet_error(exc))

    print(
        f"완료: 공고 {len(snapshot['rows'])}건 저장 · 미분석 {left}건 · "
        f"AI 요청 한도 대기 {core.RATE_LIMITED[0]}번"
    )
    if left:
        # 실패로 표시해 GitHub가 담당자에게 알림 메일을 보내게 함
        sys.exit(f"미분석 공고 {left}건이 남았습니다. 앱에서 조회를 눌러주세요.")


if __name__ == "__main__":
    main()
