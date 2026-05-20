import {
  closeReproducibilityStepsModal,
  initReproducibility,
  renderReproducibility as renderCommonReproducibility,
  resetReproducibility as resetCommonReproducibility,
} from "/static-common/app/repro/view.js?v=20260428a";
import { $ } from "../core/dom.js";

export {
  closeReproducibilityStepsModal,
  initReproducibility,
};

function renderReproducibilityPlaceholder(message) {
  const placeholder = $("reproducibility-placeholder");
  if (!placeholder || placeholder.hidden) {
    return;
  }
  placeholder.replaceChildren();
  const title = document.createElement("div");
  title.className = "results-placeholder-title";
  title.textContent = "Reproducibility";
  const body = document.createElement("div");
  body.className = "results-placeholder-message";
  body.textContent = String(
    message || "Reproducibility snippets will be available after execution."
  );
  placeholder.append(title, body);
}

export function resetReproducibility(
  message = "Reproducibility snippets will be available after execution."
) {
  resetCommonReproducibility(message);
  renderReproducibilityPlaceholder(message);
}

export function renderReproducibility(payload = null) {
  renderCommonReproducibility(payload);
  if (!payload || !payload.available) {
    renderReproducibilityPlaceholder(
      String(payload?.message || "").trim()
        || "Reproducibility snippets will be available after execution."
    );
  }
}
