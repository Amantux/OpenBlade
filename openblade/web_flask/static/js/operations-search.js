(() => {
  const searchInputs = document.querySelectorAll("[data-table-search]");
  if (!searchInputs.length) {
    return;
  }

  const normalize = (value) => value.trim().toLowerCase();

  searchInputs.forEach((input) => {
    const tableId = input.getAttribute("data-table-search");
    if (!tableId) {
      return;
    }
    const table = document.getElementById(tableId);
    if (!table) {
      return;
    }
    const rows = Array.from(table.querySelectorAll("tbody tr[data-search-row]"));
    input.addEventListener("input", () => {
      const term = normalize(input.value || "");
      rows.forEach((row) => {
        const text = normalize(row.textContent || "");
        row.classList.toggle("hidden", Boolean(term) && !text.includes(term));
      });
    });
  });
})();
