// SoundReels Application Logic (Spotify Style & Reels Keyboard Control)

let allSlots = [];
let currentCategory = "all";
let currentSlot = null;
let currentCandidates = [];
let currentIndex = -1;
let compareDock = [null, null, null, null, null]; // 5 quick compare slots
let isLooping = false;

// Web Audio API setup for live spectrum & ambient glow
let audioCtx = null;
let analyser = null;
let audioSource = null;
let isAudioCtxSetup = false;

const audioPlayer = document.getElementById("audioPlayer");
const canvas = document.getElementById("waveformCanvas");
const ctx = canvas.getContext("2d");
const ambientBackdrop = document.getElementById("ambientBackdrop");

// UI Elements
const currentSlotName = document.getElementById("currentSlotName");
const currentSlotCategory = document.getElementById("currentSlotCategory");
const currentSlotMemo = document.getElementById("currentSlotMemo");
const activeSlotBadge = document.getElementById("activeSlotBadge");
const currentSoundName = document.getElementById("currentSoundName");
const currentSoundMeta = document.getElementById("currentSoundMeta");
const soundBadgeSource = document.getElementById("soundBadgeSource");
const soundMatchScore = document.getElementById("soundMatchScore");
const queuePosition = document.getElementById("queuePosition");
const timeCurrent = document.getElementById("timeCurrent");
const timeDuration = document.getElementById("timeDuration");
const timeSeek = document.getElementById("timeSeek");
const playIcon = document.getElementById("playIcon");
const pauseIcon = document.getElementById("pauseIcon");
const loadingSpinner = document.getElementById("loadingSpinner");
const unityPathInput = document.getElementById("unityPathInput");

// LocalStorage Persistence for Unity Path
if (localStorage.getItem("soundreels_unity_path")) {
  unityPathInput.value = localStorage.getItem("soundreels_unity_path");
}
unityPathInput.addEventListener("change", () => {
  localStorage.setItem("soundreels_unity_path", unityPathInput.value.trim());
});

function initAudioContext() {
  if (isAudioCtxSetup) {
    if (audioCtx && audioCtx.state === "suspended") {
      audioCtx.resume();
    }
    return;
  }
  try {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 128;
    audioSource = audioCtx.createMediaElementSource(audioPlayer);
    audioSource.connect(analyser);
    analyser.connect(audioCtx.destination);
    isAudioCtxSetup = true;
    if (audioCtx.state === "suspended") {
      audioCtx.resume();
    }
    renderVisualizer();
  } catch (e) {
    console.warn("Web Audio API AudioContext waiting for user gesture:", e);
  }
}

function renderVisualizer() {
  requestAnimationFrame(renderVisualizer);
  if (!analyser) return;

  const bufferLength = analyser.frequencyBinCount;
  const dataArray = new Uint8Array(bufferLength);
  analyser.getByteFrequencyData(dataArray);

  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const barWidth = (canvas.width / bufferLength) * 1.5;
  let x = 0;
  let totalEnergy = 0;
  let lowEnergy = 0;
  let highEnergy = 0;

  for (let i = 0; i < bufferLength; i++) {
    const barHeight = (dataArray[i] / 255) * canvas.height;
    totalEnergy += dataArray[i];
    if (i < bufferLength / 3) lowEnergy += dataArray[i];
    else if (i > (bufferLength * 2) / 3) highEnergy += dataArray[i];

    // Spotify green to cyan gradient for bars
    ctx.fillStyle = `rgb(${Math.min(255, 30 + dataArray[i])}, ${Math.min(255, 185 + dataArray[i] / 2)}, ${Math.min(255, 84 + dataArray[i])})`;
    ctx.fillRect(x, canvas.height - barHeight, barWidth - 1, barHeight);
    x += barWidth;
  }

  // Dynamic ambient backdrop color based on frequency spectrum
  if (totalEnergy > 50) {
    if (lowEnergy > highEnergy * 1.5) {
      ambientBackdrop.className = "absolute inset-0 opacity-25 pointer-events-none transition-colors duration-500 bg-red-700 blur-3xl";
    } else if (highEnergy > lowEnergy * 1.2) {
      ambientBackdrop.className = "absolute inset-0 opacity-25 pointer-events-none transition-colors duration-500 bg-cyan-600 blur-3xl";
    } else {
      ambientBackdrop.className = "absolute inset-0 opacity-25 pointer-events-none transition-colors duration-500 bg-emerald-600 blur-3xl";
    }
  }
}

