const $ = (id) => document.getElementById(id);

const dropzone = $("dropzone");
const fileInput = $("file-input");
const pickBtn = $("pick-btn");
const fileInfo = $("file-info");
const fileIcon = $("file-icon");
const fileNameEl = $("file-name");
const fileSubEl = $("file-sub");
const previewWrap = $("preview-wrap");
const preview = $("preview");
const clearBtn = $("clear-btn");
const runBtn = $("run-btn");
const runLabel = runBtn.querySelector(".btn__label");
const spinner = runBtn.querySelector(".spinner");
const promptEl = $("prompt");
const modelSelect = $("model-select");
const dpiSelect = $("dpi-select");
const statusEl = $("status");
const output = $("output");
const copyBtn = $("copy-btn");
const downloadBtn = $("download-btn");
const cancelBtn = $("cancel-btn");
const deleteBtn = $("delete-btn");
const epubBtn = $("epub-btn");
const modelBadge = $("model-badge");
const healthBadge = $("health-badge");
const progressWrap = $("progress-wrap");
const progressFill = $("progress-fill");
const progressText = $("progress-text");
const uploadProgress = $("upload-progress");
const uploadFill = $("upload-fill");
const uploadText = $("upload-text");
const jobsList = $("jobs-list");
const jobDetail = $("job-detail");
const detailName = $("detail-name");
const detailMeta = $("detail-meta");
const refreshJobsBtn = $("refresh-jobs");
const previewNotice = $("preview-notice");
const historyBtn = $("history-btn");
const historyModal = $("history-modal");
const historyList = $("history-list");
const filterButtons = document.querySelectorAll(".filter-btn");
let historyFilter = "all";

let currentFile = null;
let activeJobId = null;
let pollTimer = null;
let lastTextSnapshot = "";
let lastSeenPage = -1;

const STATUS_LABEL = {
  queued: "Menunggu",
  running: "Memproses",
  done: "Selesai",
  error: "Gagal",
  canceled: "Dibatalkan",
};

function setStatus(msg, kind = "") {
  statusEl.textContent = msg || "";
  statusEl.className = "status" + (kind ? " is-" + kind : "");
}

function setBusy(busy) {
  runBtn.disabled = busy || !currentFile;
  spinner.hidden = !busy;
  runLabel.textContent = busy ? "Mengunggah…" : "Mulai OCR";
  fileInput.disabled = busy;
  clearBtn.disabled = busy;
}

function fmtBytes(n) {
  if (!n && n !== 0) return "";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) {
    n /= 1024;
    i++;
  }
  return `${n.toFixed(n >= 10 || i === 0 ? 0 : 1)} ${u[i]}`;
}

function fmtTime(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return d.toLocaleString();
}

function setFile(file) {
  currentFile = file || null;
  if (!file) {
    fileInfo.hidden = true;
    previewWrap.hidden = true;
    preview.removeAttribute("src");
    runBtn.disabled = true;
    return;
  }
  const isPdf =
    file.type === "application/pdf" || /\.pdf$/i.test(file.name || "");

  fileNameEl.textContent = file.name || (isPdf ? "document.pdf" : "image");
  fileSubEl.textContent = `${isPdf ? "PDF" : "Gambar"} · ${fmtBytes(file.size)}`;
  fileIcon.textContent = isPdf ? "📕" : "🖼️";
  fileInfo.hidden = false;

  if (!isPdf) {
    preview.src = URL.createObjectURL(file);
    previewWrap.hidden = false;
  } else {
    previewWrap.hidden = true;
    preview.removeAttribute("src");
  }

  runBtn.disabled = false;
  setStatus("");
  uploadProgress.hidden = true;
}

pickBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  fileInput.click();
});
dropzone.addEventListener("click", () => fileInput.click());

fileInput.addEventListener("change", (e) => {
  const f = e.target.files && e.target.files[0];
  if (f) setFile(f);
});

