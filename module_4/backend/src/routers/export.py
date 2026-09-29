"""
routers/export.py
=================
확정 식단 PDF 내보내기 — 모듈 4 전용.

표의 **내용은 프론트가 만든다**(CSV 식단표와 같은 칸 데이터 — 식단표는 웹처럼 행=날짜·열=끼니
그리드(grids)로, 조리 지시서는 메뉴별 상세(recipes)로 보냄). 여기서는 받은 내용을 A4 PDF 로
조판만 한다. 그래서 검토 화면에서 교체·삭제한 결과까지 CSV 와 PDF 가 항상 같은 내용이 되고,
계산 로직이 두 곳으로 갈라지지 않는다.

한글: TrueType 한글 폰트를 PDF 에 **서브셋 임베드**한다(뷰어에 폰트가 없어도 깨지지 않음).
폰트는 OPTIMEAL_PDF_FONT(·_BOLD) 환경변수 → Docker 이미지의 fonts-nanum(NanumGothic)
→ Windows 개발 PC 의 맑은 고딕 순으로 찾는다. 한글 폰트가 하나도 없으면 한글이 빠진 PDF 를
만들지 않고 503(reason=pdf_font_unavailable)을 돌려준다.
"""

from __future__ import annotations

import io
import os
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/menu/export", tags=["export"])

# (regular, bold) 후보. bold 가 없으면 regular 로 대신한다.
_FONT_CANDIDATES = [
    ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),
    ("C:/Windows/Fonts/malgun.ttf", "C:/Windows/Fonts/malgunbd.ttf"),
]
_FONT, _FONT_BOLD = "OptiMealKR", "OptiMealKR-Bold"
_font_lock = threading.Lock()
_font_ready = False

# 화면(theme.ts)과 같은 계열의 절제된 색 — 흰 배경, 머리행만 옅은 초록.
_TEXT, _SUB, _LINE, _HEAD_BG, _LABEL_BG = "#16211C", "#5D6B64", "#D9E3DE", "#E4F7EB", "#F4F7F5"


class SummaryItem(BaseModel):
    label: str = Field(..., max_length=40)
    value: str = Field(..., max_length=200)


class PdfTable(BaseModel):
    """표 하나. rows 의 각 행은 header 와 길이가 같아야 한다(짧으면 빈 칸으로 채움)."""
    title: str = Field(..., max_length=100)
    header: list[str] = Field(..., min_length=1, max_length=12)
    rows: list[list[str | float | int | None]] = Field(default_factory=list, max_length=5000)


class GridCell(BaseModel):
    """그리드 한 칸(그 날·그 끼니) — 메뉴 이름들 + 1인 기준 열량·단백질."""
    menus: list[str] = Field(default_factory=list, max_length=20)
    kcal: float | None = None
    protein: float | None = None


class GridRow(BaseModel):
    """그리드 한 행 = 하루. cells 는 PdfGrid.columns 와 같은 순서(끼니)."""
    label: str = Field(..., max_length=40, description="예: '9/30 (화)'")
    sub: str = Field("", max_length=40, description="보조 표기(예: '1주차')")
    cells: list[GridCell | None] = Field(default_factory=list, max_length=7)


class PdfGrid(BaseModel):
    """식단표 그리드 — 행 = 날짜, 열 = 끼니(웹 식단표와 같은 읽기 흐름)."""
    title: str = Field(..., max_length=100)
    corner: str = Field("날짜", max_length=20, description="왼쪽 위 머리칸")
    columns: list[str] = Field(..., min_length=1, max_length=7, description="끼니 이름(실제 생성된 끼니만)")
    rows: list[GridRow] = Field(default_factory=list, max_length=400)


class PdfRecipe(BaseModel):
    """조리 지시서의 메뉴 하나 — 재료 표 + 조리 순서(원문). 비어 있으면 빈 상태 문구만 찍는다."""
    name: str = Field(..., max_length=200)
    meta: str = Field("", max_length=300)
    ingredients: list[list[str | float | int | None]] = Field(default_factory=list, max_length=200)
    steps: list[str] = Field(default_factory=list, max_length=40)


