#!/usr/bin/env python3
"""
Stage 3: the check that makes the authored prose trustworthy.

The whole pipeline is built so that inventing API is STRUCTURALLY hard - a writer never
types a signature, only annotates one the extractor found. This script proves that held.

Checks:
  1. src_ immutability   - authored files must not contain any src_ key
  2. orphan keys         - every params[].field resolves to a real serialized field, and
                           every apiNotes / memberContracts key to a real member id.
                           THIS is the check that catches invented API.
  3. snippet symbols     - every `Thing.Member(` in a code block resolves to a documented
                           type's real member, or to a Unity/BCL whitelist entry
  4. cross-links         - every seeAlso / drivenBy target resolves to a documented entry
  5. coverage ledger     - every extracted type is either an entry or explicitly omitted
  6. schema              - required keys per entryKind, summary length, id validity
  7. provenance          - a workedExample declares verifiedBy, and a 'selftest:<file>' claim must
                           point at a file that really exists
  8. house style         - spaced em dash for prose parentheticals, never '--' or a spaced hyphen
  9. sub-group map       - every name in build.py's GROUP_ORDER resolves to a real type in that
                           category, no type is in two groups, and nothing silently falls to "Other"

Exit code is non-zero if any ERROR is found. Warnings do not fail the run.

Usage:  python tools/verify.py [--cat <category-slug>]
"""

import argparse
import json
import os
import re
import glob
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
SKEL = os.path.join(REPO, "data", "_skeleton")
AUTH = os.path.join(REPO, "data", "_authored")
# Where the Unity toolkit source lives. Defaults to a path RELATIVE to this repo, which is correct
# for the normal layout (<Workspace>/toolkit-field-manual beside <Workspace>/UnityWorkspace/...) and
# keeps a personal absolute path out of a public repository. Override with the TOOLKIT_PATH
# environment variable if the Unity project sits somewhere else.
TOOLKIT_PATH = os.environ.get("TOOLKIT_PATH") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                 "UnityWorkspace", "UnityToolWorkspace", "Assets", "UnityTools", "Toolkit")
)

TOOLKIT_ROOT = TOOLKIT_PATH

# Characters that mark a spaced hyphen as arithmetic rather than a prose dash.
_ARITH = re.compile(r"[0-9()\[\]*/%^=+]|\bmin\b|\bmax\b|\bexp\b")


_QUOTED = re.compile(r"'[^']{0,60}'|\"[^\"]{0,60}\"")