// Resize canvas
function resizeCanvas() {
  canvas.width = canvas.parentElement.clientWidth || 400;
  canvas.height = canvas.parentElement.clientHeight || 100;
}
window.addEventListener("resize", resizeCanvas);
resizeCanvas();

// --- API Calls ---

async function loadSlots() {
  try {
    const res = await fetch("/api/slots");
    const data = await res.json();
    allSlots = data.slots || [];
    renderSlotsList();
    updateProgress();

    // Select first slot with candidates or first slot
    if (!currentSlot && allSlots.length > 0) {
      const firstActive = allSlots.find(s => s.candidate_count > 0) || allSlots[0];
      selectSlot(firstActive.id);
    }
  } catch (err) {
    console.error("Failed to load slots:", err);
  }
}

async function selectSlot(slotId) {
  const slot = allSlots.find(s => s.id === slotId);
  if (!slot) return;
  currentSlot = slot;

  currentSlotName.textContent = slot.name;
  currentSlotCategory.textContent = slot.category.toUpperCase();
  currentSlotMemo.textContent = slot.memo;
  activeSlotBadge.textContent = `${slot.name} (${slot.candidate_count || 0} 후보)`;

  // Fetch candidates
  try {
    loadingSpinner.classList.remove("hidden");
    const res = await fetch(`/api/slots/${slotId}/candidates`);
    const data = await res.json();
    currentCandidates = data.candidates || [];
    loadingSpinner.classList.add("hidden");

    renderCandidatesQueue();
    renderSlotsList();

    if (currentCandidates.length > 0) {
      playCandidate(0);
    } else {
      // Clean stop if no candidates
      audioPlayer.pause();
      audioPlayer.removeAttribute("src");
      currentSoundName.textContent = "후보 사운드 없음";
      currentSoundMeta.textContent = "LotsOfSounds 검색 중이거나 대기 상태";
      queuePosition.textContent = "0 / 0";
    }
  } catch (e) {
    loadingSpinner.classList.add("hidden");
    console.error("Failed to load candidates:", e);
  }
}

function playCandidate(index) {
  if (index < 0 || index >= currentCandidates.length) return;
  initAudioContext();

  currentIndex = index;
  const cand = currentCandidates[index];

  currentSoundName.textContent = cand.name;
  currentSoundMeta.textContent = `${cand.duration.toFixed(1)}s · ${cand.license_type} · ${cand.author || "LotsOfSounds"}`;
  soundBadgeSource.textContent = `LotsOfSounds · ${cand.license_type}`;
  soundMatchScore.textContent = `${Math.round(cand.score * 100)}% Match`;
  queuePosition.textContent = `${index + 1} / ${currentCandidates.length}`;

  // Update button like state
  const btnLike = document.getElementById("btnLike");
  if (cand.candidate_status === "liked") {
    btnLike.classList.add("text-yellow-400", "border-yellow-500");
  } else {
    btnLike.classList.remove("text-yellow-400", "border-yellow-500");
  }

  // Clear dock active highlights
  document.querySelectorAll(".dock-btn").forEach(b => b.classList.remove("active", "border-[#1DB954]"));

  // Load and play audio
  audioPlayer.src = cand.audio_url;
  audioPlayer.load();
  audioPlayer.play().catch(err => console.log("Auto-play waiting for key/click gesture:", err));

  renderCandidatesQueue();
}

// --- Render Methods ---

function renderSlotsList() {
  const container = document.getElementById("tabSlots");
  const previousScroll = container.scrollTop; // Preserve scroll position during polling
  container.innerHTML = "";

  const filtered = allSlots.filter(s => {
    if (currentCategory !== "all" && s.category !== currentCategory) return false;
    const searchVal = document.getElementById("searchInput").value.toLowerCase();
    if (searchVal && !s.name.toLowerCase().includes(searchVal) && !s.memo.toLowerCase().includes(searchVal)) return false;
    return true;
  });

  filtered.forEach(s => {
    const item = document.createElement("div");
    item.className = `slot-item p-2.5 rounded-lg flex items-center justify-between cursor-pointer text-xs ${currentSlot && currentSlot.id === s.id ? "active bg-[#282828]" : "bg-[#181818]"}`;
    
    let statusBadge = "";
    if (s.status === "confirmed") {
      statusBadge = `<span class="bg-emerald-950 text-emerald-400 border border-emerald-800 text-[10px] px-1.5 py-0.5 rounded font-bold">확정</span>`;
    } else if (s.status === "on_hold") {
      statusBadge = `<span class="bg-[#242424] text-gray-500 text-[10px] px-1.5 py-0.5 rounded">보류</span>`;
    } else if (s.candidate_count > 0) {
      statusBadge = `<span class="bg-[#242424] text-[#1DB954] text-[10px] px-1.5 py-0.5 rounded font-mono">${s.candidate_count} 후보</span>`;
    } else {
      statusBadge = `<span class="bg-[#242424] text-gray-400 text-[10px] px-1.5 py-0.5 rounded">탐색 중</span>`;
    }

    item.innerHTML = `
      <div class="truncate max-w-[190px]">
        <div class="font-bold text-gray-200 truncate">${s.name}</div>
        <div class="text-[11px] text-gray-500 truncate">${s.memo}</div>
      </div>
      <div>${statusBadge}</div>
    `;

    item.addEventListener("click", () => selectSlot(s.id));
    container.appendChild(item);
  });
  container.scrollTop = previousScroll;
}

