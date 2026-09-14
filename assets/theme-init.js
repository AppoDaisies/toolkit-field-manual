/* Inlined into the <head> of every page, deliberately.
   As an external <script src> this runs after first paint, which produces a visible
   flash of the wrong theme on every navigation. It must be tiny and synchronous. */
(function () {
  try {
    var t = localStorage.getItem("fm-theme");
    if (t === "light" || t === "dark") {
      document.documentElement.setAttribute("data-theme", t);
    }
  } catch (e) {
    /* private mode / blocked storage: fall back to the OS preference, which the
       CSS already handles via prefers-color-scheme. */
  }
})();
