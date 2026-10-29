"""공고 하나를 기준표(profile.yaml)로 채점하는 모듈.

무료 방식: 공고 제목·키워드·직무분류에 기준표 단어가 들어 있는지 세어서 점수를 매김.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field


# ── 글자 비교 도우미 ─────────────────────────────────────────

def _squash(text: str) -> str:
    """대소문자·띄어쓰기·하이픈·밑줄을 없애 비교하기 쉽게 만듦."""
    return re.sub(r"[\s\-_]+", "", text.lower())


def _is_short_ascii(term: str) -> bool:
    # PK, SD, R, AI 같은 짧은 영문은 다른 단어 속에 숨어 있을 수 있어서 따로 처리
    return bool(re.fullmatch(r"[A-Za-z0-9]{1,3}", term))


def term_in(term: str, text: str) -> bool:
    if _is_short_ascii(term):
        # 앞뒤에 영문·숫자가 붙어 있지 않은 경우만 인정 (예: "PK분석"은 O, "SPK"는 X)
        pattern = rf"(?<![a-z0-9]){re.escape(term.lower())}(?![a-z0-9])"
        return re.search(pattern, text.lower()) is not None
    return _squash(term) in _squash(text)


# ── 채점 결과 ───────────────────────────────────────────────

@dataclass
class Score:
    total: int
    matched: dict[str, list[str]] = field(default_factory=dict)  # 묶음 이름 → 맞은 단어
    gaps: list[str] = field(default_factory=list)                # 부족한 점
    notes: list[str] = field(default_factory=list)               # 경력 조건 등 기타 설명


def job_text(job: dict) -> str:
    """채점에 쓸 공고 글자를 한 줄로 모음."""
    parts = [
        job.get("title", ""),
        job.get("keyword", ""),
        job.get("job_code", ""),
        job.get("job_mid", ""),
        job.get("industry", ""),
    ]
    return " | ".join(p for p in parts if p)


def score_job(job: dict, track: dict, exp_cfg: dict, my_years: int, track_key: str) -> Score:
    text = job_text(job)
    matched: dict[str, list[str]] = {}
    earned = 0.0
    weight_sum = 0.0

    for g in track["groups"]:
        weight_sum += g["weight"]
        hits = [t for t in g["terms"] if term_in(t, text)]
        if hits:
            matched[g["name"]] = hits
            earned += g["weight"] * (1.0 if len(hits) >= 2 else 0.5)

    total = earned / weight_sum * 100 if weight_sum else 0.0
    notes: list[str] = []

    gaps = [t for t in track.get("gaps", []) if term_in(t, text)]
    total -= 5 * len(gaps)

    excluded = [t for t in track.get("exclude", []) if term_in(t, text)]
    if excluded:
        total -= 40
        notes.append("방향이 다른 직무 표현: " + ", ".join(excluded))

    # 경력 조건
    exp_code = str(job.get("exp_code", ""))
    exp_max = job.get("exp_max") or 0
    if exp_code == "1":  # 신입만
        pen = exp_cfg["junior_only_penalty_ai" if track_key == "ai" else "junior_only_penalty"]
        total -= pen
        notes.append(f"신입 공고 (-{pen})")
    elif exp_max and exp_max < my_years - 3:
        total -= exp_cfg["too_low_max_penalty"]
        notes.append(f"경력 상한 {exp_max}년 (-{exp_cfg['too_low_max_penalty']})")

    total = max(0, min(100, math.floor(total + 0.5)))
    return Score(total=total, matched=matched, gaps=gaps, notes=notes)


# ── 지역·거리 ───────────────────────────────────────────────

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def locate(job: dict, profile: dict) -> tuple[str | None, float | None]:
    """공고 지역을 설정 파일의 지역 목록과 맞춰 (지역 이름, 집까지 직선거리 km)를 돌려줌.
    목록에 없으면 (None, None). 재택 공고는 ("재택", 0)."""
    loc = job.get("location", "")
    if any(term_in(w, job.get("title", "") + " " + loc) for w in profile.get("remote_words", [])):
        return "재택", 0.0

    home = profile["home"]
    best: tuple[str | None, float | None] = (None, None)
    # 한 공고에 지역이 여러 개(쉼표로 구분) 적혀 있을 수 있음 → 가장 가까운 곳으로
    for entry in loc.split(","):
        # 한 지역 안에서는 가장 구체적으로(긴 글자로) 맞는 지역을 고름
        # 예: "용인시 처인구"는 '용인시'보다 '용인시 처인구'로 판정
        hit, hit_len = None, 0
        for reg in profile["regions"]:
            for m in reg["match"]:
                if m in entry and len(m) > hit_len:
                    hit, hit_len = reg, len(m)
        if hit:
            d = round(haversine_km(home["lat"], home["lon"], hit["lat"], hit["lon"]), 1)
            if best[1] is None or d < best[1]:
                best = (hit["name"], d)
    return best
