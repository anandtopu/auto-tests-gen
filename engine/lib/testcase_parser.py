#!/usr/bin/env python3
"""Dependency-free S1 parser for JavaScript/TypeScript E2E test cases.

This is deliberately a parser *adapter*, not a claim that regular expressions
understand JavaScript. A lexical mask removes strings/comments, balanced-delimiter
scanning locates callback bodies, and a small framework vocabulary recognizes the
Playwright and node:test shapes registered in this estate. Unsupported syntax is
reported to the caller; it is never translated into "this file has no tests".
"""
import re


_CALL_RE = re.compile(
    r"(?<![\w$.])(?P<base>test|it|describe|context|suite)"
    r"(?P<mods>(?:\s*\.\s*[A-Za-z_$][\w$]*)*)\s*\(")
_CASE_MODS = {"", "skip", "only", "fixme", "fail", "slow", "todo"}
_SUITE_MODS = {"", "skip", "only", "serial", "parallel"}
_TAG_RE = re.compile(r"(?<![\w-])@[A-Za-z0-9][A-Za-z0-9_-]*")
_PATH_RE = re.compile(r"(?<![\w.])(/[A-Za-z0-9_{}:-]+(?:/[A-Za-z0-9_{}:.-]+)+)")
_URL_PATH_RE = re.compile(r"https?://[^/'\"`]+(/[A-Za-z0-9_{}:.-]+(?:/[A-Za-z0-9_{}:.-]+)*)")
_TESTID_RE = re.compile(
    r"(?:getByTestId\s*\(\s*['\"]([^'\"]+)['\"]|"
    r"data-testid\s*=\s*['\"]([^'\"]+)['\"])")
_PAGE_RE = re.compile(r"\b([A-Z][A-Za-z0-9_$]*(?:Page|Screen|View))\b")
_CALL_NAME_RE = re.compile(r"(?<![.$\w])([A-Za-z_$][\w$]*)\s*\(")
_IMPORT_RE = re.compile(
    r"(?:from\s+|require\s*\(\s*)['\"]([^'\"]+)['\"]")
_EXPECT_RE = re.compile(
    r"expect\s*\(\s*([^\n)]{1,120})\s*\)\s*\.\s*([A-Za-z_$][\w$]*)")
_ASSERT_RE = re.compile(
    r"assert(?:\.[A-Za-z_$][\w$]*)+\s*\(\s*([^,\n)]{1,120})")
_HELPER_EXCLUDE = {
    "assert", "async", "describe", "expect", "fetch", "it", "require", "suite", "test",
    "String", "Number", "Boolean", "Object", "Array", "Promise", "JSON",
}


def _lexical_mask(text):
    """Return (mask, error); mask[i] is true only for executable code text."""
    mask = bytearray(b"\x01") * len(text)
    i, state, quote = 0, "code", ""
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if state == "line":
            mask[i] = 0
            if ch == "\n":
                state = "code"
            i += 1
            continue
        if state == "block":
            mask[i] = 0
            if ch == "*" and nxt == "/":
                mask[i + 1] = 0
                i += 2
                state = "code"
            else:
                i += 1
            continue
        if state == "string":
            mask[i] = 0
            if ch == "\\":
                if i + 1 < len(text):
                    mask[i + 1] = 0
                i += 2
            elif ch == quote:
                i += 1
                state = "code"
            else:
                i += 1
            continue
        if ch == "/" and nxt == "/":
            mask[i] = mask[i + 1] = 0
            i += 2
            state = "line"
        elif ch == "/" and nxt == "*":
            mask[i] = mask[i + 1] = 0
            i += 2
            state = "block"
        elif ch in ("'", '"', "`"):
            mask[i] = 0
            quote = ch
            state = "string"
            i += 1
        else:
            i += 1
    if state == "block":
        return mask, "unterminated block comment"
    if state == "string":
        return mask, "unterminated string or template literal"
    return mask, ""


def _matching(text, mask, start, opening, closing):
    depth = 0
    for i in range(start, len(text)):
        if not mask[i]:
            continue
        if text[i] == opening:
            depth += 1
        elif text[i] == closing:
            depth -= 1
            if depth == 0:
                return i
    return None


def _first_string(text, start, stop):
    i = start
    while i < stop and text[i].isspace():
        i += 1
    if i >= stop or text[i] not in ("'", '"', "`"):
        return None
    quote, j, out = text[i], i + 1, []
    while j < stop:
        if text[j] == "\\" and j + 1 < stop:
            out.append(text[j:j + 2])
            j += 2
        elif text[j] == quote:
            return "".join(out), j + 1
        else:
            out.append(text[j])
            j += 1
    return None