["dragenter", "dragover"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.add("is-drag");
  })
);
["dragleave", "drop"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.remove("is-drag");
  })
);
dropzone.addEventListener("drop", (e) => {
  const f = e.dataTransfer.files && e.dataTransfer.files[0];
  if (!f) return;
  const ok =
    f.type.startsWith("image/") ||
    f.type === "application/pdf" ||
    /\.pdf$/i.test(f.name || "");
  if (ok) setFile(f);
  else setStatus("Hanya menerima file gambar atau PDF", "err");
});

clearBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  fileInput.value = "";
  setFile(null);
});

function uploadWithProgress(url, formData) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.responseType = "json";
    xhr.upload.addEventListener("progress", (e) => {
      if (!e.lengthComputable) return;
      const pct = Math.round((e.loaded / e.total) * 100);
      uploadProgress.hidden = false;
      uploadFill.style.width = pct + "%";
      uploadText.textContent =
        pct < 100
          ? `Mengunggah ${pct}% (${fmtBytes(e.loaded)} / ${fmtBytes(e.total)})`
          : "Selesai diunggah, server mulai memproses…";
    });
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(xhr.response);
      } else {
        const detail =
          (xhr.response && xhr.response.detail) ||
          xhr.statusText ||
          "Upload failed";
        reject(new Error(detail));
      }
    };
    xhr.onerror = () => reject(new Error("Network error"));
    xhr.onabort = () => reject(new Error("Upload aborted"));
    xhr.send(formData);
  });
}

runBtn.addEventListener("click", async () => {
  if (!currentFile) return;
  setBusy(true);
  setStatus("Mengunggah…");
  uploadProgress.hidden = false;
  uploadFill.style.width = "0%";
  uploadText.textContent = "Mengunggah…";

  const fd = new FormData();
  fd.append("file", currentFile);
  if (promptEl.value.trim()) fd.append("prompt", promptEl.value.trim());
  if (modelSelect.value) fd.append("model", modelSelect.value);
  if (dpiSelect.value) fd.append("dpi", dpiSelect.value);

  try {
    const job = await uploadWithProgress("/api/jobs", fd);
    setStatus(
      `Job dibuat (${job.id}) · ${job.total_pages} halaman, server mulai memproses.`,
      "ok"
    );
    fileInput.value = "";
    setFile(null);
    await refreshJobs();
    selectJob(job.id);
  } catch (e) {
    setStatus(e.message || "Upload gagal", "err");
  } finally {
    setBusy(false);
    setTimeout(() => {
      uploadProgress.hidden = true;
    }, 800);
  }
});

// --- Jobs ------------------------------------------------------------------

async function fetchJobs() {
  const r = await fetch("/api/jobs");
  if (!r.ok) throw new Error("Gagal memuat job");
  return (await r.json()).jobs || [];
}

async function refreshJobs() {
  try {
    const jobs = await fetchJobs();
    renderJobs(jobs);
  } catch (e) {
    jobsList.innerHTML = `<div class="job-empty">${e.message}</div>`;
  }
}

function renderJobs(jobs) {
  if (!jobs.length) {
    jobsList.innerHTML =
      '<div class="job-empty">Belum ada job. Upload PDF atau gambar di kiri.</div>';
    return;
  }
  jobsList.innerHTML = "";
  jobs.forEach((j) => {
    const el = document.createElement("button");
    el.type = "button";
    el.className = "job" + (j.id === activeJobId ? " is-active" : "");
    const pct =
      j.total_pages > 0 ? Math.round((j.done_pages / j.total_pages) * 100) : 0;
    const statusKlass = "job__status job__status--" + j.status;
    el.innerHTML = `
      <div class="job__row">
        <div class="job__name" title="${escapeHtml(j.filename)}">${escapeHtml(
      j.filename || j.id
    )}</div>
        <span class="${statusKlass}">${STATUS_LABEL[j.status] || j.status}</span>
      </div>
      <div class="job__row job__row--sub">
        <span>${j.kind === "pdf" ? "PDF" : "Gambar"} · ${j.done_pages}/${
      j.total_pages
    } hal · ${pct}%</span>
        <span>${fmtTime(j.created_at)}</span>
      </div>
      <div class="progress__bar progress__bar--mini">
        <div class="progress__fill" style="width:${pct}%"></div>
      </div>
    `;
    el.addEventListener("click", () => selectJob(j.id));
    jobsList.appendChild(el);
  });
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[c])
  );
}

