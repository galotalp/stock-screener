"""
Generate Methodology_Sentiment_Price_Study.docx from methodology.md, matching the
existing formatting exactly:
  Title      TNR 16pt bold, centered
  Subtitle   TNR 13pt italic, centered
  Byline     TNR 11pt bold-lead, centered
  Section    TNR 13pt bold, left ("N.  Title")
  Body       TNR 12pt, justified; **bold**/*italic* inline; citation superscripts
             as real Word superscript runs (¹⁻⁴ etc. -> superscript "1-4")
  References TNR 11pt, left, numbered
"""
import re
from docx import Document
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

SRC = "methodology.md"
OUT = "Methodology_Sentiment_Price_Study.docx"
SERIF = "Times New Roman"

# unicode superscript -> normal chars
SUP = {"¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5", "⁶": "6",
       "⁷": "7", "⁸": "8", "⁹": "9", "⁰": "0", "⁻": "-"}
SUP_CHARS = "".join(SUP.keys())
# a "citation cluster" = superscript digits/dash possibly joined by a comma,
# e.g. ²,³  or  ¹⁻⁴ — the comma should also render superscript.
CITE_CLUSTER = re.compile(f"([{SUP_CHARS}](?:[{SUP_CHARS},]*[{SUP_CHARS}])?)")


def reflow(md):
    """Join hard-wrapped lines into one logical line per block (same as gen_proposal)."""
    out, buf = [], ""
    def is_block_start(s):
        return (not s.strip() or s.startswith("#") or s.strip() == "---"
                or (s[:1] != " " and (s.startswith("- ") or re.match(r"^\d+\.\s", s))))
    for ln in md.split("\n"):
        if not ln.strip() or ln.startswith("#") or ln.strip() == "---":
            if buf.strip():
                out.append(buf); buf = ""
            out.append(ln)
        elif ln[:1] != " " and (ln.startswith("- ") or re.match(r"^\d+\.\s", ln)):
            if buf.strip():
                out.append(buf)
            buf = ln
        else:
            buf = (buf + " " + ln.strip()).strip() if buf else ln
    if buf.strip():
        out.append(buf)
    return out


def add_runs(p, text, base_bold=False):
    """Add runs to paragraph p, handling **bold**, *italic*, and unicode superscripts."""
    # split on bold/italic markers, preserving them
    tokens = re.split(r"(\*\*.+?\*\*|\*[^*].*?\*)", text)
    for tok in tokens:
        if not tok:
            continue
        bold = base_bold
        italic = False
        inner = tok
        if tok.startswith("**") and tok.endswith("**"):
            bold = True; inner = tok[2:-2]
        elif tok.startswith("*") and tok.endswith("*"):
            italic = True; inner = tok[1:-1]
        # within inner, split out citation clusters (superscript, incl. a joining comma)
        for seg in CITE_CLUSTER.split(inner):
            if not seg:
                continue
            is_cite = bool(seg) and all(c in SUP or c == "," for c in seg) and any(c in SUP for c in seg)
            r = p.add_run("".join(SUP.get(c, c) for c in seg))
            r.font.name = SERIF
            r.font.size = Pt(12)
            r.bold = bold
            r.italic = italic
            if is_cite:
                r.font.superscript = True


def main():
    md = open(SRC, encoding="utf-8").read()
    lines = reflow(md)
    doc = Document()
    doc.styles["Normal"].font.name = SERIF
    doc.styles["Normal"].font.size = Pt(12)

    in_refs = False
    ref_num = 0
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if s.startswith("# "):   # title
            p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            r = p.add_run(s[2:]); r.font.name = SERIF; r.font.size = Pt(16); r.bold = True
            # title uses en-dash literally
        elif s.startswith("### "):  # subtitle
            p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            r = p.add_run(s[4:]); r.font.name = SERIF; r.font.size = Pt(13); r.italic = True
        elif s.startswith("## "):
            in_refs = ("reference" in s.lower() or "bibliography" in s.lower())
            p = doc.add_paragraph()
            r = p.add_run(s[3:]); r.font.name = SERIF; r.font.size = Pt(13); r.bold = True
        elif s == "---":
            continue  # skip the horizontal rules (docx uses spacing)
        elif re.match(r"^\d+\.\s", s):
            body_txt = re.sub(r'^\d+\.\s', '', s)
            if in_refs:
                # bibliography entry (11pt, left)
                ref_num += 1
                p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.LEFT
                r = p.add_run(f"{ref_num}.  " + body_txt)
                r.font.name = SERIF; r.font.size = Pt(11)
            else:
                # numbered research question (justified body, keeps its own number)
                num = re.match(r"^(\d+)\.\s", s).group(1)
                p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                p.paragraph_format.left_indent = Pt(18)
                lead = p.add_run(f"{num}.  ")
                lead.font.name = SERIF; lead.font.size = Pt(12)
                add_runs(p, body_txt)
        elif re.match(r"^\*\*(Principal investigator|Project type|Contact):\*\*", s):
            # byline: may be a single reflowed line holding all three fields;
            # split back into separate centered lines.
            fields = re.split(r"(?=\*\*(?:Principal investigator|Project type|Contact):\*\*)", s)
            for fld in fields:
                fld = fld.strip()
                if not fld:
                    continue
                p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                add_runs(p, fld)
                for r in p.runs:
                    r.font.size = Pt(11)
        else:
            p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            add_runs(p, s)

    doc.save(OUT)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
