#!/usr/bin/env python3
"""
Stages 1+4 of the field-manual pipeline: cross-reference, merge, and emit the site.

Reads   data/_skeleton/*.json   (machine-extracted, from extract.py)
        data/_authored/*.json   (prose written by a person/agent, optional per entry)
Writes  assets/catalog.js       global lightweight index - powers hub + nav + search
        data/<cat>.js           full per-category detail, loaded only by that page
        c/<cat>.html            one generated shell per category
        index.html              the hub

Authored files are merged ON TOP of the skeleton, but may only contribute non-`src_`
keys. Anything `src_`-prefixed always comes from the extractor, so a writer cannot
"correct" a signature into something that does not exist.

Usage:  python tools/build.py
"""

import json
import os
import re
import glob
import collections

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

TOOLKIT = TOOLKIT_PATH

# Opaque implementation detail behind QrCodeGenerator - a user never touches these to
# generate or customise a QR code, so they are deliberately not documented. Every other
# internal/interface/data type IS documented, because implementing, passing, reading from
# or editing it is part of using its tool.
OMIT = {
    "qr-galois-field": "internal QR maths behind QrCodeGenerator",
    "qr-format-info": "internal QR spec table behind QrCodeGenerator",
    "qr-version-info": "internal QR spec table behind QrCodeGenerator",
    "qr-matrix-builder": "internal QR bitmap assembly behind QrCodeGenerator",
    "qr-data-encoder": "internal QR byte encoding behind QrCodeGenerator",
}

CATS = [
    ("Core",                 "Core Utilities",        "Pooling, state machines, timers, singletons - the primitives everything else builds on."),
    ("Math",                 "Math & Formulas",       "Pure functions: easing, splines, noise, probability, stat curves."),
    ("Formatting",           "Formatting",            "Human-readable numbers, times and strings."),
    ("Color",                "Colour",                "Palette derivation and colour maths."),
    ("Tweening",             "Tweening & Fades",      "Lightweight tweens, punch effects and screen fades - no DOTween."),
    ("UI",                   "UI Toolkit",            "The largest group: layouts, navigation, scrolling, drag/drop, polish."),
    ("TMPEffects",           "TMP Effects",           "Per-character TextMeshPro effects: wave, typewriter, highlight, links."),
    ("Camera",               "Camera",                "Follow rigs, shake, cursor pan, first/third person."),
    ("Controller",           "Character Control",     "Kinematic movement, ledge climbing, locomotion state."),
    ("Raycasting",           "Raycasting",            "Click-to-select, ground snapping, look-at, debug visualisation."),
    ("Grid",                 "Grid",                  "Generic grid container, placement and A* pathfinding."),
    ("AI",                   "AI & Pathfinding",      "Vision, steering, flow fields, Dijkstra, blackboard, visual state machine."),
    ("Combat",               "Combat",                "Hitboxes, hurtboxes, melee/ranged attacks, turn-based scaffolding."),
    ("Gameplay",             "Gameplay",              "Health, currency, abilities, checkpoints, triggers, watering."),
    ("Mechanics",            "Mechanics",             "Interaction, wave spawning, patrols, tower defence."),
    ("Inventory",            "Inventory",             "Items, containers, equipment and the item registry."),
    ("Dialogue",             "Dialogue",              "Branching dialogue nodes and the runner that walks them."),
    ("ProceduralGeneration", "Procedural Generation", "Mazes, caves, terrain heightmaps and name generation."),
    ("PathBuilder",          "Path Builder",          "Catmull-Rom splines, path following and road/fence generation."),
    ("PageCurl",             "Page Curl",             "Mesh-based page turning for both UI and 3D surfaces."),
    ("Rendering",            "Rendering & Shaders",   "Water, decals, dissolve, fake lights, UI shader effects, post-processing."),
    ("Kiosk",                "Kiosk",                 "Attract loops, session timeouts, virtual keyboard, QR generation."),
    ("Persistence",          "Persistence",           "Save system, JSON helpers and ScriptableObject-backed data."),
    ("ScriptableObjects",    "ScriptableObject Kit",  "Variables, events and runtime sets as assets."),
    ("Optimization",         "Optimization",          "Update manager, frame budgeting and distance culling."),
    ("Settings",             "Settings",              "Volume, quality and fullscreen persistence."),
    ("InputManagers",        "Input Managers",        "Legacy Input and the new Input System behind one wrapper."),
    ("Input",                "Touch Input",           "Touch gesture recognition."),
    ("Data",                 "Data / CSV",            "CSV parsing and conversion utilities."),
    ("BackgroundMatte",      "Background Matte",      "Editor-time texture background removal."),
    ("EditorTools",          "Editor Tools",          "Windows and wizards: level guidance, renamers, CSV tables, session recall."),
]
CAT_ORDER = {name: i for i, (name, _t, _b) in enumerate(CATS)}
CAT_META = {name: (title, blurb) for name, title, blurb in CATS}

