#!/usr/bin/env python3
"""
Stage 0 of the field-manual pipeline.

Reads the UnityTools Toolkit C# sources and emits one JSON skeleton per PUBLIC TYPE
containing only mechanically-derived facts (every key prefixed `src_`). Nothing in here
is ever written by hand or by a model: stage 3 (verify.py) re-runs this extractor and
byte-compares, so any later edit to a `src_` block is caught.

Why regex and not Roslyn: this codebase is conventionally formatted (attributes on their
own lines, one namespace per file, no preprocessor tricks), and a regex pass over it is
both sufficient and dependency-free. A full C# parser would be a large dependency for no
extra fidelity here.

Two traps this file deliberately handles, both measured against the real source:
  * `[Range(...)]` is NEVER written bare in this codebase - all 61 uses are combined,
    as `[SerializeField, Range(0f, 1f)]`. Matching only `^\\[Range\\(` silently finds none.
  * XML `///` docs are effectively absent (2 files). The real prose is the leading `//`
    block above each type and member, so that is copied VERBATIM rather than paraphrased.

Usage:  python tools/extract.py [--toolkit PATH] [--out PATH]
"""

import argparse
import json
import os
import re
import sys

# Where the Unity toolkit source lives. Defaults to a path RELATIVE to this repo, which is correct
# for the normal layout (<Workspace>/toolkit-field-manual beside <Workspace>/UnityWorkspace/...) and
# keeps a personal absolute path out of a public repository. Override with the TOOLKIT_PATH
# environment variable if the Unity project sits somewhere else.
TOOLKIT_PATH = os.environ.get("TOOLKIT_PATH") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                 "UnityWorkspace", "UnityToolWorkspace", "Assets", "UnityTools", "Toolkit")
)

DEFAULT_TOOLKIT = TOOLKIT_PATH
DEFAULT_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "_skeleton")

# Folders that are not documentable tools.
SKIP_DIR_PARTS = {"_Demos"}
# DemoSceneBuilder is 6.8k lines of scene-generation scaffolding, not a tool. Omitting it
# here (rather than silently) keeps the coverage ledger in verify.py honest.
SKIP_FILES = {"DemoSceneBuilder.cs", "DemoLayoutAudit.cs"}

TYPE_RE = re.compile(
    r"^\s*(?P<mods>(?:public|internal|private|protected|static|sealed|abstract|partial|readonly|\s)*)"
    r"\b(?P<kind>class|struct|interface|enum)\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*(?P<generic><[^>]*>)?\s*"
    r"(?::\s*(?P<bases>[^{]+?))?\s*(?:\{|$)"
)

FIELD_RE = re.compile(
    r"^\s*(?:\[[^\]]*\]\s*)*"
    r"(?:public|private|protected|internal)\s+"
    r"(?:static\s+|readonly\s+)*"
    r"(?P<type>[\w<>\[\],\.\?\s]+?)\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*"
    r"(?:=\s*(?P<default>[^;]+?))?\s*;"
)

MEMBER_RE = re.compile(
    r"^\s*(?P<mods>(?:public|protected|internal|static|virtual|override|abstract|sealed|async|new|extern|unsafe|\s)*)"
    r"(?P<rest>[\w<>\[\],\.\?]+\s+[A-Za-z_]\w*\s*(?:\(|\{|=>|$))"
)

UNITY_MSG = {
    "Awake", "Start", "Update", "LateUpdate", "FixedUpdate", "OnEnable", "OnDisable",
    "OnDestroy", "OnValidate", "OnGUI", "OnDrawGizmos", "OnDrawGizmosSelected",
    "OnTriggerEnter", "OnTriggerExit", "OnTriggerStay", "OnCollisionEnter",
    "OnCollisionExit", "OnCollisionStay", "OnApplicationQuit", "OnApplicationPause",
    "OnRenderImage", "OnPreCull", "OnPreRender", "OnPostRender", "OnBecameVisible",
    "OnBecameInvisible", "CreateGUI", "OnTriggerEnter2D", "OnTriggerExit2D",
}