async function selectJob(id) {
  activeJobId = id;
  jobDetail.hidden = false;
  output.value = "";
  lastTextSnapshot = "";
  lastSeenPage = -1;
  copyBtn.disabled = true;
  downloadBtn.disabled = true;
  epubBtn.disabled = true;
  previewNotice.hidden = true;
  await pollOnce();
  startPolling();
  // refresh list to show active highlight
  refreshJobs();
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(pollOnce, 2000);
}
function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function pollOnce() {
  if (!activeJobId) return;
  try {
    // While the job is still running, only fetch a small tail window
    // so we don't re-download the entire book on every poll. After it
    // finishes we fetch once for the complete text (or rely on the
    // download endpoint if the user wants to export).
    const job = await fetch(`/api/jobs/${activeJobId}`).then((r) =>
      r.ok ? r.json() : null
    );
    if (!job) {
      stopPolling();
      jobDetail.hidden = true;
      activeJobId = null;
      refreshJobs();
      return;
    }

    detailName.textContent = activeJobId;
    detailMeta.textContent =
      `${STATUS_LABEL[job.status] || job.status} · ${job.done_pages}/${job.total_pages} halaman` +
      (job.failed_pages ? ` · ${job.failed_pages} gagal` : "");

    const pct =
      job.total_pages > 0
        ? Math.round((job.done_pages / job.total_pages) * 100)
        : 0;
    progressFill.style.width = pct + "%";
    progressText.textContent = `${job.done_pages} / ${job.total_pages} halaman (${pct}%)`;

    const finished = ["done", "error", "canceled"].includes(job.status);

    if (job.done_pages !== lastSeenPage || finished) {
      let qs;
      if (finished && job.total_pages <= 200) {
        qs = ""; // small enough to render whole text
      } else if (finished) {
        qs = "?tail=20"; // big book finished: only show tail in textarea
        previewNotice.hidden = false;
      } else {
        qs = "?tail=10"; // running: live preview of recent pages
        previewNotice.hidden = job.total_pages <= 200;
      }
      const r = await fetch(`/api/jobs/${activeJobId}/text${qs}`);
      if (r.ok) {
        const d = await r.json();
        if (d.text !== lastTextSnapshot) {
          const atBottom =
            output.scrollTop + output.clientHeight >=
            output.scrollHeight - 4;
          output.value = d.text || "";
          lastTextSnapshot = d.text || "";
          if (atBottom) output.scrollTop = output.scrollHeight;
        }
        copyBtn.disabled = !d.text;
        downloadBtn.disabled = !d.text;
        epubBtn.disabled = !d.text;
      }
      lastSeenPage = job.done_pages;
    }

    cancelBtn.hidden = !(job.status === "running" || job.status === "queued");

    if (finished) {
      stopPolling();
      refreshJobs();
    }
  } catch {
    // soft-fail; next tick will retry
  }
}

cancelBtn.addEventListener("click", async () => {
  if (!activeJobId) return;
  cancelBtn.disabled = true;
  try {
    await fetch(`/api/jobs/${activeJobId}/cancel`, { method: "POST" });
    await pollOnce();
  } finally {
    cancelBtn.disabled = false;
  }
});

deleteBtn.addEventListener("click", async () => {
  if (!activeJobId) return;
  if (!confirm("Hapus job ini beserta hasilnya?")) return;
  try {
    await fetch(`/api/jobs/${activeJobId}`, { method: "DELETE" });
    stopPolling();
    activeJobId = null;
    jobDetail.hidden = true;
    output.value = "";
    refreshJobs();
  } catch {}
});

copyBtn.addEventListener("click", async () => {
  if (!output.value) return;
  try {
    await navigator.clipboard.writeText(output.value);
    setStatus("Disalin ke clipboard", "ok");
  } catch {
    setStatus("Gagal menyalin", "err");
  }
});

downloadBtn.addEventListener("click", () => {
  if (!activeJobId) return;
  window.location.href = `/api/jobs/${activeJobId}/download`;
});

