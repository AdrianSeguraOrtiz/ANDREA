async function readJson(response, fallbackMessage) {
  let payload = null;
  try {
    payload = await response.json();
  } catch (_err) {
    payload = null;
  }
  if (!response.ok) {
    throw new Error(payload?.detail || fallbackMessage || `Request failed (${response.status})`);
  }
  return payload;
}

export async function fetchBootstrapData() {
  const response = await fetch("/api/infer-network/bootstrap");
  return readJson(response, `Failed to load bootstrap data (${response.status})`);
}

export async function submitPreflightRequest(formData) {
  const response = await fetch("/api/infer-network/preflight", {
    method: "POST",
    body: formData,
  });
  return readJson(response, "Preflight submission failed");
}

export async function submitPlanRequest(body) {
  const response = await fetch("/api/infer-network/plan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return readJson(response, "Plan submission failed");
}

export async function submitRunRequest(body) {
  const response = await fetch("/api/infer-network/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return readJson(response, "Run submission failed");
}

export async function fetchJobData(jobId) {
  const response = await fetch(`/api/infer-network/jobs/${jobId}`);
  return readJson(response, `Failed to load job status (${response.status})`);
}

export async function fetchPlanData(jobId) {
  const response = await fetch(`/api/infer-network/jobs/${jobId}/plan`);
  return readJson(response, `Failed to load plan (${response.status})`);
}

export async function fetchFilesData(jobId, bundleId) {
  const response = await fetch(`/api/infer-network/jobs/${jobId}/files?bundle_id=${encodeURIComponent(bundleId)}`);
  return readJson(response, `Failed to load files (${response.status})`);
}

export async function fetchFileContentData(jobId, virtualPath, bundleId, options = {}) {
  const response = await fetch(
    `/api/infer-network/jobs/${jobId}/file-content?bundle_id=${encodeURIComponent(bundleId)}&path=${encodeURIComponent(
      virtualPath
    )}`,
    {
      signal: options.signal,
    }
  );
  return readJson(response, `Failed to load file preview (${response.status})`);
}
