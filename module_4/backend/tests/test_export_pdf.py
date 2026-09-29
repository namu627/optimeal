"""
test_export_pdf.py
==================
식단표 PDF 내보내기(/api/menu/export/pdf) 계약.

요구: 한글이 깨지지 않도록 한글 TTF 가 **임베드**되고(기본 Helvetica 미포함), 한글 폰트가 없으면
한글이 빠진 PDF 대신 503 을 돌려준다. reportlab 미설치 환경에서는 skip.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("reportlab")

BODY = {
    "title": "9/29 · 초등학생 중식",
    "summary": [{"label": "대상", "value": "초등학생"}, {"label": "인원", "value": "100명"}],
    "tables": [{"title": "식단표", "header": ["주차", "날짜", "요일", "끼니", "메뉴", "열량(kcal)", "단백질(g)"],
                "rows": [["1주차", "9/29", "화", "중식", "잡곡밥 · 미역국", 577, 31.2]]}],
    "recipes": [{"name": "미역국", "meta": "끓이기", "ingredients": [["건미역", "부재료", 200, 2]], "steps": []}],
}


def _font_available() -> bool:
    import os
    from pathlib import Path

    from module_4.backend.src.routers import export

    return bool(os.getenv("OPTIMEAL_PDF_FONT")) or any(Path(r).is_file() for r, _ in export._FONT_CANDIDATES)


def test_pdf_embeds_korean_font(client):
    """200 · application/pdf · 한글 TTF 서브셋 임베드 · 파일명 UTF-8 인코딩('/' → '-')."""
    if not _font_available():
        pytest.skip("한글 TTF 없음(fonts-nanum·맑은 고딕 모두 부재)")
    r = client.post("/api/menu/export/pdf", json=BODY)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")
    fonts = set(re.findall(rb"/BaseFont /([A-Za-z0-9+\-]+)", r.content))
    assert fonts and all(b"+" in f for f in fonts), fonts   # 전부 서브셋 임베드 폰트
    assert not any(b"Helvetica" in f for f in fonts)
    assert b"FontFile2" in r.content
    assert "9-29" in r.headers["content-disposition"]


def test_pdf_503_without_korean_font(client, monkeypatch):
    """한글 폰트를 못 찾으면 503 + reason=pdf_font_unavailable (한글 빠진 PDF 를 만들지 않음)."""
    from module_4.backend.src.routers import export

    monkeypatch.delenv("OPTIMEAL_PDF_FONT", raising=False)
    monkeypatch.setattr(export, "_FONT_CANDIDATES", [("/nonexistent/a.ttf", "/nonexistent/b.ttf")])
    monkeypatch.setattr(export, "_font_ready", False)
    r = client.post("/api/menu/export/pdf", json=BODY)
    assert r.status_code == 503
    assert r.json()["detail"]["reason"] == "pdf_font_unavailable"


def test_pdf_grid_rows_are_days(client):
    """식단표 그리드(행=날짜, 열=끼니) — 모든 날짜·끼니 머리칸·메뉴·영양값이 PDF 텍스트에 들어간다."""
    if not _font_available():
        pytest.skip("한글 TTF 없음")
    grid = {"title": "식단표", "columns": ["중식", "석식"], "rows": [
        {"label": "9/30 (화)", "sub": "1주차", "cells": [
            {"menus": ["잡곡밥", "미역국"], "kcal": 612, "protein": 24.3},
            {"menus": ["카레라이스"], "kcal": 540, "protein": 18}]},
        {"label": "10/1 (수)", "sub": "1주차", "cells": [{"menus": ["콩나물국"], "kcal": 500}, None]},
    ]}
    r = client.post("/api/menu/export/pdf", json={**BODY, "tables": [], "recipes": [], "grids": [grid]})
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    # 구조 확인은 폰트 서브셋 때문에 텍스트 추출이 필요 — pypdf 가 있을 때만(없으면 생성 성공까지만).
    pypdf = pytest.importorskip("pypdf")
    import io

    text = "".join(p.extract_text() for p in pypdf.PdfReader(io.BytesIO(r.content)).pages).replace(" ", "")
    for tok in ["9/30(화)", "10/1(수)", "중식", "석식", "잡곡밥", "미역국", "카레라이스", "콩나물국", "612kcal", "24.3g"]:
        assert tok in text, tok


def test_pdf_rejects_empty_title(client):
    assert client.post("/api/menu/export/pdf", json={**BODY, "title": ""}).status_code == 422