class ExportPdfRequest(BaseModel):
    """PDF 내보내기 요청 — 프론트 planExport.ts 가 만든다."""
    model_config = {"json_schema_extra": {"example": {
        "title": "9/29 · 초등학생 중식", "file_name": "9/29 · 초등학생 중식_식단표",
        "summary": [{"label": "대상", "value": "초등학생 저학년(남) · 100명"}],
        "grids": [{"title": "식단표", "columns": ["중식"],
                   "rows": [{"label": "9/29 (월)", "sub": "1주차",
                             "cells": [{"menus": ["잡곡밥", "미역국"], "kcal": 612, "protein": 24.3}]}]}],
        "recipes": []}}}
    title: str = Field(..., min_length=1, max_length=200, description="문서 제목(식단 이름)")
    file_name: str = Field("", max_length=200, description="내려받을 파일명(확장자 제외). 비면 title")
    summary: list[SummaryItem] = Field(default_factory=list, max_length=12)
    grids: list[PdfGrid] = Field(default_factory=list, max_length=10, description="식단표 그리드(행=날짜, 열=끼니)")
    tables: list[PdfTable] = Field(default_factory=list, max_length=10, description="일반 표(행 나열)")
    recipes_title: str = Field("조리 지시서", max_length=100)
    recipes_note: str = Field("", max_length=300)
    recipes: list[PdfRecipe] = Field(default_factory=list, max_length=500)


def _register_fonts() -> None:
    """한글 TTF 를 한 번만 등록한다. 없으면 503."""
    global _font_ready
    with _font_lock:
        if _font_ready:
            return
        from reportlab.lib.fonts import addMapping
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        env = os.getenv("OPTIMEAL_PDF_FONT")
        candidates = ([(env, os.getenv("OPTIMEAL_PDF_FONT_BOLD") or env)] if env else []) + _FONT_CANDIDATES
        for regular, bold in candidates:
            if regular and Path(regular).is_file():
                pdfmetrics.registerFont(TTFont(_FONT, regular))
                pdfmetrics.registerFont(TTFont(_FONT_BOLD, bold if bold and Path(bold).is_file() else regular))
                addMapping(_FONT, 0, 0, _FONT)
                addMapping(_FONT, 1, 0, _FONT_BOLD)
                _font_ready = True
                return
        raise HTTPException(status_code=503, detail={
            "reason": "pdf_font_unavailable",
            "message": "PDF용 한글 폰트를 찾지 못했습니다.",
            "hint": "Docker 이미지의 fonts-nanum 설치를 확인하거나 OPTIMEAL_PDF_FONT 에 한글 TTF 경로를 지정하세요."})


def _cell_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:,.1f}".rstrip("0").rstrip(".") if v % 1 else f"{int(v):,}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _disp_width(s: str) -> int:
    """열 폭 추정용 표시 폭 — 한글·전각은 2, 그 외 1."""
    return sum(2 if ord(ch) >= 0x1100 else 1 for ch in s)


