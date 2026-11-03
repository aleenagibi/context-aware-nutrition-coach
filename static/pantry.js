/* Delegated Pantry interactions.
   Delegation is important because app.js replaces panel HTML after each AJAX action. */
(function () {
  "use strict";

  function byId(id) { return document.getElementById(id); }

  function setOpen(button, area, open) {
    if (!button || !area) return;
    area.hidden = !open;
    button.setAttribute("aria-expanded", open ? "true" : "false");
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest("#pantry-add-toggle, #custom-food-toggle");
    if (!button) return;
    event.preventDefault();

    if (button.id === "pantry-add-toggle") {
      var addArea = byId("pantry-add-area");
      var next = !addArea || addArea.hidden;
      setOpen(button, addArea, next);
      // Keep the custom-food form collapsed when switching sections.
      setOpen(byId("custom-food-toggle"), byId("custom-food-area"), false);
      if (next) {
        var search = document.querySelector(".pantry-search input");
        if (search) search.focus();
      }
    } else {
      var customArea = byId("custom-food-area");
      var customNext = !customArea || customArea.hidden;
      setOpen(button, customArea, customNext);
      setOpen(byId("pantry-add-toggle"), byId("pantry-add-area"), false);
      if (customNext) {
        var name = customArea.querySelector('input[name="name"]');
        if (name) name.focus();
      }
    }
  });

  document.addEventListener("input", function (event) {
    var search = event.target.closest(".pantry-search input");
    if (!search) return;

    var query = search.value.trim().toLocaleLowerCase();
    var area = byId("pantry-catalogue");
    var addArea = byId("pantry-add-area");
    if (!area) return;

    area.querySelectorAll(".pantry-category").forEach(function (group) {
      var visible = 0;
      group.querySelectorAll(".pantry-item").forEach(function (item) {
        var label = (item.dataset.text || item.textContent || "").toLocaleLowerCase();
        var matches = !query || label.indexOf(query) !== -1;
        item.hidden = !matches;
        if (matches) visible += 1;
      });
      group.hidden = visible === 0;
      if (query && visible) group.open = true;
      if (!query) group.open = false;
    });

    if (query) {
      setOpen(byId("pantry-add-toggle"), addArea, true);
      setOpen(byId("custom-food-toggle"), byId("custom-food-area"), false);
    }
  });
})();
