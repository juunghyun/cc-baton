"""화면 문구 언어 (en | ko). 문구는 쓰는 자리에서 L("English", "한국어") 로 두 벌을 나란히 둔다.

고르는 순서: CC_BATON_LANG 환경변수 → 설정 "language" → 로캘(ko_*) → en.
"""
import os

LANGS = ("en", "ko")
_lang = None


def detect():
    """설정이 없을 때 고를 기본 언어."""
    loc = os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or ""
    return "ko" if loc.lower().startswith("ko") else "en"


def lang():
    global _lang
    if _lang is None:
        v = os.environ.get("CC_BATON_LANG")
        if v not in LANGS:
            from . import state  # state 가 이 모듈을 쓰므로 여기서 늦게
            v = state.config().get("language")
        _lang = v if v in LANGS else detect()
    return _lang


def set_lang(v):
    global _lang
    _lang = v


def L(en, ko):
    return ko if lang() == "ko" else en