function renderCandidatesQueue() {
  const container = document.getElementById("tabCandidates");
  container.innerHTML = "";

  currentCandidates.forEach((c, idx) => {
    const item = document.createElement("div");
    item.className = `candidate-item p-2 rounded flex items-center justify-between cursor-pointer text-xs ${idx === currentIndex ? "active bg-[#282828]" : "bg-[#181818] hover:bg-[#222]"}`;
    
    const likeIcon = c.candidate_status === "liked" ? "⭐ " : "";
    const confirmedTag = c.candidate_status === "confirmed" ? " [확정]" : "";

    item.innerHTML = `
      <div class="truncate max-w-[190px]">
        <div class="font-semibold text-gray-200 truncate">${likeIcon}${c.name}${confirmedTag}</div>
        <div class="text-[10px] text-gray-400 font-mono">${c.duration.toFixed(1)}s · ${Math.round(c.score * 100)}%</div>
      </div>
      <button class="text-gray-500 hover:text-white px-1.5 py-0.5 text-[10px] bg-[#242424] rounded">듣기</button>
    `;

    item.addEventListener("click", () => playCandidate(idx));
    container.appendChild(item);
  });
}

function updateProgress() {
  const confirmed = allSlots.filter(s => s.status === "confirmed").length;
  const total = allSlots.length;
  document.getElementById("confirmedProgress").textContent = `${confirmed} / ${total}`;
  const pct = total > 0 ? (confirmed / total) * 100 : 0;
  document.getElementById("progressBar").style.width = `${pct}%`;
}

// --- Action Handlers ---

async function toggleLikeCurrent() {
  if (currentIndex < 0 || !currentCandidates[currentIndex]) return;
  const cand = currentCandidates[currentIndex];

  try {
    const res = await fetch("/api/actions/like", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slot_id: currentSlot.id, sound_id: cand.sound_id })
    });
    const data = await res.json();
    cand.candidate_status = data.new_candidate_status;

    // Update Quick Compare Dock
    if (cand.candidate_status === "liked") {
      addToCompareDock(cand);
    }

    renderCandidatesQueue();
    playCandidate(currentIndex);
  } catch (e) {
    console.error("Like error:", e);
  }
}

function addToCompareDock(sound) {
  const existingIdx = compareDock.findIndex(s => s && s.sound_id === sound.sound_id);
  if (existingIdx !== -1) return;

  const emptyIdx = compareDock.findIndex(s => s === null);
  if (emptyIdx !== -1) {
    compareDock[emptyIdx] = sound;
  } else {
    compareDock.shift();
    compareDock.push(sound);
  }
  updateCompareDockUI();
}

function updateCompareDockUI() {
  const dockBtns = document.querySelectorAll(".dock-btn");
  dockBtns.forEach((btn, idx) => {
    const s = compareDock[idx];
    const spanText = btn.querySelector("span:last-child");
    if (s) {
      spanText.textContent = s.name;
      spanText.classList.add("text-emerald-400");
      btn.title = `${s.name} (${idx + 1}번 키)`;
    } else {
      spanText.textContent = "비어있음";
      spanText.classList.remove("text-emerald-400");
      btn.title = `비어있음 (${idx + 1}번 키)`;
    }
  });
}

// Action: F (Similar Sounds)
async function triggerSimilarCurrent() {
  if (currentIndex < 0 || !currentCandidates[currentIndex] || !currentSlot) return;
  const cand = currentCandidates[currentIndex];
  try {
    const res = await fetch("/api/actions/reorder_similar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slot_id: currentSlot.id, reference_sound_id: cand.sound_id })
    });
    if (res.ok) {
      // Reload candidates
      const cRes = await fetch(`/api/slots/${currentSlot.id}/candidates`);
      const cData = await cRes.json();
      currentCandidates = cData.candidates || [];
      renderCandidatesQueue();
      playCandidate(0);
    }
  } catch (e) {
    console.error("Similar error:", e);
  }
}

