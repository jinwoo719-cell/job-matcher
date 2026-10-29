"""채용공고 매칭 앱 — 실행: streamlit run app.py"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml
from dotenv import load_dotenv

import saramin
import storage
from scoring import haversine_km, locate, score_job

BASE = Path(__file__).parent
load_dotenv(BASE / ".env")
DAILY_LIMIT = 500

st.set_page_config(page_title="채용공고 매칭", page_icon="🔎", layout="wide")


# ── 준비 ─────────────────────────────────────────────────────

def load_profile() -> dict:
    prof = yaml.safe_load((BASE / "profile.yaml").read_text(encoding="utf-8"))
    # 실제 집 위치는 GitHub에 안 올라가는 .env에만 두고, 있으면 그걸 씀
    try:
        if os.getenv("HOME_LAT") and os.getenv("HOME_LON"):
            prof["home"] = {"name": os.getenv("HOME_NAME", "집"),
                            "lat": float(os.getenv("HOME_LAT")), "lon": float(os.getenv("HOME_LON"))}
    except ValueError:
        st.sidebar.warning(".env의 HOME_LAT/HOME_LON이 숫자가 아니라서 profile.yaml 기준점을 씁니다.")
    return prof


@st.cache_resource
def get_con():
    return storage.connect()


profile = load_profile()
con = get_con()
has_key = saramin.get_key() is not None
source = "api" if has_key else "sample"

# 키가 없을 때는 가짜 공고를 한 번 넣어 둠
if not has_key and not storage.load_jobs(con, "sample"):
    storage.upsert_jobs(con, saramin.load_sample(), "sample")


def refresh_from_api() -> None:
    queries = profile["search"]["queries"]["career"] + profile["search"]["queries"]["ai"]
    remaining = DAILY_LIMIT - storage.calls_today(con)
    if remaining < len(queries):
        st.sidebar.error(f"오늘 남은 호출이 {remaining}회라 {len(queries)}개 검색어를 다 돌릴 수 없어요.")
        return
    new_total, errors = 0, []
    bar = st.sidebar.progress(0.0, text="불러오는 중…")
    for i, q in enumerate(queries, 1):
        try:
            jobs = saramin.fetch(q, profile["search"]["loc_mcd"], profile["search"]["count"])
            new_total += storage.upsert_jobs(con, jobs, "api")
        except Exception as e:  # 한 검색어가 실패해도 나머지는 계속
            errors.append(f"{q}: {e}")
        finally:
            storage.add_calls(con, 1)
        bar.progress(i / len(queries), text=f"{q} 완료 ({i}/{len(queries)})")
    bar.empty()
    st.sidebar.success(f"새 공고 {new_total}건")
    for e in errors:
        st.sidebar.warning(e)


# ── 사이드바 ─────────────────────────────────────────────────

sb = st.sidebar
sb.title("🔎 채용공고 매칭")

if has_key:
    used = storage.calls_today(con)
    sb.caption(f"사람인 API 연결됨 · 오늘 호출 {used} / {DAILY_LIMIT}회")
    if sb.button("새 공고 불러오기", type="primary", use_container_width=True):
        refresh_from_api()
else:
    sb.info("API 키가 아직 없어서 **가짜 공고(예시)** 로 보여 주고 있어요. "
            "키를 받으면 `.env` 파일에 넣고 앱을 다시 켜세요.")

if sb.button("설정 파일 다시 읽고 채점", use_container_width=True):
    st.rerun()

sb.divider()
home = profile["home"]
regions_sorted = sorted(
    profile["regions"], key=lambda r: haversine_km(home["lat"], home["lon"], r["lat"], r["lon"])
)
region_names = ["재택"] + [r["name"] for r in regions_sorted]
sb.markdown(f"**지역** (집: {home['name']}에서 가까운 순)")
max_km = sb.slider("집에서 최대 거리 (직선 km)", 5, 40, 40, step=5)
picked = sb.multiselect("포함할 지역", region_names, default=region_names, label_visibility="collapsed")

sort_by = sb.radio("정렬", ["가까운 순", "점수 높은 순"], horizontal=True)
hide_expired = sb.checkbox("마감 지난 공고 빼기", value=True)
show_hidden = sb.checkbox("'숨김' 표시한 공고도 보기", value=False)
new_only = sb.checkbox("오늘 새로 들어온 공고만", value=False)


# ── 채점 ─────────────────────────────────────────────────────

today = date.today()


def d_day(deadline: str, close_type: str) -> tuple[str, int]:
    """(표시 글자, 정렬용 남은 일수)"""
    if close_type in ("3", "4") or not deadline:
        return ("상시" if close_type == "3" else "수시/채용시"), 9999
    left = (date.fromisoformat(deadline) - today).days
    return (f"D-{left}" if left > 0 else ("오늘 마감" if left == 0 else "마감")), left


rows = []
for j in storage.load_jobs(con, source):
    reg, km = locate(j, profile)
    if reg is None or reg not in picked or km > max_km:
        continue
    if j["status"] == "숨김" and not show_hidden:
        continue
    if new_only and j["first_seen"] != today.isoformat():
        continue
    dd_text, dd_left = d_day(j["deadline"], j["close_type"])
    if hide_expired and dd_left < 0:
        continue
    for key in ("career", "ai"):
        s = score_job(j, profile["tracks"][key], profile["experience"], profile["my_years"], key)
        rows.append(dict(job=j, track=key, score=s, region=reg, km=km, dd_text=dd_text, dd_left=dd_left))


# ── 공고 카드 ────────────────────────────────────────────────

def save_mark(job_id: str, tab: str) -> None:
    # 같은 공고가 두 탭에 다 나올 수 있어서, 화면 칸 이름에 탭 이름을 붙임
    storage.set_mark(con, job_id, st.session_state[f"st_{tab}_{job_id}"],
                     st.session_state.get(f"memo_{tab}_{job_id}", ""))


def card(r: dict, tab: str) -> None:
    j, s = r["job"], r["score"]
    with st.container(border=True):
        c1, c2 = st.columns([1, 9], vertical_alignment="center")
        color = "green" if s.total >= 60 else ("orange" if s.total >= 35 else "gray")
        c1.markdown(f"### :{color}[{s.total}]")
        badges = []
        if j["first_seen"] == today.isoformat():
            badges.append(":blue-badge[NEW]")
        if j["status"]:
            badges.append(f":violet-badge[{j['status']}]")
        if 0 <= r["dd_left"] <= 7:
            badges.append(f":red-badge[{r['dd_text']}]")
        c2.markdown(f"**[{j['title']}]({j['url']})** {' '.join(badges)}")
        km_txt = "재택" if r["region"] == "재택" else f"{r['region']} · 약 {r['km']:.0f}km"
        c2.caption(f"{j['company']}  ·  📍 {km_txt}  ·  {j['exp_name'] or '경력 정보 없음'}  ·  ⏰ {r['dd_text']}")

        with st.expander("왜 이 점수인지 · 표시하기"):
            if s.matched:
                st.markdown("**맞는 이유**")
                for g, hits in s.matched.items():
                    st.markdown(f"- {g}: {', '.join(hits)}")
            else:
                st.markdown("기준표 단어와 맞는 게 없어요.")
            if s.gaps:
                st.markdown("**부족할 수 있는 점**: " + ", ".join(s.gaps))
            for n in s.notes:
                st.markdown(f"- {n}")
            st.caption(f"공고 키워드: {j['keyword'] or '-'}  |  직무: {j['job_mid']} / {j['job_code']}")
            k1, k2 = st.columns([1, 3])
            k1.selectbox("표시", storage.STATUSES, index=storage.STATUSES.index(j["status"]),
                         key=f"st_{tab}_{j['id']}", on_change=save_mark, args=(j["id"], tab),
                         format_func=lambda x: x or "(없음)")
            k2.text_input("메모", value=j["memo"], key=f"memo_{tab}_{j['id']}",
                          on_change=save_mark, args=(j["id"], tab))


def show_track(key: str) -> None:
    t = profile["tracks"][key]
    cutoff = st.slider("최소 점수", 0, 100, t["min_score"], step=5, key=f"cut_{key}")
    sel = [r for r in rows if r["track"] == key and r["score"].total >= cutoff]
    if sort_by == "가까운 순":
        sel.sort(key=lambda r: (r["km"], -r["score"].total))
    else:
        sel.sort(key=lambda r: (-r["score"].total, r["km"]))
    st.caption(f"{len(sel)}건")
    if not sel:
        st.info("조건에 맞는 공고가 없어요. 최소 점수나 거리를 조정해 보세요.")
    for r in sel:
        card(r, key)


# ── 본문 ─────────────────────────────────────────────────────

if not has_key:
    st.warning("지금 보이는 공고는 화면 시험용 **가짜 데이터**입니다. 회사 이름 앞에 (예시)가 붙어 있어요.")

tab1, tab2, tab3 = st.tabs([
    f"🧪 {profile['tracks']['career']['label']}",
    f"🤖 {profile['tracks']['ai']['label']}",
    "⭐ 표시한 공고",
])
with tab1:
    show_track("career")
with tab2:
    show_track("ai")
with tab3:
    marked = {}
    for r in rows:
        j = r["job"]
        if j["status"] in ("관심", "지원함"):
            best = marked.get(j["id"])
            if best is None or r["score"].total > best["점수"]:
                marked[j["id"]] = {"표시": j["status"], "점수": r["score"].total,
                                   "탭": profile["tracks"][r["track"]]["label"], "회사": j["company"],
                                   "공고": j["title"], "지역": r["region"], "마감": r["dd_text"],
                                   "메모": j["memo"], "링크": j["url"]}
    if marked:
        st.dataframe(pd.DataFrame(marked.values()), hide_index=True, use_container_width=True,
                     column_config={"링크": st.column_config.LinkColumn("링크", display_text="열기")})
    else:
        st.info("공고 카드에서 '관심'이나 '지원함'을 표시하면 여기에 모여요.")