def strip_code(line):
    """Blank out string/char literals and line comments so brace counting is reliable."""
    out, i, n = [], 0, len(line)
    while i < n:
        c = line[i]
        if c == '"':
            i += 1
            while i < n and line[i] != '"':
                if line[i] == "\\":
                    i += 1
                i += 1
            i += 1
            out.append('""')
            continue
        if c == "'":
            i += 1
            while i < n and line[i] != "'":
                if line[i] == "\\":
                    i += 1
                i += 1
            i += 1
            out.append("''")
            continue
        if c == "/" and i + 1 < n and line[i + 1] == "/":
            break
        out.append(c)
        i += 1
    return "".join(out)


def slugify(name):
    """CamelCase -> kebab-case, keeping trailing digits attached to their word.

    These slugs become permanent URLs, so the naive rule mattered: it split
    `GeometryUtils2D` into `geometry-utils2-d`. A digit run and any single trailing
    capital after it belong to the preceding word.
    """
    s = re.sub(r"<.*?>", "", name)
    s = re.sub(r"(?<!^)(?=[A-Z][a-z])", "-", s)
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", "-", s)
    s = re.sub(r"-+", "-", s).lower().strip("-")
    # "utils2-d" -> "utils2d"
    return re.sub(r"(\d)-([a-z])(?![a-z])", r"\1\2", s)


def peel_attributes(stripped):
    """Split a line into (leading attribute groups, remaining declaration).

    Unity code overwhelmingly writes the attribute and the declaration on ONE line
    (`[SerializeField] private float maxOffset = 0.5f;`). Treating any line that starts
    with '[' as attributes-only silently drops every serialized field - which is exactly
    what happened on the first run of this extractor (0 of 1071 fields found).
    """
    attrs = []
    i, n = 0, len(stripped)
    while i < n and stripped[i] == "[":
        depth = 0
        j = i
        while j < n:
            c = stripped[j]
            if c == '"':
                j += 1
                while j < n and stripped[j] != '"':
                    if stripped[j] == "\\":
                        j += 1
                    j += 1
            elif c in "[(":
                depth += 1
            elif c in "])":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        attrs.append(stripped[i : j + 1])
        i = j + 1
        while i < n and stripped[i] == " ":
            i += 1
    return attrs, stripped[i:].strip()


def parse_attr_list(attr_lines):
    """Split raw attribute source into individual attribute names+args, handling
    combined form `[SerializeField, Range(0f, 1f)]` which is the ONLY form Range
    appears in anywhere in this codebase."""
    found = []
    for raw in attr_lines:
        inner = raw.strip()
        if inner.startswith("[") and inner.endswith("]"):
            inner = inner[1:-1]
        depth, cur = 0, ""
        for ch in inner:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if ch == "," and depth == 0:
                found.append(cur.strip())
                cur = ""
            else:
                cur += ch
        if cur.strip():
            found.append(cur.strip())
    return found


_CS_ESCAPES = {
    "n": chr(10), "r": chr(13), "t": chr(9), "0": chr(0),
    chr(34): chr(34), chr(39): chr(39), chr(92): chr(92),
}


def decode_cs_string(text):
    """Turn the RAW source text of a C# string literal into the string it actually represents.

    The extractor reads source, not a compiled assembly, so a [Tooltip("...")] arrives with its
    escapes intact: a deliberate paragraph break came through as a literal backslash-n and rendered
    on the page as "\\n\\n", and an escaped quote rendered as \\". Only ever applied to text that
    was genuinely wrapped in quotes - a // comment is raw text where a backslash means nothing, and
    decoding it would mangle things like a regex or a generic written as List\\<T\\>.

    Verbatim literals (@"...") would need different rules, but the toolkit has none; if one is ever
    added it would arrive here with its @ already stripped and its doubled quotes intact.
    """
    if chr(92) not in text:
        return text
    out = []
    i = 0
    while i < len(text):
        c = text[i]
        if c == chr(92) and i + 1 < len(text):
            nxt = text[i + 1]
            if nxt in _CS_ESCAPES:
                out.append(_CS_ESCAPES[nxt])
                i += 2
                continue
            # Not a C# escape we know - keep the backslash so nothing is silently lost.
        out.append(c)
        i += 1
    return "".join(out)