// Action: M (Mutate Take)
async function triggerMutateCurrent() {
  if (currentIndex < 0 || !currentCandidates[currentIndex] || !currentSlot) return;
  const cand = currentCandidates[currentIndex];
  try {
    loadingSpinner.classList.remove("hidden");
    const res = await fetch("/api/actions/mutate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slot_id: currentSlot.id, sound_id: cand.sound_id })
    });
    loadingSpinner.classList.add("hidden");
    if (res.ok) {
      // Reload candidates
      const cRes = await fetch(`/api/slots/${currentSlot.id}/candidates`);
      const cData = await cRes.json();
      currentCandidates = cData.candidates || [];
      renderCandidatesQueue();
      playCandidate(0);
    }
  } catch (e) {
    loadingSpinner.classList.add("hidden");
    console.error("Mutate error:", e);
  }
}

async function confirmCurrent() {
  if (currentIndex < 0 || !currentCandidates[currentIndex]) return;
  const cand = currentCandidates[currentIndex];
  const unityPath = unityPathInput.value.trim();

  try {
    const res = await fetch("/api/actions/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        slot_id: currentSlot.id,
        sound_id: cand.sound_id,
        unity_project_path: unityPath || null
      })
    });
    const data = await res.json();
    cand.candidate_status = "confirmed";
    currentSlot.status = "confirmed";

    renderCandidatesQueue();
    renderSlotsList();
    updateProgress();

    // Move to next pending slot automatically (Reels flow!)
    const nextSlot = allSlots.find(s => s.status !== "confirmed" && s.status !== "on_hold");
    if (nextSlot) {
      selectSlot(nextSlot.id);
    }
  } catch (e) {
    console.error("Confirm error:", e);
  }
}

// Batch Unity Export Handler
document.getElementById("btnUnityExport").addEventListener("click", async () => {
  const unityPath = unityPathInput.value.trim();
  if (!unityPath) {
    alert("Unity 프로젝트 경로를 입력해주세요. (예: E:/MyGame/)");
    unityPathInput.focus();
    return;
  }

  try {
    const res = await fetch("/api/export/batch_unity", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ unity_project_path: unityPath })
    });
    const data = await res.json();
    if (res.ok) {
      alert(`[내보내기 완료!] 총 ${data.exported_count}개의 확정 사운드가 무음 제거 및 -1dBFS 마스터링된 WAV 파일로 Assets/Audio/SFX 에 복사되었으며 CREDITS.txt가 작성되었습니다.`);
    } else {
      alert(`내보내기 실패: ${data.detail || "오류 발생"}`);
    }
  } catch (e) {
    alert(`내보내기 요청 오류: ${e}`);
  }
});

// --- Player Events ---

audioPlayer.addEventListener("timeupdate", () => {
  if (!audioPlayer.duration) return;
  timeCurrent.textContent = formatTime(audioPlayer.currentTime);
  timeDuration.textContent = formatTime(audioPlayer.duration);
  timeSeek.value = (audioPlayer.currentTime / audioPlayer.duration) * 100;
});

audioPlayer.addEventListener("play", () => {
  playIcon.classList.add("hidden");
  pauseIcon.classList.remove("hidden");
});

audioPlayer.addEventListener("pause", () => {
  playIcon.classList.remove("hidden");
  pauseIcon.classList.add("hidden");
});

audioPlayer.addEventListener("ended", () => {
  if (isLooping) {
    audioPlayer.currentTime = 0;
    audioPlayer.play();
  } else {
    // Auto advance to next in reels mode
    if (currentIndex + 1 < currentCandidates.length) {
      playCandidate(currentIndex + 1);
    }
  }
});

