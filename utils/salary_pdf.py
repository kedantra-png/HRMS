"""Generate salary slip PDF bytes."""
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _num(v):
    try:
        if v is None:
            return 0.0
        s = str(v).strip()
        if s == "":
            return 0.0
        return float(s)
    except Exception:
        return 0.0


def _display_amt(v):
    if v is None:
        return ""
    s = str(v).strip()
    if s == "":
        return ""
    try:
        if float(s.replace(",", "")) == 0:
            return ""
    except Exception:
        pass
    return s


def build_salary_pdf_bytes(payload: dict) -> tuple[bytes, str]:
    """Return (pdf_bytes, filename) for a salary slip payload."""
    payload = payload or {}
    e = payload.get("earnings") or {}
    d = payload.get("deductions") or {}

    total_earn = sum(_num(v) for v in e.values())
    total_ded = sum(_num(v) for v in d.values())
    net_pay = total_earn - total_ded

    buffer = BytesIO()
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("t", parent=styles["Title"], fontSize=18, alignment=1, spaceAfter=6)
    sub_style = ParagraphStyle("s", parent=styles["Normal"], fontSize=12, alignment=1, spaceAfter=8)
    slip_style = ParagraphStyle("slip", parent=styles["Normal"], fontSize=12, alignment=1, spaceAfter=10)
    meta_style = ParagraphStyle("meta", parent=styles["Normal"], fontSize=12, spaceAfter=4)
    note_style = ParagraphStyle("note", parent=styles["Normal"], fontSize=11, alignment=1, spaceBefore=12)

    doc_pdf = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=18)
    story = []

    story.append(Paragraph("Dr. B. B. HEGDE FIRST GRADE COLLEGE", title_style))
    story.append(Paragraph("KUNDAPURA-576 201", sub_style))
    story.append(Paragraph(f"Salary Slip for the month of {payload.get('month_year', '')}", slip_style))

    def _meta_line(label, value):
        return Paragraph(f"<b>{label}</b> {value or ''}", meta_style)

    meta = [
        [
            _meta_line("Employee Name:", payload.get("employee_name", "")),
            _meta_line("Employee ID:", payload.get("employee_id", "")),
        ],
        [
            _meta_line("Department:", payload.get("department", "")),
            _meta_line("Paid Days:", payload.get("paid_days", "")),
        ],
        [_meta_line("Bank A/c No:", payload.get("bank_ac_no", "")), ""],
    ]
    meta_table = Table(meta, colWidths=[260, 260])
    meta_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 12),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(meta_table)
    story.append(Spacer(1, 12))

    cell_label_style = ParagraphStyle(
        "cell_label", parent=styles["Normal"], fontSize=10, leading=12, alignment=TA_LEFT
    )
    cell_amt_style = ParagraphStyle(
        "cell_amt", parent=styles["Normal"], fontSize=10, leading=12, alignment=TA_CENTER
    )
    cell_head_style = ParagraphStyle(
        "cell_head",
        parent=styles["Normal"],
        fontSize=10,
        leading=12,
        alignment=TA_CENTER,
        fontName="Helvetica-Bold",
    )

    def _lbl(text, bold=False):
        if bold:
            return Paragraph(f"<b>{text}</b>", cell_label_style)
        return Paragraph(text, cell_label_style)

    def _amt(text):
        return Paragraph(str(text) if text not in (None, "") else "", cell_amt_style)

    rows = [
        [
            Paragraph("<b>Earnings</b>", cell_label_style),
            Paragraph("<b>Amount</b>", cell_head_style),
            Paragraph("<b>Deductions</b>", cell_label_style),
            Paragraph("<b>Amount</b>", cell_head_style),
        ],
        [
            _lbl("Basic Pay"),
            _amt(_display_amt(e.get("basic_pay", ""))),
            _lbl("PF"),
            _amt(_display_amt(d.get("pf", ""))),
        ],
        [
            _lbl("DA"),
            _amt(_display_amt(e.get("da", ""))),
            _lbl("PT"),
            _amt(_display_amt(d.get("pt", ""))),
        ],
        [
            _lbl("HRA"),
            _amt(_display_amt(e.get("hra", ""))),
            _lbl("ESI"),
            _amt(_display_amt(d.get("esi", ""))),
        ],
        [
            _lbl("Spl. Allowance"),
            _amt(_display_amt(e.get("spl_allowance", ""))),
            _lbl("LIC Premium"),
            _amt(_display_amt(d.get("lic_premium", ""))),
        ],
        [
            Paragraph("Allowance for Ph.D/M.Phil/NET/SLET/KSET", cell_label_style),
            _amt(_display_amt(e.get("allow_phd", ""))),
            _lbl("Others"),
            _amt(_display_amt(d.get("others", ""))),
        ],
        [_lbl("HOD/other Allowance"), _amt(_display_amt(e.get("hod_allowance", ""))), "", ""],
        [_lbl("Addl. Remuneration"), _amt(_display_amt(e.get("addl_remuneration", ""))), "", ""],
        [
            _lbl("Total", bold=True),
            _amt(f"{total_earn:.2f}"),
            _lbl("Total", bold=True),
            _amt(f"{total_ded:.2f}"),
        ],
        [_lbl("Net Pay Rs.", bold=True), _amt(f"{net_pay:.2f}"), "", ""],
        [
            _lbl("Rupees", bold=True),
            Paragraph(payload.get("netpay_words", "") or "", cell_amt_style),
            "",
            "",
        ],
    ]
    tbl = Table(rows, colWidths=[200, 85, 175, 85])
    tbl.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (1, 0), (1, -1), "CENTER"),
                ("ALIGN", (3, 0), (3, -1), "CENTER"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -4), [colors.white, colors.Color(0.98, 0.98, 0.99)]),
                ("SPAN", (1, 10), (3, 10)),
                ("ALIGN", (1, 10), (3, 10), "CENTER"),
            ]
        )
    )
    story.append(tbl)
    story.append(Spacer(1, 10))
    story.append(
        Paragraph(
            "Note: This is a computer generated salary slip. Hence, no signature is required.",
            note_style,
        )
    )

    doc_pdf.build(story)
    filename = f"salary_{payload.get('employee_id', '')}_{payload.get('month_year', '')}.pdf".replace(" ", "_")
    return buffer.getvalue(), filename
