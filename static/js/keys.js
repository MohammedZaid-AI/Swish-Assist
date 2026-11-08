// Keyboard navigation for key-chip links: any element with data-key="d" is clicked when "d" is pressed.
// Ignored while typing in a field or when a modifier key is held.
(() => {
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey || e.repeat) return;
    const t = e.target;
    if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
    const el = document.querySelector(`[data-key="${CSS.escape(e.key.toLowerCase())}"]`);
    if (el) { e.preventDefault(); el.click(); }
  });
})();
