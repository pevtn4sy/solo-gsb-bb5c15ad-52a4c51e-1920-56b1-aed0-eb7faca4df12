"""双面拼版校样 PDF（/api/v1/impose/proof.pdf）测试。"""
import re
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from app.main import app

client = TestClient(app)

MM_TO_PT = 72.0 / 25.4

PROOF_BASE = {
    "total_pages": 4,
    "max_sheets_per_signature": 1,
    "binding": "left",
    "page_width_mm": 210.0,
    "page_height_mm": 297.0,
    "gutter_mm": 10.0,
    "duplex_flip": "long",
}


def _post(body) -> bytes:
    response = client.post("/api/v1/impose/proof.pdf", json=body)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment")
    assert "proof.pdf" in disposition
    assert response.content.startswith(b"%PDF")
    return response.content


def _reader(pdf_bytes: bytes) -> PdfReader:
    return PdfReader(BytesIO(pdf_bytes))


def _page_text(reader: PdfReader, index: int) -> str:
    """提取一页文本并去掉空白，便于断言。"""
    text = reader.pages[index].extract_text()
    return re.sub(r"\s+", "", text)


def _page_content(reader: PdfReader, index: int) -> bytes:
    return reader.pages[index].get_contents().get_data()


def test_proof_pdf_basic_long_flip():
    pdf_bytes = _post(PROOF_BASE)
    reader = _reader(pdf_bytes)
    # 1 张印张 -> 正面、背面两页
    assert len(reader.pages) == 2
    # 纸面尺寸与机台版面一致：430 × 297 mm
    box = reader.pages[0].mediabox
    assert float(box.width) == pytest.approx(430.0 * MM_TO_PT, abs=0.01)
    assert float(box.height) == pytest.approx(297.0 * MM_TO_PT, abs=0.01)

    # 正面：左装订最外张 (4, 1)
    front = _page_text(reader, 0)
    assert "第4页" in front
    assert "第1页" in front
    assert "帖1·张1·正面" in front
    # 背面：long 翻转交换左右槽 -> (3, 2)
    back = _page_text(reader, 1)
    assert "第3页" in back
    assert "第2页" in back
    assert "帖1·张1·背面" in back
    # long 翻转不旋转页位
    assert b"-1 0 0 -1" not in _page_content(reader, 0)
    assert b"-1 0 0 -1" not in _page_content(reader, 1)


def test_proof_pdf_paper_size_matches_press_layout():
    layout = client.post("/api/v1/impose/press-layout", json=PROOF_BASE).json()
    pdf_bytes = _post(PROOF_BASE)
    box = _reader(pdf_bytes).pages[0].mediabox
    assert float(box.width) == pytest.approx(
        layout["paper_width_mm"] * MM_TO_PT, abs=0.01
    )
    assert float(box.height) == pytest.approx(
        layout["paper_height_mm"] * MM_TO_PT, abs=0.01
    )


def test_proof_pdf_short_flip_rotates_back_180():
    body = {**PROOF_BASE, "duplex_flip": "short", "gutter_mm": 0}
    reader = _reader(_post(body))
    assert len(reader.pages) == 2
    # short：背面槽位不变（背左=2、背右=3），页位绕中心旋转 180°
    back = _page_text(reader, 1)
    assert "第2页" in back
    assert "第3页" in back
    assert b"-1 0 0 -1" in _page_content(reader, 1)
    # 正面不旋转
    assert b"-1 0 0 -1" not in _page_content(reader, 0)


def test_proof_pdf_blank_slots_marked():
    body = {**PROOF_BASE, "total_pages": 1}
    reader = _reader(_post(body))
    assert len(reader.pages) == 2
    front = _page_text(reader, 0)
    # 1 页补 3 空白：正面 空白/1
    assert "空白" in front
    assert "第1页" in front
    back = _page_text(reader, 1)
    assert "第1页" not in back
    assert back.count("空白") == 2


