(() => {
  const form = document.getElementById("register-device-form");
  if (!form) {
    return;
  }

  const connectionField = form.querySelector('input[name="connection_url"]');
  const usernameField = form.querySelector('input[name="device_username"]');
  const passwordField = form.querySelector('input[name="device_password"]');
  const statusNode = document.getElementById("register-probe-status");
  if (!connectionField) {
    return;
  }

  const runProbe = async () => {
    const csrfNode = document.querySelector('meta[name="csrf-token"]');
    const token = csrfNode ? csrfNode.getAttribute("content") : "";
    const formData = new FormData();
    formData.set("connection_url", connectionField.value);
    if (usernameField) {
      formData.set("device_username", usernameField.value);
    }
    if (passwordField) {
      formData.set("device_password", passwordField.value);
    }
    formData.set("csrf_token", token || "");
    try {
      const response = await fetch("/devices/probe", {
        method: "POST",
        body: formData,
        credentials: "same-origin",
      });
      if (!statusNode) {
        return;
      }
      if (response.ok) {
        statusNode.textContent = "Connection probe passed.";
        statusNode.classList.remove("error");
      } else {
        const payload = await response.json().catch(() => ({}));
        statusNode.textContent = payload.detail || "Connection probe failed.";
        statusNode.classList.add("error");
      }
    } catch (_err) {
      if (statusNode) {
        statusNode.textContent = "Connection probe unavailable.";
        statusNode.classList.add("error");
      }
    }
  };
  connectionField.addEventListener("blur", runProbe);
  if (usernameField) {
    usernameField.addEventListener("blur", runProbe);
  }
})();