def attr_arg(attr, quoted=True):
    m = re.search(r"\((.*)\)\s*$", attr, re.S)
    if not m:
        return None
    arg = m.group(1).strip()
    if quoted:
        q = re.match(r'^\s*"(.*)"\s*$', arg, re.S)
        # Decode ONLY when this really was a quoted string literal. An unquoted argument list
        # (a Range, a CreateAssetMenu spec) is code, not text, and must pass through untouched.
        return decode_cs_string(q.group(1)) if q else arg
    return arg


def menu_path(attr):
    """Return the menu path from a [MenuItem(...)], or None if this is a validate overload.

    MenuItem takes optional extra arguments - [MenuItem("Toolkit/Find References", false, 20)] - so
    taking the whole argument list gave `"Toolkit/Find References", false, 20` with the quotes still on.
    Only the first string literal is the path. A MenuItem whose second argument is `true` is the VALIDATE
    function for the same path (it only decides whether the item is greyed out), so it is not a separate
    menu entry and is dropped.
    """
    m = re.search(r"\((.*)\)\s*$", attr, re.S)
    if not m:
        return None
    args = m.group(1)
    q = re.match(r'\s*"((?:[^"\\]|\\.)*)"\s*(.*)$', args, re.S)
    if not q:
        return None
    rest = q.group(2).lstrip(", \t\r\n")
    if rest.lower().startswith("true"):
        return None
    return q.group(1)


def category_of(rel_path):
    return rel_path.replace("\\", "/").split("/")[0]


def balanced_args(head):
    """Return the text inside a method's OUTERMOST parentheses.

    A non-greedy `\\((.*?)\\)` stops at the first closing paren, which is wrong the moment
    a parameter contains one - `WeightedPick<T>(IReadOnlyList<(T item, float weight)> o)`
    got truncated to `IReadOnlyList<(T, float)`.
    """
    start = head.find("(")
    if start < 0:
        return ""
    depth = 0
    for i in range(start, len(head)):
        if head[i] == "(":
            depth += 1
        elif head[i] == ")":
            depth -= 1
            if depth == 0:
                return head[start + 1 : i]
    return head[start + 1 :]


def split_top_level(args):
    """Split a parameter list on commas that are NOT inside <>, () or []."""
    out, depth, cur = [], 0, ""
    for ch in args:
        if ch in "<([":
            depth += 1
        elif ch in ">)]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return [x for x in out if x.strip()]


