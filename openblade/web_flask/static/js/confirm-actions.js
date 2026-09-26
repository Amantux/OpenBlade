(() => {
  document.querySelectorAll("[data-confirm]").forEach((button) => {
    const form = button.closest("form");
    if (!form) {
      return;
    }
    form.addEventListener("submit", (event) => {
      if (form.dataset.confirmed === "true") {
        form.dataset.confirmed = "";
        return;
      }
      const message = button.getAttribute("data-confirm") || "Confirm this action?";
      if (!window.confirm(message)) {
        event.preventDefault();
        return;
      }
      form.dataset.confirmed = "true";
    });
  });
})();