epubBtn.addEventListener("click", () => {
  if (!activeJobId) return;
  window.location.href = `/api/jobs/${activeJobId}/epub`;
});

refreshJobsBtn.addEventListener("click", refreshJobs);

// --- History modal --------------------------------------------------------

historyBtn.addEventListener("click", openHistory);
historyModal.addEventListener("click", (e) => {
  if (e.target.dataset && e.target.dataset.close !== undefined) {
    closeHistory();
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !historyModal.hidden) closeHistory();
});
filterButtons.forEach((b) =>
  b.addEventListener("click", () => {
    filterButtons.forEach((x) => x.classList.remove("is-active"));
    b.classList.add("is-active");
    historyFilter = b.dataset.filter;
    renderHistory();
  })
);

let historyJobs = [];
async function openHistory() {
  historyModal.hidden = false;
  historyList.innerHTML = '<div class="job-empty">Memuat…</div>';
  try {
    historyJobs = await fetchJobs();
    renderHistory();
  } catch (e) {
    historyList.innerHTML = `<div class="job-empty">${e.message}</div>`;
  }
}
function closeHistory() {
  historyModal.hidden = true;
}
function renderHistory() {
  const filtered =
    historyFilter === "all"
      ? historyJobs
      : historyFilter === "running"
      ? historyJobs.filter(
          (j) => j.status === "running" || j.status === "queued"
        )
      : historyJobs.filter((j) => j.status === historyFilter);

  if (!filtered.length) {
    historyList.innerHTML = '<div class="job-empty">Tidak ada job.</div>';
    return;
  }
  historyList.innerHTML = "";
  filtered.forEach((j) => {
    const pct =
      j.total_pages > 0
        ? Math.round((j.done_pages / j.total_pages) * 100)
        : 0;
    const el = document.createElement("button");
    el.type = "button";
    el.className = "job";
    el.innerHTML = `
      <div class="job__row">
        <div class="job__name" title="${escapeHtml(j.filename)}">${escapeHtml(
      j.filename || j.id
    )}</div>
        <span class="job__status job__status--${j.status}">${
      STATUS_LABEL[j.status] || j.status
    }</span>
      </div>
      <div class="job__row job__row--sub">
        <span>${j.kind === "pdf" ? "PDF" : "Gambar"} · ${j.done_pages}/${
      j.total_pages
    } hal · ${j.model || ""}</span>
        <span>${fmtTime(j.created_at)}</span>
      </div>
      <div class="progress__bar progress__bar--mini">
        <div class="progress__fill" style="width:${pct}%"></div>
      </div>
    `;
    el.addEventListener("click", () => {
      closeHistory();
      selectJob(j.id);
    });
    historyList.appendChild(el);
  });
}

async function loadHealth() {
  try {
    const r = await fetch("/api/health");
    const d = await r.json();
    healthBadge.textContent =
      "ollama: " + d.ollama_host.replace(/^https?:\/\//, "");
    healthBadge.className = "badge badge--ok";
    modelBadge.textContent = "model: " + d.default_model;
  } catch {
    healthBadge.textContent = "offline";
    healthBadge.className = "badge badge--err";
  }
}

async function loadModels() {
  try {
    const r = await fetch("/api/models");
    const d = await r.json();
    modelSelect.innerHTML = "";
    const names = d.models || [];
    if (d.default && !names.includes(d.default)) names.unshift(d.default);
    if (names.length === 0) names.push("(no models)");
    names.forEach((n) => {
      const opt = document.createElement("option");
      opt.value = n;
      opt.textContent = n;
      if (n === d.default) opt.selected = true;
      modelSelect.appendChild(opt);
    });
  } catch {
    modelSelect.innerHTML = '<option value="">(unavailable)</option>';
  }
}

loadHealth();
loadModels();
refreshJobs();
// Re-list jobs every 5s so background progress shows up even when no job is selected
setInterval(() => {
  // light refresh; skip if a poll for active job is already covering it
  fetchJobs().then(renderJobs).catch(() => {});
}, 5000);
