"""Synthetic English evaluation corpus. No real customer data; all names are fictional.

Each question names the document and an exact evidence sentence (a substring of that
document). Retrieval passes when a top-k chunk contains that evidence in full.
"kind" is "direct" when the question shares key vocabulary with the evidence and
"paraphrase" when it deliberately uses different words (a lexical-only weak spot).
"""

from typing import NamedTuple

DOCS: dict[str, str] = {
    "services_agreement": """MASTER SERVICES AGREEMENT between Northwind Logistics Ltd ("Client") and Brightline Software Inc ("Supplier"), effective 1 March 2026.

1. Scope. Supplier will design, build and maintain the Client's fleet scheduling portal as described in Schedule A.

2. Fees. The Client shall pay a fixed implementation fee of USD 184,500, invoiced in three equal instalments on signature, on delivery of the beta release and on final acceptance. Hosting and support are charged at USD 6,200 per month.

3. Payment terms. Undisputed invoices are payable within 45 days of receipt. Late payments accrue interest at 1.5 percent per month.

4. Term and renewal. The initial term is 24 months. The agreement renews automatically for successive 12 month periods unless either party gives written notice of non-renewal at least 90 days before the end of the term.

5. Termination. Either party may terminate for material breach that remains uncured 30 days after written notice. The Client may terminate for convenience on 60 days notice by paying all fees for work completed up to the termination date.

6. Liability. Supplier's total aggregate liability under this agreement is capped at the fees paid in the twelve months preceding the claim. Neither party is liable for indirect or consequential loss.

7. Intellectual property. The Client owns all deliverables created specifically for it. Supplier retains ownership of its pre-existing tools and grants the Client a perpetual, non-exclusive licence to use them inside the deliverables.

8. Governing law. This agreement is governed by the laws of the Province of Ontario, and disputes will be heard by the courts sitting in Toronto.

9. Service levels. Supplier guarantees 99.5 percent monthly availability of the portal. If availability falls below that level, the Client receives a service credit of 5 percent of that month's hosting fee for each full 0.5 percentage points of shortfall.
""",
    "nda": """MUTUAL NON-DISCLOSURE AGREEMENT between Cedar Ridge Analytics and Harbor Point Capital, dated 12 June 2026.

1. Purpose. The parties wish to evaluate a possible acquisition of Cedar Ridge's data platform division and will exchange confidential information for that purpose only.

2. Definition. Confidential Information means non-public business, technical or financial information disclosed in any form and marked confidential or reasonably understood to be confidential.

3. Exclusions. Information is not confidential if it is already public, was lawfully known to the recipient before disclosure, or is independently developed without use of the discloser's information.

4. Obligations. The recipient shall use Confidential Information solely for the stated purpose and shall protect it with at least the care it uses for its own sensitive information, and no less than reasonable care.

5. Duration. The confidentiality obligations last for five years from the date of the last disclosure. Trade secrets remain protected for as long as they qualify as trade secrets.

6. Return of materials. Within 15 business days after a written request, the recipient must return or securely destroy all Confidential Information and confirm in writing that it has done so.

7. Compelled disclosure. If the recipient is legally compelled to disclose Confidential Information, it must give the discloser prompt written notice so the discloser can seek a protective order.

8. Remedies. Each party acknowledges that breach may cause irreparable harm and that the discloser may seek injunctive relief in addition to other remedies.

9. Non-solicitation. For twelve months after the date of this agreement, neither party will solicit for employment any employee of the other party who was involved in the evaluation.
""",
    "employee_handbook": """EMPLOYEE HANDBOOK, Maple Street Design Studio. Revised January 2026.

Working hours. Core hours are 10:00 to 15:00 local time. Employees agree their remaining hours with their manager. Standard full-time employment is 37.5 hours per week.

Annual leave. Full-time employees receive 22 days of paid annual leave per calendar year, plus public holidays. Up to five unused days may be carried over into the next year and must be used by 31 March.

Sick leave. Employees receive ten paid sick days per year. A medical certificate is required when an absence lasts more than three consecutive working days.

Parental leave. Birth parents receive 20 weeks of paid parental leave at full salary. Non-birth parents and adoptive parents receive 10 weeks at full salary.

Remote work. Employees may work remotely up to three days per week with manager approval. All employees must be in the office on Tuesdays for the team planning meeting.

Expenses. Reasonable business expenses are reimbursed when submitted with receipts within 30 days. Any single expense above CAD 500 requires written approval from a department head before purchase.

Training budget. Each employee has an annual learning budget of CAD 1,200 for courses, books and conferences.

Probation. New employees serve a probation period of three months, during which either side may end employment with one week of notice.

Equipment. Company laptops are replaced every four years. Lost or stolen equipment must be reported to IT within 24 hours.
""",
    "security_policy": """INFORMATION SECURITY POLICY, Orchard Health Partners. Version 4.2.

Passwords. Passwords must contain at least 14 characters. Passwords are not rotated on a schedule but must be changed immediately after any suspected compromise. Password managers approved by IT are required for all staff.

Multi-factor authentication. Multi-factor authentication is mandatory for email, the VPN and all administrative consoles. Hardware security keys are required for system administrators.

Data classification. Information is classified as Public, Internal, Confidential or Restricted. Patient records are always Restricted and must never be stored on personal devices or in personal cloud accounts.

Encryption. Data at rest on laptops and servers must be encrypted with AES-256. Data in transit must use TLS 1.2 or higher.

Backups. Production databases are backed up nightly and backups are retained for 35 days. A restore test is performed every quarter and the result is recorded by the security officer.

Incident response. Suspected security incidents must be reported to the security team within one hour of discovery. The security officer decides whether regulators and affected patients must be notified, and notifications must go out within 72 hours of confirming a breach.

Access reviews. Managers review user access rights every six months. Accounts of departing employees are disabled on their last working day.

Vendor risk. Any vendor that processes Restricted data must complete a security questionnaire and sign a data processing agreement before onboarding.

Patching. Critical vulnerabilities must be patched within 7 days and high severity vulnerabilities within 30 days.
""",
    "purchase_order": """PURCHASE ORDER PO-77231, issued by Lakeshore Manufacturing to Keystone Industrial Supply on 3 September 2026.

Items. 400 units of hydraulic valve assembly, part number HV-220, at CAD 86.40 per unit; 1,200 units of gasket kit, part number GK-15, at CAD 9.75 per unit.

Total. The order total before tax is CAD 46,260.00. Applicable taxes are added at invoicing.

Delivery. Goods must be delivered to the Lakeshore receiving dock at 48 Foundry Road, Hamilton, no later than 30 October 2026. Partial shipments are accepted only with prior written approval.

Late delivery. A penalty of 0.5 percent of the order value applies for each full week of delay, up to a maximum of 10 percent.

Inspection and warranty. Lakeshore will inspect goods within 10 business days of delivery and may reject non-conforming units. Accepted goods carry a 24 month warranty against defects in materials and workmanship.

Payment. Payment is due 30 days after acceptance of the goods. A 2 percent discount applies if payment is made within 10 days.

Shipping terms. Prices are delivered duty paid. Keystone bears the risk of loss until the goods are unloaded at the receiving dock.

Cancellation. Lakeshore may cancel any undelivered portion of this order with 14 days written notice without penalty.
""",
    "project_plan": """PROJECT PLAN: Customer Portal Migration, Summit Credit Union. Draft 3.

Objective. Move the member portal from the legacy on-premises hosting environment to a managed cloud platform with no more than two hours of planned downtime.

Timeline. Discovery runs from 5 to 23 January. Build and test runs from 26 January to 17 April. The production cutover is planned for the weekend of 9 May. Hypercare support follows for four weeks.

Budget. The approved budget is CAD 412,000, including a contingency reserve of 12 percent. Spending beyond the contingency requires approval from the steering committee.

Team. The project manager is Dana Okafor. The technical lead is Priya Raman. Security review is owned by Tomas Eriksen. The business sponsor is the Chief Member Officer.

Risks. The main risks are data migration errors, third-party identity provider changes and staff availability during the quarter-end close. Each risk has an owner and is reviewed weekly.

Dependencies. The migration depends on the identity provider upgrade, scheduled for completion by 28 February, and on a signed hosting agreement by 15 January.

Success criteria. The migration succeeds if member login success rate stays above 99 percent in the first week, average page load time is under two seconds, and no member data is lost.

Communication. A status report goes to the steering committee every second Friday. Members receive notice of the cutover weekend at least three weeks in advance.
""",
    "vendor_policy": """VENDOR ONBOARDING STANDARD, Fernwood Retail Group.

Eligibility. Vendors must be registered businesses with at least two years of trading history and must provide a certificate of insurance showing general liability cover of at least CAD 2 million.

Due diligence. Procurement performs a financial check, a sanctions screening and a reference check for every new vendor. Vendors with annual spend over CAD 250,000 also receive an on-site audit.

Approval limits. Contracts up to CAD 25,000 may be approved by a department manager. Contracts from CAD 25,001 to CAD 150,000 require the Director of Procurement. Anything above CAD 150,000 requires the CFO.

Payment terms. The standard payment term is net 60 days. Shorter terms require CFO approval.

Conflicts of interest. Employees must declare any personal or family relationship with a vendor before the vendor is selected, and must not take part in the selection decision.

Performance reviews. Strategic vendors are reviewed every quarter against delivery, quality and responsiveness scorecards. Underperforming vendors enter a 90 day improvement plan.

Termination. Procurement may suspend a vendor immediately for fraud, repeated quality failures or breach of the code of conduct.
""",
}


