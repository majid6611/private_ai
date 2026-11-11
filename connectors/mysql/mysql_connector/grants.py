"""Verify that the database account really is read-only (never by trying to write)."""

import re

from .db import Session

ALLOWED = {"SELECT", "SHOW VIEW", "USAGE", "SHOW DATABASES"}
GRANT = re.compile(r"^GRANT (?P<privs>.+?) ON (?P<scope>.+?) TO ", re.I)


def check_grants(session: Session) -> tuple[list[str], list[str]]:
    """Returns (violations, granted_scopes). A violation is any privilege beyond reading."""
    violations: list[str] = []
    scopes: list[str] = []
    for (line,) in session.query("SHOW GRANTS FOR CURRENT_USER()"):
        text = str(line)
        match = GRANT.match(text)
        if not match:
            if re.match(r"^GRANT .+ TO ", text, re.I):
                violations.append("roles are granted; their privileges cannot be verified")
            continue
        privileges = re.sub(r"\([^)]*\)", "", match.group("privs"))
        for privilege in (p.strip().upper() for p in privileges.split(",")):
            if privilege and privilege not in ALLOWED:
                violations.append(f"{privilege} on {match.group('scope')}")
        scopes.append(match.group("scope"))
        if re.search(r"WITH GRANT OPTION\s*$", text, re.I):
            violations.append(f"GRANT OPTION on {match.group('scope')}")
    return sorted(set(violations)), scopes