def _build_pdf(req: ExportPdfRequest) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    c = colors.HexColor
    base = ParagraphStyle("base", fontName=_FONT, fontSize=8.8, leading=12, textColor=c(_TEXT), wordWrap="CJK")
    right = ParagraphStyle("right", parent=base, alignment=TA_RIGHT)
    head = ParagraphStyle("head", parent=base, fontName=_FONT_BOLD)
    head_r = ParagraphStyle("head_r", parent=head, alignment=TA_RIGHT)
    title = ParagraphStyle("title", parent=base, fontName=_FONT_BOLD, fontSize=16, leading=21)
    h2 = ParagraphStyle("h2", parent=base, fontName=_FONT_BOLD, fontSize=11.5, leading=16, spaceBefore=10, spaceAfter=6)
    h3 = ParagraphStyle("h3", parent=base, fontName=_FONT_BOLD, fontSize=10, leading=14)
    sub = ParagraphStyle("sub", parent=base, textColor=c(_SUB), fontSize=8.3, leading=11)
    step = ParagraphStyle("step", parent=base, fontSize=8.8, leading=12.5, leftIndent=2, spaceAfter=2)
    p = lambda s, st=base: Paragraph(escape(s).replace("\n", "<br/>"), st)  # noqa: E731

    page_w, _ = A4
    margin = 16 * mm
    avail = page_w - 2 * margin
    grid = TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, c(_LINE)), ("FONTNAME", (0, 0), (-1, -1), _FONT),
        ("BACKGROUND", (0, 0), (-1, 0), c(_HEAD_BG)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ])

    def table(header: list[str], rows: list[list], widths: list[float] | None = None) -> Table:
        n = len(header)
        rows = [(list(r) + [None] * n)[:n] for r in rows]
        # 숫자만 있는 열은 오른쪽 정렬(빈 칸은 무시)
        numeric = [bool(rows) and all(r[i] is None or r[i] == "" or isinstance(r[i], (int, float)) for r in rows)
                   and any(isinstance(r[i], (int, float)) for r in rows) for i in range(n)]
        data = [[p(h, head_r if numeric[i] else head) for i, h in enumerate(header)]]
        data += [[p(_cell_text(v), right if numeric[i] else base) for i, v in enumerate(r)] for r in rows]
        if widths is None:
            # 긴 글(메뉴·재료명)이 들어가는 열에 폭을 몰아준다. 한글은 영문·숫자의 약 2배 폭으로 센다.
            # 열마다 한 줄에 들어갈 폭(반각 1자 ≈ 1.75mm @8.8pt + 좌우 여백)을 주고, 남거나 모자란 폭은
            # 가장 넓은 열이 흡수한다 → 주차·요일 같은 짧은 열이 꺾이지 않는다.
            lens = [max([_disp_width(header[i])] + [_disp_width(_cell_text(r[i])) for r in rows[:300]]) for i in range(n)]
            widths = [x * 1.75 * mm + 3.4 * mm for x in lens]
            widest = widths.index(max(widths))
            widths[widest] = max(30 * mm, widths[widest] + avail - sum(widths))
        t = Table(data, colWidths=widths, repeatRows=1)
        t.setStyle(grid)
        return t

    story: list = [p(req.title, title), Spacer(1, 3)]
    story.append(p(f"OptiMeal · {datetime.now():%Y-%m-%d %H:%M} 생성", sub))
    story.append(Spacer(1, 8))
    if req.summary:
        cells = [[p(s.label, sub), p(s.value)] for s in req.summary]
        if len(cells) % 2:
            cells.append([p(""), p("")])
        pairs = [cells[i] + cells[i + 1] for i in range(0, len(cells), 2)]
        lw, vw = avail * 0.14, avail * 0.36
        st = Table(pairs, colWidths=[lw, vw, lw, vw])
        st.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, c(_LINE)), ("FONTNAME", (0, 0), (-1, -1), _FONT),
            ("BACKGROUND", (0, 0), (0, -1), c(_LABEL_BG)), ("BACKGROUND", (2, 0), (2, -1), c(_LABEL_BG)),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(st)

    # 메뉴마다 '·' 머리표 + 내어쓰기 — 긴 메뉴명이 칸 안에서 꺾여도 메뉴 경계가 보인다.
    menu_st = ParagraphStyle("menu", parent=base, fontSize=8.8, leading=12.2, leftIndent=7, firstLineIndent=-7)
    nutri_st = ParagraphStyle("nutri", parent=sub, fontSize=7.4, leading=9.5)
    day_st = ParagraphStyle("day", parent=head, fontSize=9, leading=12)

    def day_grid(g: PdfGrid) -> Table:
        """행 = 하루, 열 = 끼니. 칸 = 메뉴(줄마다 하나) + 아래 작게 1인 열량·단백질."""
        n = len(g.columns)
        date_w = 24 * mm
        meal_w = (avail - date_w) / n   # 끼니 열은 남은 폭을 똑같이 — 메뉴명은 칸 안에서 줄바꿈된다
        data = [[p(g.corner, head)] + [p(col, head) for col in g.columns]]
        for r in g.rows:
            row = [[p(r.label, day_st)] + ([p(r.sub, nutri_st)] if r.sub else [])]
            cells = (list(r.cells) + [None] * n)[:n]
            for cell in cells:
                if cell is None or not cell.menus:
                    row.append(p("—", sub))
                    continue
                parts = [p(f"· {m}", menu_st) for m in cell.menus]
                nutri = " · ".join(x for x in (
                    f"{_cell_text(cell.kcal)} kcal" if cell.kcal is not None else "",
                    f"단백질 {_cell_text(cell.protein)} g" if cell.protein is not None else "") if x)
                if nutri:
                    parts += [Spacer(1, 2), p(nutri, nutri_st)]
                row.append(parts)
            data.append(row)
        t = Table(data, colWidths=[date_w] + [meal_w] * n, repeatRows=1)
        t.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, c(_LINE)), ("FONTNAME", (0, 0), (-1, -1), _FONT),
            ("BACKGROUND", (0, 0), (-1, 0), c(_HEAD_BG)), ("BACKGROUND", (0, 1), (0, -1), c(_LABEL_BG)),
            ("VALIGN", (0, 0), (-1, 0), "MIDDLE"), ("VALIGN", (0, 1), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ]))
        return t

    for g in req.grids:
        story.append(p(g.title, h2))
        story.append(day_grid(g) if g.rows else p("표시할 행이 없어요", sub))

    for t in req.tables:
        story.append(p(t.title, h2))
        story.append(table(t.header, t.rows) if t.rows else p("표시할 행이 없어요", sub))

    if req.recipes:
        story.append(p(req.recipes_title, h2))
        if req.recipes_note:
            story.append(p(req.recipes_note, sub))
            story.append(Spacer(1, 4))
        ing_header = ["재료명", "역할", "투입량(g, 총량)", "1인분(g)"]
        ing_widths = [avail * 0.44, avail * 0.16, avail * 0.22, avail * 0.18]
        for r in req.recipes:
            block = [Spacer(1, 6), p(r.name, h3)]
            if r.meta:
                block.append(p(r.meta, sub))
            block.append(Spacer(1, 3))
            block.append(table(ing_header, r.ingredients, ing_widths) if r.ingredients else p("재료 데이터 없음", sub))
            block.append(Spacer(1, 4))
            story.append(KeepTogether(block))
            if r.steps:
                story.extend(p(s, step) for s in r.steps)
            else:
                story.append(p("레시피 미등록 — 데이터셋에 조리 순서가 없어요", sub))

    class NumberedCanvas(Canvas):
        """바닥글에 'n / 전체' 쪽 번호."""
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._pages: list = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                self.setFont(_FONT, 7.5)
                self.setFillColor(c(_SUB))
                self.drawRightString(page_w - margin, 9 * mm, f"{self._pageNumber} / {total}")
                self.drawString(margin, 9 * mm, req.title[:80])
                super().showPage()
            super().save()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=margin, rightMargin=margin,
                            topMargin=14 * mm, bottomMargin=16 * mm, title=req.title, author="OptiMeal",
                            initialFontName=_FONT)  # 기본 Helvetica(한글 없음·미임베드)가 문서에 끼지 않게
    doc.build(story, canvasmaker=NumberedCanvas)
    return buf.getvalue()


