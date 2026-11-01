"""Deterministic synthetic distractor documents: same clause types, different parties/values.

They make the real corpus's answers compete with near-duplicates, as in a real
document store. Values never equal the ones the questions ask about.
"""

import random

ADJ = """Cedar Maple Harbor Summit Northern Lakeview Granite Silver Evergreen Bluewater Ironwood
Redwood Pinecrest Stonebridge Falcon Willow Copperfield Crescent Highland Meadow Riverbend
Sterling Tidewater Union Vantage Westgate Alder Birchwood Clearwater Driftwood Eastport""".split()
NOUN = """Logistics Analytics Capital Software Manufacturing Foods Health Energy Media Systems
Consulting Textiles Robotics Marine Pharma Insurance Builders Networks Labs Retail""".split()
SUFFIX = ["Ltd", "Inc", "Partners", "Group", "Co"]
CITIES = ["Calgary", "Ottawa", "Montreal", "Vancouver", "Winnipeg", "Halifax", "Edmonton", "Regina"]
PROVINCES = ["Alberta", "Quebec", "British Columbia", "Manitoba", "Nova Scotia", "Saskatchewan"]


def _names(rng: random.Random) -> tuple[str, str]:
    def one() -> str:
        return f"{rng.choice(ADJ)} {rng.choice(NOUN)} {rng.choice(SUFFIX)}"

    a, b = one(), one()
    while b == a:
        b = one()
    return a, b


def _pick(rng: random.Random, options: list[int], banned: set[int]) -> int:
    return rng.choice([o for o in options if o not in banned])


def _services(rng: random.Random, a: str, b: str) -> str:
    fee = rng.randrange(40, 400) * 1000 + rng.choice([0, 250, 500])
    clauses = [
        f"Fees. The Client shall pay a fixed implementation fee of USD {fee:,}, invoiced in "
        f"{rng.choice([2, 4])} equal instalments. Hosting and support are charged at USD "
        f"{rng.randrange(2, 12) * 1000 + 100} per month.",
        f"Payment terms. Undisputed invoices are payable within {_pick(rng, [15, 20, 30, 60, 75], {45})} "
        f"days of receipt. Late payments accrue interest at {rng.choice(['1', '2', '0.75'])} percent per month.",
        f"Term and renewal. The initial term is {rng.choice([12, 18, 36])} months. The agreement renews "
        f"automatically for successive {rng.choice([6, 24])} month periods unless either party gives "
        f"written notice of non-renewal at least {_pick(rng, [30, 45, 60, 120], {90})} days before the end of the term.",
        f"Termination. Either party may terminate for material breach that remains uncured "
        f"{_pick(rng, [10, 15, 45, 60], {30})} days after written notice.",
        f"Liability. Supplier's total aggregate liability under this agreement is capped at the fees "
        f"paid in the {rng.choice([3, 6, 18, 24])} months preceding the claim.",
        f"Governing law. This agreement is governed by the laws of {rng.choice(PROVINCES)}, and "
        f"disputes will be heard by the courts sitting in {rng.choice(CITIES)}.",
        f"Service levels. Supplier guarantees {rng.choice(['98', '99', '99.9', '99.95'])} percent monthly "
        f"availability of the service.",
        "Intellectual property. The Client owns all deliverables created specifically for it. "
        "Supplier retains ownership of its pre-existing tools.",
    ]
    rng.shuffle(clauses)
    body = "\n\n".join(f"{i}. {c}" for i, c in enumerate(clauses[: rng.randrange(6, 9)], 1))
    return f'MASTER SERVICES AGREEMENT between {a} ("Client") and {b} ("Supplier").\n\n{body}\n'