class Question(NamedTuple):
    text: str
    document: str
    evidence: str
    kind: str  # "direct" or "paraphrase"


ANSWERABLE: list[Question] = [
    Question("How much is the fixed implementation fee under the services agreement?", "services_agreement", "fixed implementation fee of USD 184,500", "direct"),
    Question("How many days does the client have to pay an undisputed invoice?", "services_agreement", "payable within 45 days of receipt", "direct"),
    Question("How much notice is needed to stop the agreement from renewing automatically?", "services_agreement", "at least 90 days before the end of the term", "paraphrase"),
    Question("What is the cap on Supplier's liability?", "services_agreement", "capped at the fees paid in the twelve months preceding the claim", "direct"),
    Question("Which courts hear disputes under the services agreement?", "services_agreement", "courts sitting in Toronto", "direct"),
    Question("What availability does Brightline guarantee for the portal?", "services_agreement", "99.5 percent monthly availability", "direct"),
    Question("How long do NDA confidentiality obligations last?", "nda", "last for five years from the date of the last disclosure", "direct"),
    Question("How quickly must confidential material be returned after someone asks for it?", "nda", "Within 15 business days after a written request", "paraphrase"),
    Question("What must a recipient do if a court orders it to disclose confidential information?", "nda", "give the discloser prompt written notice", "paraphrase"),
    Question("For how long can the parties not hire each other's employees?", "nda", "For twelve months after the date of this agreement", "paraphrase"),
    Question("What is the purpose of the non-disclosure agreement between Cedar Ridge and Harbor Point?", "nda", "evaluate a possible acquisition of Cedar Ridge's data platform division", "direct"),
    Question("How many days of annual leave do full-time employees get?", "employee_handbook", "22 days of paid annual leave per calendar year", "direct"),
    Question("How many weeks of paid parental leave do birth parents receive?", "employee_handbook", "Birth parents receive 20 weeks of paid parental leave", "direct"),
    Question("On which day must everyone come to the office?", "employee_handbook", "must be in the office on Tuesdays", "paraphrase"),
    Question("When does a medical certificate become necessary for sick leave?", "employee_handbook", "more than three consecutive working days", "paraphrase"),
    Question("What is the yearly learning budget per employee?", "employee_handbook", "annual learning budget of CAD 1,200", "paraphrase"),
    Question("How long is the probation period for new hires?", "employee_handbook", "probation period of three months", "direct"),
    Question("What is the minimum password length?", "security_policy", "at least 14 characters", "paraphrase"),
    Question("How long are production database backups kept?", "security_policy", "retained for 35 days", "direct"),
    Question("Within how many hours must suspected security incidents be reported?", "security_policy", "within one hour of discovery", "direct"),
    Question("What encryption standard is required for data at rest?", "security_policy", "encrypted with AES-256", "direct"),
    Question("How fast must critical vulnerabilities be patched?", "security_policy", "Critical vulnerabilities must be patched within 7 days", "direct"),
    Question("What is the total of purchase order PO-77231 before tax?", "purchase_order", "order total before tax is CAD 46,260.00", "direct"),
    Question("What is the penalty for late delivery of the goods?", "purchase_order", "0.5 percent of the order value applies for each full week of delay", "direct"),
    Question("What warranty do accepted goods carry?", "purchase_order", "24 month warranty against defects", "direct"),
    Question("Is there an early payment discount on the purchase order?", "purchase_order", "A 2 percent discount applies if payment is made within 10 days", "direct"),
    Question("What is the approved budget for the portal migration project?", "project_plan", "approved budget is CAD 412,000", "direct"),
    Question("Who is the project manager for the migration?", "project_plan", "The project manager is Dana Okafor", "direct"),
    Question("When is the production cutover planned?", "project_plan", "production cutover is planned for the weekend of 9 May", "direct"),
    Question("Who may approve a vendor contract above CAD 150,000?", "vendor_policy", "Anything above CAD 150,000 requires the CFO", "direct"),
]