def parse_file(abs_path, rel_path, toolkit_root):
    """Parse one .cs file into a list of public-type records.

    Control flow note (this was a real bug): every branch below falls through to ONE
    shared epilogue that updates brace depth and closes the open type. An earlier version
    `continue`d straight past that epilogue, so a type which opened and closed on a single
    line - `public enum UILightingMode { Shine, Rim, SceneLight }` - never closed, and the
    class declared after it in the same file was therefore never opened at all. That
    silently dropped every serialized field in 15 files (88 fields) while reporting success.
    """
    with open(abs_path, "r", encoding="utf-8-sig", errors="replace") as fh:
        lines = fh.read().splitlines()

    namespace = ""
    depth = 0
    types = []
    open_type = None
    type_depth = None
    pending_attrs = []
    seen_brace = False
    pending_comment = []

    for idx, raw in enumerate(lines):
        line = raw.rstrip()
        stripped = line.strip()
        code = strip_code(line)          # brace counting always uses the ORIGINAL line
        clear_pending = True

        if not stripped:
            pending_comment = []
            clear_pending = False

        elif stripped.startswith("//"):
            pending_comment.append(stripped.lstrip("/").strip())
            clear_pending = False

        else:
            if stripped.startswith("["):
                peeled, remainder = peel_attributes(stripped)
                pending_attrs.extend(peeled)
                if not remainder:
                    clear_pending = False
                    stripped = ""
                else:
                    stripped = remainder
                    line = remainder

            if stripped:
                ns = re.match(r"^\s*namespace\s+([\w\.]+)", line)
                tm = TYPE_RE.match(line)
                mods = (tm.group("mods") or "") if tm else ""
                is_public_type = bool(tm) and ("public" in mods or "internal" in mods)

                if ns:
                    namespace = ns.group(1)

                elif is_public_type and open_type is None:
                    open_type = _new_type(tm, namespace, rel_path, idx, pending_comment,
                                          parse_attr_list(pending_attrs))
                    type_depth = depth
                    seen_brace = False

                elif open_type is not None and depth == (type_depth + 1):
                    _member_line(open_type, stripped, line, pending_attrs, pending_comment)

        # ---- shared epilogue: every path reaches this ----
        if clear_pending:
            pending_attrs, pending_comment = [], []

        if "{" in code:
            seen_brace = True
        depth += code.count("{") - code.count("}")

        # Only close once the body has actually been entered. Most types here are written
        # `class X` with `{` on the FOLLOWING line, so depth is still == type_depth on the
        # declaration line itself; closing on that would end the type before its body and
        # drop every member. seen_brace also handles `public enum X { A, B }`, which opens
        # and closes on one line and must close immediately.
        if (open_type is not None and type_depth is not None
                and seen_brace and depth <= type_depth):
            open_type["src_lines"][1] = idx + 1
            types.append(open_type)
            open_type, type_depth, seen_brace = None, None, False

    if open_type is not None:
        open_type["src_lines"][1] = len(lines)
        types.append(open_type)

    return types


def _new_type(tm, namespace, rel_path, idx, pending_comment, attrs):
    bases = [b.strip() for b in (tm.group("bases") or "").split(",") if b.strip()]
    base, implements = None, []
    for b in bases:
        if re.match(r"^I[A-Z]", b):
            implements.append(b)
        elif base is None:
            base = b
        else:
            implements.append(b)

    rec = {
        "id": slugify(tm.group("name")),
        "name": tm.group("name") + (tm.group("generic") or ""),
        "cat": category_of(rel_path),
        "src_typeKind": tm.group("kind"),
        "src_ns": namespace,
        "src_file": rel_path.replace("\\", "/"),
        "src_lines": [idx + 1, idx + 1],
        "src_base": base,
        "src_implements": implements,
        "src_static": "static" in (tm.group("mods") or ""),
        "src_internal": "internal" in (tm.group("mods") or ""),
        "src_runtime": "/Editor/" not in ("/" + rel_path.replace("\\", "/")),
        "src_attributes": {},
        "src_typeComment": " ".join(pending_comment).strip(),
        "src_serialized": [],
        "src_members": [],
        "src_enumValues": [],
    }
    for a in attrs:
        if a.startswith("AddComponentMenu"):
            rec["src_attributes"]["addComponentMenu"] = attr_arg(a)
        elif a.startswith("CreateAssetMenu"):
            rec["src_attributes"]["createAssetMenu"] = attr_arg(a, quoted=False)
        elif a.startswith("RequireComponent"):
            rec["src_attributes"].setdefault("requireComponent", []).append(attr_arg(a, quoted=False))
        elif a.startswith("ExecuteAlways") or a.startswith("ExecuteInEditMode"):
            rec["src_attributes"]["executeAlways"] = True
        elif a.startswith("DisallowMultipleComponent"):
            rec["src_attributes"]["disallowMultiple"] = True
        elif a.startswith("InitializeOnLoad"):
            rec["src_attributes"]["initializeOnLoad"] = True
        elif a.startswith("MenuItem"):
            mp = menu_path(a)
            if mp:
                rec["src_attributes"].setdefault("menuItem", []).append(mp)
    return rec


