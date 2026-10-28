import html
import io
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import urlopen

import pandas as pd
import streamlit as st
from wordcloud import WordCloud

st.set_page_config(page_title="댓글에서 찾는 마케팅 힌트", page_icon="💬", layout="wide")


class APIError(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def video_id_from_url(value):
    parsed = urlparse(value.strip())
    host = (parsed.hostname or "").lower()
    parts = [part for part in parsed.path.split("/") if part]
    candidate = ""
    if host in {"youtu.be", "www.youtu.be"} and parts:
        candidate = parts[0]
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
        if parsed.path == "/watch":
            candidate = parse_qs(parsed.query).get("v", [""])[0]
        elif len(parts) >= 2 and parts[0] in {"shorts", "live", "embed"}:
            candidate = parts[1]
    return candidate if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate) else None


def api_get(endpoint, params, key):
    url = "https://www.googleapis.com/youtube/v3/" + endpoint + "?" + urlencode({**params, "key": key})
    try:
        with urlopen(url, timeout=20) as response:
            return json.load(response)
    except HTTPError as exc:
        try:
            errors = json.loads(exc.read()).get("error", {}).get("errors", [])
            reason = errors[0].get("reason", "requestFailed") if errors else "requestFailed"
        except (ValueError, TypeError, KeyError):
            reason = "requestFailed"
        # 원문 오류와 요청 URL에는 API 키가 들어갈 수 있으므로 노출하지 않습니다.
        raise APIError(reason) from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise APIError("connectionFailed") from None


@st.cache_data(ttl=600, max_entries=30, show_spinner=False)
def load_video(video_id, key):
    data = api_get("videos", {"part": "snippet,statistics", "id": video_id}, key)
    items = data.get("items", [])
    return items[0] if items else None


@st.cache_data(ttl=600, max_entries=30, show_spinner=False)
def load_comments(video_id, maximum, order, key):
    rows, seen, page = [], set(), ""
    partial_error = ""
    more_available = False
    while len(rows) < maximum:
        params = {"part": "snippet", "videoId": video_id, "maxResults": min(100, maximum-len(rows)), "order": order, "textFormat": "plainText"}
        if page:
            params["pageToken"] = page
        try:
            data = api_get("commentThreads", params, key)
        except APIError as exc:
            if not rows:
                raise
            partial_error = exc.reason
            break
        for item in data.get("items", []):
            comment = item["snippet"]["topLevelComment"]
            if comment["id"] in seen:
                continue
            seen.add(comment["id"])
            snippet = comment["snippet"]
            rows.append({"댓글": html.unescape(snippet["textDisplay"]), "좋아요 수": int(snippet.get("likeCount", 0)), "작성일": snippet["publishedAt"][:10]})
        next_page = data.get("nextPageToken", "")
        more_available = bool(next_page)
        if not next_page or next_page == page:
            break
        page = next_page
    return rows[:maximum], more_available, partial_error


def tokenize(text):
    text = re.sub(r"https?://\S+", " ", text.lower())
    return re.findall(r"[가-힣]{2,}|[a-z]{2,}", text)


def theme_hits(comments, keywords):
    return [any(word.lower() in text.lower() for word in keywords) for text in comments]