# Questions whose answers are absent from every document. Retrieval may still return
# related chunks; the model/answer layer must decline. These are used by later gates.
NO_ANSWER: list[str] = [
    "What is the CEO's home address?",
    "What is the company's policy on pets in the office?",
    "How much does Brightline charge for on-site training?",
    "What is the maximum amount of stock options for new employees?",
    "Which cloud provider will host the migrated portal?",
    "What is the penalty for breaching the NDA?",
    "How many parking spaces does Lakeshore have at its dock?",
    "What is Orchard Health's policy on using personal phones for work calls?",
    "What are Fernwood's store opening hours?",
    "When was Keystone Industrial Supply founded?",
]


TITLES: dict[str, str] = {
    "services_agreement": "Northwind Logistics - Brightline Software Master Services Agreement",
    "nda": "Cedar Ridge Analytics - Harbor Point Capital Non-Disclosure Agreement",
    "employee_handbook": "Maple Street Design Studio Employee Handbook",
    "security_policy": "Orchard Health Partners Information Security Policy",
    "purchase_order": "Lakeshore Manufacturing - Keystone Industrial Supply Purchase Order PO-77231",
    "project_plan": "Summit Credit Union Customer Portal Migration Project Plan",
    "vendor_policy": "Fernwood Retail Group Vendor Onboarding Standard",
}