def _member_line(open_type, stripped, line, pending_attrs, pending_comment):
    attrs = parse_attr_list(pending_attrs)
    has_sf = any(a.startswith("SerializeField") for a in attrs)

    # [MenuItem] decorates the static method that opens a window, NEVER the class - so reading only
    # class-level attributes silently lost all 69 menu paths in the project. For an editor tool the menu
    # path is the single most useful machine-derived fact there is ("where do I find this?"), so it is
    # hoisted onto the owning type here. A class may register several, hence a list.
    for a in attrs:
        if a.startswith("MenuItem"):
            path = menu_path(a)
            if path:
                paths = open_type["src_attributes"].setdefault("menuItem", [])
                if path not in paths:
                    paths.append(path)
    tooltip = next((attr_arg(a) for a in attrs if a.startswith("Tooltip")), None)
    header = next((attr_arg(a) for a in attrs if a.startswith("Header")), None)
    rng = next((attr_arg(a, quoted=False) for a in attrs if a.startswith("Range")), None)
    comment = " ".join(pending_comment).strip() or None

    if open_type["src_typeKind"] == "enum":
        # Enums here often pack several values onto one line
        # (`Linear, InQuad, OutQuad,`). Matching a whole line as a single value kept
        # only the last one - EaseType reported 1 of its 13 values.
        for part in stripped.split(","):
            part = part.strip()
            if not part or part in ("{", "}"):
                continue
            ev = re.match(r"^([A-Za-z_]\w*)\s*(?:=\s*.+)?$", part)
            if ev:
                open_type["src_enumValues"].append(ev.group(1))
        return

    fm = FIELD_RE.match(line)
    if has_sf and fm:
        open_type["src_serialized"].append({
            "name": fm.group("name"),
            "type": fm.group("type").strip(),
            "default": (fm.group("default") or "").strip() or None,
            "tooltip": tooltip,
            "header": header,
            "range": rng,
            "order": len(open_type["src_serialized"]),
            "comment": comment,
        })
        return

    # Interface members carry NO access modifier in C# (`void Enter();`), so requiring
    # public/protected here silently produced zero members for every interface - exactly
    # the list a contract entry exists to document.
    is_interface = open_type["src_typeKind"] == "interface"
    if not is_interface and not re.match(r"^\s*(public|protected)\b", line):
        return
    if is_interface and (stripped.startswith("{") or stripped.startswith("}")):
        return

    sig = stripped.rstrip("{").strip().rstrip(";").strip()
    if sig.endswith("=>"):
        sig = sig[:-2].strip()

    mname, kind, generic = None, "field", ""
    head = stripped.split("=>")[0]
    # A GENERIC method puts <T> between the name and the paren
    # (`WeightedPick<T>(...)`, `IsInState<T>()`). Requiring name-then-paren missed all of
    # them, and worse, `IsInState<T>() where T : IState => ...` then fell through to the
    # property branch and recorded the member as "IState".
    mm = re.search(r"([A-Za-z_]\w*)\s*(<[^<>()]*>)?\s*\(", head)
    if mm:
        mname, kind = mm.group(1), "method"
        generic = mm.group(2) or ""
    else:
        pm = re.search(r"([A-Za-z_]\w*)\s*(?:\{|=>)", stripped)
        if pm:
            mname, kind = pm.group(1), "property"
        else:
            fm2 = re.search(r"([A-Za-z_]\w*)\s*(?:=|;)", stripped)
            if fm2:
                mname = fm2.group(1)
            else:
                # A property whose opening brace is on the NEXT line, e.g.
                #     public static T Instance
                #     {
                # has no paren, brace, arrow or semicolon to key on - which is why the
                # entire public surface of Singleton<T> came back empty.
                tail = re.search(r"([A-Za-z_]\w*)\s*$", stripped)
                if tail:
                    mname, kind = tail.group(1), "property"

    if "event " in stripped:
        kind = "event"
    if " const " in stripped:
        kind = "const"

    bare_type = open_type["name"].split("<")[0]
    # A constructor IS the public API for a plain C# class (`new CountdownTimer(3f)`),
    # so record it rather than skipping it as noise.
    if mname == bare_type and kind == "method":
        kind = "ctor"
    if not mname or mname in UNITY_MSG:
        return
    if kind != "ctor" and mname == bare_type:
        return

    member_id = mname
    if kind in ("method", "ctor"):
        inner = balanced_args(head)
        arg_types = []
        for part in split_top_level(inner):
            tok = part.strip().split("=")[0].strip().split()
            if len(tok) >= 2:
                arg_types.append(" ".join(tok[:-1]))
            elif tok:
                arg_types.append(tok[0])
        member_id = f"{mname}{generic}({', '.join(arg_types)})"

    open_type["src_members"].append({
        "id": member_id,
        "sig": sig,
        "kind": kind,
        "name": mname,
        "static": " static " in f" {stripped} ",
        "comment": comment,
    })

