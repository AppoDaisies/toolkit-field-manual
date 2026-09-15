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
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# Reuse the extractor's slug rule rather than re-deriving it: these slugs are permanent URLs, and
# a second implementation would drift (its trailing-digit handling is the whole reason it exists).
from extract import slugify  # noqa: E402

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
    ("EditorTools",          "Editor Tools",          "Windows and wizards: level guidance, renamers, CSV tables, task recall."),
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
        ("Curves & Easing", ["EasingFunctions", "StatCurves"]),
        ("Splines & Geometry", ["BezierUtils", "SplineUtils", "GeometryUtils2D"]),
        ("Scalars & Noise", ["MathUtils", "NoiseUtils"]),
    ],
    "Core": [
        ("Lifetime & Instances", ["Singleton<T>", "ObjectPool"]),
        ("State", ["StateMachine"]),
        ("Time & Coroutines", ["CountdownTimer", "WaitForSecondsCache"]),
        ("Extensions", ["ExtensionMethods"]),
    ],
    "Rendering": [
        ("Lighting", ["FakeLight", "UIFakeLight", "SceneLightUIHook", "UILightingEffect"]),
        ("Water", ["RippleEmitter", "PlanarReflection", "WaterDepthTextureEnabler", "WaterInstanceOverrides",
            "WaterPourVisual"]),
        ("UI Effects", ["UIEdgeGlow", "UIBlurSource", "UIRippleEffect"]),
        ("Object Effects", ["DissolveEffect", "ForceFieldEffect", "GlitchEffect", "HologramEffect", "HighlightToggle",
            "WeaponTrail"]),
        ("Decals & Surfaces", ["Decals", "GrassBendEmitter"]),
        ("Post-processing & Plumbing", ["SpeedRadialBlur", "MaterialPropertyBlockHelper"]),
    ],
    "UI": [
        ("Layout & Fitting", ["AccordionPanel", "CardCarousel", "MasonryLayoutGroup", "RadialLayoutGroup",
            "AspectRatioImageFitter", "TextAutoShrinkFitter", "DeferredLayoutRebuilder",
            "LayoutRebuildHelper", "SafeAreaHandler"]),
        ("Scrolling", ["FancyScrollView", "ScrollSnap", "PullToRefresh", "TimedLineScrollView"]),
        ("Navigation", ["ScreenStackNavigator", "TabGroup", "PaginationDots", "AsyncSceneLoader"]),
        ("Input & Controls", ["DragAndDrop", "DraggableUIPanel", "HoldToConfirmButton", "ToggleSwitch"]),
        ("Feedback & Overlays", ["ToastNotification", "ModalDialog", "Tooltips", "LoadingBarUI", "RadialProgressBar"]),
        ("Motion & Effects", ["FlyToUIEffect", "FloatingCombatText", "AnimatedNumberCounter", "MarqueeText",
            "WorldSpaceUIFollow"]),
        ("Device & Platform", ["InputGlyphs"]),
        ("Undo / Redo", ["UndoRedo"]),
    ],
    "EditorTools": [
        ("Level & Scene Tools", ["LevelTransformGuidance", "GreyboxPlacer", "BulkHierarchyTools", "LocomotionAnimatorGenerator"]),
        ("Project & Assets", ["BatchAssetRenamer", "FindReferences", "TodoScanner", "CsvTableEditor",
            "CsvToScriptableObjectImporter", "ScriptableObjectTableView", "PlayerPrefsEditor"]),
        ("Inspector Attributes", ["ButtonAttribute", "LayerAttribute", "ReadOnlyAttribute", "TagAttribute"]),
        ("Workflow", ["TaskRecaller"]),
    ],
    "AI": [
        ("Pathfinding", ["NavMeshAIController", "DijkstraPathfinder", "GraphNode", "FlowField"]),
        ("Behaviour", ["VisualStateMachine", "Steering", "Blackboard"]),
        ("Perception", ["Perception"]),
        ("Racing", ["RacingAI"]),
    ],
    "Kiosk": [
        ("QR Codes", ["QrCodeGenerator"]),
        ("Runtime & Resources", ["StreamingAssetLoader", "VideoPlayerLifecycleManager", "MemoryUsageWatchdog",
            "LruCache<TKey, TValue>"]),
        ("Session & Attract", ["IdleAttractManager", "SessionTimeoutManager"]),
        ("On-Screen Input", ["VirtualKeyboard", "KeyButton"]),
    ],
    "Combat": [
        ("Hitboxes & Attacks", ["HitboxCombat"]),
        ("Turn-Based Combat", ["TurnManager", "TurnBasedActor", "ActionDefinition", "TargetingStrategies", "UtilityAIBrain"]),
    ],
    "Gameplay": [
        ("Health & Damage", ["Health", "ProjectileMover"]),
        ("Progression & Economy", ["Currency", "CooldownAbilitySlot", "CheckpointRespawn"]),
        ("World Interaction", ["TriggerEventRelay", "Watering"]),
    ],
    "Raycasting": [
        ("Selection & Movement", ["ClickToSelect", "ClickToMoveNavigator", "LookAtTargetFinder"]),
        ("Placement & Debug", ["GroundSnapper", "RaycastUtils", "RaycastDebugVisualizer"]),
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
        return [(None, items, None)]

    by_name = {e["name"]: e for e in items}
    out, placed = [], set()

    for title, spec in GROUP_ORDER.get(cat, []):
        # A group's members are either a flat list of type names, or - for a system big enough that
        # a flat list stops being readable - a list of (subtitle, names) pairs.
        if spec and isinstance(spec[0], tuple):
            subs, members = [], []
            for sub_title, names in spec:
                sub_members = [by_name[n] for n in names if n in by_name]
                if sub_members:
                    subs.append((sub_title, sub_members))
                    members.extend(sub_members)
            if members:
                out.append((title, members, subs))
                placed.update(e["name"] for e in members)
            continue
        members = [by_name[n] for n in spec if n in by_name]
        if members:
            out.append((title, members, None))
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
        return [(k, v, None) for k, v in sorted(folders.items())]

    out.append(("Other", leftover, None))
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


# ---------------------------------------------------------------------------------------------
# SYSTEMS. Some tools are one thing you use but many scripts you read. Level Transform Guidance is
# a single editor window with four tabs across 11 files - listing those as 11 sibling rows told the
# reader "here are 11 tools", which is wrong. A system collapses to ONE entry named after the TOOL
# (LevelTransformGuidance, not LevelTransformGuidanceWindow), and its dropdown holds the parts,
# sorted into the sections a user actually thinks in: Heatmap, Clipping, Measuring tape.
#
#   name      the entry's display name - the tool's name, not its main class's name
#   cat       which category the single resulting entry lives in
#   primary   the type whose own documentation becomes the entry's body (the window itself)
#   group     which GROUP_ORDER group the composite entry should sit in
#   sections  ordered (section title, [member type names]) - every remaining member must appear
SYSTEMS = [
    {
        "name": "TaskRecaller",
        "cat": "EditorTools",
        "primary": "TaskRecallerWindow",
        "sections": [
            ("Supporting scripts", ["TaskRecallerService", "SessionHistoryStorage"]),
        ],
    },
    {
        "name": "LevelTransformGuidance",
        "cat": "EditorTools",
        "primary": "LevelTransformGuidanceWindow",
        "sections": [
            ("Window & tab framework", ["ILtgTab", "LtgGuiUtility"]),
            ("Heatmap", ["LtgHeatmapTab", "HeatmapGrid"]),
            ("Clipping", ["LtgClippingTab", "ClippingCandidateFinder", "ClippingHighlightRenderer"]),
            ("Measuring tape", ["LtgMeasuringTapeTab", "MeasurementUnits"]),
            ("Snap to mouse", ["LtgSnapToMouseTab"]),
        ],
    },
    {
        "name": "VisualStateMachine",
        "cat": "AI",
        "primary": "VisualStateMachineRunner",
        "sections": [
            ("Graph asset", ["VisualStateMachineAsset"]),
            ("Graph data", ["VSMNode", "VSMTransition", "VSMCondition", "VSMParameter"]),
            ("Graph editor", ["VisualStateMachineEditorWindow", "VSMGraphView", "VSMGraphNode"]),
        ],
    },
    {
        "name": "FlowField",
        "cat": "AI",
        "primary": "FlowFieldGrid",
        "sections": [
            ("Supporting scripts", ["FlowField", "FlowFieldAgent"]),
        ],
    },
    {
        "name": "PageCurl",
        "cat": "PageCurl",
        "primary": "BookPageCurl",
        "sections": [
            ("Shared core", ["PageCurlMeshBuilder", "PageContent", "ICurlSurface", "ITextureDisplay"]),
            ("Canvas (UI) book", ["PageCurlGraphic", "PageCurlDragHandler", "RawImageTextureDisplay"]),
            ("World (3D) book", ["PageCurlMeshRenderer", "PageCurlDragHandler3D", "MeshRendererTextureDisplay"]),
        ],
    },
    {
        "name": "PathBuilder",
        "cat": "PathBuilder",
        "primary": "PathBuilder",
        "sections": [
            ("Path core", ["SplinePath", "SplineFollower"]),
            ("Editor integration", ["PathBuilderEditor", "SplinePathEditor", "SplinePathSceneEditing"]),
        ],
    },
    {
        "name": "Tween",
        "cat": "Tweening",
        "primary": "Tween",
        "sections": [
            ("Supporting scripts", ["TweenRunner", "TweenHandle", "TweenExtensions"]),
        ],
    },
    {
        "name": "ScriptableVariables",
        "cat": "ScriptableObjects",
        "primary": "ScriptableVariable",
        "sections": [
            ("Concrete variable assets", ["FloatVariable", "IntVariable", "BoolVariable"]),
        ],
    },
    {
        "name": "ObjectPool",
        "cat": "Core",
        "primary": "ObjectPool",
        "sections": [
            ("Supporting scripts", ["PoolableObject"]),
        ],
    },
    {
        "name": "StateMachine",
        "cat": "Core",
        "primary": "StateMachine",
        "sections": [
            ("Supporting scripts", ["IState"]),
        ],
    },
    {
        "name": "UpdateManager",
        "cat": "Optimization",
        "primary": "UpdateManager",
        "sections": [
            ("Supporting scripts", ["ITickable"]),
        ],
    },
    {
        "name": "GameData",
        "cat": "Persistence",
        "primary": "GameDataAsset",
        "sections": [
            ("Supporting scripts", ["GameDataRegistry"]),
        ],
    },
    {
        "name": "Health",
        "cat": "Gameplay",
        "primary": "Health",
        "sections": [
            ("Supporting scripts", ["IDamageable"]),
        ],
    },
    {
        "name": "Currency",
        "cat": "Gameplay",
        "primary": "CurrencyWallet",
        "sections": [
            ("Supporting scripts", ["CurrencyDefinition"]),
        ],
    },
    {
        "name": "TriggerEventRelay",
        "cat": "Gameplay",
        "primary": "TriggerEventRelay",
        "sections": [
            ("2D variant", ["TriggerEventRelay2D"]),
        ],
    },
    {
        "name": "Watering",
        "cat": "Gameplay",
        "primary": "WateringCanController",
        "sections": [
            ("Supporting scripts", ["PlantWaterLevel"]),
        ],
    },
    {
        "name": "Interaction",
        "cat": "Mechanics",
        "primary": "InteractionDetector",
        "sections": [
            ("Supporting scripts", ["IInteractable"]),
        ],
    },
    {
        "name": "TowerDefense",
        "cat": "Mechanics",
        "primary": "TDGameManager",
        "sections": [
            ("Towers & enemies", ["TDTower", "TDTowerPlacer", "TDEnemy"]),
            ("Attack behaviours", ["IAttackBehavior", "TDHitscanAttack", "TDProjectileAttack", "TDProjectile"]),
        ],
    },
    {
        "name": "HitboxCombat",
        "cat": "Combat",
        "primary": "Hitbox",
        "sections": [
            ("Volumes & hit data", ["Hurtbox", "HitData", "IKnockbackReceiver"]),
            ("Attacks", ["MeleeAttack", "RangedAttack", "CombatProjectile"]),
        ],
    },
    {
        "name": "RacingAI",
        "cat": "AI",
        "primary": "RacingAIController",
        "sections": [
            ("Supporting scripts", ["RaceProgressTracker"]),
        ],
    },
    {
        "name": "Steering",
        "cat": "AI",
        "primary": "SteeringAgent",
        "sections": [
            ("Supporting scripts", ["SteeringBehaviors"]),
        ],
    },
    {
        "name": "Perception",
        "cat": "AI",
        "primary": "VisionCone",
        "sections": [
            ("Supporting scripts", ["PerceptionMemory"]),
        ],
    },
    {
        "name": "PlayerCameraRig",
        "cat": "Camera",
        "primary": "PlayerCameraRig",
        "sections": [
            ("Supporting scripts", ["PlayerCameraRigSetup"]),
        ],
    },
    {
        "name": "Locomotion",
        "cat": "Controller",
        "primary": "LocomotionStateMachine",
        "sections": [
            ("Supporting scripts", ["LedgeClimbController", "LocomotionAnimatorDriver"]),
        ],
    },
    {
        "name": "Decals",
        "cat": "Rendering",
        "primary": "DecalProjector",
        "sections": [
            ("Supporting scripts", ["DecalMeshBuilder"]),
        ],
    },
    {
        "name": "GameEvents",
        "cat": "ScriptableObjects",
        "primary": "GameEvent",
        "sections": [
            ("Supporting scripts", ["GameEventListener"]),
        ],
    },
    {
        "name": "RuntimeSets",
        "cat": "ScriptableObjects",
        "primary": "GameObjectRuntimeSet",
        "sections": [
            ("Supporting scripts", ["RuntimeSetRegistrar"]),
        ],
    },
    {
        "name": "DragAndDrop",
        "cat": "UI",
        "primary": "DraggableItem",
        "sections": [
            ("Supporting scripts", ["DropZone"]),
        ],
    },
    {
        "name": "Tooltips",
        "cat": "UI",
        "primary": "TooltipSystem",
        "sections": [
            ("Supporting scripts", ["TooltipTrigger"]),
        ],
    },
    {
        "name": "UndoRedo",
        "cat": "UI",
        "primary": "CommandHistory",
        "sections": [
            ("Supporting scripts", ["ICommand"]),
        ],
    },
    {
        "name": "FloatingCombatText",
        "cat": "UI",
        "primary": "FloatingCombatTextSpawner",
        "sections": [
            ("Supporting scripts", ["FloatingCombatText"]),
        ],
    },
    {
        "name": "InputGlyphs",
        "cat": "UI",
        "primary": "InputGlyphSwapper",
        "sections": [
            ("Supporting scripts", ["InputDeviceMonitor"]),
        ],
    },
    {
        "name": "GreyboxPlacer",
        "cat": "EditorTools",
        "primary": "GreyboxPlacerWindow",
        "sections": [
            ("Supporting scripts", ["GreyboxBlockMarker"]),
        ],
    },
    {
        "name": "BackgroundMatte",
        "cat": "BackgroundMatte",
        "primary": "BackgroundMatteWindow",
        "sections": [
            ("Supporting scripts", ["BackgroundMatteProcessor"]),
        ],
    },
    {
        "name": "Grid",
        "cat": "Grid",
        "primary": "GridComponent",
        "sections": [
            ("Data structure", ["Grid"]),
            ("Grid tools", ["GridPathfinder", "GridPlacer"]),
        ],
    },
    {
        "name": "BatchAssetRenamer",
        "cat": "EditorTools",
        "primary": "BatchAssetRenamerWindow",
        # No sections: this tool is one script, but the class is named after the
        # implementation rather than the tool. The name comes from its own [MenuItem].
        "sections": [],
    },
    {
        "name": "BulkHierarchyTools",
        "cat": "EditorTools",
        "primary": "BulkHierarchyToolsWindow",
        # No sections: this tool is one script, but the class is named after the
        # implementation rather than the tool. The name comes from its own [MenuItem].
        "sections": [],
    },
    {
        "name": "CsvTableEditor",
        "cat": "EditorTools",
        "primary": "CsvTableWindow",
        # No sections: this tool is one script, but the class is named after the
        # implementation rather than the tool. The name comes from its own [MenuItem].
        "sections": [],
    },
    {
        "name": "FindReferences",
        "cat": "EditorTools",
        "primary": "FindReferencesWindow",
        # No sections: this tool is one script, but the class is named after the
        # implementation rather than the tool. The name comes from its own [MenuItem].
        "sections": [],
    },
    {
        "name": "PlayerPrefsEditor",
        "cat": "EditorTools",
        "primary": "PlayerPrefsEditorWindow",
        # No sections: this tool is one script, but the class is named after the
        # implementation rather than the tool. The name comes from its own [MenuItem].
        "sections": [],
    },
    {
        "name": "ScriptableObjectTableView",
        "cat": "EditorTools",
        "primary": "ScriptableObjectTableWindow",
        # No sections: this tool is one script, but the class is named after the
        # implementation rather than the tool. The name comes from its own [MenuItem].
        "sections": [],
    },
    {
        "name": "TodoScanner",
        "cat": "EditorTools",
        "primary": "TodoScannerWindow",
        # No sections: this tool is one script, but the class is named after the
        # implementation rather than the tool. The name comes from its own [MenuItem].
        "sections": [],
    },
    {
        "name": "LocomotionAnimatorGenerator",
        "cat": "EditorTools",
        "primary": "LocomotionAnimatorGeneratorWindow",
        # No sections: this tool is one script, but the class is named after the
        # implementation rather than the tool. The name comes from its own [MenuItem].
        "sections": [],
    },
]


def build_systems(entries):
    """Turn each SYSTEMS spec into one composite entry and absorb its members.

    Returns (composite entries, set of absorbed member ids). The composite reuses the PRIMARY type's
    extracted data verbatim - namespace, file, parameters, API, demo scene - so nothing is invented;
    only the display name and the nested sections are added on top.
    """
    by_name = {e["name"].split("<")[0]: e for e in entries}
    composites, absorbed = [], set()

    for spec in SYSTEMS:
        primary = by_name.get(spec["primary"])
        if primary is None:
            print(f"  WARN system '{spec['name']}': primary type "
                  f"'{spec['primary']}' not found - skipped")
            continue

        sections, missing = [], []
        for title, names in spec["sections"]:
            members = []
            for n in names:
                m = by_name.get(n)
                if m is None:
                    missing.append(n)
                    continue
                members.append(m)
                absorbed.add(m["id"])
            if members:
                sections.append({"title": title, "entries": members})
        if missing:
            print(f"  WARN system '{spec['name']}': unknown member(s) {', '.join(missing)}")

        composite = dict(primary)
        # A system is named after the TOOL, and that name sometimes already belongs to one of its
        # own parts - the FlowField system contains a type called FlowField. Two elements sharing an
        # id breaks the anchor for both, so fall back to the primary's id, which is always unique.
        taken = {e["id"] for e in entries}
        wanted = slugify(spec["name"])
        member_ids = {m["id"] for sec in sections for m in sec["entries"]}
        composite["id"] = wanted if wanted not in (taken - {primary["id"]}) or wanted not in member_ids \
            else primary["id"]
        if composite["id"] != wanted:
            print(f"  note system '{spec['name']}': id '{wanted}' is taken by one of its own parts, "
                  f"using '{composite['id']}'")
        composite["name"] = spec["name"]
        composite["cat"] = spec["cat"]
        composite["isSystem"] = True
        composite["systemOf"] = primary["name"]
        composite["systemPrimaryId"] = primary["id"]
        composite["systemSections"] = sections
        absorbed.add(primary["id"])
        composites.append(composite)

    return composites, absorbed


# Types that belong inside another entry but cannot be folded automatically, because the fold rule
# only reaches types declared in the SAME file. A [ReadOnly] attribute and the ReadOnlyDrawer that
# renders it are one feature split across two files only because Unity forces a PropertyDrawer to
# live in an Editor/ folder - exactly the split documented in the Unity lessons, and not something a
# reader should have to reassemble.
FOLD_INTO = {
    "ButtonAttributeEditor": "ButtonAttribute",
    "LayerDrawer": "LayerAttribute",
    "ReadOnlyDrawer": "ReadOnlyAttribute",
    "TagDrawer": "TagAttribute",
    "InputDeviceMonitorRunner": "InputDeviceMonitor",
}


def fold_satellites(entries):
    """Collapse same-file helper types into the entry for the type the FILE is named after.

    A single .cs file routinely declares a component plus the enums and small structs it needs -
    FakeLight.cs holds FakeLight, FakeLightType and FakeLightInfluenceMode. Emitting one entry per
    public TYPE turned that one file into three sibling rows, which made the toolkit look far more
    fragmented than it is: 46 files were producing 106 entries. The satellites are now rendered
    INSIDE their owner's entry, where a reader already is when the enum matters.

    Two deliberate exclusions:
      - Interfaces never fold. A contract is something you implement, it has its own entry template
        (member contracts, must-nots, who calls it), and burying it inside a sibling would hide it.
      - A file whose types are all helpers (no type shares the filename) falls back to the LARGEST
        type by line span, which is the substantial one rather than an arbitrary pick.
    """
    by_file = {}
    for e in entries:
        by_file.setdefault(e["src_file"], []).append(e)

    folded = {}
    for path, group in by_file.items():
        if len(group) < 2:
            continue
        stem = path.split("/")[-1][: -len(".cs")]
        primary = next((e for e in group if e["name"].split("<")[0] == stem), None)
        if primary is None:
            primary = max(group, key=lambda e: e["src_lines"][1] - e["src_lines"][0])
        for e in group:
            if e is primary or e["entryKind"] == "contract":
                continue
            primary.setdefault("parts", []).append(e)
            e["foldedInto"] = primary["id"]
            folded[e["id"]] = primary["id"]

    # Explicit cross-file folds, applied after the same-file pass.
    by_name = {e["name"].split("<")[0]: e for e in entries}
    for child_name, owner_name in FOLD_INTO.items():
        child, owner = by_name.get(child_name), by_name.get(owner_name)
        if child is None or owner is None:
            print(f"  WARN FOLD_INTO: {child_name} -> {owner_name} - one of them does not exist")
            continue
        if "foldedInto" in child:
            continue
        owner.setdefault("parts", []).append(child)
        child["foldedInto"] = owner["id"]
        folded[child["id"]] = owner["id"]

    # Keep a part's own order stable and predictable: enums first (they are what a reader is
    # usually looking up), then structs, then classes, each alphabetically.
    rank = {"enum": 0, "data": 1}
    for e in entries:
        if e.get("parts"):
            e["parts"].sort(key=lambda x: (rank.get(x["entryKind"], 2), x["name"].lower()))

    return folded


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
    def merge_authored(targets, warn_unmatched):
        """Overlay authored prose onto entries, by id. Never lets prose touch a src_ key."""
        by_id = {e["id"]: e for e in targets}
        n = 0
        for path in sorted(glob.glob(os.path.join(AUTH, "*.json"))):
            a = json.load(open(path, encoding="utf-8"))
            tgt = by_id.get(a.get("id"))
            if not tgt:
                if warn_unmatched:
                    print(f"  WARN authored file has no matching skeleton: {os.path.basename(path)}")
                continue
            for k, v in a.items():
                if k.startswith("src_") or k in ("id", "cat"):
                    continue
                tgt[k] = v
            tgt["status"] = "published"
            n += 1
        return n

    authored = 0
    if os.path.isdir(AUTH):
        # A composite system entry does not exist yet at this point - it is assembled later from its
        # primary type - so an authored file addressed to it is expected to miss here and is merged
        # in a second pass below rather than warned about.
        authored = merge_authored(entries, warn_unmatched=False)

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

    # Fold same-file helper types into their owner. Done AFTER cross-referencing so a part keeps
    # its own derived "used by" data, and BEFORE grouping so groups only ever see top-level entries.
    folded = fold_satellites(entries)
    top_level = [e for e in entries if "foldedInto" not in e]

    # Systems are assembled AFTER folding so each member keeps the enums declared in its own file.
    composites, absorbed = build_systems(top_level)
    # The catalog drives global search and the headline count, so it must list the COMPOSITE and
    # treat everything it absorbed - including the primary the composite was built from - as a child
    # pointing at it. Without the primary here, searching "LevelTransformGuidanceWindow" landed on a
    # row that no longer exists, and "LevelTransformGuidance" could not be found at all.
    absorbed_by = {}
    for c in composites:
        for sec in c["systemSections"]:
            for m in sec["entries"]:
                absorbed_by[m["id"]] = c["id"]
        primary_id = c.get("systemPrimaryId")
        if primary_id and primary_id != c["id"]:
            absorbed_by[primary_id] = c["id"]
        absorbed_by.pop(c["id"], None)
    # A composite whose id fell back to its primary's REPLACES that primary in the catalog - the
    # primary is absorbed, so listing it instead of the composite would show the helper's name
    # ("FlowFieldGrid") where the tool's name ("FlowField") belongs.
    composite_ids = {c["id"] for c in composites}
    catalog_entries = [e for e in entries if e["id"] not in composite_ids] + composites
    if composites:
        top_level = [e for e in top_level if e["id"] not in absorbed] + composites
        print(f"build: {len(composites)} system entr(y/ies) absorbed {len(absorbed)} type(s)")
        # Second pass: a composite only exists now, so authored prose addressed to it merges here.
        if os.path.isdir(AUTH):
            authored += merge_authored(composites, warn_unmatched=False)
    print(f"build: folded {len(folded)} satellite type(s) into their owner "
          f"({len(entries)} types -> {len(top_level)} entries)")

    # ---- group ----
    groups = collections.defaultdict(list)
    for e in top_level:
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
        item_groups = []
        for g, members, subs in grouped:
            if not g:
                continue
            row = {"title": g, "ids": [e["id"] for e in members]}
            if subs:
                row["subs"] = [{"title": st, "ids": [e["id"] for e in sm]} for st, sm in subs]
            item_groups.append(row)
        # Re-order the flat list to match the grouped order, so an ungrouped render still reads right.
        if item_groups:
            items = [e for _g, members, _s in grouped for e in members]
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
        # Folded satellites stay in the SEARCH index even though they no longer own a row: someone
        # looking up "FakeLightType" must still find it. "parent" tells the renderer to link at the
        # owner's entry, where the satellite is rendered with its own anchor.
        "entries": [dict({
            "id": e["id"], "cat": slug_cat(e["cat"]), "name": e["name"],
            "kind": e["entryKind"],
            "summary": summarise(e),
            "search": (e["name"] + " " + e["src_ns"] + " " + summarise(e)).lower(),
        }, **({"parent": e["foldedInto"]} if "foldedInto" in e
              else {"parent": absorbed_by[e["id"]]} if e["id"] in absorbed_by
              else {})) for e in catalog_entries],
    }
    with open(os.path.join(REPO, "assets", "catalog.js"), "w", encoding="utf-8") as fh:
        fh.write("window.FM_INDEX = ")
        json.dump(index, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write(";\n")

    with open(os.path.join(REPO, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(hub_shell(len(top_level), len(cats_out)))

    print(f"build: {len(top_level)} entries / {len(cats_out)} categories "
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
