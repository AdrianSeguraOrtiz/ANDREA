import {
  fetchFiles as fetchCommonFiles,
  renderFilePreview,
  renderFiles as renderCommonFiles,
  resetFilesView as resetCommonFilesView,
} from "/static-common/app/files/explorer.js?v=20260611a";
import { fetchFileContentData, fetchFilesData } from "../core/api.js";
import { state } from "../core/state.js";
import { renderPlanInlinePreview } from "../plan/view.js";

function fileApi(jobId) {
  return {
    fetchFiles: (mode) => fetchFilesData(jobId, mode),
    fetchFileContent: (path, mode, options) => fetchFileContentData(jobId, path, mode, options),
  };
}

function explorerOptions() {
  return {
    preferredPathSuffixes: ["merged_network_normalized.csv"],
    renderPlanPreview: (path) => renderPlanInlinePreview(state.lastPlan, path),
    renderSummary: ({ summaryEl, mode, filesCount, dirsCount }) => {
      const label = mode === "available_outputs" ? "available outputs" : `bundle=${mode}`;
      summaryEl.textContent = `${label} | files=${filesCount} | dirs=${dirsCount}`;
    },
  };
}

export { renderFilePreview };

export function resetFilesView(message) {
  resetCommonFilesView(state, message);
}

export function renderFiles(entries, mode) {
  renderCommonFiles(state, fileApi(state.jobId), entries, mode, {}, explorerOptions());
}

export async function fetchFiles(jobId) {
  state.jobId = jobId || state.jobId;
  return fetchCommonFiles(state, fileApi(state.jobId), {}, explorerOptions());
}
