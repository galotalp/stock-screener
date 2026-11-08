"""
Render the research proposal page (proposal.html) from methodology.md, styled to
match galavgay.com. A starting point the user will edit manually.
"""
import re

SRC = "methodology.md"


def inline(t):
    t = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', t)
    t = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<em>\1</em>', t)
    t = re.sub(r'`(.+?)`', r'<code>\1</code>', t)
    return t


def reflow(md):
    """Collapse hard-wrapped source lines into one logical line per block, so inline
    markup and multi-line bullets/paragraphs render as a single unit. A new block
    begins at a blank line, a heading, an hr, or a NEW list item (a line starting at
    column 0 with '- ' or 'N.'). An INDENTED line, or a plain wrapped line, continues
    the current block."""
    out, buf = [], ""
    def flush():
        nonlocal buf
        if buf.strip():
            out.append(buf)
        buf = ""
    for ln in md.split("\n"):
        stripped = ln.strip()
        # hard breaks: blank / heading / hr end the current block
        if not stripped or ln.startswith("#") or stripped == "---":
            flush()
            out.append(ln)
            continue
        # a NEW list item (not indented) starts a fresh block
        is_new_item = (ln[:1] != " ") and (ln.startswith("- ") or re.match(r"^\d+\.\s", ln))
        if is_new_item:
            flush()
            buf = ln
        else:
            # continuation (indented wrap OR plain wrapped paragraph line)
            buf = (buf + " " + stripped).strip() if buf else ln
    flush()
    return "\n".join(out)


def build_body(md):
    md = reflow(md)
    out, in_list, in_refs = [], None, False
    for ln in md.split("\n"):
        if ln.startswith("### "):
            if in_list: out.append(f"</{in_list}>"); in_list = None
            out.append(f'<p class="subtitle">{inline(ln[4:])}</p>')
        elif ln.startswith("## "):
            if in_list: out.append(f"</{in_list}>"); in_list = None
            in_refs = ("reference" in ln.lower() or "bibliography" in ln.lower())
            out.append(f"<h2>{inline(ln[3:])}</h2>")
        elif ln.startswith("# "):
            if in_list: out.append(f"</{in_list}>"); in_list = None
            out.append(f"<h1>{inline(ln[2:])}</h1>")
        elif ln.strip() == "---":
            if in_list: out.append(f"</{in_list}>"); in_list = None
            out.append("<hr>")
        elif re.match(r"^\d+\.\s", ln):
            if in_list != "ol":
                if in_list: out.append(f"</{in_list}>")
                out.append('<ol class="refs">' if in_refs else "<ol>"); in_list = "ol"
            item = re.sub(r'^\d+\.\s', '', ln)
            out.append(f"<li>{inline(item)}</li>")
        elif ln.startswith("- "):
            if in_list != "ul":
                if in_list: out.append(f"</{in_list}>")
                out.append("<ul>"); in_list = "ul"
            out.append(f"<li>{inline(ln[2:])}</li>")
        else:
            if in_list: out.append(f"</{in_list}>"); in_list = None
            if ln.strip():
                out.append(f"<p>{inline(ln)}</p>")
    if in_list: out.append(f"</{in_list}>")
    return "\n".join(out)


TEMPLATE = """<style>
  /* Mirrors the Word document: Times New Roman, justified body, centered title. */
  :root{{--bg:#fff;--ink:#1a1a1a;--body:#222;--faint:#666;--navy:#00008b;--link:#597ccd;--link-hover:#00008b;--hair:#d8d8d8;--panel:#fafafb;
    --serif:"Times New Roman",Times,Georgia,serif;--sans:Arial,Helvetica,sans-serif;--mono:"Cascadia Mono","SF Mono",ui-monospace,monospace;}}
  *{{box-sizing:border-box;}}
  body{{margin:0;background:var(--bg);color:var(--body);font-family:var(--serif);font-size:12pt;line-height:1.5;padding:3px;}}
  .wrap{{max-width:720px;margin:0 auto;padding:0 26px 80px;}}
  a{{color:var(--link);text-decoration:none;}} a:hover{{color:var(--link-hover);text-decoration:underline;}}
  .topbar{{display:flex;gap:16px;flex-wrap:wrap;padding:14px 0 0;font-size:11pt;font-family:var(--sans);}} .topbar a{{font-weight:bold;}}
  /* title block — centered, Times New Roman */
  h1{{font-family:var(--serif);color:#000;font-size:20pt;font-weight:bold;text-align:center;margin:26px 0 4px;line-height:1.25;}}
  .subtitle{{font-family:var(--serif);font-style:italic;color:#333;font-size:13pt;text-align:center;margin:0 0 18px;}}
  /* section headers — Times New Roman bold, left */
  h2{{font-family:var(--serif);color:#000;font-size:13pt;font-weight:bold;margin:22px 0 6px;}}
  /* body — justified */
  p{{margin:9px 0;text-align:justify;}}
  ul,ol{{margin:9px 0 9px 4px;padding-left:26px;text-align:left;}} li{{margin:5px 0;}}
  code{{font-family:var(--mono);font-size:11pt;background:#f2f2f2;padding:1px 4px;}}
  hr{{border:none;border-top:1px solid var(--hair);margin:18px 0;}}
  strong{{color:#000;}}
  /* references: hanging-indent numbered list, left-aligned */
  ol.refs{{list-style:decimal;}} ol.refs li{{text-align:left;font-size:11pt;}}
  .banner{{background:var(--panel);border:1px solid var(--hair);border-left:3px solid var(--navy);border-radius:4px;
    padding:10px 14px;font-size:10.5pt;font-family:var(--sans);color:var(--faint);margin:14px 0 6px;}}
</style>
<div class="wrap">
  <nav class="topbar">
    <a href="../index.html">← galavgay.com</a>
    <a href="../projects.html">Projects</a>
    <a href="index.html">Sentiment vs Price</a>
  </nav>
  <div class="banner">Research proposal &amp; methodology for the
     <a href="index.html">Sentiment vs Price</a> study. Working draft.</div>
  {body}
</div>"""


def main():
    md = open(SRC, encoding="utf-8").read()
    body = build_body(md)
    html = TEMPLATE.format(body=body)
    out = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Research Proposal · Sentiment vs Price · Gal Av-Gay</title>
</head>
<body>
{html}
</body>
</html>
'''
    open("proposal.html", "w", encoding="utf-8").write(out)
    print("wrote proposal.html", len(out), "bytes")


if __name__ == "__main__":
    main()
