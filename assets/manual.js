/* Field Manual - shared renderer and page behaviour.
 *
 * Data contract (both are plain <script src> globals, never fetch()):
 *   FM_INDEX = { categories: [{id, code, title, blurb, count, written}],
 *                entries:    [{id, cat, name, kind, summary, search}] }
 *   FM_DATA[cat] = { entries: [ <full entry> ] }
 *
 * Why globals and not fetch(): a category page opened straight off disk (file://)
 * cannot fetch a sibling JSON file - the browser blocks it as cross-origin. Since
 * these pages get previewed locally before every push, fetch would break that loop
 * constantly. A <script src> works identically from disk and from GitHub Pages.
 */
(function () {
  "use strict";

  var idx = window.FM_INDEX || { categories: [], entries: [] };
  var data = window.FM_DATA || {};
  var page = document.body.getAttribute("data-page") || "hub";
  var curCat = document.body.getAttribute("data-cat") || null;
  var root = document.body.getAttribute("data-root") || "";

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function el(html) {
    var d = document.createElement("div");
    d.innerHTML = html.trim();
    return d.firstChild;
  }
  var byId = {};
  idx.entries.forEach(function (e) { byId[e.id] = e; });
  var byName = {};
  idx.entries.forEach(function (e) { byName[e.name.replace(/<.*>/, "")] = e; });

  /* ---------- cross-link resolution ----------
     A link target is a TYPE NAME. It resolves to a documented entry, or it degrades
     to plain code text. It must never render as a dead <a>. */
  function linkFor(typeName) {
    var e = byName[String(typeName).replace(/<.*>/, "")];
    if (!e) return '<code class="chip dead">' + esc(typeName) + "</code>";
    var href = (e.cat === curCat && page === "category")
      ? "#" + e.id
      : root + "c/" + e.cat + ".html#" + e.id;
    return '<a class="chip" href="' + href + '">' + esc(e.name) + "</a>";
  }
  function chipStrip(label, names) {
    if (!names || !names.length) return "";
    return '<div class="chipstrip"><span class="chiplabel">' + esc(label) + "</span>" +
      names.map(linkFor).join("") + "</div>";
  }

  /* ---------- shared section renderers ---------- */
  // A tooltip may contain real newlines - an author wrote a paragraph break into the [Tooltip].
  // HTML collapses those to a space, so the two halves ran together as one wall of text.
  function escLines(t) {
    return esc(t == null ? "" : t)
      .replace(/\n{2,}/g, '</span><span class="tline">')
      .replace(/\n/g, "<br>");
  }
  function multiline(t) {
    var html = escLines(t);
    return html.indexOf("tline") === -1 && html.indexOf("<br>") === -1
      ? html
      : '<span class="tline">' + html + "</span>";
  }
  function label(t) { return '<div class="tool-section-label">' + esc(t) + "</div>"; }
  function prose(t) { return t ? "<p>" + esc(t) + "</p>" : ""; }
  function code(src, note) {
    if (!src) return "";
    return '<div class="codewrap"><pre class="impl">' + esc(src) +
      '</pre><button class="copybtn" type="button">copy</button></div>' +
      (note ? '<p class="tool-desc">' + esc(note) + "</p>" : "");
  }
  function bullets(arr, cls) {
    if (!arr || !arr.length) return "";
    return '<ul class="' + (cls || "uses") + '">' +
      arr.map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("") + "</ul>";
  }
  function callouts(arr) {
    if (!arr || !arr.length) return "";
    // A gotcha may declare which sub-tool it belongs to. On a window with four tabs an ungrouped
    // list forces the reader to work out which warning applies to what they are doing.
    var lastGroup = null;
    return arr.map(function (g) {
      var head = "";
      if (g.group && g.group !== lastGroup) {
        head = '<div class="caveat-group">' + esc(g.group) + "</div>";
        lastGroup = g.group;
      }
      var title = g.title ? "<strong>" + esc(g.title) + "</strong> — " : "";
      return head + '<div class="caveat">' + title + esc(g.body || g) + "</div>";
    }).join("");
  }
  function paramTable(params, serialized) {
    if (!serialized || !serialized.length) return "";
    var notes = {};
    (params || []).forEach(function (p) { notes[p.field] = p; });
    var rows = serialized.map(function (s) {
      var n = notes[s.name] || {};
      var why = n.why || s.tooltip || "";
      // NOT .pdef - that class is white-space:nowrap for short type/default tokens, and
      // tuning advice is a full sentence, so it overflowed the table off the page.
      var tuning = n.tuning ? '<div class="ptuning">' + multiline(n.tuning) + "</div>" : "";
      return "<tr><td class=\"pname\">" + esc(s.name) + "</td>" +
        '<td class="ptype">' + esc(s.type) + "</td>" +
        '<td class="pdef">' + esc(s.default == null ? "-" : s.default) +
        (s.range ? " <span>[" + esc(s.range) + "]</span>" : "") + "</td>" +
        "<td>" + multiline(why) + tuning + "</td></tr>";
    }).join("");
    // The type/default cells are white-space:nowrap, so the table has a min-content width that
    // exceeds a phone viewport. Without this wrapper it was simply CLIPPED - and the column that
    // got cut is "What it controls", the one worth reading.
    return '<div class="tablewrap"><table class="paramtable"><thead><tr><th>Field</th><th>Type</th>' +
      "<th>Default</th><th>What it controls</th></tr></thead><tbody>" + rows +
      "</tbody></table></div>";
  }
  function apiList(members, notes) {
    if (!members || !members.length) return "";
    notes = notes || {};
    var items = members.map(function (m) {
      var gloss = notes[m.id] || m.comment || "";
      return '<li><code class="sig">' + esc(m.sig) + "</code>" +
        (gloss ? " — " + esc(gloss) : "") + "</li>";
    }).join("");
    return '<ul class="api-list">' + items + "</ul>";
  }
  function worked(w) {
    if (!w) return "";
    if (w.na) return '<p class="tool-desc">' + esc(w.na) + "</p>";
    var inputs = (w.inputs || []).map(function (i) {
      return "<li><code>" + esc(i.name) + "</code> = <code>" + esc(i.value) + "</code></li>";
    }).join("");
    var steps = (w.steps || []).map(function (s) { return "<li>" + esc(s) + "</li>"; }).join("");
    return '<div class="worked">' +
      (w.setup ? "<p>" + esc(w.setup) + "</p>" : "") +
      (inputs ? "<ul>" + inputs + "</ul>" : "") +
      (steps ? "<ol>" + steps + "</ol>" : "") +
      (w.result ? '<div class="result">' + esc(w.result) + "</div>" : "") +
      (w.verifiedBy ? '<div class="verified">verified by: ' + esc(w.verifiedBy) + "</div>" : "") +
      "</div>";
  }
  function keyed(obj, fn) {
    if (!obj) return "";
    var keys = Object.keys(obj);
    if (!keys.length) return "";
    return '<ul class="api-list">' + keys.map(fn).join("") + "</ul>";
  }

  /* ---------- entry body, dispatched on entryKind ---------- */
  function renderBody(e) {
    var out = [];
    var kind = e.entryKind || "tool";

    // The lead paragraph: what this tool IS and what it is for, before any wiring detail. Everything
    // below it answers "how"; this answers "why would I open this at all".
    if (e.overview) {
      // The separator rule belongs to whichever of overview/features comes last, or the two stack
      // two rules on top of each other.
      out.push('<div class="overview' + (e.features && e.features.length ? " nosep" : "") + '">' +
        (Array.isArray(e.overview) ? e.overview : [e.overview])
        // A line break inside an overview paragraph is deliberate - it separates a per-feature
        // one-liner list. HTML would otherwise collapse it to a space and run them together.
        .map(function (para) { return "<p>" + multiline(para) + "</p>"; }).join("") + "</div>");
    }

    // The per-feature lines of a multi-tool entry. Structured rather than a prose string split on
    // colons: a description is allowed to contain a colon, and parsing would silently mangle it.
    // Styled like the settings list so a name reads as a name in both places.
    if (e.features && e.features.length) {
      out.push('<dl class="features">' + e.features.map(function (f) {
        return '<dd><code class="featname">' + esc(f.name) + ":</code> " + esc(f.what) + "</dd>";
      }).join("") + "</dl>");
    }

    // Use cases sit directly under the overview: "would I use this at all" is the next question
    // after "what is it", and it was previously buried below the walkthrough.
    if (e.useCases && e.useCases.length) { out.push(label("Use cases")); out.push(bullets(e.useCases)); }

    if (e.src_attributes && e.src_attributes.addComponentMenu) {
      out.push('<div class="tool-menu"><span class="mlabel">Add Component:</span> <code>' +
        esc(e.src_attributes.addComponentMenu) + "</code></div>");
    }
    // When an entry states where to find it in its own words, the auto-derived Menu chip is the
    // same fact twice on consecutive lines.
    if (e.src_attributes && e.src_attributes.menuItem && !e.whereToFind) {
      // A class can register several menu items (a window plus an Assets/ context-menu variant), so the
      // extractor hoists them from the decorated static methods into a list.
      var menus = e.src_attributes.menuItem;
      if (typeof menus === "string") menus = [menus];
      out.push('<div class="tool-menu"><span class="mlabel">Menu:</span> ' +
        menus.map(function (m) { return "<code>" + esc(m) + "</code>"; }).join(" ") + "</div>");
    }

    if (e.problem) { out.push(label("Problem it solves")); out.push(prose(e.problem)); }
    if (kind === "contract" && e.purpose) { out.push(label("What this contract is for")); out.push(prose(e.purpose)); }
    if (kind === "contract" && e.whenToImplement) { out.push(label("When you implement it")); out.push(prose(e.whenToImplement)); }
    if (kind === "editor" && e.whereToFind) { out.push(label("Where to find it")); out.push(prose(e.whereToFind)); }

    if (kind === "tool" || kind === "editor") {
      var pt = paramTable(e.params, e.src_serialized);
      // An EditorWindow's [SerializeField]s are not Inspector tuning knobs - they are the window's own
      // state, and the reason they are serialized at all is that Unity restores them across a domain
      // reload and a window close. Labelling them "Parameters" implied something a user sets from outside.
      if (pt) { out.push(label(kind === "editor" ? "Window state (persists across reloads)" : "Parameters")); out.push(pt); }
    }
    if (kind === "data" && e.fields) {
      out.push(label("Fields"));
      out.push(keyed(e.fields, function (k) {
        var f = e.fields[k];
        return '<li><code class="sig">' + esc(k) + "</code> — " + esc(f.meaning || f) + "</li>";
      }));
    }
    if (kind === "contract" && e.memberContracts) {
      out.push(label("Members you must implement"));
      out.push(keyed(e.memberContracts, function (k) {
        var m = e.memberContracts[k];
        // The authored text already opens with "Must not ...", so prefixing another "Must not:" here
        // produced a visible "Must not: Must not assume ..." on every member. The constraint gets its own
        // block instead - same treatment as a gotcha, because it is the same kind of content.
        var must = m.mustNot ? '<div class="mustnot">' + esc(m.mustNot) + "</div>" : "";
        return '<li><code class="sig">' + esc(k) + "</code> — " + esc(m.contract || m) + must + "</li>";
      }));
    }
    if (kind === "contract" && e.calledBy) {
      out.push(label("Who calls it"));
      out.push(prose(e.calledBy));
    }

    // Types declared in the SAME .cs file - the enums and small structs a component needs. They
    // used to be sibling rows, which made one file look like three separate tools. Each keeps its
    // own id as an anchor so existing deep links and cross-reference chips still land.
    if (e.parts && e.parts.length) {
      out.push(label("Defined in the same file"));
      out.push('<div class="parts">' + e.parts.map(function (p) {
        var body = "";
        if (p.src_enumValues && p.src_enumValues.length) {
          body += '<ul class="partvals">' + p.src_enumValues.map(function (v) {
            var note = (p.valueNotes || {})[v];
            return "<li><code>" + esc(v) + "</code>" + (note ? " \u2014 " + esc(note) : "") + "</li>";
          }).join("") + "</ul>";
        }
        if (p.src_serialized && p.src_serialized.length) {
          body += '<ul class="partvals">' + p.src_serialized.map(function (f) {
            return "<li><code>" + esc(f.type + " " + f.name) + "</code>" +
              (f.tooltip ? " \u2014 " + multiline(f.tooltip) : "") + "</li>";
          }).join("") + "</ul>";
        }
        if (!body && p.src_members && p.src_members.length) {
          body += '<ul class="partvals">' + p.src_members.map(function (m) {
            return '<li><code class="sig">' + esc(m.sig) + "</code></li>";
          }).join("") + "</ul>";
        }
        return '<div class="part" id="' + esc(p.id) + '">' +
          '<div class="parthead"><code class="partname">' + esc(p.name) + "</code>" +
          '<span class="partkind">' + esc(p.src_typeKind) + "</span>" +
          '<a class="entry-anchor" href="#' + esc(p.id) + '" title="Link to this type">#</a></div>' +
          (p.summary ? '<p class="tool-desc">' + esc(p.summary) + "</p>" : "") + body + "</div>";
      }).join("") + "</div>");
    }

    if (e.workedExample) { out.push(label("Worked example")); out.push(worked(e.workedExample)); }
    if (e.walkthrough && e.walkthrough.length) {
      // Two shapes share this section. An item with a `name` is a SETTING - the control as it is
      // labelled in the window, and what it does, one line each. An item with a `step` is an
      // ordered instruction. Settings won out for multi-tool windows: a numbered tour of nine
      // steps read as filler next to a plain list of what each control actually does.
      var isSettings = e.walkthrough[0] && e.walkthrough[0].name;
      out.push(label(isSettings ? "Settings" : "Walkthrough"));
      if (isSettings) {
        var lastSet = null;
        out.push('<dl class="settings">' + e.walkthrough.map(function (v) {
          var head = "";
          if (v.group && v.group !== lastSet) {
            head = '<dt class="setgroup">' + esc(v.group) + "</dt>";
            lastSet = v.group;
          }
          return head + '<dd><code class="setname">' + esc(v.name) + "</code> " + esc(v.what) + "</dd>";
        }).join("") + "</dl>");
      } else {
        out.push("<ol>" + e.walkthrough.map(function (s) {
          return "<li>" + esc(s.step) + (s.whatYouSee ? " — <em>" + esc(s.whatYouSee) + "</em>" : "") + "</li>";
        }).join("") + "</ol>");
      }
    }

    if (e.usage) { out.push(label("How to use")); out.push(code(e.usage.code, e.usage.codeNote)); }
    if (e.minimalImpl) { out.push(label("Minimal implementation")); out.push(code(e.minimalImpl.code, e.minimalImpl.note)); }
    if (e.wiring) { out.push(label("How the host picks it up")); out.push(code(e.wiring.code, e.wiring.note)); }
    if (e.extendGuide) { out.push(label("Extending / modifying")); out.push(prose(e.extendGuide)); }

    var api = apiList(e.src_members, e.apiNotes);
    if (api) { out.push(label("Key API")); out.push(api); }

    if (e.gotchas && e.gotchas.length) { out.push(label("Gotchas")); out.push(callouts(e.gotchas)); }

    var rel = "";
    rel += chipStrip("Implements", e.src_implements);
    rel += chipStrip("Extension points", e.src_extensionPoints);
    if (kind === "contract") {
      // e.src_usedBy mixes the type that DRIVES the interface (StateMachine calling Enter/Tick/Exit) with
      // the types that IMPLEMENT it (RacingAIController). For an implementer those are opposite facts, so
      // authored `drivenBy` names the callers and everything else falls through as an implementer.
      var drivers = e.drivenBy || [];
      var impls = (e.src_usedBy || []).filter(function (n) { return drivers.indexOf(n) === -1; });
      rel += chipStrip("Driven by", drivers);
      rel += chipStrip("Implemented by", impls);
    } else {
      rel += chipStrip("Used by", e.src_usedBy);
    }
    rel += chipStrip("See also", e.seeAlso);
    if (rel) { out.push(label("Related")); out.push(rel); }

    // A system entry (one tool, many scripts) carries its parts INSIDE its own dropdown, grouped
    // into the sections a user thinks in - Heatmap, Clipping - rather than as sibling rows on the
    // page. Each part is a full entry in its own right, just nested one level down.
    // A system is named after the tool, so its entry no longer carries the id of the TYPE it was
    // built from. Keep that id reachable, or a link to the primary (and its own catalog row) lands
    // nowhere. An empty anchor is enough - openFromHash opens the containing <details>.
    if (e.systemPrimaryId && e.systemPrimaryId !== e.id) {
      out.push('<span class="alias-anchor" id="' + esc(e.systemPrimaryId) + '"></span>');
    }

    if (e.systemSections && e.systemSections.length) {
      out.push('<div class="sysparts">' + e.systemSections.map(function (sec) {
        return '<section class="syssec">' +
          '<h4 class="syssec-head" id="' + groupSlug(e.id + "-" + sec.title) + '">' +
          '<span class="syssec-title">' + esc(sec.title) + "</span>" +
          // "scripts", not "components": these are editor classes, an interface and static helpers.
          // In Unity "component" means a MonoBehaviour you attach, which none of these are.
          '<span class="partcount">' + sec.entries.length + " script" +
          (sec.entries.length === 1 ? "" : "s") + "</span></h4>" +
          sec.entries.map(function (m) { return renderEntry(m, true); }).join("") +
          "</section>";
      }).join("") + "</div>");
    }

    out.push('<div class="tool-ns">' + esc(e.src_ns || "") +
      ' <span class="tool-path">— ' + esc(e.src_file || "") + "</span></div>");

    var demos = e.src_demoScene;
    if (typeof demos === "string") demos = [demos];
    if (demos && demos.length) {
      out.push('<div class="tool-menu"><span class="mlabel">Shown in demo scene' +
        (demos.length > 1 ? "s" : "") + ":</span> " +
        demos.map(function (d) { return "<code>" + esc(d) + "</code>"; }).join(" ") + "</div>");
    }
    return out.join("");
  }

  function badgesFor(e) {
    var b = [];
    var base = e.src_base || "";
    var t = base === "MonoBehaviour" ? ["MonoBehaviour", "type-mono"]
      : base === "ScriptableObject" ? ["ScriptableObject", "type-so"]
      : e.src_typeKind === "interface" ? ["Interface", "type-plain"]
      : e.src_static ? ["Static", "type-static"]
      : [e.src_typeKind === "struct" ? "Struct" : "Plain C#", "type-plain"];
    b.push('<span class="badge ' + t[1] + '">' + t[0] + "</span>");
    if (!e.src_runtime) b.push('<span class="badge">Editor only</span>');
    if (e.src_selfTest) b.push('<span class="badge">Self-tested</span>');
    if (e.status === "skeleton") b.push('<span class="statuspill">skeleton</span>');
    return b.join("");
  }

  // Filtering the page searches each row's data-search. A system's members live inside the parent
  // row, so their names have to reach the parent too or typing "heatmap" would hide the only row
  // that contains it.
  function sysText(e) {
    return (e.systemSections || []).map(function (sec) {
      return sec.title + " " + sec.entries.map(function (m) {
        return m.name + " " + (m.summary || "");
      }).join(" ");
    }).join(" ");
  }

  function renderEntry(e, nested) {
    // A folded satellite no longer owns a row, so its name has to reach the PARENT's search text
    // or filtering for "FakeLightType" would come back empty.
    var partText = (e.parts || []).map(function (p) {
      return p.name + " " + (p.summary || "") + " " + (p.src_enumValues || []).join(" ");
    }).join(" ");
    var searchText = (e.name + " " + (e.src_ns || "") + " " + (e.summary || "") + " " +
      (e.src_members || []).map(function (m) { return m.id; }).join(" ") + " " +
      (e.src_serialized || []).map(function (s) { return s.name; }).join(" ") + " " +
      partText + " " + sysText(e)).toLowerCase();
    return '<details class="entry' + (nested ? " subentry" : "") + '" id="' + esc(e.id) +
      '" data-search="' + esc(searchText) + '">' +
      '<summary><span class="entry-name">' + esc(e.name) + "</span>" +
      '<span class="entry-summary">' + esc(e.summary || e.src_typeComment || "") + "</span>" +
      '<span class="tool-badges">' + badgesFor(e) + "</span>" +
      '<a class="entry-anchor" href="#' + esc(e.id) + '" title="Link to this entry">#</a>' +
      "</summary><div class=\"entry-body\">" + renderBody(e) + "</div></details>";
  }

  /* ---------- page builders ---------- */
  function groupSlug(title) {
    return "g-" + String(title).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  }

  function buildCategoryPage() {
    var host = document.getElementById("entries");
    if (!host) return;
    var d = data[curCat];
    if (!d || !d.entries || !d.entries.length) {
      host.innerHTML = '<div class="empty-state">No entries in this category yet.</div>';
      return;
    }

    // A category with fewer than ~6 entries ships no groups and renders flat - a heading above one
    // row is noise. build.py decides that, not this file.
    var groups = d.groups || [];
    if (!groups.length) {
      // NOT .map(renderEntry): Array.map passes (value, index, array), so the index landed in
      // renderEntry's `nested` flag and every row after the first rendered as a nested sub-entry.
      host.innerHTML = d.entries.map(function (e) { return renderEntry(e); }).join("");
      return;
    }

    var byId = {};
    d.entries.forEach(function (e) { byId[e.id] = e; });

    // Jump strip: on a 50-entry page the group names are the fastest way in, and they double as
    // an at-a-glance table of contents.
    var strip = '<nav class="groupjump" aria-label="Groups in this category">' +
      groups.map(function (g) {
        return '<a href="#' + groupSlug(g.title) + '">' + esc(g.title) +
          '<span class="gcount">' + g.ids.length + "</span></a>";
      }).join("") + "</nav>";

    function rowsFor(ids) {
      return ids.map(function (id) { return byId[id] ? renderEntry(byId[id]) : ""; }).join("");
    }

    var sections = groups.map(function (g) {
      // A group big enough to need it (one window with four tabs, a runtime/editor split) carries
      // subs and renders a labelled block per part. Everything else stays a flat list.
      var body = g.subs && g.subs.length
        ? g.subs.map(function (sub) {
            return '<div class="tsub"><h3 class="tsub-head" id="' +
              groupSlug(g.title + "-" + sub.title) + '">' + esc(sub.title) +
              '<span class="gcount">' + sub.ids.length + "</span></h3>" + rowsFor(sub.ids) + "</div>";
          }).join("")
        : rowsFor(g.ids);
      return '<section class="tgroup" data-group="' + esc(g.title) + '">' +
        '<h2 class="tgroup-head" id="' + groupSlug(g.title) + '">' + esc(g.title) +
        '<span class="gcount">' + g.ids.length + "</span></h2>" + body + "</section>";
    }).join("");

    host.innerHTML = strip + sections;
  }

  function buildHub() {
    var host = document.getElementById("cats");
    if (!host) return;
    host.innerHTML = '<div class="catgrid">' + idx.categories.map(function (c) {
      return '<a class="catcard" href="' + root + "c/" + c.id + '.html">' +
        '<span class="catcode">' + esc(c.code) + "</span>" +
        "<h3>" + esc(c.title) + "</h3>" +
        (c.blurb ? "<p>" + esc(c.blurb) + "</p>" : "") +
        '<p class="n">' + c.count + " entr" + (c.count === 1 ? "y" : "ies") +
        (c.written < c.count ? " - " + c.written + " written" : "") + "</p></a>";
    }).join("") + "</div>";
  }

  function buildNav() {
    var nav = document.getElementById("catnav");
    if (!nav) return;
    var d = data[curCat];
    var subs = (d && d.groups) || [];
    nav.innerHTML = idx.categories.map(function (c) {
      var isActive = c.id === curCat;
      var row = '<a href="' + root + "c/" + c.id + '.html"' + (isActive ? ' class="active"' : "") + ">" +
        '<span class="navcode">' + esc(c.code) + "</span>" +
        '<span class="navtitle">' + esc(c.title) + "</span>" +
        '<span class="navcount">' + c.count + "</span></a>";
      // Only the category you are ON expands. Nesting all 31 at once would make the sidebar
      // longer than the page it is navigating.
      if (isActive && subs.length) {
        row += '<div class="navsubs">' + subs.map(function (g) {
          return '<a class="navsub" href="#' + groupSlug(g.title) + '">' + esc(g.title) +
            '<span class="navcount">' + g.ids.length + "</span></a>";
        }).join("") + "</div>";
      }
      return row;
    }).join("");
  }

  /* ---------- search ----------
     With collapsed rows, a filter that matches text inside a CLOSED body and shows
     nothing reads as broken - so on a category page every match is force-opened. */
  function wireSearch() {
    var input = document.getElementById("filter");
    var status = document.getElementById("filterStatus");
    var results = document.getElementById("searchResults");
    if (!input) return;

    function run() {
      var q = input.value.trim().toLowerCase();

      if (page === "category") {
        var nodes = Array.prototype.slice.call(document.querySelectorAll(".entry"));
        var sections = Array.prototype.slice.call(document.querySelectorAll(".tgroup"));
        var jump = document.querySelector(".groupjump");
        if (!q) {
          nodes.forEach(function (n) { n.hidden = false; });
          sections.forEach(function (sec) { sec.hidden = false; });
          Array.prototype.slice.call(document.querySelectorAll(".tsub"))
            .forEach(function (sub) { sub.hidden = false; });
          if (jump) jump.hidden = false;
          if (status) status.textContent = "";
          var es = document.getElementById("emptyState");
          if (es) es.hidden = true;
          return;
        }
        var hits = 0;
        nodes.forEach(function (n) {
          var match = (n.getAttribute("data-search") || "").indexOf(q) > -1;
          n.hidden = !match;
          if (match) { n.open = true; hits++; }
        });
        // A nested component can match while its system parent does not. Hiding the parent would
        // hide the match with it, so re-show and open every ancestor of a visible row. Counted
        // AFTER this, so the tally reflects rows you can actually see.
        nodes.forEach(function (n) {
          if (n.hidden) return;
          for (var a = n.parentElement; a; a = a.parentElement) {
            if (a.classList && a.classList.contains("entry")) { a.hidden = false; a.open = true; }
          }
        });
        hits = nodes.filter(function (n) { return !n.hidden; }).length;
        // A group heading left standing over zero visible rows reads as a broken filter.
        Array.prototype.slice.call(document.querySelectorAll(".tsub")).forEach(function (sub) {
          var any = Array.prototype.slice.call(sub.querySelectorAll(".entry"))
            .some(function (n) { return !n.hidden; });
          sub.hidden = !any;
        });
        sections.forEach(function (sec) {
          var any = Array.prototype.slice.call(sec.querySelectorAll(".entry"))
            .some(function (n) { return !n.hidden; });
          sec.hidden = !any;
        });
        if (jump) jump.hidden = true;
        if (status) status.textContent = hits + " of " + nodes.length + ' match "' + input.value + '"';
        var empty = document.getElementById("emptyState");
        if (empty) empty.hidden = hits !== 0;
        return;
      }

      if (!results) return;
      if (!q) { results.innerHTML = ""; if (status) status.textContent = ""; return; }
      var found = idx.entries.filter(function (e) { return e.search.indexOf(q) > -1; });
      if (status) status.textContent = found.length + " of " + idx.entries.length + " match";
      if (!found.length) {
        results.innerHTML = '<div class="empty-state">Nothing matches "' + esc(input.value) + '".</div>';
        return;
      }
      results.innerHTML = found.slice(0, 60).map(function (e) {
        return '<a class="sresult" href="' + root + "c/" + e.cat + ".html#" + e.id + '">' +
          (e.parent ? '<span class="sparent">in ' + esc(e.parent) + "</span>" : "") +
          '<span class="sname">' + esc(e.name) + '</span> <span class="scat">' + esc(e.cat) + "</span>" +
          '<div class="sdesc">' + esc(e.summary || "") + "</div></a>";
      }).join("");
    }

    input.addEventListener("input", run);
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "/" && document.activeElement !== input) { ev.preventDefault(); input.focus(); }
      if (ev.key === "Escape" && document.activeElement === input) { input.value = ""; run(); input.blur(); }
    });
  }

  /* ---------- deep links ----------
     Arriving at c/ui.html#scroll-snap must OPEN that entry, not just scroll near it. */
  function openFromHash() {
    var id = decodeURIComponent((location.hash || "").slice(1));
    // The hash may name a folded satellite, which lives INSIDE an entry rather than being one.
    // Open its owner and scroll to the part, so old links and cross-reference chips keep working.
    var part = id && document.getElementById(id);
    if (part && part.classList && part.classList.contains("part")) {
      var owner = part.closest("details.entry");
      if (owner) {
        owner.open = true;
        for (var a2 = owner.parentElement; a2; a2 = a2.parentElement) {
          if (a2.tagName && a2.tagName.toLowerCase() === "details") a2.open = true;
        }
        // A setTimeout(0) here fired before the just-opened <details> had laid out, so the scroll
        // landed on a stale position. Two frames guarantees layout has settled first.
        requestAnimationFrame(function () {
          requestAnimationFrame(function () { part.scrollIntoView({ block: "center" }); });
        });
        return;
      }
    }
    if (!id) return;
    var target = document.getElementById(id);
    if (!target) return;
    if (target.tagName.toLowerCase() === "details") target.open = true;
    // A nested component sits inside its system's <details>. Opening only the target leaves it
    // inside a closed parent, so the link appears to do nothing.
    for (var anc = target.parentElement; anc; anc = anc.parentElement) {
      if (anc.tagName && anc.tagName.toLowerCase() === "details") anc.open = true;
    }
    var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    target.scrollIntoView({ block: "start", behavior: reduce ? "auto" : "smooth" });
  }

  function wireEntryHash() {
    document.addEventListener("toggle", function (ev) {
      var t = ev.target;
      if (!t || !t.classList || !t.classList.contains("entry")) return;
      var cur = decodeURIComponent((location.hash || "").slice(1));
      var curEl = cur ? document.getElementById(cur) : null;
      var curIsOwnPart = curEl && curEl.classList && curEl.classList.contains("part") &&
        curEl.closest("details.entry") === t;
      if (t.open) {
        // Opening an entry normally puts its id in the URL. But if the URL ALREADY points at a
        // folded type inside this entry, that is the more specific location and coarsening it to
        // the entry id would silently break the link someone just followed.
        if (curIsOwnPart) return;
        if (history.replaceState) history.replaceState(null, "", "#" + t.id);
      } else if (location.hash === "#" + t.id || curIsOwnPart) {
        if (history.replaceState) history.replaceState(null, "", location.pathname + location.search);
      }
    }, true);
    window.addEventListener("hashchange", openFromHash);
  }

  function wireExpandAll() {
    function setAll(open) {
      Array.prototype.forEach.call(document.querySelectorAll(".entry"), function (n) { n.open = open; });
    }
    var ex = document.getElementById("expandAll");
    var co = document.getElementById("collapseAll");
    if (ex) ex.addEventListener("click", function () { setAll(true); });
    if (co) co.addEventListener("click", function () { setAll(false); });
  }

  function wireCopy() {
    document.addEventListener("click", function (ev) {
      var b = ev.target;
      if (!b || !b.classList || !b.classList.contains("copybtn")) return;
      var pre = b.parentNode.querySelector("pre");
      if (!pre) return;
      var txt = pre.textContent;
      var done = function () {
        b.textContent = "copied";
        b.classList.add("done");
        setTimeout(function () { b.textContent = "copy"; b.classList.remove("done"); }, 1400);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(txt).then(done, function () {});
      } else {
        var ta = document.createElement("textarea");
        ta.value = txt; document.body.appendChild(ta); ta.select();
        try { document.execCommand("copy"); done(); } catch (e) {}
        document.body.removeChild(ta);
      }
    });
  }

  /* ---------- theme ----------
     The three-way CSS (light default / prefers-color-scheme / [data-theme]) already
     existed; only the control was missing, so the attribute hooks were dead code. */
  function wireTheme() {
    var btn = document.getElementById("themeBtn");
    if (!btn) return;
    function current() {
      return document.documentElement.getAttribute("data-theme") || "auto";
    }
    function paint() {
      var c = current();
      btn.textContent = c === "auto" ? "theme: auto" : c === "dark" ? "theme: dark" : "theme: light";
    }
    btn.addEventListener("click", function () {
      var order = ["auto", "light", "dark"];
      var next = order[(order.indexOf(current()) + 1) % 3];
      if (next === "auto") document.documentElement.removeAttribute("data-theme");
      else document.documentElement.setAttribute("data-theme", next);
      try {
        if (next === "auto") localStorage.removeItem("fm-theme");
        else localStorage.setItem("fm-theme", next);
      } catch (e) {}
      paint();
    });
    paint();
  }

  function wireDrawer() {
    var burger = document.getElementById("burger");
    var side = document.querySelector(".sidebar");
    var scrim = document.getElementById("scrim");
    if (!burger || !side) return;
    function set(open) {
      side.classList.toggle("open", open);
      if (scrim) scrim.classList.toggle("open", open);
    }
    burger.addEventListener("click", function () { set(!side.classList.contains("open")); });
    if (scrim) scrim.addEventListener("click", function () { set(false); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") set(false); });
  }

  function counts() {
    var t = document.getElementById("totalCount");
    var c = document.getElementById("catCount");
    var w = document.getElementById("writtenCount");
    // idx.entries deliberately still carries folded satellites so they stay searchable, but the
    // headline count should say how many ENTRIES there are, not how many public types.
    if (t) t.textContent = idx.entries.filter(function (e) { return !e.parent; }).length;
    if (c) c.textContent = idx.categories.length;
    if (w) {
      var written = idx.categories.reduce(function (a, x) { return a + (x.written || 0); }, 0);
      w.textContent = written;
    }
  }

  buildNav();
  if (page === "hub") buildHub(); else buildCategoryPage();
  counts();
  wireSearch();
  wireEntryHash();
  wireExpandAll();
  wireCopy();
  wireTheme();
  wireDrawer();
  openFromHash();
})();