function formatTime(sec) {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s < 10 ? "0" : ""}${s}`;
}

timeSeek.addEventListener("input", () => {
  if (!audioPlayer.duration) return;
  audioPlayer.currentTime = (timeSeek.value / 100) * audioPlayer.duration;
});

// Controls
document.getElementById("btnPlay").addEventListener("click", () => {
  initAudioContext();
  if (audioPlayer.paused) audioPlayer.play();
  else audioPlayer.pause();
});

document.getElementById("btnNext").addEventListener("click", () => {
  if (currentIndex + 1 < currentCandidates.length) playCandidate(currentIndex + 1);
});

document.getElementById("btnPrev").addEventListener("click", () => {
  if (currentIndex - 1 >= 0) playCandidate(currentIndex - 1);
});

document.getElementById("btnLike").addEventListener("click", toggleLikeCurrent);
document.getElementById("btnSimilar").addEventListener("click", triggerSimilarCurrent);
document.getElementById("btnMutate").addEventListener("click", triggerMutateCurrent);
document.getElementById("btnConfirm").addEventListener("click", confirmCurrent);

document.getElementById("btnLoop").addEventListener("click", () => {
  isLooping = !isLooping;
  const loopBtn = document.getElementById("btnLoop");
  loopBtn.classList.toggle("text-emerald-400", isLooping);
});

// Category filtering
document.querySelectorAll(".cat-btn").forEach(btn => {
  btn.addEventListener("click", (e) => {
    document.querySelectorAll(".cat-btn").forEach(b => b.classList.remove("active", "text-white", "bg-[#282828]"));
    e.target.classList.add("active", "text-white", "bg-[#282828]");
    currentCategory = e.target.dataset.cat;
    renderSlotsList();
  });
});

// Tabs
document.getElementById("tabSlotsBtn").addEventListener("click", () => {
  document.getElementById("tabSlots").classList.remove("hidden");
  document.getElementById("tabCandidates").classList.add("hidden");
  document.getElementById("tabSlotsBtn").classList.add("border-[#1DB954]", "text-white");
  document.getElementById("tabCandidatesBtn").classList.remove("border-[#1DB954]", "text-white");
});

document.getElementById("tabCandidatesBtn").addEventListener("click", () => {
  document.getElementById("tabCandidates").classList.remove("hidden");
  document.getElementById("tabSlots").classList.add("hidden");
  document.getElementById("tabCandidatesBtn").classList.add("border-[#1DB954]", "text-white");
  document.getElementById("tabSlotsBtn").classList.remove("border-[#1DB954]", "text-white");
});

// Left panel toggle
document.getElementById("toggleLeftBtn").addEventListener("click", () => {
  const panel = document.getElementById("leftPanel");
  panel.classList.toggle("hidden");
});

// Search input
document.getElementById("searchInput").addEventListener("input", renderSlotsList);

// Dock button click
document.querySelectorAll(".dock-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    const idx = parseInt(btn.dataset.dock, 10);
    const sound = compareDock[idx];
    if (sound) {
      document.querySelectorAll(".dock-btn").forEach(b => b.classList.remove("active", "border-[#1DB954]"));
      btn.classList.add("active", "border-[#1DB954]");
      
      const cIdx = currentCandidates.findIndex(c => c.sound_id === sound.sound_id);
      if (cIdx !== -1) {
        playCandidate(cIdx);
      } else {
        initAudioContext();
        audioPlayer.src = sound.audio_url || `/api/sounds/${sound.sound_id}/audio`;
        audioPlayer.play();
        currentSoundName.textContent = sound.name;
      }
    }
  });
});

// --- Keyboard Shortcuts (Reels Flow) ---
window.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;

  if (e.key === "ArrowDown" || e.key === "j" || e.key === "J") {
    e.preventDefault();
    if (currentIndex + 1 < currentCandidates.length) playCandidate(currentIndex + 1);
  } else if (e.key === "ArrowUp" || e.key === "k" || e.key === "K") {
    e.preventDefault();
    if (currentIndex - 1 >= 0) playCandidate(currentIndex - 1);
  } else if (e.key === " " || e.code === "Space") {
    e.preventDefault();
    initAudioContext();
    if (audioPlayer.paused) audioPlayer.play();
    else audioPlayer.pause();
  } else if (e.key === "s" || e.key === "S") {
    e.preventDefault();
    toggleLikeCurrent();
  } else if (e.key === "f" || e.key === "F") {
    e.preventDefault();
    triggerSimilarCurrent();
  } else if (e.key === "m" || e.key === "M") {
    e.preventDefault();
    triggerMutateCurrent();
  } else if (e.key === "Enter") {
    e.preventDefault();
    confirmCurrent();
  } else if (e.key === "l" || e.key === "L") {
    e.preventDefault();
    document.getElementById("btnLoop").click();
  } else if (["1", "2", "3", "4", "5"].includes(e.key)) {
    const dockIdx = parseInt(e.key, 10) - 1;
    const btn = document.querySelector(`.dock-btn[data-dock="${dockIdx}"]`);
    if (btn) btn.click();
  }
});

// Load slots initially
loadSlots();
setInterval(loadSlots, 10000); // Auto poll updates
