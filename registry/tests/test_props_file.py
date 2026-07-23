"""Properties-file configuration (engine/lib/props_file.py).

Two things carry this feature:
  1. The PRECEDENCE — aiqe.properties < .env < explicit environment. Get it wrong
     and a Settings-page save silently appears to do nothing, which is the worst
     kind of config bug to diagnose.
  2. The PARSER — real .properties files in Atlassian shops use ':' and bare-space
     separators, line continuations and escapes. Half-parsing them hands adapters a
     truncated URL rather than failing loudly.
"""
import pathlib, subprocess, sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import props_file as pf
import settings_store as ss


@pytest.fixture
def props(tmp_path, monkeypatch):
    def _write(text):
        p = tmp_path / "aiqe.properties"
        p.write_text(text, encoding="utf-8")
        monkeypatch.setenv("AIQE_PROPERTIES", str(p))
        return p
    return _write


# ------------------------------------------------------------------- the parser

@pytest.mark.parametrize("line,key,value", [
    ("JIRA_URL=https://j.example.com", "JIRA_URL", "https://j.example.com"),
    ("JIRA_URL: https://j.example.com", "JIRA_URL", "https://j.example.com"),
    ("JIRA_URL https://j.example.com", "JIRA_URL", "https://j.example.com"),
    ("  JIRA_URL   =   https://j.example.com  ", "JIRA_URL", "https://j.example.com"),
    ("EMPTY=", "EMPTY", ""),
    ("BARE_KEY", "BARE_KEY", ""),
    ("WITH_EQUALS=a=b=c", "WITH_EQUALS", "a=b=c"),
    ("WITH_HASH=value#notacomment", "WITH_HASH", "value#notacomment"),
])
def test_separator_forms(line, key, value):
    assert pf.parse(line) == {key: value}


def test_space_form_is_not_split_on_a_url_colon():
    """The bug this test exists for: searching '=' / ':' before whitespace split
    'STASH_URL https://host' on the colon inside 'https:'."""
    assert pf.parse("STASH_URL https://stash.example.com") == \
        {"STASH_URL": "https://stash.example.com"}
    assert pf.parse("A_KEY\thttps://x.example.com:8443/p") == \
        {"A_KEY": "https://x.example.com:8443/p"}


def test_comments_and_blank_lines_are_ignored():
    d = pf.parse("# comment\n! also a comment\n\n  \nREAL=1\n#TRAILING=no")
    assert d == {"REAL": "1"}


def test_line_continuation():
    d = pf.parse("URL=https://example.com/\\\n    services/T/B\nNEXT=2")
    assert d == {"URL": "https://example.com/services/T/B", "NEXT": "2"}


def test_escapes_including_unicode():
    d = pf.parse(r"A=tab\there" "\n" r"B=—dash" "\n" r"C\=key=v" "\n" r"D=back\\slash")
    assert d["A"] == "tab\there"
    assert d["B"] == "—dash"
    assert d["C=key"] == "v"
    assert d["D"] == "back\\slash"


def test_an_escaped_trailing_backslash_does_not_continue():
    d = pf.parse("A=ends with a backslash\\\\\nB=2")
    assert d["B"] == "2", "escaped backslash was treated as a continuation"


@pytest.mark.parametrize("junk", ["", "   ", "#only a comment", "=novalue", ":x"])
def test_malformed_input_never_raises(junk):
    pf.parse(junk)                                  # must not raise


def test_unreadable_file_degrades_to_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("AIQE_PROPERTIES", str(tmp_path / "does-not-exist.properties"))
    assert pf.find() is None and pf.load() == {}


# ---------------------------------------------------------------- discovery

def test_explicit_path_wins_and_accepts_a_list(tmp_path, monkeypatch):
    a, b = tmp_path / "a.properties", tmp_path / "b.properties"
    b.write_text("FROM=b", encoding="utf-8")
    monkeypatch.setenv("AIQE_PROPERTIES", f"{a},{b}")   # a is missing -> falls to b
    assert pf.find() == b and pf.load()["FROM"] == "b"


def test_default_locations_are_searched():
    names = [pathlib.Path(c).name for c in pf.candidates()]
    assert "aiqe.properties" in names


# --------------------------------------------------------------- precedence

def test_properties_fill_unset_keys(props, monkeypatch):
    props("OPENHANDS_URL=https://oh.example.com\n")
    monkeypatch.delenv("OPENHANDS_URL", raising=False)
    env = {}
    pf.apply_to(env)
    assert env["OPENHANDS_URL"] == "https://oh.example.com"