def map_demo_scenes(toolkit_root):
    """Return {TypeName: [scene name, ...]} for every toolkit type a demo scene actually uses.

    Two independent sources, because neither alone is complete:
      1. The saved .unity file. Every MonoBehaviour placed in a scene stores `m_Script: {... guid: X}`,
         and X resolves through the .cs.meta files to exactly one source file. This is ground truth for
         components, and the only way to catch a tool wired up by the scene builder rather than named
         in demo code.
      2. The demo runner .cs files sitting beside the scene. Static classes (ExtensionMethods, MathUtils)
         and plain C# types (StateMachine, SeededRandom) are never scene components, so source (1) can
         never see them - but the runner that exercises them names them in code.
    """
    demos_root = os.path.join(toolkit_root, "_Demos")
    if not os.path.isdir(demos_root):
        return {}

    # guid -> source file stem, built from every .cs.meta under Assets/ (not just Toolkit/, so a demo
    # referencing something outside the toolkit still resolves instead of silently dropping out).
    assets_root = os.path.dirname(os.path.dirname(toolkit_root))
    guid_to_stem = {}
    guid_re = re.compile(r"guid:\s*([0-9a-f]{32})")
    for root, _dirs, files in os.walk(assets_root):
        for f in files:
            if not f.endswith(".cs.meta"):
                continue
            try:
                text = open(os.path.join(root, f), encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            m = guid_re.search(text)
            if m:
                guid_to_stem[m.group(1)] = f[: -len(".cs.meta")]

    # Known toolkit type names, so the source scan below matches real identifiers, not arbitrary words.
    known = set()
    for root, dirs, files in os.walk(toolkit_root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIR_PARTS]
        for f in files:
            if f.endswith(".cs") and not f.endswith("SelfTest.cs"):
                known.add(f[:-3])

    out = {}

    def add(type_name, scene):
        if type_name in known:
            out.setdefault(type_name, set()).add(scene)

    line_comment = re.compile(r"//[^\n]*")
    block_comment = re.compile(r"/\*.*?\*/", re.S)
    ident_re = re.compile(r"\b([A-Z][A-Za-z0-9_]{2,})\b")

    for root, _dirs, files in os.walk(demos_root):
        scenes_here = [f[: -len(".unity")] for f in sorted(files) if f.endswith(".unity")]
        if not scenes_here:
            continue

        for scene in scenes_here:
            try:
                text = open(os.path.join(root, scene + ".unity"), encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            for g in set(guid_re.findall(text)):
                stem = guid_to_stem.get(g)
                if stem:
                    add(stem, scene)

        # Source (2): runner scripts living in the same folder as the scene(s).
        for sib in sorted(files):
            if not sib.endswith(".cs"):
                continue
            try:
                code = open(os.path.join(root, sib), encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            # Strip comments so a type merely MENTIONED in prose is not counted as a usage.
            code = block_comment.sub("", line_comment.sub("", code))
            idents = set(ident_re.findall(code))
            for scene in scenes_here:
                for ident in idents:
                    add(ident, scene)

    return {k: sorted(v) for k, v in out.items()}


def find_selftest(toolkit_root, type_name):
    for root, _dirs, files in os.walk(toolkit_root):
        for f in files:
            if f == f"{type_name}SelfTest.cs":
                return os.path.relpath(os.path.join(root, f), toolkit_root).replace("\\", "/")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--toolkit", default=DEFAULT_TOOLKIT)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()

    toolkit = os.path.abspath(args.toolkit)
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)

    selftests = {}
    for root, _dirs, files in os.walk(toolkit):
        for f in files:
            if f.endswith("SelfTest.cs"):
                selftests[f[: -len("SelfTest.cs")]] = os.path.relpath(
                    os.path.join(root, f), toolkit
                ).replace("\\", "/")

    demo_scenes = map_demo_scenes(toolkit)

    all_types = []
    file_count = 0
    for root, dirs, files in os.walk(toolkit):
        dirs[:] = [d for d in dirs if d not in SKIP_DIR_PARTS]
        for f in sorted(files):
            if not f.endswith(".cs") or f.endswith("SelfTest.cs") or f in SKIP_FILES:
                continue
            abs_path = os.path.join(root, f)
            rel = os.path.relpath(abs_path, toolkit)
            file_count += 1
            try:
                for t in parse_file(abs_path, rel, toolkit):
                    bare = t["name"].split("<")[0]
                    t["src_selfTest"] = selftests.get(bare)
                    t["src_demoScene"] = demo_scenes.get(bare, [])
                    all_types.append(t)
            except Exception as exc:  # noqa: BLE001
                print(f"  ERROR parsing {rel}: {exc}", file=sys.stderr)

    # Disambiguate duplicate slugs deterministically (category-prefixed).
    seen = {}
    for t in all_types:
        if t["id"] in seen:
            t["id"] = f"{slugify(t['cat'])}-{t['id']}"
        seen[t["id"]] = True

    for t in all_types:
        with open(os.path.join(out_dir, f"{t['id']}.json"), "w", encoding="utf-8") as fh:
            json.dump(t, fh, indent=2, ensure_ascii=False)
            fh.write("\n")

    print(f"extract: {file_count} files -> {len(all_types)} public types -> {out_dir}")

    if args.stats:
        from collections import Counter
        print("\nby kind:   ", dict(Counter(t["src_typeKind"] for t in all_types)))
        print("by runtime:", dict(Counter("runtime" if t["src_runtime"] else "editor" for t in all_types)))
        print("serialized fields:", sum(len(t["src_serialized"]) for t in all_types))
        print("  with tooltip:   ", sum(1 for t in all_types for s in t["src_serialized"] if s["tooltip"]))
        print("  with range:     ", sum(1 for t in all_types for s in t["src_serialized"] if s["range"]))
        print("public members:   ", sum(len(t["src_members"]) for t in all_types))
        print("types w/ selftest:", sum(1 for t in all_types if t["src_selfTest"]))
        print("types w/ demo scene:", sum(1 for t in all_types if t["src_demoScene"]))
        print("types w/ comment: ", sum(1 for t in all_types if t["src_typeComment"]))
        top = Counter(t["cat"] for t in all_types).most_common(8)
        print("top categories:   ", top)


if __name__ == "__main__":
    main()
