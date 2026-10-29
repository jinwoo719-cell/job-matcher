"""사람인 오픈API에서 공고를 받아 와서, 앱이 쓰기 쉬운 모양으로 바꾸는 모듈.

API 키가 없으면 sample_jobs.json(가짜 공고)을 대신 읽어서 화면을 시험할 수 있음.

※ 요청 변수 이름(loc_mcd, count, sort 등)과 응답 구조는 사람인 API 문서 기준으로 작성했지만,
   키를 받으면 실제 응답과 한 번 맞춰 봐야 함 → README의 '키 받은 뒤 확인할 것' 참고.
"""
from __future__ import annotations

import html
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

API_URL = "https://oapi.saramin.co.kr/job-search"
SAMPLE_PATH = Path(__file__).parent / "sample_jobs.json"


class SaraminError(RuntimeError):
    pass


def get_key() -> str | None:
    key = os.getenv("SARAMIN_ACCESS_KEY", "").strip()
    return key or None


def fetch(keyword: str, loc_mcd: str, count: int = 110, start: int = 0) -> list[dict]:
    """검색어 하나로 공고 목록을 받아 옴 (호출 1회)."""
    key = get_key()
    if key is None:
        raise SaraminError("API 키가 없습니다 (.env 파일의 SARAMIN_ACCESS_KEY)")
    params = {
        "access-key": key,
        "keywords": keyword,
        "loc_mcd": loc_mcd,
        "count": count,
        "start": start,
        "sort": "pd",  # 최근 게시순
    }
    resp = requests.get(API_URL, params=params, headers={"Accept": "application/json"}, timeout=15)
    if resp.status_code != 200:
        raise SaraminError(f"사람인 응답 오류 {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    if "code" in data and "jobs" not in data:  # 오류 응답 (키 오류, 한도 초과 등)
        raise SaraminError(f"사람인 오류 코드 {data.get('code')}: {data.get('message', '')}")
    return [normalize(j) for j in data.get("jobs", {}).get("job", [])]


def load_sample() -> list[dict]:
    data = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    return [normalize(j) for j in data["jobs"]["job"]]


def _name(obj: dict | None) -> str:
    if not isinstance(obj, dict):
        return ""
    return html.unescape(str(obj.get("name", "")))


def _ts_to_date(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone().strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return ""


def normalize(raw: dict) -> dict:
    """사람인 응답의 공고 하나를 평평한 딕셔너리로 바꿈."""
    pos = raw.get("position", {}) or {}
    exp = pos.get("experience-level", {}) or {}
    close_type = (raw.get("close-type", {}) or {}).get("code", "")
    return {
        "id": str(raw.get("id", "")),
        "url": raw.get("url", ""),
        "company": html.unescape(((raw.get("company", {}) or {}).get("detail", {}) or {}).get("name", "")),
        "title": html.unescape(pos.get("title", "")),
        "location": _name(pos.get("location")).replace(" > ", " "),
        "industry": _name(pos.get("industry")),
        "job_mid": _name(pos.get("job-mid-code")),
        "job_code": _name(pos.get("job-code")),
        "keyword": html.unescape(raw.get("keyword", "")),
        "exp_code": str(exp.get("code", "")),
        "exp_min": int(exp.get("min") or 0),
        "exp_max": int(exp.get("max") or 0),
        "exp_name": _name(exp),
        "edu": _name(pos.get("required-education-level")),
        "salary": _name(raw.get("salary")),
        "posted": _ts_to_date(raw.get("posting-timestamp")),
        "deadline": _ts_to_date(raw.get("expiration-timestamp")),
        # 사람인 마감 유형: 1 마감일, 2 채용시 마감, 3 상시, 4 수시 (문서 기준)
        "close_type": str(close_type),
        "active": int(raw.get("active", 1) or 0),
    }