# ---------------------------------------------------------------------------------------------
# SUB-GROUPS. A category page with 50 collapsed rows is a wall; these break each one into
# labelled sections (Math > Randomness & Probability > SeededRandom).
#
# The toolkit's own folder layout already encodes most of this (Math/Probability/SeededRandom.cs)
# and is used as the FALLBACK, but it is not enough on its own for two reasons: it is too granular
# in places (UI has 28 folders for 50 types, Math 8 for 12), and the useful grouping sometimes
# crosses folders - "Lighting" wants FakeLight from Rendering/FakeLight/ together with UIFakeLight
# and SceneLightUIHook from Rendering/UI/. So the map below is curated, by TYPE NAME.
#
# Anything not named here falls into a trailing "Other" group AND is reported by verify.py, so
# adding a tool without placing it is visible rather than silent. Categories absent from this map
# (everything under ~6 entries) render as one ungrouped list, which is the right call at that size.
GROUP_ORDER = {
    "Math": [
        ("Randomness & Probability", ["ProbabilityUtils", "SeededRandom", "RandomBag<T>", "PityTimer"]),
        ("Curves & Easing", ["EasingFunctions", "EaseType", "StatCurves"]),
        ("Splines & Geometry", ["BezierUtils", "SplineUtils", "GeometryUtils2D"]),
        ("Scalars & Noise", ["MathUtils", "NoiseUtils"]),
    ],
    "Core": [
        ("Lifetime & Instances", ["Singleton<T>", "ObjectPool", "PoolableObject"]),
        ("State", ["StateMachine", "IState"]),
        ("Time & Coroutines", ["CountdownTimer", "WaitForSecondsCache"]),
        ("Extensions", ["ExtensionMethods"]),
    ],
    "Rendering": [
        ("Lighting", ["FakeLight", "FakeLightType", "FakeLightInfluenceMode", "UIFakeLight",
                      "UIFakeLightType", "SceneLightUIHook", "UILightingEffect", "UILightingMode"]),
        ("Water", ["RippleEmitter", "PlanarReflection", "WaterDepthTextureEnabler",
                   "WaterInstanceOverrides", "WaterPourVisual"]),
        ("UI Effects", ["UIEdgeGlow", "EdgeGlowPattern", "EdgeGlowShape", "UIBlurSource",
                        "UIRippleEffect"]),
        ("Object Effects", ["DissolveEffect", "ForceFieldEffect", "GlitchEffect", "HologramEffect",
                            "HighlightToggle", "WeaponTrail"]),
        ("Decals & Surfaces", ["DecalProjector", "DecalMeshBuilder", "DecalVertex", "GrassBendEmitter"]),
        ("Post-processing & Plumbing", ["SpeedRadialBlur", "MaterialPropertyBlockHelper"]),
    ],
    "UI": [
        ("Layout & Fitting", ["AccordionPanel", "CardCarousel", "MasonryLayoutGroup", "RadialLayoutGroup",
                              "AspectRatioImageFitter", "TextAutoShrinkFitter", "ImageFitMode",
                              "DeferredLayoutRebuilder", "LayoutRebuildHelper", "SafeAreaHandler"]),
        ("Scrolling", ["FancyScrollView", "FancyScrollViewCurve", "ScrollSnap", "PullToRefresh",
                       "RefreshIndicatorAnchor", "TimedLineScrollView", "TimedLine"]),
        ("Navigation", ["ScreenStackNavigator", "TabGroup", "TabEntry", "PaginationDots",
                        "AsyncSceneLoader"]),
        ("Input & Controls", ["DraggableItem", "DropZone", "DraggableUIPanel", "HoldToConfirmButton",
                              "ToggleSwitch"]),
        ("Feedback & Overlays", ["ToastNotification", "ModalDialog", "TooltipSystem", "TooltipTrigger",
                                 "LoadingBarUI", "RadialProgressBar"]),
        ("Motion & Effects", ["FlyToUIEffect", "AnimatedNumberCounter", "NumberDisplayFormat",
                              "MarqueeText", "WorldSpaceUIFollow"]),
        ("Floating Combat Text", ["FloatingCombatText", "FloatingCombatTextSpawner", "CombatTextMotion",
                                  "CombatTextPreset", "CombatTextStyle"]),
        ("Device & Platform", ["InputGlyphSwapper", "InputDeviceMonitor", "InputDeviceMonitorRunner",
                               "InputDeviceKind"]),
        ("Undo / Redo", ["CommandHistory", "ActionCommand", "ICommand"]),
    ],
    "EditorTools": [
        ("Level Transform Guidance", ["LevelTransformGuidanceWindow", "ILtgTab", "LtgClippingTab",
                                      "LtgHeatmapTab", "LtgMeasuringTapeTab", "LtgSnapToMouseTab",
                                      "LtgGuiUtility", "ClippingCandidate", "ClippingCandidateFinder",
                                      "ClippingDisplayMode", "ClippingHighlightRenderer",
                                      "ClippingHighlightStyle", "FixedPlaneAxis", "HeatmapCell",
                                      "HeatmapGrid", "HeatmapMetric", "MeasurementMode",
                                      "MeasurementUnit", "MeasurementUnits", "TapeLabelStyle"]),
        ("Inspector Attributes", ["ButtonAttribute", "ButtonAttributeEditor", "LayerAttribute",
                                  "LayerDrawer", "ReadOnlyAttribute", "ReadOnlyDrawer", "TagAttribute",
                                  "TagDrawer"]),
        ("Data & Assets", ["CsvTableWindow", "CsvToScriptableObjectImporter", "ScriptableObjectTableWindow",
                           "PlayerPrefsEditorWindow", "PlayerPrefsEntry", "PlayerPrefsValueType",
                           "BatchAssetRenamerWindow"]),
        ("Scene & Project Tools", ["BulkHierarchyToolsWindow", "FindReferencesWindow", "TodoScannerWindow",
                                   "GreyboxPlacerWindow", "GreyboxBlockMarker",
                                   "LocomotionAnimatorGeneratorWindow"]),
        ("Session Recorder", ["SessionRecorderWindow", "SessionRecorderService", "SessionHistory",
                              "SessionHistoryStorage", "SessionEntry"]),
    ],
    "AI": [
        ("Pathfinding", ["NavMeshAIController", "NavMeshAIState", "DijkstraPathfinder", "GraphNode",
                         "GraphEdge", "FlowField", "FlowFieldGrid", "FlowFieldAgent"]),
        ("Visual State Machine", ["VisualStateMachineRunner", "VisualStateMachineAsset",
                                  "VisualStateMachineEditorWindow", "VSMGraphView", "VSMGraphNode",
                                  "VSMNode", "VSMTransition", "VSMCondition", "VSMComparison",
                                  "VSMParameter", "VSMParameterType"]),
        ("Perception", ["VisionCone", "PerceptionMemory", "PerceptionState", "Blackboard"]),
        ("Steering", ["SteeringAgent", "SteeringBehaviors", "SteeringMode"]),
        ("Racing", ["RacingAIController", "RaceProgressTracker"]),
    ],
    "Combat": [
        ("Hitboxes & Attacks", ["Hitbox", "Hurtbox", "HitData", "MeleeAttack", "RangedAttack",
                                "RangedAttackMode", "CombatProjectile", "IKnockbackReceiver"]),
        ("Turn-Based Combat", ["TurnManager", "TurnBasedActor", "BattleState", "ActionDefinition",
                               "ActionTargetType", "CandidateAction", "TargetingMode",
                               "TargetingStrategies", "UtilityAIBrain"]),
    ],
    "Kiosk": [
        ("QR Codes", ["QrCodeGenerator", "QrDataEncoder", "QrMatrixBuilder", "QrFormatInfo",
                      "QrGaloisField", "QrVersionInfo"]),
        ("Runtime & Resources", ["StreamingAssetLoader", "VideoPlayerLifecycleManager",
                                 "MemoryUsageWatchdog", "LruCache<TKey, TValue>"]),
        ("Session & Attract", ["IdleAttractManager", "SessionTimeoutManager"]),
        ("On-Screen Input", ["VirtualKeyboard", "KeyButton"]),
    ],
    "Mechanics": [
        ("Tower Defense", ["TDGameManager", "TDTower", "TDTowerPlacer", "TDEnemy", "TDProjectile",
                           "TDProjectileAttack", "TDHitscanAttack", "IAttackBehavior"]),
        ("Patrol & Spawning", ["WaypointPatrol", "PatrolMode", "WaveSpawner"]),
        ("Interaction", ["InteractionDetector", "IInteractable"]),
    ],
    "PageCurl": [
        ("Core", ["BookPageCurl", "PageCurlMeshBuilder", "PageContent", "ICurlSurface", "ITextureDisplay"]),
        ("Canvas (UI)", ["PageCurlGraphic", "PageCurlDragHandler", "RawImageTextureDisplay"]),
        ("World (3D)", ["PageCurlMeshRenderer", "PageCurlDragHandler3D", "MeshRendererTextureDisplay"]),
    ],
    "Gameplay": [
        ("Health & Damage", ["Health", "IDamageable", "ProjectileMover"]),
        ("Progression & Economy", ["CurrencyWallet", "CurrencyDefinition", "CooldownAbilitySlot",
                                   "CheckpointRespawn"]),
        ("World Interaction", ["TriggerEventRelay", "TriggerEventRelay2D", "WateringCanController",
                               "PlantWaterLevel"]),
    ],
    "PathBuilder": [
        ("Spline & Path", ["SplinePath", "SplineFollower", "SplineFollowMode", "PathBuilder",
                           "PathBuilderMode", "PrefabSpacingMode", "BoundsAxis"]),
        ("Editor Integration", ["PathBuilderEditor", "SplinePathEditor", "SplinePathSceneEditing"]),
    ],
    "ScriptableObjects": [
        ("Variables", ["ScriptableVariable<T>", "FloatVariable", "IntVariable", "BoolVariable"]),
        ("Events", ["GameEvent", "GameEventListener"]),
        ("Runtime Sets", ["GameObjectRuntimeSet", "RuntimeSetRegistrar"]),
    ],
    "Tweening": [
        ("Tween Core", ["Tween", "TweenRunner", "TweenHandle", "TweenExtensions", "TweenLoopMode"]),
        ("Fades & Transitions", ["FadeManager", "ShaderTransitionManager"]),
        ("Punch", ["PunchEffects"]),
    ],
    "Camera": [
        ("Player Camera Rig", ["PlayerCameraRig", "PlayerCameraMode", "CameraFollowMode",
                               "PlayerCameraRigSetup"]),
        ("Follow & Effects", ["SmoothFollowCamera", "CursorPanCamera", "CameraShake"]),
    ],
    "Raycasting": [
        ("Selection & Movement", ["ClickToSelect", "ClickToMoveNavigator", "LookAtTargetFinder"]),
        ("Placement & Debug", ["GroundSnapper", "RaycastUtils", "RaycastDebugVisualizer"]),
    ],
    "Inventory": [
        ("Items", ["ItemDefinition", "ItemRegistry"]),
        ("Containers & Equipment", ["InventoryContainer", "InventorySlot", "EquipmentManager",
                                    "EquipSlotType"]),
    ],
    "TMPEffects": [
        ("Reveal & Motion", ["TMPTypewriterEffect", "TMPWaveEffect"]),
        ("Highlighting & Links", ["TMPWordHighlighter", "TMPTimedHighlighter", "WordTiming",
                                  "TMPLinkHandler"]),
    ],
}

