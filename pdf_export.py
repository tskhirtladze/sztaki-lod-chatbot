"""
PDF export of a chat conversation.

Builds a PDF from the list of messages that /history returns
([{"role": "user"|"assistant", "content": str, "viz": {...}|None}, ...]),
with every chart embedded at its place in the conversation.

Fonts: Matplotlib (already a dependency) ships the DejaVu TrueType fonts,
which cover Hungarian characters such as ő and ű, so no font files need
to be added to the project.
"""
import io
import os
import re
import base64
from datetime import datetime

import matplotlib
from fpdf import FPDF

_FONT_DIR = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
_FONT = "DejaVu"

# Colours match the chat page (templates/index.html)
_USER_COLOUR      = (42, 75, 124)    # #2A4B7C
_ASSISTANT_COLOUR = (185, 114, 15)   # #B9720F
_TEXT_COLOUR      = (33, 29, 23)     # #211D17
_MUTED_COLOUR     = (122, 117, 102)  # #7A7566
_USER_FILL        = (232, 238, 246)
_RULE_COLOUR      = (217, 210, 191)  # #D9D2BF


class _ChatPDF(FPDF):
    def footer(self):
        self.set_y(-12)
        self.set_font(_FONT, "", 8)
        self.set_text_color(*_MUTED_COLOUR)
        self.cell(0, 6, f"NDA Explorer · page {self.page_no()}/{{nb}}", align="C")


def _content_to_text(content) -> str:
    """AIMessage content can be a string or a list of content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return str(content or "")


def _clean_inline(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 (\2)", text)  # [label](url)
    text = text.replace("`", "")
    return text


def _write_rich_line(pdf: FPDF, line: str, size: float, line_h: float):
    """Write one line, honouring **bold** spans, with automatic wrapping."""
    parts = re.split(r"(\*\*.+?\*\*)", line)
    for part in parts:
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            pdf.set_font(_FONT, "B", size)
            pdf.write(line_h, part[2:-2])
        else:
            pdf.set_font(_FONT, "", size)
            pdf.write(line_h, part)
    pdf.ln(line_h)


def _write_answer(pdf: FPDF, text: str):
    """Render the assistant's prose: paragraphs, headings, bullets, bold."""
    size, line_h = 10.5, 5.6
    pdf.set_text_color(*_TEXT_COLOUR)
    for raw in _clean_inline(text).replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        if not line.strip():
            pdf.ln(2.5)
            continue
        heading = re.match(r"^\s*#{1,6}\s+(.*)$", line)
        if heading:
            pdf.set_font(_FONT, "B", 11.5)
            pdf.multi_cell(0, 6.2, heading.group(1).replace("**", ""))
            continue
        bullet = re.match(r"^(\s*)[-*•]\s+(.*)$", line)
        numbered = re.match(r"^(\s*)(\d+[.)])\s+(.*)$", line)
        if bullet or numbered:
            indent = 4 + min(len((bullet or numbered).group(1)), 8) * 1.5
            marker = "•" if bullet else numbered.group(2)
            body = bullet.group(2) if bullet else numbered.group(3)
            left = pdf.l_margin
            pdf.set_x(left + indent)
            pdf.set_font(_FONT, "", size)
            pdf.cell(6, line_h, marker)
            # indent wrapped lines under the bullet text
            pdf.set_left_margin(left + indent + 6)
            _write_rich_line(pdf, body, size, line_h)
            pdf.set_left_margin(left)
            continue
        _write_rich_line(pdf, line, size, line_h)


def _decode_data_uri(uri: str):
    if not uri or "," not in uri:
        return None
    try:
        return io.BytesIO(base64.b64decode(uri.split(",", 1)[1]))
    except Exception:
        return None


def _write_chart(pdf: FPDF, viz: dict):
    img = _decode_data_uri(viz.get("image"))
    if img is None:
        return
    width = min(pdf.epw, 160)
    # Charts are rendered at 7.5 x 4.5 in (app.py), so keep that ratio when
    # checking whether the chart still fits on the current page.
    height = width * 4.5 / 7.5
    if pdf.get_y() + height + 12 > pdf.page_break_trigger:
        pdf.add_page()
    pdf.ln(2)
    x = pdf.l_margin + (pdf.epw - width) / 2
    pdf.image(img, x=x, w=width)
    if viz.get("description"):
        pdf.ln(1)
        pdf.set_font(_FONT, "I", 9)
        pdf.set_text_color(*_MUTED_COLOUR)
        pdf.multi_cell(0, 4.6, viz["description"], align="C")
    pdf.ln(2)


def _speaker_label(pdf: FPDF, label: str, colour):
    pdf.set_font(_FONT, "B", 8.5)
    pdf.set_text_color(*colour)
    pdf.cell(0, 5, label, new_x="LMARGIN", new_y="NEXT")


def build_chat_pdf(messages: list) -> bytes:
    pdf = _ChatPDF(format="A4")
    pdf.set_margins(18, 18, 18)
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_font(_FONT, "",  os.path.join(_FONT_DIR, "DejaVuSans.ttf"))
    pdf.add_font(_FONT, "B", os.path.join(_FONT_DIR, "DejaVuSans-Bold.ttf"))
    pdf.add_font(_FONT, "I", os.path.join(_FONT_DIR, "DejaVuSans-Oblique.ttf"))
    pdf.set_title("NDA Explorer - conversation")
    pdf.set_creator("NDA Explorer")
    pdf.add_page()

    # ── Title block ─────────────────────────────────────────────
    pdf.set_font(_FONT, "B", 18)
    pdf.set_text_color(*_TEXT_COLOUR)
    pdf.cell(0, 10, "NDA Explorer — Conversation", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(_FONT, "", 9.5)
    pdf.set_text_color(*_MUTED_COLOUR)
    pdf.cell(0, 5, "Hungary's National Digital Archive", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, f"Exported {datetime.now().strftime('%d %B %Y, %H:%M')}",
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    pdf.set_draw_color(*_RULE_COLOUR)
    pdf.set_line_width(0.4)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(6)

    # ── Conversation ────────────────────────────────────────────
    for m in messages:
        text = _content_to_text(m.get("content")).strip()
        if m.get("role") == "user":
            if pdf.get_y() + 20 > pdf.page_break_trigger:
                pdf.add_page()
            _speaker_label(pdf, "YOU", _USER_COLOUR)
            pdf.set_font(_FONT, "", 10.5)
            pdf.set_text_color(*_TEXT_COLOUR)
            pdf.set_fill_color(*_USER_FILL)
            pdf.multi_cell(0, 6, text or "(empty)", fill=True, padding=(2.5, 3.5))
            pdf.ln(4)
        elif m.get("role") == "assistant":
            _speaker_label(pdf, "ARCHIVE", _ASSISTANT_COLOUR)
            _write_answer(pdf, text or "No response generated.")
            viz = m.get("viz") or {}
            if viz.get("is_visual") and viz.get("image"):
                _write_chart(pdf, viz)
            pdf.ln(3)
            pdf.set_draw_color(*_RULE_COLOUR)
            pdf.set_line_width(0.2)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
            pdf.ln(6)

    return bytes(pdf.output())