def test_explicit_environment_always_wins(props):
    props("SCM_KIND=stash\n")
    env = {"SCM_KIND": "github"}
    pf.apply_to(env)
    assert env["SCM_KIND"] == "github", "properties overrode an exported variable"


def test_dotenv_beats_properties(props, tmp_path, monkeypatch):
    """.env is what the Settings page writes — a UI save must not be shadowed."""
    props("JIRA_URL=https://from-properties.example.com\n")
    dotenv = tmp_path / ".env"
    dotenv.write_text("JIRA_URL=https://from-dotenv.example.com\n", encoding="utf-8")
    monkeypatch.setenv("AIQE_ENV_FILE", str(dotenv))
    env = {}
    ss.load_env_into(env)
    assert env["JIRA_URL"] == "https://from-dotenv.example.com"


def test_full_chain_properties_then_dotenv_then_explicit(props, tmp_path, monkeypatch):
    props("A=props\nB=props\nC=props\n")
    dotenv = tmp_path / ".env"
    dotenv.write_text("B=dotenv\nC=dotenv\n", encoding="utf-8")
    monkeypatch.setenv("AIQE_ENV_FILE", str(dotenv))
    env = {"C": "explicit"}
    ss.load_env_into(env)
    assert (env["A"], env["B"], env["C"]) == ("props", "dotenv", "explicit")


def test_empty_values_do_not_mask_a_later_layer(props, tmp_path, monkeypatch):
    """An unset key in the example file must not block .env from supplying it."""
    props("JIRA_URL=\n")
    dotenv = tmp_path / ".env"
    dotenv.write_text("JIRA_URL=https://real.example.com\n", encoding="utf-8")
    monkeypatch.setenv("AIQE_ENV_FILE", str(dotenv))
    env = {}
    ss.load_env_into(env)
    assert env["JIRA_URL"] == "https://real.example.com"


# ------------------------------------------------------------------- surfaces

def test_shell_defaults_quotes_dangerous_values(props):
    props("EVIL=$(touch /tmp/pwned) `id` ; rm -rf /\nOK=plain\n")
    r = subprocess.run([sys.executable, str(ROOT / "engine/lib/props_file.py"),
                        "shell-defaults"], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    assert r.returncode == 0
    assert "export OK='plain'" in r.stdout
    evil = [l for l in r.stdout.splitlines() if l.startswith("export EVIL=")][0]
    assert evil.startswith("export EVIL='") and evil.endswith("'"), \
        f"value not single-quoted, bash could execute it: {evil}"


def test_shell_defaults_skips_keys_already_in_the_environment(props, monkeypatch):
    props("SCM_KIND=stash\n")
    import os
    env = {**os.environ, "SCM_KIND": "github",
           "AIQE_PROPERTIES": os.environ["AIQE_PROPERTIES"]}
    r = subprocess.run([sys.executable, str(ROOT / "engine/lib/props_file.py"),
                        "shell-defaults"], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
                       env=env)
    assert "SCM_KIND" not in r.stdout, "would have clobbered an exported variable"


def test_status_reports_names_but_never_values(props):
    props("ATLASSIAN_MCP_TOKEN=super-secret-value\n")
    s = pf.status()
    assert "ATLASSIAN_MCP_TOKEN" in s["keys"]
    assert "super-secret-value" not in repr(s), "status leaked a credential"


def test_pipeline_loads_properties_before_dotenv():
    src = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    assert "props_file.py shell-defaults" in src
    assert src.index("shell-defaults") < src.index("source .env"), \
        ".env must be sourced AFTER properties so it overrides them"


def test_example_covers_every_settings_variable():
    """The example is generated from SPEC; a new Settings field must appear in it."""
    text = (ROOT / "aiqe.properties.example").read_text(encoding="utf-8")
    missing = [f["env"] for sec in ss.SPEC for f in sec["fields"]
               if f"\n{f['env']}=" not in text]
    assert not missing, f"aiqe.properties.example is missing: {missing}"


def test_real_properties_file_is_gitignored():
    """It carries API tokens — only the example belongs in git."""
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "aiqe.properties" in gi
    r = subprocess.run(["git", "ls-files", "aiqe.properties", "config/aiqe.properties"],
                       cwd=ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert not r.stdout.strip(), f"credential file is tracked: {r.stdout}"
