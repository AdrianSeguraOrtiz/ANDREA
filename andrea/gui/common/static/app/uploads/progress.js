function clampPercent(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) {
    return 0;
  }
  return Math.max(0, Math.min(100, n));
}

function parseJsonPayload(text) {
  if (!text) {
    return null;
  }
  try {
    return JSON.parse(text);
  } catch (_err) {
    return null;
  }
}

function fileItemsWithOffsets(fileItems) {
  let offset = 0;
  return fileItems.map((item) => {
    const size = Math.max(0, Number(item.file?.size || 0));
    const result = { ...item, size, offset };
    offset += size;
    return result;
  });
}

function setItemState(item, { state, label, percent }) {
  item.row?.classList.remove("uploading", "uploaded", "validating", "failed");
  if (state) {
    item.row?.classList.add(state);
  }
  if (item.status) {
    item.status.textContent = label || state || "";
  }
  if (item.fill) {
    item.fill.style.width = `${clampPercent(percent)}%`;
  }
  if (item.percent) {
    item.percent.textContent = `${Math.round(clampPercent(percent))}%`;
  }
}

function markAll(fileItems, state, label, percent) {
  for (const item of fileItems) {
    setItemState(item, { state, label, percent });
  }
}

function updateWeightedProgress(fileItems, event) {
  const totalFileBytes = fileItems.reduce((sum, item) => sum + item.size, 0);
  if (totalFileBytes <= 0) {
    const percent = event.lengthComputable ? (event.loaded / event.total) * 100 : 0;
    markAll(fileItems, "uploading", "Uploading", percent);
    return percent;
  }
  const loadedFileBytes = event.lengthComputable && event.total > 0
    ? (event.loaded / event.total) * totalFileBytes
    : Math.min(event.loaded, totalFileBytes);
  for (const item of fileItems) {
    const localLoaded = Math.max(0, Math.min(item.size, loadedFileBytes - item.offset));
    const percent = item.size > 0 ? (localLoaded / item.size) * 100 : 100;
    setItemState(item, { state: "uploading", label: "Uploading", percent });
  }
  return (loadedFileBytes / totalFileBytes) * 100;
}

export function resetUploadProgress(fileItems) {
  for (const item of fileItems) {
    setItemState(item, { state: "", label: item.idleLabel || "Waiting", percent: 0 });
  }
}

export function uploadFormDataWithProgress({
  url,
  formData,
  fileItems,
  overallItem = null,
  overallUploadWeight = 20,
  overallProcessingLimit = 45,
  overallCompleteOnLoad = true,
  method = "POST",
  onServerProcessing = null,
}) {
  const items = fileItemsWithOffsets(fileItems.filter((item) => item.file));
  const uploadWeight = clampPercent(overallUploadWeight);
  const processingStart = uploadWeight;
  const processingLimit = Math.max(processingStart, clampPercent(overallProcessingLimit));
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();

    const setOverall = ({ state, label, percent }) => {
      if (!overallItem) {
        return;
      }
      setItemState(overallItem, { state, label, percent });
    };

    xhr.open(method, url);
    xhr.setRequestHeader("Accept", "application/json");

    xhr.upload.onloadstart = () => {
      markAll(items, "uploading", "Uploading", 0);
      setOverall({ state: "uploading", label: "Uploading ZIPs", percent: 0 });
    };
    xhr.upload.onprogress = (event) => {
      const uploadPercent = updateWeightedProgress(items, event);
      setOverall({
        state: "uploading",
        label: "Uploading ZIPs",
        percent: clampPercent(uploadPercent) * (uploadWeight / 100),
      });
    };
    xhr.upload.onload = () => {
      markAll(items, "validating", "Saving on server", 100);
      setOverall({
        state: "validating",
        label: "Saving uploads on server",
        percent: processingLimit,
      });
      if (typeof onServerProcessing === "function") {
        onServerProcessing();
      }
    };
    xhr.onerror = () => {
      markAll(items, "failed", "Upload failed", 0);
      setOverall({ state: "failed", label: "Upload failed", percent: 0 });
      reject(new Error("Upload failed before the server could validate the ZIPs."));
    };
    xhr.onabort = () => {
      markAll(items, "failed", "Upload aborted", 0);
      setOverall({ state: "failed", label: "Upload aborted", percent: 0 });
      reject(new Error("Upload was aborted."));
    };
    xhr.onload = () => {
      const payload = parseJsonPayload(xhr.responseText);
      if (xhr.status >= 200 && xhr.status < 300) {
        markAll(items, "uploaded", "Validated", 100);
        if (overallCompleteOnLoad) {
          setOverall({ state: "uploaded", label: "Ready", percent: 100 });
        } else {
          setOverall({
            state: "validating",
            label: "Starting job",
            percent: processingLimit,
          });
        }
        resolve(payload || {});
        return;
      }
      const message = payload?.detail || `Request failed (${xhr.status})`;
      markAll(items, "failed", "Failed", 100);
      setOverall({ state: "failed", label: "Failed", percent: 100 });
      reject(new Error(message));
    };
    xhr.send(formData);
  });
}
