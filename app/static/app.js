(() => {
  const CHUNK = 8 * 1024 * 1024;
  const $ = (id) => document.getElementById(id);
  let videos = [];          // 화면에 보이는 순서 그대로
  let titleEdited = false;

  // ---------- 유틸 ----------
  const fmtSize = (b) => b >= 1e9 ? (b / 1e9).toFixed(2) + " GB" : (b / 1e6).toFixed(1) + " MB";
  const fmtDur = (s) => {
    s = Math.round(s);
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0");
  };
  const esc = (s) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  async function api(url, opt) {
    const r = await fetch(url, opt);
    if (r.status === 401) { location.href = "/login"; throw new Error("로그인 필요"); }
    if (!r.ok) {
      let msg = r.statusText;
      try { msg = (await r.json()).detail || msg; } catch (_) {}
      throw new Error(msg);
    }
    return r.json();
  }

  // ---------- 순서/렌더 ----------
  function sortByTime() {
    videos.sort((a, b) => (a.start_ts == null) - (b.start_ts == null) || (a.start_ts ?? 0) - (b.start_ts ?? 0) || a.name.localeCompare(b.name));
  }

  function outOfOrder(i) {
    // 앞 영상보다 촬영시간이 더 이른 경우 표시
    const cur = videos[i], prev = videos[i - 1];
    return prev && cur.start_ts != null && prev.start_ts != null && cur.start_ts < prev.start_ts;
  }

  function render() {
    const list = $("list");
    list.innerHTML = videos.map((v, i) => `
      <li class="item" data-id="${v.id}">
        <input class="num" type="number" min="1" max="${videos.length}" value="${i + 1}" inputmode="numeric" aria-label="순서">
        <div class="info">
          <div class="name">${esc(v.name)}</div>
          <div class="time ${v.start_display ? "" : "none"}">
            ${v.start_display ? "🎬 " + v.start_display : "촬영시간 정보 없음"}
            ${v.start_source === "file" ? '<span class="tag" title="영상에 촬영시간이 없어 파일 수정시간으로 표시">추정</span>' : ""}
            ${outOfOrder(i) ? '<span class="tag warn" title="앞 영상보다 촬영시간이 빠릅니다">순서 확인</span>' : ""}
          </div>
          <div class="meta">${fmtDur(v.duration)} · ${v.width}×${v.height} · ${fmtSize(v.size)}</div>
        </div>
        <button class="del" type="button" aria-label="삭제">✕</button>
      </li>`).join("");

    const has = videos.length > 0;
    $("listhead").hidden = $("hint").hidden = $("publish").hidden = !has;
    $("count").textContent = videos.length;
    $("total").textContent = fmtSize(videos.reduce((s, v) => s + v.size, 0));
    updateTitle();
  }

  function updateTitle() {
    if (titleEdited) return;
    // 실제 촬영 메타데이터가 있는 영상의 날짜를 우선, 없으면 추정(파일 수정시간) 사용
    const real = videos.filter((v) => v.start_source === "metadata");
    const dates = (real.length ? real : videos).map((v) => v.start_date).filter(Boolean).sort();
    const d = dates[0] || new Date().toISOString().slice(0, 10).replaceAll("-", "");
    $("title").value = `${d} PIR풋`;
  }
  $("title").addEventListener("input", () => { titleEdited = true; });

  function move(id, pos) {
    const from = videos.findIndex((v) => v.id === id);
    const to = Math.max(0, Math.min(videos.length - 1, pos));
    const [x] = videos.splice(from, 1);
    videos.splice(to, 0, x);
    render();
  }

  $("list").addEventListener("change", (e) => {
    if (!e.target.classList.contains("num")) return;
    const n = parseInt(e.target.value, 10);
    const id = e.target.closest(".item").dataset.id;
    if (Number.isNaN(n)) return render();
    move(id, n - 1);
  });
  $("list").addEventListener("click", async (e) => {
    if (!e.target.classList.contains("del")) return;
    const li = e.target.closest(".item");
    const v = videos.find((x) => x.id === li.dataset.id);
    if (!confirm(`"${v.name}" 을(를) 목록에서 삭제할까요?`)) return;
    await api("/api/videos/" + v.id, { method: "DELETE" });
    videos = videos.filter((x) => x.id !== v.id);
    render();
  });
  $("sortTime").addEventListener("click", () => { sortByTime(); render(); });

  Sortable.create($("list"), {
    animation: 150,
    filter: ".num,.del",
    preventOnFilter: false,
    delay: 150,            // 모바일에서 스크롤과 구분: 잠깐 누르면 드래그
    delayOnTouchOnly: true,
    ghostClass: "ghost-item",
    onEnd: () => {
      const order = [...$("list").children].map((li) => li.dataset.id);
      videos.sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id));
      render();
    },
  });

  // ---------- 업로드 ----------
  const queue = $("queue");
  const pending = [];
  let running = false;

  function addFiles(files) {
    for (const f of files) {
      const el = document.createElement("div");
      el.className = "qitem";
      el.innerHTML = `<div class="qname">${esc(f.name)}</div><div class="bar"><i></i></div><div class="qmsg">대기 중</div>`;
      queue.appendChild(el);
      pending.push({ file: f, el });
    }
    if (!running) runQueue();
  }

  async function runQueue() {
    running = true;
    while (pending.length) {
      const { file, el } = pending.shift();
      try {
        const v = await uploadFile(file, el);
        if (!videos.some((x) => x.id === v.id)) {
          videos.push(v);
          sortByTime();
          render();
        }
        el.classList.add("ok");
        el.querySelector(".qmsg").textContent = "완료";
        setTimeout(() => el.remove(), 1500);
      } catch (err) {
        el.classList.add("fail");
        el.querySelector(".qmsg").textContent = "실패: " + err.message;
      }
    }
    running = false;
  }

  async function postChunk(fd, tries = 4) {
    for (let t = 1; ; t++) {
      try { return await api("/api/videos/chunk", { method: "POST", body: fd }); }
      catch (err) {
        if (t >= tries || /형식|조각|크기|읽을 수/.test(err.message)) throw err;
        await new Promise((r) => setTimeout(r, 1000 * t));   // 네트워크 끊김 재시도
      }
    }
  }

  async function uploadFile(file, el) {
    const bar = el.querySelector(".bar i"), msg = el.querySelector(".qmsg");
    const key = `${file.name}|${file.size}|${file.lastModified}`;
    const total = Math.max(1, Math.ceil(file.size / CHUNK));
    const base = { key, total, name: file.name, size: file.size, last_modified: file.lastModified };

    const st = await api(`/api/videos/status?key=${encodeURIComponent(key)}&name=${encodeURIComponent(file.name)}`);
    if (st.complete) { bar.style.width = "100%"; return st.video; }
    const have = new Set(st.received);

    let result = null, sent = 0;
    for (let i = 0; i < total; i++) {
      const blob = file.slice(i * CHUNK, (i + 1) * CHUNK);
      if (have.has(i)) { sent += blob.size; continue; }   // 이어받기
      const fd = new FormData();
      for (const [k, v] of Object.entries(base)) fd.append(k, v);
      fd.append("index", i);
      fd.append("chunk", blob, "chunk");
      msg.textContent = i === total - 1 ? "처리 중…" : "업로드 중";
      const r = await postChunk(fd);
      sent += blob.size;
      bar.style.width = Math.round((sent / file.size) * 100) + "%";
      msg.textContent = Math.round((sent / file.size) * 100) + "%";
      if (r.done) result = r.video;
    }
    if (!result) {   // 조각은 다 있었는데 조립만 안 된 경우
      const fd = new FormData();
      for (const [k, v] of Object.entries(base)) fd.append(k, v);
      result = (await api("/api/videos/finalize", { method: "POST", body: fd })).video;
    }
    return result;
  }

  // ---------- 입력: 클릭 / 드래그 ----------
  $("file").addEventListener("change", (e) => { addFiles([...e.target.files]); e.target.value = ""; });
  const drop = $("drop");
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => addFiles([...e.dataTransfer.files].filter((f) => f.type.startsWith("video/") || /\.(mp4|mov|m4v|mkv|avi|webm|mts|m2ts|3gp)$/i.test(f.name))));
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => e.preventDefault());

  // ---------- 유튜브 연결 / 병합+업로드 ----------
  let ytOk = false;
  function showYt() {
    $("yt").innerHTML = ytOk
      ? '<span class="ok">✔ 유튜브 연결됨</span>'
      : '<span class="no">유튜브가 연결되지 않았습니다.</span> <a class="btn" href="/oauth/start">유튜브 연결</a>';
  }
  api("/api/youtube/status").then((s) => { ytOk = s.connected; showYt(); });

  const STAGE = { merge: "영상 병합 중", upload: "유튜브 업로드 중", ready: "병합 완료 (미리 보기)", done: "완료", error: "오류" };
  let polling = null, currentJob = null, videoShownFor = null;

  function showJob(j) {
    currentJob = j;
    $("job").hidden = j.stage === "none";
    if (j.stage === "none") return;
    const busy = j.stage === "merge" || j.stage === "upload";
    $("jobStage").textContent = STAGE[j.stage] || j.stage;
    $("jobPct").textContent = busy ? Math.round(j.progress * 100) + "%" : "";
    $("jobBar").style.width = Math.round((j.progress || 0) * 100) + "%";
    $("jobMsg").textContent = j.message || "";
    $("jobMsg").style.color = j.stage === "error" || /실패/.test(j.message || "") ? "#b91c1c" : "";

    const ready = j.stage === "ready";
    $("jobReady").hidden = !ready;
    $("jobVideo").hidden = !ready;
    if (ready && videoShownFor !== j.id) {   // 폴링 때마다 플레이어를 다시 만들지 않음
      $("jobVideo").src = `/api/jobs/${j.id}/video`;
      videoShownFor = j.id;
    }
    if (!ready) { $("jobVideo").pause(); }
    $("jobDone").hidden = !(j.stage === "done" || j.stage === "error");
    $("jobLink").hidden = j.stage !== "done";
    if (j.url) $("jobLink").href = j.url;
    $("go").disabled = $("preview").disabled = busy;
    $("go").textContent = busy ? "진행 중…" : "병합 후 유튜브에 업로드";
  }

  function poll(id) {
    clearInterval(polling);
    const tick = async () => {
      try {
        const j = await api("/api/jobs/" + id);
        showJob(j);
        if (j.stage === "done" || j.stage === "error" || j.stage === "ready") {
          clearInterval(polling);
          if (j.stage === "done") { videos = []; render(); }   // 서버가 원본을 정리함
          $("job").scrollIntoView({ behavior: "smooth", block: "nearest" });
        }
      } catch (_) { /* 일시적 네트워크 오류는 다음 주기에 재시도 */ }
    };
    tick();
    polling = setInterval(tick, 1500);
  }

  async function startPublish(upload) {
    if (upload && !ytOk) return alert("먼저 '유튜브 연결'을 해주세요.");
    const title = $("title").value.trim();
    if (!title) return alert("제목을 입력하세요.");
    const order = videos.map((v, i) => `${i + 1}. ${v.start_display || "시간 정보 없음"}  ${v.name}`).join("\n");
    const what = upload ? "병합해 유튜브에 올립니다" : "병합만 하고 미리 보기를 만듭니다";
    if (!confirm(`아래 순서로 ${what}.\n${$("encode").checked ? "(안전 모드: 재인코딩)\n" : ""}\n제목: ${title}\n\n${order}`)) return;
    try {
      videoShownFor = null;
      const j = await api("/api/publish", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: videos.map((v) => v.id), title, privacy: $("privacy").value, encode: $("encode").checked, upload }),
      });
      showJob(j);
      poll(j.id);
    } catch (err) { alert(err.message); }
  }
  $("go").addEventListener("click", () => startPublish(true));
  $("preview").addEventListener("click", () => startPublish(false));

  $("jobUpload").addEventListener("click", async () => {
    if (!ytOk) return alert("먼저 '유튜브 연결'을 해주세요.");
    try {
      const j = await api(`/api/jobs/${currentJob.id}/upload`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: $("title").value.trim(), privacy: $("privacy").value }),
      });
      showJob(j);
      poll(j.id);
    } catch (err) { alert(err.message); }
  });
  $("jobDiscard").addEventListener("click", async () => {
    if (!confirm("병합본을 버릴까요? (원본 영상은 목록에 그대로 남아 있습니다)")) return;
    await api("/api/jobs/" + currentJob.id, { method: "DELETE" });
    videoShownFor = null;
    $("jobVideo").removeAttribute("src");
    showJob({ stage: "none" });
  });
  $("jobReset").addEventListener("click", async () => {
    if (currentJob) await api("/api/jobs/" + currentJob.id, { method: "DELETE" }).catch(() => {});
    $("job").hidden = true;
    $("go").disabled = $("preview").disabled = false;
  });

  // ---------- 시작 ----------
  api("/api/videos").then((list) => { videos = list; render(); });
  api("/api/jobs/active").then((j) => { if (j.stage !== "none") { showJob(j); poll(j.id); } });
})();