def show_api_error(reason):
    if reason == "commentsDisabled":
        st.warning("댓글이 사용 중지된 영상이에요. 다른 영상으로 시도해 주세요.")
    elif reason in {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"}:
        st.error("API 사용 한도에 도달했어요. Google Cloud 할당량을 확인하거나 나중에 다시 시도하세요.")
    elif reason == "connectionFailed":
        st.error("유튜브에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.")
    else:
        st.error("데이터 요청이 거절됐어요. 영상 공개 여부, API 사용 설정, API 키와 키 제한 설정을 확인해 주세요.")


st.title("💬 댓글에서 찾는 마케팅 힌트")
st.write("유튜브 영상 링크를 넣고, 댓글 속 반복 단어와 가격·품질·구매 관련 언급을 살펴보세요.")
st.caption("실제 YouTube 공개 데이터 · 규칙 기반 탐색 도구 · AI 감성 분석이 아닙니다")
with st.form("video_form"):
    url = st.text_input("유튜브 영상 링크", placeholder="https://www.youtube.com/watch?v=...")
    left, right = st.columns(2)
    maximum = left.selectbox("최대 수집 댓글 수", [100, 200, 500], index=1)
    order_name = right.selectbox("수집 순서", ["최신순", "관련성순"])
    submitted = st.form_submit_button("영상과 댓글 분석하기", type="primary")

if submitted:
    video_id = video_id_from_url(url)
    if not video_id:
        st.warning("올바른 YouTube 영상 링크를 입력해 주세요. watch·youtu.be·shorts·live 링크를 지원해요.")
    else:
        try:
            key = st.secrets["YOUTUBE_API_KEY"]
        except (KeyError, FileNotFoundError):
            key = ""
        if not key:
            st.error('Streamlit의 Secrets에 YOUTUBE_API_KEY = "발급받은 키"를 설정해 주세요.')
        else:
            try:
                with st.spinner("영상 정보와 댓글을 불러오고 있어요..."):
                    video = load_video(video_id, key)
                    if not video:
                        st.warning("영상을 찾지 못했어요. 공개된 영상인지 확인해 주세요.")
                    else:
                        rows, more, partial = load_comments(video_id, maximum, "time" if order_name == "최신순" else "relevance", key)
                        st.session_state["result"] = {"video": video, "rows": rows, "more": more, "partial": partial, "order": order_name, "maximum": maximum, "time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
            except APIError as exc:
                show_api_error(exc.reason)

if "result" not in st.session_state:
    st.info("브랜드 광고, 제품 리뷰, 카페 소개 같은 공개 영상의 링크로 시작해 보세요.")
    st.stop()
result = st.session_state["result"]
video, rows = result["video"], result["rows"]
snippet, stats = video["snippet"], video.get("statistics", {})
st.subheader(html.unescape(snippet["title"]))
st.write(f"채널: {snippet['channelTitle']} · 게시일: {snippet['publishedAt'][:10]}")
st.link_button("YouTube 원본 영상 보기", "https://www.youtube.com/watch?v=" + video["id"])
cols = st.columns(3)
for col, field, label in zip(cols, ["viewCount", "likeCount", "commentCount"], ["조회수", "좋아요 수", "전체 댓글 수(API)"]):
    col.metric(label, f"{int(stats[field]):,}" if field in stats else "미제공")
st.caption(f"마지막으로 불러온 결과: {result['time']} · 같은 요청은 최대 10분간 재사용합니다.")
st.info(f"분석 대상: {result['order']}로 수집한 최상위 댓글 {len(rows)}개. 답글은 제외합니다. 전체 댓글 수와 분석 개수는 다를 수 있어요.")
if result["partial"]:
    st.warning("수집 도중 요청이 실패해 일부 댓글만 분석합니다.")
if result["more"]:
    st.caption("추가 댓글이 있지만, 선택한 최대 개수까지만 수집했어요.")
if not rows:
    st.warning("현재 수집할 수 있는 댓글이 없어요. 다른 영상으로 시도해 주세요.")
    st.stop()
df = pd.DataFrame(rows)

st.subheader("1. 댓글 워드클라우드")
stop_text = st.text_input("제외할 단어 (쉼표로 구분)", "진짜,정말,너무,그냥,영상,보고,이거,이게,있는,하는,ㅋㅋ,ㅎㅎ,https,www,com")
stop_words = {word.strip().lower() for word in stop_text.split(",") if word.strip()}
frequency = Counter(word for text in df["댓글"] for word in tokenize(text) if word not in stop_words)
st.caption("2글자 이상 한글·영문 문자열의 출현 횟수입니다. 명사 추출이나 감정 판정은 하지 않으며, ‘가격’과 ‘가격이’는 다른 단어로 셉니다.")
if frequency:
    font = Path(__file__).parent / "NanumGothic.ttf"
    if not font.exists():
        font = Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf")
    if font.exists():
        cloud = WordCloud(font_path=str(font), width=1200, height=600, background_color="white", colormap="viridis", max_words=80, random_state=42).generate_from_frequencies(frequency)
        st.image(cloud.to_array())
        image_buffer = io.BytesIO()
        cloud.to_image().save(image_buffer, format="PNG")
        st.download_button("워드클라우드 PNG 다운로드", image_buffer.getvalue(), "comment_wordcloud.png", "image/png")
    else:
        st.warning("한글 폰트가 없어요. GitHub에 packages.txt를 올렸는지 확인하고 Streamlit 앱을 재부팅하세요. 아래 단어 빈도표는 계속 사용할 수 있어요.")
    word_df = pd.DataFrame(frequency.most_common(30), columns=["단어", "출현 횟수"])
    st.dataframe(word_df, hide_index=True)
else:
    st.info("분석할 단어가 없어요. 제외 단어를 줄이거나 다른 영상을 선택해 보세요.")

st.subheader("2. 마케팅·경영 관점으로 댓글 읽기")
st.caption("관련 단어가 포함된 댓글 수를 셉니다. 한 댓글이 여러 항목에 포함될 수 있으며, 언급이 많다고 긍정적인 반응이나 구매 의도를 뜻하지는 않아요.")
themes = {
    "가격·가성비": ("가격,비싸,저렴,가성비,할인,금액", "가격 설명이나 구성별 비교 정보가 필요한지 원문을 확인하세요."),
    "제품·품질": ("품질,성능,맛,재질,내구,불량", "칭찬·불만의 구체적 대상을 확인하고 제품 설명 또는 개선 항목을 정리하세요."),
    "구매·이용": ("구매,구입,사고,사려,주문,예약,방문,이용", "구매 경험·질문·의향을 구분하고 안내가 부족한 부분을 찾으세요."),
    "서비스·배송": ("서비스,친절,불친절,배송,포장,응대,대기", "고객 경험과 운영 과정 중 점검할 지점을 확인하세요."),
    "정보 요청": ("어디,어떻게,궁금,알려,문의,위치,링크", "반복 질문을 FAQ·설명란·후속 영상으로 해결할 수 있는지 검토하세요."),
}
summary = []
for name, (defaults, hint) in themes.items():
    words = st.text_input(f"{name} 관련 단어", defaults, key="theme_" + name)
    keywords = [word.strip() for word in words.split(",") if word.strip()]
    mask = theme_hits(df["댓글"].tolist(), keywords)
    hits = df.loc[mask]
    summary.append({"관점": name, "언급 댓글 수": len(hits)})
    with st.expander(f"{name}: {len(hits)}개 / 분석 댓글 {len(df)}개"):
        st.write(hint)
        if not hits.empty:
            st.dataframe(hits.head(10), hide_index=True)
        else:
            st.write("설정한 단어가 포함된 댓글이 없어요.")
st.bar_chart(pd.DataFrame(summary).set_index("관점"))
st.warning("이 결과는 수집한 댓글의 탐색 자료예요. 댓글 작성자는 전체 고객을 대표하지 않으며, 이 데이터만으로 매출·실제 구매전환율·연령·성별·광고 효과를 알 수는 없어요.")

st.subheader("3. 원문과 분석 메모")
st.dataframe(df, hide_index=True)
st.download_button("수집 댓글 CSV 다운로드", df.to_csv(index=False).encode("utf-8-sig"), "youtube_comments.csv", "text/csv")
note = st.text_area("관찰한 반응 → 원문 근거 → 제안할 마케팅·운영 개선 → 추가 확인할 점", height=160, key="analysis_note")
st.download_button("분석 메모 다운로드", note.encode("utf-8"), "marketing_notes.txt", "text/plain")
st.caption("댓글 작성자 이름은 수집하지 않습니다. 메모는 현재 접속 세션에만 유지되므로 보관하려면 다운로드하세요.")
