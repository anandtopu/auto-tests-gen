#!/usr/bin/env python3
"""Stage 2 — deterministic joins: endpoints<->contracts, routes<->route tables,
JIRA keys from git history of the test file."""
import json, re, subprocess, sys

entries = [json.loads(l) for l in open(sys.argv[1])]
facts = json.load(open(sys.argv[2]))

# Methods that actually ATTRIBUTE a test to an app repo. Confidence is a claim
# about the attribution, so only these may raise it — see the formula below.
ATTRIBUTING = ("contract_match", "route_match")

# A JIRA key, not any hyphenated uppercase token. `[A-Z][A-Z0-9]+-\d+` matched
# UTF-8, HTTP-2, SHA-1 and RFC-2616 in ordinary commit messages, which invented
# a `feature` value and pushed mappings over the auto-accept line. Two to ten
# characters (JIRA's own project-key limit), and well-known technical tokens
# are excluded by name.
KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9})-(\d+)\b")
NOT_PROJECTS = {"UTF", "HTTP", "HTTPS", "SHA", "RFC", "ISO", "AES", "RSA",
                "TLS", "SSL", "MD", "IPV", "IEEE", "ANSI", "BASE64", "SOCKS",
                "OAUTH", "SAML", "JSON", "XML", "CVE", "ES", "EC", "GB", "MB"}


def jira_keys(text):
    return sorted({f"{m.group(1)}-{m.group(2)}" for m in KEY_RE.finditer(text)
                   if m.group(1) not in NOT_PROJECTS})


def norm(p):
    """/v1/orders/123/discounts -> /v1/orders/{id}/discounts

    Whole segments only. `/\\d+` alone matched the digits at the START of a
    segment, so `/api/2fa/verify` became `/api/{id}fa/verify` and
    `/a/1b/c` became `/a/{id}b/c` — paths that then match nothing in the
    contract index, silently costing the test its attribution. It fails in the
    conservative direction (no mapping rather than a wrong one), which is
    precisely why it could sit here unnoticed: the test just quietly lands in
    the review queue looking like something the correlator had no opinion on.
    """
    return re.sub(r"/\d+(?=/|$)", "/{id}", p)

for e in entries:
    repos, methods = set(), []
    for ep in e["evidence"]["endpoints"]:
        path = norm(ep.split(" ", 1)[1])
        hit = facts["endpoints"].get(path) or facts["endpoints"].get(path.rstrip("/"))
        if hit: repos.update(hit); methods.append("contract_match")
    for rt in e["evidence"]["ui_routes"]:
        hit = facts["routes"].get(norm(rt))
        if hit: repos.update(hit); methods.append("route_match")
    try:
        log = subprocess.run(["git", "-C", f"workspace/bootstrap/{e['test_repo']}/repo",
                              "log", "--format=%s", "--", e["file"]],
                             capture_output=True, text=True, timeout=30).stdout
        keys = jira_keys(log)
        e["evidence"]["git_jira_keys"] = keys
        if keys: methods.append("git_history")
    except Exception:
        pass
    # Confidence is a claim about WHICH APP REPO this test covers, so only
    # evidence that attributes a repo may raise it. git_history says which
    # TICKET touched the file and contributes no repo, yet it used to count as
    # a method — taking a single-signal mapping from 0.75 to 0.95, over the
    # 0.85 auto line, so a mapping skipped human review on the strength of a
    # commit message. `covers:` regenerates from these mappings and decides
    # routing, so a wrong one silently misroutes future work. It stays recorded
    # as evidence; it just no longer votes.
    #
    # The base is 0.65, RE-CALIBRATED because the git_history term was removed.
    # One attributing match now scores 0.85 (auto) where it scored 0.75. That
    # is the tiering the old formula reached by ACCIDENT: a contract_match
    # means the test's endpoint is literally defined in that repo's OpenAPI
    # contract — deterministic and checkable, and a reviewer confirming it only
    # re-reads the contract. Mappings with NO deterministic attribution still
    # score 0.0, fall below split_residue's 0.55 line, and go to the LLM
    # classifier — whose output is what the [0.5, 0.85) review band is for.
    attributing = {m for m in methods if m in ATTRIBUTING}
    conf = min(0.99, 0.65 + 0.2 * len(attributing)) if repos else 0.0
    e["mapping"] = {"app_repos": sorted(repos), "services": sorted(repos),
                    "domain": (e["tags"][0].lstrip("@") if e["tags"] else ""),
                    "feature": (e["evidence"]["git_jira_keys"] or [""])[0],
                    "confidence": round(conf, 2), "method": sorted(set(methods)) or ["none"],
                    "status": "pending"}
    print(json.dumps(e))