def _nda(rng: random.Random, a: str, b: str) -> str:
    clauses = [
        f"Purpose. The parties wish to evaluate a possible {rng.choice(['merger', 'joint venture', 'licensing arrangement', 'supply partnership'])} "
        f"between {a} and {b} and will exchange confidential information for that purpose only.",
        "Definition. Confidential Information means non-public business, technical or financial "
        "information disclosed in any form and reasonably understood to be confidential.",
        f"Duration. The confidentiality obligations last for {_pick(rng, [2, 3, 4, 7, 10], {5})} years "
        f"from the date of the last disclosure.",
        f"Return of materials. Within {_pick(rng, [5, 10, 20, 30], {15})} business days after a written "
        f"request, the recipient must return or securely destroy all Confidential Information.",
        "Compelled disclosure. If the recipient is legally compelled to disclose Confidential "
        "Information, it must notify the discloser promptly.",
        f"Non-solicitation. For {_pick(rng, [6, 18, 24], {12})} months after the date of this agreement, "
        f"neither party will solicit for employment any employee of the other party.",
        "Remedies. The discloser may seek injunctive relief in addition to other remedies.",
    ]
    rng.shuffle(clauses)
    body = "\n\n".join(f"{i}. {c}" for i, c in enumerate(clauses[: rng.randrange(5, 8)], 1))
    return f"MUTUAL NON-DISCLOSURE AGREEMENT between {a} and {b}.\n\n{body}\n"


def _handbook(rng: random.Random, a: str, b: str) -> str:
    parts = [
        f"Annual leave. Full-time employees receive {_pick(rng, [15, 18, 20, 25, 28], {22})} days of "
        f"paid annual leave per calendar year, plus public holidays.",
        f"Parental leave. Birth parents receive {_pick(rng, [12, 16, 26, 52], {20})} weeks of paid "
        f"parental leave at {rng.choice(['full', 'partial'])} salary.",
        f"Remote work. Employees may work remotely up to {rng.choice([1, 2, 4])} days per week with "
        f"manager approval. All employees must be in the office on {rng.choice(['Mondays', 'Wednesdays', 'Thursdays'])}.",
        f"Probation. New employees serve a probation period of {rng.choice([1, 2, 6])} months.",
        f"Training budget. Each employee has an annual learning budget of CAD {rng.choice([600, 800, 1500, 2000]):,}.",
        f"Sick leave. A medical certificate is required when an absence lasts more than "
        f"{rng.choice([2, 4, 5])} consecutive working days.",
        f"Expenses. Any single expense above CAD {rng.choice([250, 750, 1000])} requires written approval "
        f"from a department head before purchase.",
        f"Working hours. Core hours are {rng.choice(['9:00 to 14:00', '11:00 to 16:00'])} local time.",
    ]
    rng.shuffle(parts)
    return f"EMPLOYEE HANDBOOK, {a}.\n\n" + "\n\n".join(parts[: rng.randrange(5, 8)]) + "\n"


def _security(rng: random.Random, a: str, b: str) -> str:
    parts = [
        f"Passwords. Passwords must contain at least {_pick(rng, [8, 10, 12, 16, 20], {14})} characters. "
        f"Passwords are rotated every {rng.choice([60, 90, 180])} days.",
        f"Backups. Production databases are backed up nightly and backups are retained for "
        f"{_pick(rng, [7, 14, 30, 60, 90], {35})} days.",
        f"Incident response. Suspected security incidents must be reported to the security team within "
        f"{rng.choice([2, 4, 24])} hours of discovery. Notifications must go out within "
        f"{rng.choice([24, 48, 96])} hours of confirming a breach.",
        f"Patching. Critical vulnerabilities must be patched within {_pick(rng, [3, 14, 15, 30], {7})} days.",
        f"Encryption. Data at rest must be encrypted with {rng.choice(['AES-128', 'AES-192'])}. "
        f"Data in transit must use TLS {rng.choice(['1.0', '1.1', '1.3'])} or higher.",
        f"Access reviews. Managers review user access rights every {rng.choice([3, 12])} months.",
        "Multi-factor authentication. Multi-factor authentication is mandatory for email and the VPN.",
    ]
    rng.shuffle(parts)
    return f"INFORMATION SECURITY POLICY, {a}.\n\n" + "\n\n".join(parts[: rng.randrange(5, 7)]) + "\n"


