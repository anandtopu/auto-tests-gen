"""One definition of "the user supplied a body of text, inline or in a file".

Two CLIs take a block of prose that is awkward on argv -- `bin/qa.py run-inline`
(a pasted ticket) and `bin/repos.py notes` (per-repo guidance) -- and they
disagreed about it in the two ways that matter:

  * `run-inline` DOCUMENTED `--file` in docs/use-cases.md and did not accept it,
    so the documented ad-hoc-ticket use case exited 2 on `unrecognized
    arguments`. Loud, at least.
  * `notes` accepted both `--set` and `--file` and resolved the tie with
    ``args.set if args.set is not None else args.file`` -- the user's file
    silently discarded while the command printed "guidance saved". That is the
    same defect `engine/lib/selection.py` was fixed for: silently dropping a
    flag is indistinguishable, to the user, from not having one. It is the
    worse of the two because the discarded content is what gets merged into
    AGENTS.md and injected into every authoring phase, so the operator believes
    their guidance is steering generation when nothing of it arrived.
  * An unreadable path raised FileNotFoundError out of `read_text` as a raw
    traceback, which does not name the fix and is not distinguishable, to
    someone reading a CI log, from a crash in the command itself.

So the rule lives here rather than in each caller, and it REFUSES rather than
guessing. "Could not read the file" is not "the file was empty" and neither is
"no text was supplied" (constitution C13): each gets its own message naming
what to do.
"""
from __future__ import annotations

import pathlib


class TextInputError(ValueError):
    """A caller-facing refusal. CLIs pass str(e) straight to sys.exit."""


def resolve(text, file, *, what="text", inline_hint='"<text>"',
            flag="--file"):
    """Return the body of text the user meant, or raise TextInputError.

    ``text`` is the inline/positional value (None when absent) and ``file`` is
    the path given to ``flag`` (None when absent). Exactly one must be present:
    passing both is ambiguous, and picking a winner is what threw the file away.
    """
    has_text = text is not None and str(text).strip() != ""
    has_file = file is not None and str(file).strip() != ""

    if has_text and has_file:
        raise TextInputError(
            f"both {inline_hint} and {flag} were given for the {what} - "
            f"pass one or the other, not both (nothing was written)")
    if not has_text and not has_file:
        raise TextInputError(
            f"no {what} supplied - pass {inline_hint} or {flag} <path>")
    if has_text:
        return str(text)

    path = pathlib.Path(str(file))
    try:
        body = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise TextInputError(f"{flag} {path}: no such file") from None
    except IsADirectoryError:
        raise TextInputError(f"{flag} {path}: is a directory, not a file") from None
    except (OSError, UnicodeDecodeError) as e:
        # The file EXISTS and we could not read it. Reporting that as an empty
        # ticket would send a run at the model with no context at all.
        raise TextInputError(f"{flag} {path}: could not be read ({e})") from None

    if not body.strip():
        raise TextInputError(
            f"{flag} {path} is empty - there is no {what} to act on")
    return body
