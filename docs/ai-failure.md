# AI failure case: an incomplete SSRF rule

## What the AI proposed
In the plan for the link checker (`.claude/plans/link-checker.md`), the AI decided whether an IP
address was safe to visit like this, after unwrapping IPv4 addresses hidden inside IPv6:

```python
return ip.is_global and not ip.is_multicast
```

The idea was that Python's `ipaddress` module already knows which addresses are public.
The plan was reviewed and approved.

## Why it was wrong
`is_global` does not mean "safe public internet address". On Python 3.12.16, the version used
here, it is also `True` for some old or reserved IPv6 forms:

| address | what it is | `is_global` |
|---|---|---|
| `::7f00:1` | `127.0.0.1` written in an old IPv6 form | `True` |
| `fec0::1` | an old "site-local" (internal) address | `True` |

The guard would have allowed both, although the spec says any non-public address is refused.
These formats are obsolete, so the practical risk was probably low. No attack was attempted.

## How it was caught
The first version of the guard passed all its tests, because the tests only covered the
addresses the AI had already thought of. The AI then printed how Python classifies some more
unusual addresses, and saw the two wrong results. This happened before the code was committed.

## The fix
`services/url_safety.py` now also refuses reserved and site-local addresses:

```python
if ip.is_multicast or ip.is_reserved:
    return False
if isinstance(ip, ipaddress.IPv6Address) and ip.is_site_local:
    return False
return ip.is_global
```

Both addresses were added to `tests/test_url_safety.py`.

## Lesson
- Passing tests only prove the cases someone thought to write.
- Do not trust a library property because of its name. Check what it returns for hostile input.
- For security rules, an approved plan is not proof. Test the unusual cases too.
- A list of what to block can miss things. Review it again when Python is upgraded.
