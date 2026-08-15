#!/usr/bin/env python3
"""Did an alarm actually reach a human? One definition, three call sites.

Several alarms in this platform advance DURABLE STATE once they believe they
have been delivered, and each says why in its own comment: coverage_drift only
moves its baseline when the notification lands, spec_drift only records a
scenario as reported once the message goes out, because "notify once per
change" is safe only if the change is committed after the notification.

Both were written against a two-state world: the channel worked, or it did not.
There is a third, and it is the DEPLOYED DEFAULT — `AIQE_MOCK: "1"` in
`deploy/openshift/configmap.yaml` and `AIQE_MOCK=1` in the Dockerfile. Under it
the Notify port resolves to `adapters/mock/notify.sh`, which appends to
`out/mock-comments.log` and exits 0. Every caller read that as delivery.

MEASURED against an isolated drift file, mock mode, uncovered surface grown
2 -> 9:

    COVERAGE DRIFT: payments-api uncovered surface grew 2 -> 9
    delivered   : True
    baseline now: {'payments-api': 9}

The alarm fired, nobody was told, and the baseline moved past it — so the next
night reports "no growth" and the drift is never mentioned again. That is
exactly the failure the delivery gate was added to prevent, arriving through
the one path nobody modelled.

THREE STATES, because the fixes differ (C13):

    sent       a real adapter accepted it. The only state that may advance
               durable alarm state.
    simulated  the mock adapter accepted it. Nothing left the machine; the fix
               is `AIQE_MOCK=0` plus channel credentials, NOT a channel repair.
    failed     a real adapter refused or could not be reached. Fix the channel.

`simulated` deliberately does NOT advance state, the same as `failed`: in both
cases no human was told, and an alarm nobody received must be raised again. The
messages stay separate because sending an operator to debug Slack when the real
answer is "you are running in mock mode" wastes the outage.
"""
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import env_flag

SENT = "sent"
SIMULATED = "simulated"
FAILED = "failed"


def outcome(returncode, mock=None):
    """Map an adapter's exit status to what can honestly be claimed.

    `mock` is injectable so a caller that already resolved the flag to pick its
    adapter passes the SAME value on, rather than re-reading the environment
    and risking a different answer than the one that chose the adapter.
    """
    if mock is None:
        mock = env_flag.mock()
    if returncode != 0:
        return FAILED
    return SIMULATED if mock else SENT


def landed(state):
    """Whether a human can be assumed to have been told. The one question a
    delivery-gated state advance is really asking."""
    return state == SENT


def note(state, what):
    """The operator-facing sentence, or None when there is nothing to say.

    Silent on success: a warning that fires on a healthy nightly run is one
    people learn to scroll past, which is how the real ones get missed.
    """
    if state == SENT:
        return None
    if state == SIMULATED:
        return (f"{what} was written to the MOCK notify adapter "
                f"(out/mock-comments.log): nobody was notified, so the state "
                f"is NOT advanced and this will be reported again. Set "
                f"AIQE_MOCK=0 and configure a channel to deliver it.")
    return (f"{what} could not be delivered: the state is NOT advanced, so "
            f"the next run will report it again. Check the notify channel.")