# Below this many entries a category renders as one flat list - a heading per one or two rows is
# noise, not structure.
GROUP_MIN_ENTRIES = 6


def assign_groups(cat, items):
    """Return [(group title, [entry, ...]), ...] for one category, in curated order.

    Falls back to the source folder when a category has no curated map, and to a trailing "Other"
    bucket for a type the map has not been updated for. Both are reported by verify.py.
    """
    if len(items) < GROUP_MIN_ENTRIES:
        return [(None, items)]

    by_name = {e["name"]: e for e in items}
    out, placed = [], set()

    for title, names in GROUP_ORDER.get(cat, []):
        members = [by_name[n] for n in names if n in by_name]
        if members:
            out.append((title, members))
            placed.update(e["name"] for e in members)

    leftover = [e for e in items if e["name"] not in placed]
    if not leftover:
        return out

    if not out:
        # No curated map for this category: group by the source folder, which the toolkit's own
        # layout already makes meaningful (Math/Probability/, Rendering/Water/).
        folders = {}
        for e in leftover:
            parts = e["src_file"].split("/")
            folder = parts[1] if len(parts) > 2 else ""
            if folder == "Editor" and len(parts) > 3:
                folder = parts[2]
            folders.setdefault(folder or "General", []).append(e)
        return [(k, v) for k, v in sorted(folders.items())]

    out.append(("Other", leftover))
    return out



