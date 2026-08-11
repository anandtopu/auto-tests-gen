#!/usr/bin/env python3
"""Is this credential the shipped placeholder rather than a real secret?

`deploy/openshift/deploy.sh` falls back to `secret.example.yaml` when an
operator has not created `secret.yaml`, and warns at DEPLOY time. Nothing said
so at RUNTIME: the token is non-empty, so `bin/dashboard_server.py` printed
`auth: token` and its "listening with NO auth" warning could not fire. A server
bound to 0.0.0.0 (which the Dockerfile and the OpenShift ConfigMap both set)
and protected by a value published in this repository therefore looked
correctly configured.

That is the same shape as the warning already in that file, whose own comment
says "the rule lived only in the comment above, which operators do not read" --
here it lived only in deploy.sh's scrollback, which they read once.

The values are LITERAL rather than parsed out of the example file, because the
manifests are not shipped inside the image; `registry/tests/
test_placeholder_secrets.py` pins them against `secret.example.yaml` so the two
cannot drift apart.
"""

# Exactly the credential values `secret.example.yaml` ships. An empty value is
# NOT included: "no token at all" is a different state with its own, louder
# warning, and folding them together would blur two situations an operator
# fixes differently.
PLACEHOLDERS = frozenset({
    "change-me-ui",      # AIQE_UI_TOKEN
    "change-me-hook",    # AIQE_HOOK_TOKEN
})


def is_placeholder(value):
    """True when `value` is a shipped placeholder credential.

    Whitespace is stripped because a YAML edit that leaves `"change-me-ui "`
    behind is still the placeholder; case is NOT folded, because a deliberately
    chosen `CHANGE-ME-UI` is a (bad, but distinct) operator decision and this
    function should not silently claim to know what they meant.
    """
    return str(value or "").strip() in PLACEHOLDERS


def warning(env_name, value, consequence):
    """The one-line warning, or None when the credential is fine.

    Returns text rather than printing it so each caller keeps control of its
    own stream and prefix -- and so this is testable without capturing stdout.
    """
    if not is_placeholder(value):
        return None
    return (f"WARNING: {env_name} is still the placeholder from "
            f"secret.example.yaml. It is published in this repository, so "
            f"{consequence} Set a real value before exposing this port.")