def test_proof_pdf_right_binding():
    body = {**PROOF_BASE, "binding": "right"}
    reader = _reader(_post(body))
    front = _page_text(reader, 0)
    # 右装订镜像：正面 (1, 4)
    assert "第1页" in front
    assert "第4页" in front
    back = _page_text(reader, 1)
    assert "第3页" in back
    assert "第2页" in back


def test_proof_pdf_sheets_emitted_front_then_back_in_order():
    body = {**PROOF_BASE, "total_pages": 8, "max_sheets_per_signature": 2}
    reader = _reader(_post(body))
    # 2 张印张 -> 4 页：张1正、张1背、张2正、张2背
    assert len(reader.pages) == 4
    expected = [
        ("张1·正面", ("第8页", "第1页")),
        ("张1·背面", ("第2页", "第7页")),
        ("张2·正面", ("第6页", "第3页")),
        ("张2·背面", ("第4页", "第5页")),
    ]
    for index, (side_label, pages) in enumerate(expected):
        text = _page_text(reader, index)
        assert f"帖1·{side_label}" in text
        for page_label in pages:
            assert page_label in text


def test_proof_pdf_multi_signature_and_last_signature_blanks():
    # 每帖 1 张（容量 4 页）：6 页 -> 帖1（1-4）+ 帖2（5、6 补 2 空白）
    body = {**PROOF_BASE, "total_pages": 6, "max_sheets_per_signature": 1}
    reader = _reader(_post(body))
    assert len(reader.pages) == 4
    sig2_front = _page_text(reader, 2)
    assert "帖2·张1·正面" in sig2_front
    assert "第5页" in sig2_front
    assert "空白" in sig2_front
    sig2_back = _page_text(reader, 3)
    assert "第6页" in sig2_back
    assert "空白" in sig2_back


def test_proof_pdf_gutter_separator_drawn():
    # 中缝分隔线为虚线（dash 操作符）
    reader = _reader(_post(PROOF_BASE))
    assert b"] 0 d" in _page_content(reader, 0)


def test_proof_pdf_exactly_100_sheets_allowed():
    # 400 页、每帖 4 张 -> 25 帖 × 4 张 = 100 张印张 -> 200 页 PDF
    body = {**PROOF_BASE, "total_pages": 400, "max_sheets_per_signature": 4}
    reader = _reader(_post(body))
    assert len(reader.pages) == 200


def test_proof_pdf_over_100_sheets_returns_422():
    # 404 页、每帖 4 张 -> 25 × 4 + 1 = 101 张印张，超限
    body = {**PROOF_BASE, "total_pages": 404, "max_sheets_per_signature": 4}
    response = client.post("/api/v1/impose/proof.pdf", json=body)
    assert response.status_code == 422


def test_proof_pdf_invalid_params_return_422():
    for override in (
        {"total_pages": 0},
        {"max_sheets_per_signature": 0},
        {"binding": "top"},
        {"page_width_mm": 0},
        {"page_height_mm": -1},
        {"gutter_mm": -0.5},
        {"duplex_flip": "side"},
        {"extra": 1},
    ):
        body = {**PROOF_BASE, **override}
        response = client.post("/api/v1/impose/proof.pdf", json=body)
        assert response.status_code == 422, override


def test_proof_pdf_missing_field_returns_422():
    body = dict(PROOF_BASE)
    del body["gutter_mm"]
    response = client.post("/api/v1/impose/proof.pdf", json=body)
    assert response.status_code == 422


def test_proof_pdf_non_finite_dimension_returns_422():
    payload = (
        '{"total_pages": 4, "max_sheets_per_signature": 1, "binding": "left", '
        '"page_width_mm": NaN, "page_height_mm": 297, "gutter_mm": 0, '
        '"duplex_flip": "long"}'
    )
    response = client.post(
        "/api/v1/impose/proof.pdf",
        content=payload,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