# Names the party a person would use to pick one document out of many similar ones.
ANCHORS: dict[str, str] = {
    "services_agreement": "Northwind Logistics and Brightline Software services agreement",
    "nda": "Cedar Ridge Analytics and Harbor Point Capital NDA",
    "employee_handbook": "Maple Street Design Studio handbook",
    "security_policy": "Orchard Health Partners security policy",
    "purchase_order": "Lakeshore Manufacturing and Keystone Industrial Supply purchase order",
    "project_plan": "Summit Credit Union portal migration plan",
    "vendor_policy": "Fernwood Retail Group vendor standard",
}

# Answers worded with almost none of the evidence's vocabulary (lexical retrieval's weak spot).
HARD: list[Question] = [
    Question("How long do I have to settle a bill from Brightline?", "services_agreement", "payable within 45 days of receipt", "hard"),
    Question("What happens to the Northwind contract if nobody says anything when the first two years are up?", "services_agreement", "renews automatically for successive 12 month periods", "hard"),
    Question("How soon must Harbor Point hand back or shred Cedar Ridge's secret papers once asked?", "nda", "Within 15 business days after a written request", "hard"),
    Question("Can Harbor Point poach Cedar Ridge's staff?", "nda", "neither party will solicit for employment", "hard"),
    Question("How much time off can Maple Street staff take when a baby is born?", "employee_handbook", "Birth parents receive 20 weeks", "hard"),
    Question("Can people at Maple Street Design Studio work from home?", "employee_handbook", "may work remotely up to three days per week", "hard"),
    Question("What is the spending limit at Maple Street before a boss has to sign off?", "employee_handbook", "above CAD 500 requires written approval", "hard"),
    Question("How often must Orchard Health people change their login secrets?", "security_policy", "Passwords are not rotated on a schedule", "hard"),
    Question("How long does Orchard Health have to tell regulators about a leak?", "security_policy", "within 72 hours of confirming a breach", "hard"),
    Question("What happens if Keystone ships the goods behind schedule?", "purchase_order", "A penalty of 0.5 percent of the order value", "hard"),
    Question("How much money has Summit Credit Union set aside for unexpected costs?", "project_plan", "contingency reserve of 12 percent", "hard"),
    Question("Who signs off on really big supplier deals at Fernwood?", "vendor_policy", "Anything above CAD 150,000 requires the CFO", "hard"),
]


def question_text(q: Question) -> str:
    """Direct/paraphrase questions get an anchor; hard ones already name their parties."""
    if q.kind == "hard":
        return q.text
    return f"{q.text.rstrip('?')} in the {ANCHORS[q.document]}?"
