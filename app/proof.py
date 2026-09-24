"""双面拼版校样 PDF 生成。

沿用 :func:`app.imposition.press_layout` 的页序、坐标与旋转角，把机台版面
（左上角原点、y 轴向下、单位 mm）换算为 PDF 坐标（左下角原点、y 轴向上、
单位 pt，1 mm = 72/25.4 pt）。每张印张依次输出正面、背面两页，PDF 纸面尺寸
与机台版面计算结果一致；各页位标注页码、帖号和张号，空白位标注“空白”，
中缝以虚线画出分隔线。
"""
from __future__ import annotations

from io import BytesIO
from typing import Optional

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

MM_TO_PT = 72.0 / 25.4  # 1 mm = 72/25.4 pt

# reportlab 内置的 Adobe 中文字体（CID 字体，无需字体文件，阅读器自动替换）
CJK_FONT = "STSong-Light"

pdfmetrics.registerFont(UnicodeCIDFont(CJK_FONT))

# 单次校样最多生成的印张数（每张印张输出正面、背面两页）
MAX_PROOF_SHEETS = 100


def count_sheets(layout: dict) -> int:
    """计算机台版面结果中的印张总数（各帖张数之和）。"""
    return sum(signature["sheet_count"] for signature in layout["signatures"])


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _draw_slot(
    pdf: canvas.Canvas,
    *,
    x_mm: float,
    y_mm: float,
    slot_w_mm: float,
    slot_h_mm: float,
    paper_h_mm: float,
    rotation_deg: int,
    main_text: str,
    sub_text: str,
) -> None:
    """画一个页位：页位框 + 居中的主标注（页码/空白）与副标注（帖/张/面）。

    ``x_mm`` / ``y_mm`` 为页位（未旋转时）左上角的机台坐标；旋转绕页位中心。
    旋转角只有 0°/180°，y 轴翻转对其无影响，PDF 的 rotate 直接取用即可。
    """
    # 左上原点、y 向下的 mm -> 左下原点、y 向上的 pt
    x_pt = x_mm * MM_TO_PT
    y_pt = (paper_h_mm - y_mm - slot_h_mm) * MM_TO_PT
    w_pt = slot_w_mm * MM_TO_PT
    h_pt = slot_h_mm * MM_TO_PT

    pdf.setLineWidth(0.6)
    pdf.rect(x_pt, y_pt, w_pt, h_pt, stroke=1, fill=0)

    main_pt = _clamp(min(slot_w_mm, slot_h_mm) * 0.12, 3.0, 30.0) * MM_TO_PT
    sub_pt = _clamp(main_pt * 0.38 / MM_TO_PT, 2.0, 12.0) * MM_TO_PT

    pdf.saveState()
    pdf.translate(x_pt + w_pt / 2, y_pt + h_pt / 2)
    pdf.rotate(rotation_deg)
    pdf.setFont(CJK_FONT, main_pt)
    pdf.drawCentredString(0, sub_pt * 0.9, main_text)
    pdf.setFont(CJK_FONT, sub_pt)
    pdf.setFillGray(0.3)
    pdf.drawCentredString(0, -main_pt * 0.85, sub_text)
    pdf.restoreState()


def _draw_side(
    pdf: canvas.Canvas,
    layout: dict,
    *,
    signature_index: int,
    sheet_index: int,
    pair: dict,
    side_label: str,
    side_rotation: int,
) -> None:
    """画印张的一面：纸面边界、中缝分隔线与左右两个页位。"""
    page_w_mm = layout["page_width_mm"]
    page_h_mm = layout["page_height_mm"]
    gutter_mm = layout["gutter_mm"]
    paper_w_pt = layout["paper_width_mm"] * MM_TO_PT
    paper_h_pt = layout["paper_height_mm"] * MM_TO_PT

    # 纸面边界
    pdf.setLineWidth(0.8)
    pdf.rect(0, 0, paper_w_pt, paper_h_pt, stroke=1, fill=0)

    # 中缝分隔线（虚线）：画在中缝两侧边界上；中缝为 0 时两线重合，只画一条
    pdf.saveState()
    pdf.setDash(3, 3)
    pdf.setLineWidth(0.5)
    gutter_left_pt = page_w_mm * MM_TO_PT
    pdf.line(gutter_left_pt, 0, gutter_left_pt, paper_h_pt)
    if gutter_mm > 0:
        gutter_right_pt = (page_w_mm + gutter_mm) * MM_TO_PT
        pdf.line(gutter_right_pt, 0, gutter_right_pt, paper_h_pt)
    pdf.restoreState()

    sub_text = f"帖 {signature_index} · 张 {sheet_index} · {side_label}"
    # 空白页位没有坐标，按槽位落版：左槽 x=0、右槽 x=页宽+中缝
    slot_x_mm = {"left": 0.0, "right": page_w_mm + gutter_mm}
    for slot_name in ("left", "right"):
        placement: Optional[dict] = pair[slot_name]
        if placement is not None:
            x_mm = placement["x_mm"]
            y_mm = placement["y_mm"]
            rotation_deg = placement["rotation_deg"]
            main_text = f"第 {placement['page']} 页"
        else:
            x_mm = slot_x_mm[slot_name]
            y_mm = 0.0
            rotation_deg = side_rotation
            main_text = "空白"
        _draw_slot(
            pdf,
            x_mm=x_mm,
            y_mm=y_mm,
            slot_w_mm=page_w_mm,
            slot_h_mm=page_h_mm,
            paper_h_mm=layout["paper_height_mm"],
            rotation_deg=rotation_deg,
            main_text=main_text,
            sub_text=sub_text,
        )


def build_proof_pdf(layout: dict) -> bytes:
    """按机台版面结果生成双面拼版校样 PDF。

    每张印张依次生成正面、背面两页；PDF 纸面尺寸与机台版面一致。
    """
    paper_w_pt = layout["paper_width_mm"] * MM_TO_PT
    paper_h_pt = layout["paper_height_mm"] * MM_TO_PT
    back_rotation = 0 if layout["duplex_flip"] == "long" else 180

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(paper_w_pt, paper_h_pt))
    pdf.setTitle("双面拼版校样")
    pdf.setSubject(
        f"共 {layout['total_pages']} 页，{layout['signature_count']} 帖，"
        f"{'左' if layout['binding'] == 'left' else '右'}装订，"
        f"{'长边' if layout['duplex_flip'] == 'long' else '短边'}翻转"
    )

    for signature in layout["signatures"]:
        for sheet in signature["sheets"]:
            for side_key, side_label in (("front", "正面"), ("back", "背面")):
                _draw_side(
                    pdf,
                    layout,
                    signature_index=signature["signature_index"],
                    sheet_index=sheet["sheet_index"],
                    pair=sheet[side_key],
                    side_label=side_label,
                    side_rotation=0 if side_key == "front" else back_rotation,
                )
                pdf.showPage()

    pdf.save()
    return buffer.getvalue()