@router.post("/pdf", summary="식단표·조리 지시서 PDF 내보내기 (한글 폰트 임베드)",
             response_class=Response,
             responses={200: {"content": {"application/pdf": {}}, "description": "PDF 파일"}})
def export_pdf(req: ExportPdfRequest) -> Response:
    """프론트가 만든 표(CSV 와 같은 행)를 A4 PDF 로 조판해 내려준다.

    Raises:
        HTTPException(503): reportlab 미설치 또는 한글 폰트 없음.
    """
    try:
        import reportlab  # noqa: F401
    except ImportError as exc:
        raise HTTPException(status_code=503, detail={
            "reason": "reportlab_missing", "message": "PDF 생성 라이브러리(reportlab)가 설치되지 않았습니다.",
            "hint": "pip install -r requirements.txt"}) from exc
    _register_fonts()
    pdf = _build_pdf(req)
    name = (req.file_name or req.title).strip() or "식단"
    # 파일명에 못 쓰는 문자는 '-' 로(예: 9/29 → 9-29)
    safe = "".join("-" if ch in '\\/:*?"<>|' else ch for ch in name).strip() or "식단"
    return Response(content=pdf, media_type="application/pdf", headers={
        "Content-Disposition": f"attachment; filename=\"optimeal.pdf\"; filename*=UTF-8''{quote(safe + '.pdf')}"})
