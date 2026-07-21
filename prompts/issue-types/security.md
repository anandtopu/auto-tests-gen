# Issue-type guidance: Security fix
- Generate NEGATIVE tests proving the vulnerability is closed: malformed input,
  out-of-range values, injection-shaped payloads (sent as inert DATA strings —
  never executable content), missing/invalid auth where applicable. Assert
  rejection (4xx) and that no sensitive detail leaks in error bodies.
- Assert the FIX, not the exploit: tests must demonstrate the hardened behavior,
  never weaponize the flaw. Do not include working exploit payloads, credentials,
  or secrets in test data — synthetic placeholders only (the gate scans for
  secret patterns and will reject).
- Verify adjacent surfaces that share the same input path (same parser, same
  endpoint family) — security fixes often miss sibling routes.
- Ambiguity here is expensive: if the vulnerable behavior or fixed contract is
  unclear, stop at an open question rather than guessing.