def _walk_strings(obj, path=()):
    """Yield (path, string) for every string in an authored entry, so a style rule can see all prose."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_strings(v, path + (k,))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_strings(v, path + (i,))
    elif isinstance(obj, str):
        yield path, obj

# Symbols a snippet may reference without them being documented tools of this library.
WHITELIST_TYPES = {
    "Debug", "Mathf", "Vector2", "Vector3", "Vector4", "Quaternion", "Time", "Random",
    "Color", "Gizmos", "Physics", "Physics2D", "Input", "Application", "Screen",
    "Camera", "Transform", "GameObject", "Component", "Object", "Resources",
    "SceneManager", "Instantiate", "Destroy", "DontDestroyOnLoad", "FindObjectOfType",
    "List", "Dictionary", "HashSet", "Queue", "Stack", "Array", "Enumerable", "String",
    "Math", "Convert", "Int32", "Single", "Boolean", "Console", "JsonUtility",
    "PlayerPrefs", "Coroutine", "WaitForSeconds", "WaitForSecondsRealtime", "IEnumerator",
    "EditorGUILayout", "EditorGUI", "GUILayout", "GUI", "Selection", "AssetDatabase",
    "Undo", "EditorUtility", "SerializedObject", "SerializedProperty", "MonoBehaviour",
    "ScriptableObject", "EditorWindow", "Task", "Action", "Func", "Rect", "Bounds",
    "RectTransform", "Canvas", "Image", "Text", "TMP_Text", "TextMeshProUGUI", "Sprite",
    "Texture2D", "Material", "Shader", "Renderer", "Collider", "Rigidbody", "Animator",
    "NavMeshAgent", "AudioSource", "LayerMask", "KeyCode", "Keyframe", "AnimationCurve",
}
# Local variable names that obviously aren't types.
IGNORE_RECEIVERS = re.compile(r"^[a-z_][A-Za-z0-9_]*$")

errors = []
warnings = []


def err(msg):
    errors.append(msg)


def warn(msg):
    warnings.append(msg)


def load_skeletons():
    out = {}
    for p in glob.glob(os.path.join(SKEL, "*.json")):
        d = json.load(open(p, encoding="utf-8"))
        out[d["id"]] = d
    return out


def load_authored():
    out = {}
    if not os.path.isdir(AUTH):
        return out
    for p in glob.glob(os.path.join(AUTH, "*.json")):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except json.JSONDecodeError as e:
            err(f"[schema] {os.path.basename(p)} is not valid JSON: {e}")
            continue
        if "id" not in d:
            err(f"[schema] {os.path.basename(p)} has no id")
            continue
        out[d["id"]] = (d, p)
    return out


def collect_code(a):
    blocks = []
    for key in ("usage", "minimalImpl", "wiring"):
        v = a.get(key)
        if isinstance(v, dict) and v.get("code"):
            blocks.append((key, v["code"]))
    we = a.get("workedExample")
    if isinstance(we, dict):
        for s in we.get("steps", []) or []:
            blocks.append(("workedExample.steps", s))
    return blocks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cat")
    args = ap.parse_args()

    skel = load_skeletons()
    auth = load_authored()
    if not auth:
        print("verify: no authored entries yet - nothing to check.")
        return 0

    # name -> id, for cross-link + snippet resolution
    name_to_id = {s["name"].split("<")[0]: sid for sid, s in skel.items()}
    members_by_name = {
        s["name"].split("<")[0]: {m["name"] for m in s["src_members"]}
        for s in skel.values()
    }

    checked = 0
    for eid, (a, path) in sorted(auth.items()):
        s = skel.get(eid)
        base = os.path.basename(path)
        if not s:
            err(f"[orphan] {base}: no skeleton with id '{eid}'")
            continue
        if args.cat and s["cat"].lower() != args.cat.lower():
            continue
        checked += 1

        # 1 - src_ immutability
        for k in a:
            if k.startswith("src_"):
                err(f"[src] {base}: authored file must not contain '{k}' - src_ fields come from the extractor")

        # 6 - schema basics
        summary = a.get("summary", "")
        if not summary:
            err(f"[schema] {base}: missing summary")
        elif len(summary) > 130:
            warn(f"[schema] {base}: summary is {len(summary)} chars (aim <=120)")
        uc = a.get("useCases")
        if uc is not None and not (2 <= len(uc) <= 6):
            warn(f"[schema] {base}: {len(uc)} useCases (aim 3-5)")

        # 2 - orphan keys. The load-bearing anti-invention check.
        real_fields = {f["name"] for f in s["src_serialized"]}
        for p in a.get("params", []) or []:
            fname = p.get("field")
            if fname not in real_fields:
                err(f"[INVENTED FIELD] {base}: params references '{fname}' which is not a "
                    f"[SerializeField] on {s['name']}. Real fields: {sorted(real_fields) or 'none'}")

        real_members = {m["id"] for m in s["src_members"]}
        for key in ("apiNotes", "memberContracts", "fields"):
            for mid in (a.get(key) or {}):
                if key == "fields":
                    if mid not in real_members and mid not in real_fields:
                        err(f"[INVENTED MEMBER] {base}: {key} references '{mid}' which is not on {s['name']}")
                elif mid not in real_members:
                    err(f"[INVENTED MEMBER] {base}: {key} references '{mid}' which is not a member of "
                        f"{s['name']}. Real members: {sorted(real_members) or 'none'}")

        # 3 - snippet symbols
        for where, codeblock in collect_code(a):
            for m in re.finditer(r"\b([A-Za-z_][\w]*)\s*\.\s*([A-Za-z_]\w*)\s*\(", codeblock):
                recv, member = m.group(1), m.group(2)
                if recv in WHITELIST_TYPES or member in WHITELIST_TYPES:
                    continue
                if IGNORE_RECEIVERS.match(recv):
                    continue  # local variable, type unknown - can't check statically
                if recv not in members_by_name:
                    continue  # not one of ours
                if member not in members_by_name[recv]:
                    err(f"[INVENTED CALL] {base} ({where}): '{recv}.{member}()' - {recv} has no "
                        f"member '{member}'. Real: {sorted(members_by_name[recv]) or 'none'}")

        # 4 - cross-links. drivenBy is checked the same way as seeAlso: it names the types that CALL an
        # interface, so a typo there would silently drop a chip rather than show a broken one.
        for key in ("seeAlso", "drivenBy"):
            for target in a.get(key, []) or []:
                if target.split("<")[0] not in name_to_id and target not in WHITELIST_TYPES:
                    warn(f"[link] {base}: {key} '{target}' does not resolve to a documented entry")

        # 7 - worked example must declare how it was verified, and a 'selftest:' claim must point at a file
        # that actually exists. Without this the strongest provenance marker in the manual is unchecked text.
        we = a.get("workedExample")
        if isinstance(we, dict) and "na" not in we:
            vb = we.get("verifiedBy")
            if not vb:
                warn(f"[numeric] {base}: workedExample has no verifiedBy (use 'selftest:<file>' or 'derivation')")
            elif vb.startswith("selftest:"):
                claimed = vb[len("selftest:"):].strip()
                # Accept either a path relative to the toolkit root or one relative to Assets/.
                # Accept a path written relative to the toolkit root ("Math/Spline/Editor/X.cs"), to the
                # Unity project root ("Assets/UnityTools/Toolkit/.../X.cs"), or as an absolute path.
                project_root = os.path.dirname(os.path.dirname(os.path.dirname(TOOLKIT_ROOT)))
                candidates = [
                    os.path.join(TOOLKIT_ROOT, claimed),
                    os.path.join(project_root, claimed),
                    claimed,
                ]
                if not any(os.path.isfile(c) for c in candidates):
                    err(f"[UNVERIFIABLE] {base}: verifiedBy claims '{claimed}' but no such file exists")
            elif vb != "derivation":
                warn(f"[numeric] {base}: verifiedBy '{vb}' is neither 'derivation' nor 'selftest:<file>'")

        # 8 - dash convention. The manual uses a spaced em dash for parentheticals; ASCII '--' and a spaced
        # ASCII hyphen both render as what looks like a typo, and the two pilot categories drifted apart on
        # exactly this before it was caught. Subtraction inside prose is legitimate, so only flag a spaced
        # hyphen whose surroundings are NOT arithmetic.
        for path, val in _walk_strings(a):
            if "code" in path:
                continue
            if " -- " in val:
                warn(f"[style] {base} ({'.'.join(str(x) for x in path)}): ASCII '--' - use a spaced em dash")
            quoted = [(q.start(), q.end()) for q in _QUOTED.finditer(val)]
            for m in re.finditer(r"\s-\s", val):
                lo, hi = m.start(), m.end()
                if _ARITH.search(val[max(0, lo - 14):lo]) and _ARITH.search(val[hi:hi + 14]):
                    continue  # real subtraction, e.g. Mod(index - 1, count)
                # A hyphen inside a quoted span is literal text the tool emits or the user types
                # (a name suffix like ' - 1'), not a prose dash - rewriting it would be wrong.
                if any(a <= lo and hi <= b for a, b in quoted):
                    continue
                warn(f"[style] {base} ({'.'.join(str(x) for x in path)}): spaced hyphen in prose "
                     f"- use a spaced em dash")

    # 9 - sub-group map. The map in build.py is curated BY TYPE NAME, so it is the one part of the
    # pipeline that can silently rot: rename a type and its group entry stops matching; add a tool
    # and it falls into "Other" with no warning. Both are checked here.
    try:
        sys.path.insert(0, HERE)
        from build import GROUP_ORDER, GROUP_MIN_ENTRIES  # noqa
    except Exception as exc:  # noqa: BLE001
        warn(f"[groups] could not import the group map from build.py: {exc}")
        GROUP_ORDER, GROUP_MIN_ENTRIES = {}, 6

    by_cat = {}
    for sid, sk in skel.items():
        by_cat.setdefault(sk["cat"], set()).add(sk["name"])

    try:
        from build import OMIT as _OMIT  # noqa
    except Exception:
        _OMIT = {}
    omitted_names = {sk["name"] for sid, sk in skel.items() if sid in _OMIT}

    for cat, spec in GROUP_ORDER.items():
        real = by_cat.get(cat, set())
        if not real:
            err(f"[GROUPS] category '{cat}' in the group map does not exist in the extracted source")
            continue
        seen = {}
        for title, names in spec:
            for n in names:
                if n in seen:
                    err(f"[GROUPS] {cat}: '{n}' is in two groups ('{seen[n]}' and '{title}')")
                seen[n] = title
                if n not in real:
                    if n in omitted_names:
                        continue  # deliberately omitted from the manual, fine to leave in the map
                    err(f"[GROUPS] {cat}: group '{title}' lists '{n}', which is not a type in "
                        f"this category - renamed or deleted?")
        ungrouped = sorted(real - set(seen) - omitted_names)
        if ungrouped:
            warn(f"[groups] {cat}: {len(ungrouped)} type(s) fall into 'Other' because the group map "
                 f"has not been updated: {', '.join(ungrouped)}")

    for cat, names in sorted(by_cat.items()):
        if cat not in GROUP_ORDER and len(names - omitted_names) >= GROUP_MIN_ENTRIES:
            warn(f"[groups] {cat} has {len(names - omitted_names)} entries and no group map, so it "
                 f"renders as one flat list - consider adding one")

    # 5 - coverage ledger
    try:
        sys.path.insert(0, HERE)
        from build import OMIT  # noqa
    except Exception:
        OMIT = {}
    if not args.cat:
        missing = [sid for sid in skel if sid not in auth and sid not in OMIT]
        if missing:
            warn(f"[coverage] {len(missing)} extracted types are neither authored nor omitted "
                 f"(expected while authoring is in progress)")

    print(f"verify: checked {checked} authored entries")
    for w in warnings:
        print("  WARN  " + w)
    for e in errors:
        print("  ERROR " + e)
    if errors:
        print(f"\nverify: FAILED - {len(errors)} error(s), {len(warnings)} warning(s)")
        return 1
    print(f"\nverify: PASSED - 0 errors, {len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