def slug_cat(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def summarise(e):
    """One-line summary for collapsed rows and search results.

    Until an entry is authored, the best available summary is the FIRST SENTENCE of the
    verbatim leading `//` comment above the type - these are unusually good in this
    codebase (they explain why, not just what), which is what makes skeleton-only pages
    genuinely readable rather than a bare API dump.
    """
    if e.get("summary"):
        return e["summary"]
    c = (e.get("src_typeComment") or "").strip()
    if not c:
        return ""
    # Don't split on abbreviations - "e.g." and "i.e." are common here and splitting on
    # them truncates the summary mid-thought. The guard is SAME-LENGTH by construction
    # ("e.g." -> "eXgX"), so the split offset maps straight back onto the original text.
    guard = c.replace("e.g.", "eXgX").replace("i.e.", "iXeX")
    first = c[: len(re.split(r"(?<=[.!?])\s+", guard)[0])]
    if len(first) > 120:
        cut = first[:120].rsplit(" ", 1)[0].rstrip(" ,;:-")
        first = cut + "..."
    return first


def infer_kind(e):
    if not e["src_runtime"]:
        return "editor"
    if e["src_typeKind"] == "interface":
        return "contract"
    if e["src_typeKind"] == "enum":
        return "enum"
    if e["src_typeKind"] == "struct":
        return "data"
    base = e.get("src_base") or ""
    if base in ("MonoBehaviour", "ScriptableObject", "EditorWindow"):
        return "tool"
    # A plain class with no members worth calling is usually a data holder.
    if not e["src_members"] and e["src_serialized"]:
        return "data"
    return "tool"


def main():
    entries = []
    for p in sorted(glob.glob(os.path.join(SKEL, "*.json"))):
        e = json.load(open(p, encoding="utf-8"))
        if e["id"] in OMIT:
            continue
        e["entryKind"] = infer_kind(e)
        e["status"] = "skeleton"
        entries.append(e)

    # Give every entry a CAPPED one-line summary. Without this the renderer falls back to
    # the full leading comment, and several of those run to a full paragraph - which makes
    # a "collapsed" row taller than the expanded ones below it.
    for e in entries:
        e["summary"] = summarise(e)

    # Merge authored prose, which may never touch a src_ key.
    authored = 0
    if os.path.isdir(AUTH):
        by_id = {e["id"]: e for e in entries}
        for p in sorted(glob.glob(os.path.join(AUTH, "*.json"))):
            a = json.load(open(p, encoding="utf-8"))
            tgt = by_id.get(a.get("id"))
            if not tgt:
                print(f"  WARN authored file has no matching skeleton: {os.path.basename(p)}")
                continue
            for k, v in a.items():
                if k.startswith("src_") or k in ("id", "cat"):
                    continue
                tgt[k] = v
            tgt["status"] = "published"
            authored += 1

    # ---- cross-reference: who mentions whom, derived from source, not authored ----
    names = {e["name"].split("<")[0]: e["id"] for e in entries}
    id_to_entry = {e["id"]: e for e in entries}
    src_cache = {}
    for e in entries:
        ap = os.path.join(TOOLKIT, e["src_file"])
        if ap not in src_cache:
            try:
                src_cache[ap] = open(ap, encoding="utf-8-sig", errors="replace").read()
            except OSError:
                src_cache[ap] = ""
        txt = src_cache[ap]
        uses = set()
        for n, tid in names.items():
            if tid == e["id"]:
                continue
            if re.search(r"\b" + re.escape(n) + r"\b", txt):
                uses.add(tid)
        e["_uses"] = sorted(uses)

    used_by = collections.defaultdict(set)
    for e in entries:
        for t in e["_uses"]:
            used_by[t].add(e["id"])

    for e in entries:
        e["src_uses"] = [id_to_entry[i]["name"] for i in e["_uses"] if i in id_to_entry]
        e["src_usedBy"] = sorted(id_to_entry[i]["name"] for i in used_by.get(e["id"], ()))
        e["src_extensionPoints"] = [
            id_to_entry[i]["name"] for i in e["_uses"]
            if i in id_to_entry and id_to_entry[i]["entryKind"] == "contract"
        ]
        del e["_uses"]
        # Keep the chip strips readable.
        e["src_usedBy"] = e["src_usedBy"][:8]
        e["src_uses"] = e["src_uses"][:8]

    # ---- group ----
    groups = collections.defaultdict(list)
    for e in entries:
        groups[e["cat"]].append(e)

    cats_out = []
    os.makedirs(os.path.join(REPO, "data"), exist_ok=True)
    os.makedirs(os.path.join(REPO, "c"), exist_ok=True)

    known = [c for c in CATS if c[0] in groups]
    extra = sorted(g for g in groups if g not in CAT_META)
    ordered = [c[0] for c in known] + extra

    for i, cat in enumerate(ordered, start=1):
        items = sorted(groups[cat], key=lambda x: (x["entryKind"] != "tool", x["name"].lower()))
        title, blurb = CAT_META.get(cat, (cat, ""))
        cid = slug_cat(cat)
        code = f"{i:02d}"
        written = sum(1 for x in items if x["status"] == "published")
        cats_out.append({"id": cid, "code": code, "title": title, "blurb": blurb,
                         "count": len(items), "written": written})

        grouped = assign_groups(cat, items)
        # Entries stay a FLAT list (deep links, search and "expand all" all index into it); the
        # groups carry ids only, so the renderer can section the same list without duplicating it.
        item_groups = [{"title": g, "ids": [e["id"] for e in members]}
                       for g, members in grouped if g]
        # Re-order the flat list to match the grouped order, so an ungrouped render still reads right.
        if item_groups:
            items = [e for _g, members in grouped for e in members]
        payload = {"id": cid, "title": title, "entries": items, "groups": item_groups}
        with open(os.path.join(REPO, "data", f"{cid}.js"), "w", encoding="utf-8") as fh:
            fh.write("window.FM_DATA = window.FM_DATA || {};\n")
            fh.write(f"window.FM_DATA[{json.dumps(cid)}] = ")
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
            fh.write(";\n")

        with open(os.path.join(REPO, "c", f"{cid}.html"), "w", encoding="utf-8") as fh:
            fh.write(category_shell(cid, code, title, blurb))

    index = {
        "categories": cats_out,
        # Deliberately lean: this file is loaded by EVERY page, so it carries only what
        # the hub grid, the sidebar nav and global search need. Member-level search lives
        # in the per-category file, where the full detail is already loaded anyway.
        "entries": [{
            "id": e["id"], "cat": slug_cat(e["cat"]), "name": e["name"],
            "kind": e["entryKind"],
            "summary": summarise(e),
            "search": (e["name"] + " " + e["src_ns"] + " " + summarise(e)).lower(),
        } for e in entries],
    }
    with open(os.path.join(REPO, "assets", "catalog.js"), "w", encoding="utf-8") as fh:
        fh.write("window.FM_INDEX = ")
        json.dump(index, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write(";\n")

    with open(os.path.join(REPO, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(hub_shell(len(entries), len(cats_out)))

    print(f"build: {len(entries)} entries / {len(cats_out)} categories "
          f"({authored} authored) -> c/*.html, data/*.js, assets/catalog.js, index.html")


HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<meta name="description" content="{desc}">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' font-size='90'%3E%3Ctext y='.9em'%3E%F0%9F%A7%B0%3C/text%3E%3C/svg%3E">
<link rel="stylesheet" href="{root}assets/field-manual.css">
<script>{theme}</script>
</head>
"""

THEME_INLINE = ('(function(){try{var t=localStorage.getItem("fm-theme");'
                'if(t==="light"||t==="dark")document.documentElement.setAttribute("data-theme",t);}catch(e){}})();')


def topbar(root, crumb_html, extra=""):
    return f"""<div class="topbar">
  <button class="iconbtn hamburger" id="burger" type="button" aria-label="Menu">menu</button>
  <span class="crumb">{crumb_html}</span>
  <span class="spacer"></span>
  {extra}
  <button class="iconbtn" id="themeBtn" type="button">theme: auto</button>
</div>
<div class="scrim" id="scrim"></div>"""


def sidebar(root, sub):
    return f"""  <aside class="sidebar">
    <div class="brand">
      <p class="kicker">UnityTools &middot; Reference</p>
      <h1>Field&nbsp;Manual</h1>
      <p class="sub">{sub}</p>
    </div>
    <div class="filterbox">
      <input id="filter" type="text" placeholder="Search all tools&hellip;  (press /)" autocomplete="off" />
      <div class="filter-status" id="filterStatus"></div>
    </div>
    <nav class="catnav" id="catnav"></nav>
  </aside>"""


def hub_shell(total, ncats):
    sub = ('<span class="count" id="totalCount">0</span> entries across '
           '<span class="count" id="catCount">0</span> categories &middot; '
           '<span class="count" id="writtenCount">0</span> fully written.')
    return (HEAD.format(title="Toolkit Field Manual", root="", theme=THEME_INLINE,
                        desc="Reference for every drop-in tool in the UnityTools Toolkit - "
                             "parameters, worked examples, gotchas and usage snippets.") +
            f"""<body data-page="hub" data-root="">
{topbar("", "<strong>Field Manual</strong>")}
<div class="app">
{sidebar("", sub)}
  <main id="content">
    <div class="intro">
      <p>Reference for the plug-and-play tools in <code class="mono">Assets/UnityTools/Toolkit</code> &mdash;
      a personal, zero-dependency Unity library (2022.3, Built-in Render Pipeline). Every entry gives the
      exact namespace and file, what problem it solves, its inspector parameters, a worked example,
      how to wire it up, and the gotchas worth knowing before you hit them.</p>
      <div class="legend">
        <span class="swatch"><span class="dot" style="background:var(--mono-accent)"></span> MonoBehaviour</span>
        <span class="swatch"><span class="dot" style="background:var(--accent)"></span> ScriptableObject</span>
        <span class="swatch"><span class="dot" style="background:var(--ink-faint)"></span> Static / Plain C#</span>
      </div>
    </div>
    <div id="searchResults" class="searchresults"></div>
    <div id="cats"></div>
    <footer class="pagefoot">UnityTools Toolkit &middot; Unity 2022.3.62f3, Built-in Render Pipeline &middot; zero external dependencies.</footer>
  </main>
</div>
<script src="assets/catalog.js"></script>
<script src="assets/manual.js"></script>
</body>
</html>
""")


def category_shell(cid, code, title, blurb):
    sub = ('<span class="count" id="totalCount">0</span> entries across '
           '<span class="count" id="catCount">0</span> categories.')
    crumb = f'<a href="../index.html">Field Manual</a> / {code} {title}'
    extra = ('<button class="iconbtn" id="expandAll" type="button">expand all</button>'
             '<button class="iconbtn" id="collapseAll" type="button">collapse all</button>')
    return (HEAD.format(title=f"{title} &middot; Field Manual", root="../", theme=THEME_INLINE,
                        desc=blurb or f"{title} tools in the UnityTools Toolkit.") +
            f"""<body data-page="category" data-cat="{cid}" data-root="../">
{topbar("../", crumb, extra)}
<div class="app">
{sidebar("../", sub)}
  <main id="content">
    <div class="cathead">
      <span class="catcode">{code}</span>
      <h2>{title}</h2>
    </div>
    <div class="intro"><p>{blurb}</p></div>
    <div id="entries"></div>
    <div class="empty-state" id="emptyState" hidden>Nothing here matches that search.</div>
    <footer class="pagefoot"><a href="../index.html">&larr; All categories</a></footer>
  </main>
</div>
<script src="../assets/catalog.js"></script>
<script src="../data/{cid}.js"></script>
<script src="../assets/manual.js"></script>
</body>
</html>
""")


if __name__ == "__main__":
    main()
