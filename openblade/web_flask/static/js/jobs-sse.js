(() => {
  const page = document.body.dataset.page;
  if (page !== "nas-jobs") {
    return;
  }

  const summaryNode = document.getElementById("jobs-summary");
  const warningNode = document.getElementById("jobs-sse-warning");
  const updateNode = document.getElementById("jobs-last-update");
  if (!summaryNode || !warningNode || !updateNode) {
    return;
  }

  let retryDelayMs = 1000;
  let source = null;

  function showWarning(visible) {
    warningNode.classList.toggle("hidden", !visible);
  }

  function connect() {
    source = new EventSource("/events/jobs");

    source.addEventListener("jobs.snapshot", (event) => {
      try {
        const payload = JSON.parse(event.data);
        ["total", "running", "pending", "failed"].forEach((key) => {
          const node = summaryNode.querySelector(`[data-jobs-summary="${key}"]`);
          if (node && typeof payload[key] === "number") {
            node.textContent = String(payload[key]);
          }
        });
        updateNode.textContent = payload.updated_at
          ? new Date(Number(payload.updated_at) * 1000).toLocaleTimeString()
          : "just now";
        showWarning(false);
        retryDelayMs = 1000;
      } catch (_err) {
        showWarning(true);
      }
    });

    source.addEventListener("jobs.error", () => {
      showWarning(true);
    });

    source.onerror = () => {
      showWarning(true);
      if (source) {
        source.close();
      }
      setTimeout(connect, retryDelayMs);
      retryDelayMs = Math.min(retryDelayMs * 2, 30000);
    };
  }

  connect();
})();

