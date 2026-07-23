#!/usr/bin/env python3
"""Java-style `.properties` config for every external system in the Settings page.

Teams that deploy this platform alongside JIRA/Stash already manage configuration as
`.properties` files (Ansible, Puppet, a config repo, an OpenShift ConfigMap). Making
them hand-write a shell `.env` — and keep it in step — is friction with no upside, so
the same variables can now come from a properties file loaded at startup.

**Precedence, lowest to highest:**

    aiqe.properties   <   .env   <   explicit environment

That order is deliberate. `.env` is what the Settings page writes, so if properties
outranked it, saving a value in the UI would appear to do nothing — the worst kind of
config bug, because it looks like the save failed. Properties are therefore the
*baseline* an operator ships with, `.env` is the local override, and an explicitly
exported variable always wins over both (matching how AIQE_MOCK and AIQE_CRITIC
already behave).

Discovery order (first file that exists wins):

    $AIQE_PROPERTIES        explicit path — also accepts a comma-separated list
    ./aiqe.properties
    ./config/aiqe.properties

Format: the practical subset of `java.util.Properties` —

    # and ! start a comment line
    key=value          key:value          key value
    key = value with spaces          (whitespace around the separator is trimmed)
    long.value = first part \\
                 continued on the next line
    escaped\\=key = value            (\\= \\: \\# \\! \\\\ \\n \\t \\r \\uXXXX)

Values are NEVER logged by this module — callers get names only, because these files
carry API tokens.
"""
import os, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_NAMES = ("aiqe.properties", "config/aiqe.properties")

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "f": "\f",
            "\\": "\\", "=": "=", ":": ":", "#": "#", "!": "!", " ": " "}


def candidates():
    """Every path we would consider, in order — used by diagnostics."""
    out = []
    explicit = os.environ.get("AIQE_PROPERTIES", "").strip()
    if explicit:
        out += [pathlib.Path(p.strip()).expanduser()
                for p in explicit.split(",") if p.strip()]
    out += [ROOT / n for n in DEFAULT_NAMES]
    return out


def find():
    """The properties file in effect, or None."""
    for p in candidates():
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    return None


def _unescape(s):
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        i += 1
        if i >= len(s):                       # trailing lone backslash
            break
        n = s[i]
        if n == "u" and i + 4 < len(s):
            try:
                out.append(chr(int(s[i + 1:i + 5], 16)))
                i += 5
                continue
            except ValueError:
                pass                          # not a real \uXXXX — fall through
        out.append(_ESCAPES.get(n, n))
        i += 1
    return "".join(out)


def _logical_lines(text):
    """Join backslash-continued physical lines into logical ones."""
    buf, out = "", []
    for raw in text.splitlines():
        line = raw
        if buf:
            line = line.lstrip()              # continuations drop leading indent
        stripped = line.rstrip()
        # a trailing backslash continues, but an ESCAPED backslash (\\) does not
        if stripped.endswith("\\") and not stripped.endswith("\\\\"):
            buf += stripped[:-1]
            continue
        out.append(buf + line)
        buf = ""
    if buf:
        out.append(buf)
    return out


def parse(text):
    """{key: value} from properties text. Total — never raises on malformed input."""
    vals = {}
    for line in _logical_lines(text):
        s = line.strip()
        if not s or s[0] in "#!":
            continue
        # The key ends at the FIRST unescaped '=', ':' or whitespace — whichever
        # comes first. Searching for '=' / ':' ahead of whitespace would split
        # "STASH_URL https://host" on the colon inside "https:".
        end = None
        for i, ch in enumerate(s):
            if (ch in "=:" or ch.isspace()) and not _is_escaped(s, i):
                end = i
                break
        if end is None:                       # bare key, empty value
            k = _unescape(s).strip()
            if k:
                vals[k] = ""
            continue
        k = _unescape(s[:end]).strip()
        rest = s[end:]
        # Java: skip whitespace, then AT MOST ONE '=' or ':', then whitespace again.
        rest = rest.lstrip()
        if rest[:1] in ("=", ":"):
            rest = rest[1:].lstrip()
        if k:
            vals[k] = _unescape(rest).strip()
    return vals


def _is_escaped(s, i):
    """True when s[i] is preceded by an odd number of backslashes."""
    n = 0
    j = i - 1
    while j >= 0 and s[j] == "\\":
        n += 1
        j -= 1
    return n % 2 == 1


def load(path=None):
    """Parsed properties from `path` (or the discovered file); {} when there is none."""
    p = pathlib.Path(path) if path else find()
    if not p:
        return {}
    try:
        return parse(p.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return {}                             # unreadable file must not break startup


def apply_to(environ=None, path=None):
    """Apply properties as process-env DEFAULTS. Returns the NAMES applied.

    Anything already present in the environment is left alone, which is what keeps
    an explicitly exported variable — and, once it is sourced, `.env` — on top.
    """
    environ = os.environ if environ is None else environ
    applied = []
    for k, v in load(path).items():
        if v != "" and k not in environ:
            environ[k] = v
            applied.append(k)
    return applied


def status():
    """Diagnostics for `make config` / the Settings view. Names only, never values."""
    p = find()
    keys = sorted(load(p)) if p else []
    return {"path": str(p) if p else None,
            "searched": [str(c) for c in candidates()],
            "count": len(keys), "keys": keys}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    if cmd == "shell-defaults":
        # Emitted for `eval` in pipeline.sh: only keys absent from the environment,
        # single-quoted so a value can never be word-split or executed by bash.
        for k, v in sorted(load().items()):
            if v != "" and k not in os.environ and k.replace("_", "").isalnum():
                print(f"export {k}='" + v.replace("'", "'\\''") + "'")
    else:
        s = status()
        if not s["path"]:
            print("no properties file found. Searched:")
            for c in s["searched"]:
                print(f"  {c}")
            print("\nCreate one from aiqe.properties.example, or set AIQE_PROPERTIES.")
            sys.exit(0)
        print(f"properties: {s['path']}  ({s['count']} setting(s))")
        for k in s["keys"]:                   # names only — these files hold tokens
            print(f"  {k}")