def _purchase(rng: random.Random, a: str, b: str) -> str:
    units = rng.randrange(100, 900)
    price = rng.randrange(20, 200) + rng.choice([0.25, 0.5, 0.95])
    parts = [
        f"Items. {units} units of part number {rng.choice(['HV', 'GK', 'PX', 'RT'])}-{rng.randrange(100, 999)} "
        f"at CAD {price:.2f} per unit. The order total before tax is CAD {units * price:,.2f}.",
        f"Late delivery. A penalty of {rng.choice(['0.25', '1', '2'])} percent of the order value applies "
        f"for each full week of delay, up to a maximum of {rng.choice([5, 15, 20])} percent.",
        f"Warranty. Accepted goods carry a {_pick(rng, [6, 12, 18, 36], {24})} month warranty against "
        f"defects in materials and workmanship.",
        f"Payment. Payment is due {rng.choice([15, 45, 60])} days after acceptance of the goods. A "
        f"{rng.choice(['1', '3'])} percent discount applies if payment is made within {rng.choice([5, 14])} days.",
        f"Cancellation. The buyer may cancel any undelivered portion of this order with "
        f"{rng.choice([7, 21, 30])} days written notice without penalty.",
    ]
    rng.shuffle(parts)
    return f"PURCHASE ORDER PO-{rng.randrange(10000, 99999)}, issued by {a} to {b}.\n\n" + "\n\n".join(parts) + "\n"


def _plan(rng: random.Random, a: str, b: str) -> str:
    first = rng.choice(["Dana", "Priya", "Tomas", "Lena", "Omar", "Mei", "Jonas", "Aisha"])
    last = rng.choice(["Okafor", "Raman", "Eriksen", "Haddad", "Novak", "Tanaka", "Silva", "Moore"])
    parts = [
        f"Budget. The approved budget is CAD {rng.randrange(80, 900) * 1000:,}, including a "
        f"contingency reserve of {rng.choice([5, 8, 10, 15, 20])} percent.",
        f"Team. The project manager is {first} {last}.",
        f"Timeline. The production cutover is planned for the weekend of {rng.randrange(1, 28)} "
        f"{rng.choice(['March', 'June', 'August', 'November'])}.",
        "Risks. The main risks are data migration errors and staff availability. Each risk has an owner.",
        f"Communication. A status report goes to the steering committee every {rng.choice(['week', 'month'])}.",
    ]
    rng.shuffle(parts)
    return f"PROJECT PLAN: {rng.choice(['Data Warehouse', 'Mobile App', 'ERP Upgrade', 'Website Relaunch'])}, {a}.\n\n" + "\n\n".join(parts) + "\n"


def _vendor(rng: random.Random, a: str, b: str) -> str:
    low = rng.choice([10, 15, 50]) * 1000
    high = rng.choice([100, 200, 300, 500]) * 1000
    parts = [
        f"Approval limits. Contracts up to CAD {low:,} may be approved by a department manager. "
        f"Anything above CAD {high:,} requires the {rng.choice(['CEO', 'Board', 'COO'])}.",
        f"Payment terms. The standard payment term is net {rng.choice([30, 45, 90])} days.",
        f"Eligibility. Vendors must provide a certificate of insurance showing general liability cover of "
        f"at least CAD {rng.choice([1, 3, 5])} million.",
        f"Performance reviews. Strategic vendors are reviewed every {rng.choice(['month', 'six months', 'year'])}.",
    ]
    rng.shuffle(parts)
    return f"VENDOR ONBOARDING STANDARD, {a}.\n\n" + "\n\n".join(parts) + "\n"


GENERATORS = [_services, _nda, _handbook, _security, _purchase, _plan, _vendor]
TYPE_LABELS = {
    "_services": "Master Services Agreement",
    "_nda": "Non-Disclosure Agreement",
    "_handbook": "Employee Handbook",
    "_security": "Information Security Policy",
    "_purchase": "Purchase Order",
    "_plan": "Project Plan",
    "_vendor": "Vendor Onboarding Standard",
}


def distractors(count: int = 100, seed: int = 20261031) -> dict[str, str]:
    rng = random.Random(seed)
    documents: dict[str, str] = {}
    while len(documents) < count:
        generator = GENERATORS[len(documents) % len(GENERATORS)]
        a, b = _names(rng)
        title = f"{a} - {b} {TYPE_LABELS[generator.__name__]}"
        if title not in documents:
            documents[title] = generator(rng, a, b)
    return documents
