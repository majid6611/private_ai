"""Write a synthetic, text-based demo contract PDF (no real parties or data).

Usage: python scripts/make-demo-pdf.py demo/Northwind-Services-Contract.pdf
Standard library only; hand-assembled PDF with a real text layer.
"""

import io
import sys
import textwrap

TITLE = "MASTER SERVICES AGREEMENT"
SECTIONS = [
    ("Parties", "This Master Services Agreement (the Agreement) is made on 1 March 2026 between Northwind Logistics Ltd, 48 Foundry Road, Hamilton, Ontario (the Client) and Brightline Software Inc, 200 King Street West, Toronto, Ontario (the Supplier)."),
    ("1. Scope of work", "The Supplier will design, build, test and deploy a fleet scheduling portal for the Client, as described in Schedule A, and will provide hosting and support after launch."),
    ("2. Project deadlines", "The Supplier shall meet the following deadlines. Design approval by the Client is due no later than 14 November 2026. The beta release must be delivered to the Client by 15 January 2027. User acceptance testing must be completed by 12 February 2027. Final delivery and acceptance of the portal is due on 28 February 2027. Time is of the essence for each deadline."),
    ("3. Late delivery", "If the Supplier delivers any milestone after its deadline, the Client may deduct 0.5 percent of the implementation fee for each full week of delay, up to a maximum of 10 percent of the implementation fee. A delay caused by the Client's late approvals extends the affected deadline by the same number of days."),
    ("4. Fees and payment", "The Client shall pay a fixed implementation fee of USD 184,500 in three equal instalments: on signature, on delivery of the beta release, and on final acceptance. Hosting and support are charged at USD 6,200 per month. Undisputed invoices are payable within 45 days of receipt. Late payments accrue interest at 1.5 percent per month."),
    ("5. Term and renewal", "The initial term is 24 months from 1 March 2026 and ends on 28 February 2028. The Agreement renews automatically for successive 12 month periods unless either party gives written notice of non-renewal at least 90 days before the end of the then-current term, that is, by 30 November 2027 for the first renewal."),
    ("6. Reporting", "The Supplier shall send a written progress report to the Client on the first business day of every month. A final project report is due within 30 days after final acceptance, no later than 30 March 2027."),
    ("7. Termination", "Either party may terminate this Agreement for material breach that remains uncured 30 days after written notice. The Client may terminate for convenience on 60 days written notice by paying for all work completed up to the termination date."),
    ("8. Liability", "The Supplier's total aggregate liability under this Agreement is capped at the fees paid in the twelve months preceding the claim. Neither party is liable for indirect or consequential loss."),
    ("9. Confidentiality", "Each party shall keep the other party's confidential information secret for five years after the end of this Agreement and shall return or destroy it within 15 business days of a written request."),
    ("10. Governing law", "This Agreement is governed by the laws of the Province of Ontario. Disputes will be heard by the courts sitting in Toronto."),
    ("Signatures", "Signed for the Client by Dana Okafor, Chief Operating Officer. Signed for the Supplier by Priya Raman, Managing Director. Date: 1 March 2026."),
]


def lines() -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = [(TITLE, 16), ("", 11)]
    for heading, body in SECTIONS:
        out.append((heading, 12))
        out.extend((line, 11) for line in textwrap.wrap(body, 92))
        out.append(("", 11))
    return out


def build(pages: list[list[tuple[str, int]]]) -> bytes:
    objects: list[bytes] = [b"<</Type/Catalog/Pages 2 0 R>>", b""]
    objects.append(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")
    objects.append(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica-Bold>>")
    kids = []
    for page_lines in pages:
        ops = b""
        y = 740
        for text, size in page_lines:
            if text:
                font = b"F2" if size > 11 else b"F1"
                safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                ops += b"BT /%s %d Tf 60 %d Td (%s) Tj ET\n" % (font, size, y, safe.encode("latin-1"))
            y -= size + 5
        objects.append(b"<</Length %d>>\nstream\n" % len(ops) + ops + b"endstream")
        content = len(objects)
        objects.append(
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents %d 0 R"
            b"/Resources<</Font<</F1 3 0 R/F2 4 0 R>>>>>>" % content
        )
        kids.append(len(objects))
    objects[1] = b"<</Type/Pages/Kids[%s]/Count %d>>" % (
        b" ".join(b"%d 0 R" % k for k in kids), len(kids))
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref))
    return out.getvalue()


def main() -> None:
    pages: list[list[tuple[str, int]]] = [[]]
    used = 0
    for text, size in lines():
        if used + size + 5 > 690:
            pages.append([])
            used = 0
        pages[-1].append((text, size))
        used += size + 5
    pathlib_path = sys.argv[1] if len(sys.argv) > 1 else "contract.pdf"
    with open(pathlib_path, "wb") as handle:
        handle.write(build(pages))
    print(f"wrote {pathlib_path} ({len(pages)} pages)")


if __name__ == "__main__":
    main()