def _callback_block(text, mask, start, stop):
    """Callback block range or None. Option objects before the callback are skipped."""
    for i in range(start, stop):
        if text[i] != "{" or not mask[i]:
            continue
        prefix = text[max(start, i - 180):i]
        if not (re.search(r"=>\s*$", prefix) or
                re.search(r"\bfunction(?:\s+[A-Za-z_$][\w$]*)?\s*\([^{}]*\)\s*$",
                          prefix, re.S)):
            continue
        end = _matching(text, mask, i, "{", "}")
        if end is not None and end <= stop:
            return i, end
    return None


def _kind(base, mods):
    mods = [m.strip() for m in mods.split(".") if m.strip()]
    if base in ("describe", "context", "suite"):
        return "suite" if (mods[0] if mods else "") in _SUITE_MODS else None
    if base == "test" and mods and mods[0] == "describe":
        return "suite" if (mods[1] if len(mods) > 1 else "") in _SUITE_MODS else None
    first = mods[0] if mods else ""
    return "case" if first in _CASE_MODS else None


def _clean(value, limit=160):
    return re.sub(r"\s+", " ", value or "").strip()[:limit]


def _metadata(source):
    tags = sorted(set(_TAG_RE.findall(source)))
    paths = {p.rstrip(".,;:'\"`)") for p in _PATH_RE.findall(source)
             if not p.startswith(("/dev/", "/tmp/", "/usr/", "/bin/"))
             and ":" not in p.split("/", 2)[1]}
    paths.update(_URL_PATH_RE.findall(source))
    paths = sorted(paths)
    testids = sorted({next(v for v in m.groups() if v)
                      for m in _TESTID_RE.finditer(source)})
    pages = sorted(set(_PAGE_RE.findall(source)))
    mask, _ = _lexical_mask(source)
    code = "".join(ch if mask[i] else " " for i, ch in enumerate(source))
    calls = sorted(set(_CALL_NAME_RE.findall(code)) - _HELPER_EXCLUDE)
    helpers = [c for c in calls if not c.startswith(("toBe", "toHave"))][:24]
    imports = [m for m in _IMPORT_RE.findall(source)
               if re.search(r"fixture|factor|data|seed", m, re.I)]
    fixtures = sorted(set(imports + [c for c in helpers
                                     if re.search(r"fixture|factor|seed|login", c, re.I)]))
    assertions = [f"{_clean(m.group(1))} -> {m.group(2)}"
                  for m in _EXPECT_RE.finditer(source)]
    assertions += [_clean(m.group(1)) for m in _ASSERT_RE.finditer(source)]
    exercises = paths + [f"testid:{v}" for v in testids]
    exercises += [f"page:{v}" for v in pages]
    exercises += [f"helper:{v}" for v in helpers]
    return {"tags": tags, "exercises": exercises[:48],
            "fixtures": fixtures[:24], "assertions": assertions[:24]}


def parse(text):
    """Return ``{cases, unparsed_reason}`` for one JS/TS spec file."""
    mask, error = _lexical_mask(text)
    if error:
        return {"cases": [], "unparsed_reason": error}
    records, malformed = [], []
    for match in _CALL_RE.finditer(text):
        if not mask[match.start()]:
            continue
        kind = _kind(match.group("base"), match.group("mods"))
        if kind is None:
            continue
        open_paren = match.end() - 1
        close_paren = _matching(text, mask, open_paren, "(", ")")
        if close_paren is None:
            malformed.append(f"unclosed {kind} call at offset {match.start()}")
            continue
        title = _first_string(text, open_paren + 1, close_paren)
        if title is None:
            malformed.append(f"{kind} call without a literal title at offset {match.start()}")
            continue
        block = _callback_block(text, mask, title[1], close_paren)
        if kind == "suite" and block is None:
            malformed.append(f"suite without a block callback at offset {match.start()}")
            continue
        records.append({"kind": kind, "title": _clean(title[0], 300),
                        "start": match.start(), "end": close_paren + 1,
                        "body_start": block[0] if block else match.start(),
                        "body_end": (block[1] + 1) if block else close_paren + 1})
    if malformed:
        return {"cases": [], "unparsed_reason": "; ".join(malformed[:3])}
    file_fixtures = sorted({m for m in _IMPORT_RE.findall(text)
                            if re.search(r"fixture|factor|data|seed", m, re.I)})
    cases, suites = [], [r for r in records if r["kind"] == "suite"]
    for record in (r for r in records if r["kind"] == "case"):
        parents = [s for s in suites
                   if s["body_start"] < record["start"] < s["body_end"]]
        parents.sort(key=lambda s: s["start"])
        source = text[record["start"]:record["end"]]
        metadata = _metadata(source)
        metadata["fixtures"] = sorted(set(metadata["fixtures"] + file_fixtures))
        cases.append({"suite": [s["title"] for s in parents],
                      "title": record["title"], "body": source,
                      "start": record["start"], **metadata})
    if not cases:
        return {"cases": [],
                "unparsed_reason": "no supported Playwright/node:test case calls found"}
    return {"cases": cases, "unparsed_reason": ""}
