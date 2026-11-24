// SoundReels v1.2 Application Logic
// Features: Scene Preview (Video + Frame-Accurate GIF), Scene/Audio Non-Destructive Trimmer & Undo,
//           Scene-Audio Master Sync + Loop Modes, Original Source Direct Link, Reels Keyboard Flow

let allSlots = [];
let currentCategory = "all";
let showConfirmedOnly = false;
let currentSlot = null;
let currentCandidates = [];
let currentIndex = -1;
let compareDock = [null, null, null, null, null];

// Loop Modes: 'sync' (Scene+Audio Sync Loop) | 'audio' (Audio-Only Loop) | 'once' (1x then Next)
let loopMode = "sync";

// Web Audio API (Loudness Normalization + Soft Limiter + Master Gain + Ambient Glow + Waveform Decoding)
let audioCtx = null;
let analyser = null;
let audioSource = null;
let perSoundGainNode = null;
let compressorNode = null;
let masterGainNode = null;
let isAudioCtxSetup = false;

let isNormEnabled = localStorage.getItem("soundreels_norm_enabled") !== "false";
let currentNormGain = 1.0;
let maxSearchDuration = parseFloat(localStorage.getItem("soundreels_max_search_dur")) || 0;

// Bottom Waveform Trimmer State
let waveformPeaks = [];
let audioDuration = 0.0;
let audioIn = 0.0;
let audioOut = 0.0;
let audioUndoStack = [];
let audioDecodeAbort = null;
let isDraggingAudioHandle = null; // 'in' | 'out' | 'scrub' | null

// Scene Preview & Trimmer State
let sceneMediaType = null; // 'video' | 'gif' | null
let sceneDuration = 0.0;
let sceneIn = 0.0;
let sceneOut = 0.0;
let soundOffset = 0.0;
let sceneCurrentTime = 0.0;
let sceneUndoStack = [];
let sceneIsPlaying = true;
const sceneAnalysisCache = {};
let sceneLastTick = 0;
let audioTriggeredThisCycle = false;

// GIF Frame Player State
let gifFrames = []; // [{ bitmap: ImageBitmap, start: number, end: number }]
let gifDecodeAbort = null;

// DOM Elements
const audioPlayer = document.getElementById("audioPlayer");
const ambientBackdrop = document.getElementById("ambientBackdrop");

const currentSlotName = document.getElementById("currentSlotName");
const currentSlotCategory = document.getElementById("currentSlotCategory");
const currentSlotMemo = document.getElementById("currentSlotMemo");
const activeSlotBadge = document.getElementById("activeSlotBadge");
const currentSoundName = document.getElementById("currentSoundName");
const currentSoundMeta = document.getElementById("currentSoundMeta");
const soundBadgeSource = document.getElementById("soundBadgeSource");
const btnOpenSourceUrl = document.getElementById("btnOpenSourceUrl");
const soundMatchScore = document.getElementById("soundMatchScore");
const queuePosition = document.getElementById("queuePosition");
const loadingSpinner = document.getElementById("loadingSpinner");

// Transport & Waveform Elements
const timeCurrent = document.getElementById("timeCurrent");
const timeDuration = document.getElementById("timeDuration");
const timeSeek = document.getElementById("timeSeek");
const playIcon = document.getElementById("playIcon");
const pauseIcon = document.getElementById("pauseIcon");
const btnLoopMode = document.getElementById("btnLoopMode");
const audioWaveformCanvas = document.getElementById("audioWaveformCanvas");
const audioWaveformCtx = audioWaveformCanvas.getContext("2d");
const audioInHandle = document.getElementById("audioInHandle");
const audioOutHandle = document.getElementById("audioOutHandle");
const audioTrimRangeLabel = document.getElementById("audioTrimRangeLabel");
const audioTrimControls = document.getElementById("audioTrimControls");
const btnToggleAudioTrim = document.getElementById("btnToggleAudioTrim");

// Scene Elements
const sceneStage = document.getElementById("sceneStage");
const sceneDropPrompt = document.getElementById("sceneDropPrompt");
const sceneVideo = document.getElementById("sceneVideo");
const sceneGifCanvas = document.getElementById("sceneGifCanvas");
const sceneGifCtx = sceneGifCanvas.getContext("2d");
const sceneGifFallback = document.getElementById("sceneGifFallback");
const sceneFileInput = document.getElementById("sceneFileInput");
const sceneTrimBar = document.getElementById("sceneTrimBar");
const sceneTimelineCanvas = document.getElementById("sceneTimelineCanvas");
const sceneTimelineCtx = sceneTimelineCanvas.getContext("2d");
const sceneInHandle = document.getElementById("sceneInHandle");
const sceneOutHandle = document.getElementById("sceneOutHandle");
const sceneTimeLabel = document.getElementById("sceneTimeLabel");
const sceneTrimLabel = document.getElementById("sceneTrimLabel");
const soundOffsetInput = document.getElementById("soundOffsetInput");
const btnDeleteScene = document.getElementById("btnDeleteScene");
const btnScenePlayPause = document.getElementById("btnScenePlayPause");

let isDraggingSceneHandle = null; // 'in' | 'out' | 'scrub' | null

const btnMute = document.getElementById("btnMute");
const volumeSlider = document.getElementById("volumeSlider");
const volumeLabel = document.getElementById("volumeLabel");

// Persistent Settings Helper (localStorage + SQLite /api/settings)
function saveSetting(key, val) {
  localStorage.setItem(`soundreels_${key}`, String(val));
  fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ settings: { [key]: String(val) } }),
  }).catch(() => {});
}

// Master Volume (Default 40%, never resets across sounds or app restarts)
let masterVolume = parseFloat(localStorage.getItem("soundreels_volume"));
if (Number.isNaN(masterVolume)) masterVolume = parseFloat(localStorage.getItem("soundreels_master_volume"));
if (Number.isNaN(masterVolume)) masterVolume = 0.4;
masterVolume = Math.max(0, Math.min(1, masterVolume));
let preMuteVolume = masterVolume > 0 ? masterVolume : 0.4;

const btnToggleNorm = document.getElementById("btnToggleNorm");
const maxDurationInput = document.getElementById("maxDurationInput");
const btnRescoutSlot = document.getElementById("btnRescoutSlot");

if (maxSearchDuration > 0) {
  maxDurationInput.value = maxSearchDuration;
}

function updateNormToggleUI() {
  if (isNormEnabled) {
    btnToggleNorm.textContent = "🎚️ 음량통일";
    btnToggleNorm.className = "px-2 py-0.5 rounded bg-emerald-950 text-emerald-300 border border-emerald-800 text-[11px] font-bold hover:bg-emerald-900 transition";
  } else {
    btnToggleNorm.textContent = "🎚️ 원본음량";
    btnToggleNorm.className = "px-2 py-0.5 rounded bg-[#242424] text-gray-400 border border-[#383838] text-[11px] font-bold hover:text-white transition";
  }
}
updateNormToggleUI();

function applyAudioGainNodes() {
  const effectiveNorm = isNormEnabled ? currentNormGain : 1.0;
  if (isAudioCtxSetup && perSoundGainNode && masterGainNode) {
    audioPlayer.volume = 1.0;
    perSoundGainNode.gain.setValueAtTime(effectiveNorm, audioCtx.currentTime);
    masterGainNode.gain.setValueAtTime(masterVolume, audioCtx.currentTime);
  } else {
    audioPlayer.volume = Math.max(0, Math.min(1, masterVolume * effectiveNorm));
  }
}

function applyMasterVolume(vol, persist = true) {
  masterVolume = Math.max(0, Math.min(1, vol));
  applyAudioGainNodes();
  const pct = Math.round(masterVolume * 100);
  volumeSlider.value = pct;
  volumeLabel.textContent = `${pct}%`;
  if (masterVolume === 0) btnMute.textContent = "🔇";
  else if (masterVolume < 0.35) btnMute.textContent = "🔈";
  else if (masterVolume < 0.7) btnMute.textContent = "🔉";
  else btnMute.textContent = "🔊";

  if (persist) {
    localStorage.setItem("soundreels_volume", masterVolume.toFixed(2));
    saveSetting("master_volume", masterVolume.toFixed(2));
  }
}

btnToggleNorm.addEventListener("click", () => {
  isNormEnabled = !isNormEnabled;
  updateNormToggleUI();
  applyAudioGainNodes();
  saveSetting("norm_enabled", String(isNormEnabled));
});

applyMasterVolume(masterVolume, false);

volumeSlider.addEventListener("input", () => {
  const v = parseInt(volumeSlider.value, 10) / 100;
  if (v > 0) preMuteVolume = v;
  applyMasterVolume(v, false);
});

volumeSlider.addEventListener("change", () => {
  const v = parseInt(volumeSlider.value, 10) / 100;
  applyMasterVolume(v, true);
});

btnMute.addEventListener("click", () => {
  if (masterVolume > 0) {
    preMuteVolume = masterVolume;
    applyMasterVolume(0, true);
  } else {
    applyMasterVolume(preMuteVolume > 0 ? preMuteVolume : 0.4, true);
  }
});

const unityPathInput = document.getElementById("unityPathInput");

if (localStorage.getItem("soundreels_unity_path")) {
  unityPathInput.value = localStorage.getItem("soundreels_unity_path");
}
unityPathInput.addEventListener("change", () => {
  saveSetting("unity_path", unityPathInput.value.trim());
});

// =========================================================================
// Web Audio Initialization & Ambient Timbre Glow Loop
// =========================================================================

function initAudioContext() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  }
  if (audioCtx.state === "suspended") {
    audioCtx.resume().catch(() => {});
  }
  if (!isAudioCtxSetup) {
    try {
      perSoundGainNode = audioCtx.createGain();
      compressorNode = audioCtx.createDynamicsCompressor();
      compressorNode.threshold.value = -3;
      compressorNode.knee.value = 6;
      compressorNode.ratio.value = 12;
      compressorNode.attack.value = 0.002;
      compressorNode.release.value = 0.08;
      masterGainNode = audioCtx.createGain();
      analyser = audioCtx.createAnalyser();
      analyser.fftSize = 128;

      audioSource = audioCtx.createMediaElementSource(audioPlayer);
      audioSource.connect(perSoundGainNode);
      perSoundGainNode.connect(compressorNode);
      compressorNode.connect(masterGainNode);
      masterGainNode.connect(analyser);
      analyser.connect(audioCtx.destination);
      isAudioCtxSetup = true;
      applyAudioGainNodes();
    } catch (e) {
      console.warn("Web Audio MediaElementSource setup skipped:", e);
    }
  }
}

function updateAmbientGlow() {
  if (!analyser || audioPlayer.paused) return;
  const bufferLength = analyser.frequencyBinCount;
  const dataArray = new Uint8Array(bufferLength);
  analyser.getByteFrequencyData(dataArray);

  let total = 0, low = 0, high = 0;
  for (let i = 0; i < bufferLength; i++) {
    total += dataArray[i];
    if (i < bufferLength / 3) low += dataArray[i];
    else if (i > (bufferLength * 2) / 3) high += dataArray[i];
  }

  if (total > 40) {
    if (low > high * 1.5) {
      ambientBackdrop.className = "absolute inset-0 opacity-25 pointer-events-none transition-colors duration-500 bg-red-700 blur-3xl";
    } else if (high > low * 1.2) {
      ambientBackdrop.className = "absolute inset-0 opacity-25 pointer-events-none transition-colors duration-500 bg-cyan-600 blur-3xl";
    } else {
      ambientBackdrop.className = "absolute inset-0 opacity-25 pointer-events-none transition-colors duration-500 bg-emerald-600 blur-3xl";
    }
  }
}

// =========================================================================
// Master Animation & Sync Loop (Waveform Playhead + Scene/Audio Sync)
// =========================================================================

function mainLoop(now) {
  requestAnimationFrame(mainLoop);

  if (!sceneLastTick) sceneLastTick = now;
  const dt = Math.min(0.1, (now - sceneLastTick) / 1000.0);
  sceneLastTick = now;

  // 1. Update ambient glow
  updateAmbientGlow();

  // 2. Enforce Audio Trim Out Boundary
  if (!audioPlayer.paused && audioDuration > 0) {
    const effectiveOut = audioOut > audioIn ? audioOut : audioDuration;
    if (audioPlayer.currentTime >= effectiveOut - 0.025) {
      if (loopMode === "audio" || (loopMode === "sync" && !sceneMediaType)) {
        audioPlayer.currentTime = audioIn;
        audioPlayer.play().catch(() => {});
      } else if (loopMode === "sync" && sceneMediaType) {
        // Pause and wait for the scene loop to restart
        audioPlayer.pause();
        audioPlayer.currentTime = audioIn;
      } else if (loopMode === "once") {
        audioPlayer.pause();
        if (currentIndex + 1 < currentCandidates.length) {
          playCandidate(currentIndex + 1);
        }
      }
    }
  }

  // 3. Drive Scene Media & Sync Trigger
  if (sceneMediaType && sceneIsPlaying && sceneDuration > 0) {
    const sIn = Math.max(0, sceneIn);
    const sOut = sceneOut > sIn ? sceneOut : sceneDuration;

    if (sceneMediaType === "video") {
      if (!sceneVideo.paused) {
        sceneCurrentTime = sceneVideo.currentTime;
        if (sceneCurrentTime < sIn) {
          sceneVideo.currentTime = sIn;
          sceneCurrentTime = sIn;
        }
        if (sceneCurrentTime >= sOut - 0.02) {
          sceneVideo.currentTime = sIn;
          sceneCurrentTime = sIn;
          audioTriggeredThisCycle = false;
        }
      }
    } else if (sceneMediaType === "gif" && gifFrames.length > 0) {
      sceneCurrentTime += dt;
      if (sceneCurrentTime >= sOut || sceneCurrentTime < sIn) {
        sceneCurrentTime = sIn;
        audioTriggeredThisCycle = false;
      }
      drawGifFrameAt(sceneCurrentTime);
    }

    // Trigger Sound in Sync Mode when reaching sceneIn + soundOffset
    if (loopMode === "sync" && !audioTriggeredThisCycle) {
      const triggerAt = Math.min(sOut - 0.02, sIn + soundOffset);
      if (sceneCurrentTime >= triggerAt) {
        audioTriggeredThisCycle = true;
        triggerAudioFromIn();
      }
    }

    drawSceneTimeline();
  }

  // 4. Render Bottom Audio Waveform
  drawAudioWaveform();
}
requestAnimationFrame(mainLoop);

function triggerAudioFromIn() {
  if (currentIndex < 0 || !currentCandidates[currentIndex]) return;
  initAudioContext();
  applyAudioGainNodes();
  audioPlayer.currentTime = audioIn;
  audioPlayer.play().catch(() => {});
}

// =========================================================================
// Bottom Audio Waveform & Interactive Trimmer
// =========================================================================

function resizeWaveformCanvas() {
  const rect = audioWaveformCanvas.parentElement.getBoundingClientRect();
  if (rect.width > 0) {
    audioWaveformCanvas.width = rect.width;
    audioWaveformCanvas.height = rect.height;
  }
  const tRect = sceneTimelineCanvas.parentElement.getBoundingClientRect();
  if (tRect.width > 0) {
    sceneTimelineCanvas.width = tRect.width;
    sceneTimelineCanvas.height = tRect.height;
  }
}
window.addEventListener("resize", resizeWaveformCanvas);
resizeWaveformCanvas();

async function decodeWaveformForCandidate(cand) {
  if (audioDecodeAbort) audioDecodeAbort.abort();
  audioDecodeAbort = new AbortController();

  waveformPeaks = [];
  audioDuration = cand.duration || 0;
  audioIn = cand.audio_in || 0.0;
  audioOut = cand.audio_out > 0 ? cand.audio_out : audioDuration;
  audioUndoStack = [];
  updateAudioTrimUI();

  try {
    initAudioContext();
    const resp = await fetch(cand.audio_url, { signal: audioDecodeAbort.signal });
    const buf = await resp.arrayBuffer();
    const decoded = await audioCtx.decodeAudioData(buf);
    audioDuration = decoded.duration;

    if (cand.audio_out <= 0 || cand.audio_out > audioDuration) {
      audioOut = audioDuration;
    } else {
      audioOut = cand.audio_out;
    }
    audioIn = Math.min(cand.audio_in || 0.0, Math.max(0, audioOut - 0.05));

    // Extract peak envelope (~220 bars)
    const ch = decoded.getChannelData(0);
    const barCount = 220;
    const blockSize = Math.max(1, Math.floor(ch.length / barCount));
    const peaks = [];
    let maxPeak = 0.001;
    for (let i = 0; i < barCount; i++) {
      let sum = 0;
      const start = i * blockSize;
      const end = Math.min(ch.length, start + blockSize);
      for (let j = start; j < end; j++) {
        const v = Math.abs(ch[j]);
        if (v > sum) sum = v;
      }
      if (sum > maxPeak) maxPeak = sum;
      peaks.push(sum);
    }
    waveformPeaks = peaks.map(p => p / maxPeak);
    updateAudioTrimUI();
  } catch (e) {
    if (e.name !== "AbortError") {
      console.warn("Waveform decode failed:", e);
    }
  }
}

function drawAudioWaveform() {
  const w = audioWaveformCanvas.width;
  const h = audioWaveformCanvas.height;
  if (!w || !h) return;

  audioWaveformCtx.clearRect(0, 0, w, h);

  const dur = audioDuration || audioPlayer.duration || 1;
  const inRatio = Math.max(0, Math.min(1, audioIn / dur));
  const outRatio = Math.max(inRatio, Math.min(1, audioOut / dur));
  const playRatio = Math.max(0, Math.min(1, (audioPlayer.currentTime || 0) / dur));

  // Shade cut-out regions outside [audioIn, audioOut]
  audioWaveformCtx.fillStyle = "rgba(0, 0, 0, 0.55)";
  if (inRatio > 0) audioWaveformCtx.fillRect(0, 0, inRatio * w, h);
  if (outRatio < 1) audioWaveformCtx.fillRect(outRatio * w, 0, (1 - outRatio) * w, h);

  // Draw waveform bars
  if (waveformPeaks.length > 0) {
    const barW = w / waveformPeaks.length;
    const midY = h / 2;
    for (let i = 0; i < waveformPeaks.length; i++) {
      const ratio = i / waveformPeaks.length;
      const barH = Math.max(3, waveformPeaks[i] * (h * 0.85));
      const inRange = ratio >= inRatio && ratio <= outRatio;
      const passed = ratio <= playRatio && inRange;

      if (!inRange) {
        audioWaveformCtx.fillStyle = "#333333";
      } else if (passed) {
        audioWaveformCtx.fillStyle = "#1ed760";
      } else {
        audioWaveformCtx.fillStyle = "#1DB954";
      }
      audioWaveformCtx.fillRect(i * barW, midY - barH / 2, Math.max(1, barW - 1), barH);
    }
  } else {
    // Subtle flat line placeholder
    audioWaveformCtx.fillStyle = "#2b2b2b";
    audioWaveformCtx.fillRect(0, h / 2 - 1, w, 2);
  }

  // Playhead cursor line
  audioWaveformCtx.fillStyle = "#ffffff";
  audioWaveformCtx.fillRect(playRatio * w - 1, 0, 2, h);
}

function updateAudioTrimUI() {
  const dur = audioDuration || 1;
  const inPct = Math.max(0, Math.min(100, (audioIn / dur) * 100));
  const outPct = Math.max(inPct, Math.min(100, (audioOut / dur) * 100));
  audioInHandle.style.left = `${inPct}%`;
  audioOutHandle.style.left = `${outPct}%`;

  const selLen = Math.max(0, audioOut - audioIn);
  const isFull = audioIn <= 0.01 && Math.abs(audioOut - audioDuration) <= 0.05;
  audioTrimRangeLabel.textContent = isFull
    ? `구간: 전체 (0.00s ~ ${audioDuration.toFixed(2)}s)`
    : `선택 구간: ${audioIn.toFixed(2)}s ~ ${audioOut.toFixed(2)}s (${selLen.toFixed(2)}s)`;
}

function pushAudioUndo() {
  audioUndoStack.push([audioIn, audioOut]);
  if (audioUndoStack.length > 20) audioUndoStack.shift();
}

async function saveAudioTrimToBackend() {
  if (!currentSlot || currentIndex < 0 || !currentCandidates[currentIndex]) return;
  const cand = currentCandidates[currentIndex];
  cand.audio_in = audioIn;
  cand.audio_out = audioOut;
  try {
    await fetch("/api/actions/trim_audio", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        slot_id: currentSlot.id,
        sound_id: cand.sound_id,
        audio_in: Number(audioIn.toFixed(3)),
        audio_out: Number(audioOut.toFixed(3)),
      }),
    });
  } catch (e) {
    console.error("Failed to save audio trim:", e);
  }
}

// Mouse / Pointer Dragging on Waveform & Handles
audioInHandle.addEventListener("pointerdown", (e) => {
  e.stopPropagation();
  pushAudioUndo();
  isDraggingAudioHandle = "in";
  audioInHandle.classList.add("dragging");
});

audioOutHandle.addEventListener("pointerdown", (e) => {
  e.stopPropagation();
  pushAudioUndo();
  isDraggingAudioHandle = "out";
  audioOutHandle.classList.add("dragging");
});

audioWaveformCanvas.addEventListener("pointerdown", (e) => {
  if (audioDuration <= 0) return;
  isDraggingAudioHandle = "scrub";
  scrubAudioByEvent(e);
});

window.addEventListener("pointermove", (e) => {
  if (!isDraggingAudioHandle || audioDuration <= 0) return;
  const rect = audioWaveformCanvas.getBoundingClientRect();
  const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
  const t = ratio * audioDuration;

  if (isDraggingAudioHandle === "in") {
    audioIn = Math.min(t, Math.max(0, audioOut - 0.05));
    updateAudioTrimUI();
  } else if (isDraggingAudioHandle === "out") {
    audioOut = Math.max(t, Math.min(audioDuration, audioIn + 0.05));
    updateAudioTrimUI();
  } else if (isDraggingAudioHandle === "scrub") {
    scrubAudioByEvent(e);
  }
});

window.addEventListener("pointerup", () => {
  if (isDraggingAudioHandle === "in" || isDraggingAudioHandle === "out") {
    audioInHandle.classList.remove("dragging");
    audioOutHandle.classList.remove("dragging");
    saveAudioTrimToBackend();
  }
  isDraggingAudioHandle = null;
});

function scrubAudioByEvent(e) {
  if (audioDuration <= 0) return;
  const rect = audioWaveformCanvas.getBoundingClientRect();
  const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
  initAudioContext();
  audioPlayer.currentTime = ratio * audioDuration;
}

// Audio Trim Collapsible Toggle (Collapsed by default)
btnToggleAudioTrim.addEventListener("click", () => {
  const isHidden = audioTrimControls.classList.toggle("hidden");
  audioInHandle.classList.toggle("hidden", isHidden);
  audioOutHandle.classList.toggle("hidden", isHidden);
  btnToggleAudioTrim.textContent = isHidden ? "✂️ 소리 파형 편집 ▸" : "✂️ 소리 파형 접기 ▾";
});

// Audio Trim Buttons
document.getElementById("btnAudioSetIn").addEventListener("click", () => {
  if (audioDuration <= 0) return;
  pushAudioUndo();
  audioIn = Math.min(audioPlayer.currentTime, Math.max(0, audioOut - 0.05));
  updateAudioTrimUI();
  saveAudioTrimToBackend();
});

document.getElementById("btnAudioSetOut").addEventListener("click", () => {
  if (audioDuration <= 0) return;
  pushAudioUndo();
  audioOut = Math.max(audioPlayer.currentTime, Math.min(audioDuration, audioIn + 0.05));
  updateAudioTrimUI();
  saveAudioTrimToBackend();
});

document.getElementById("btnAudioApply").addEventListener("click", async () => {
  await saveAudioTrimToBackend();
  triggerAudioFromIn();
});

document.getElementById("btnAudioUndo").addEventListener("click", async () => {
  if (audioUndoStack.length === 0) return;
  const [prevIn, prevOut] = audioUndoStack.pop();
  audioIn = prevIn;
  audioOut = prevOut;
  updateAudioTrimUI();
  await saveAudioTrimToBackend();
});

document.getElementById("btnAudioReset").addEventListener("click", async () => {
  if (audioDuration <= 0) return;
  pushAudioUndo();
  audioIn = 0.0;
  audioOut = audioDuration;
  updateAudioTrimUI();
  if (currentSlot && currentIndex >= 0 && currentCandidates[currentIndex]) {
    const cand = currentCandidates[currentIndex];
    cand.audio_in = 0.0;
    cand.audio_out = 0.0;
    await fetch("/api/actions/trim_audio", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        slot_id: currentSlot.id,
        sound_id: cand.sound_id,
        audio_in: 0.0,
        audio_out: 0.0,
      }),
    });
  }
});

// =========================================================================
// Scene Preview (Video + Frame-Accurate GIF Decoder) & Scene Trimmer
// =========================================================================

function clearGifFrames() {
  for (const f of gifFrames) {
    if (f.bitmap && typeof f.bitmap.close === "function") {
      f.bitmap.close();
    }
  }
  gifFrames = [];
}

function resetSceneUI() {
  clearGifFrames();
  if (gifDecodeAbort) gifDecodeAbort.abort();

  sceneMediaType = null;
  sceneDuration = 0.0;
  sceneIn = 0.0;
  sceneOut = 0.0;
  soundOffset = 0.0;
  sceneCurrentTime = 0.0;
  sceneUndoStack = [];
  audioTriggeredThisCycle = false;

  sceneVideo.pause();
  sceneVideo.removeAttribute("src");
  sceneVideo.classList.add("hidden");
  sceneGifCanvas.classList.add("hidden");
  sceneGifFallback.removeAttribute("src");
  sceneGifFallback.classList.add("hidden");

  sceneDropPrompt.classList.remove("hidden");
  sceneTrimBar.classList.add("hidden");
  btnDeleteScene.classList.add("hidden");
  const analysisBadge = document.getElementById("sceneAnalysisBadge");
  if (analysisBadge) {
    analysisBadge.classList.add("hidden");
    analysisBadge.textContent = "";
  }
}

function renderSceneAnalysisBadge(slotId) {
  const analysisBadge = document.getElementById("sceneAnalysisBadge");
  if (!analysisBadge) return;
  const info = sceneAnalysisCache[slotId];
  if (!info) {
    analysisBadge.classList.add("hidden");
    analysisBadge.textContent = "";
    return;
  }
  const engineLabel = info.engine === "gemini" ? "✨ Gemini Vision" : "🤖 로컬 비전 AI";
  const tagsStr = (info.merged_tags || []).join(", ");
  analysisBadge.textContent = `${engineLabel}: ${info.summary_ko} · 키워드 [${tagsStr}] · 추천 ≤${info.applied_max_dur || info.recommended_max_dur}s (${info.found_count}개 탐색)`;
  analysisBadge.title = analysisBadge.textContent;
  analysisBadge.classList.remove("hidden");
}

async function loadSceneForSlot(slot, autoPlay = true) {
  resetSceneUI();
  if (!slot || !slot.scene_media_url) return;

  sceneIn = slot.scene_in || 0.0;
  sceneOut = slot.scene_out || 0.0;
  soundOffset = slot.sound_offset || 0.0;
  soundOffsetInput.value = soundOffset.toFixed(2);

  sceneDropPrompt.classList.add("hidden");
  sceneTrimBar.classList.remove("hidden");
  btnDeleteScene.classList.remove("hidden");
  renderSceneAnalysisBadge(slot.id);
  resizeWaveformCanvas();

  if (slot.scene_media_type === "video") {
    sceneMediaType = "video";
    sceneVideo.classList.remove("hidden");
    sceneVideo.src = slot.scene_media_url;
    sceneVideo.onloadedmetadata = () => {
      sceneDuration = sceneVideo.duration || 1.0;
      if (sceneOut <= 0 || sceneOut > sceneDuration) sceneOut = sceneDuration;
      sceneIn = Math.min(sceneIn, Math.max(0, sceneOut - 0.05));
      sceneVideo.currentTime = sceneIn;
      sceneCurrentTime = sceneIn;
      audioTriggeredThisCycle = !autoPlay;
      sceneIsPlaying = autoPlay;
      if (autoPlay) sceneVideo.play().catch(() => {});
      else sceneVideo.pause();
      updateSceneTrimUI();
    };
  } else if (slot.scene_media_type === "gif") {
    await loadGifScene(slot.scene_media_url, autoPlay);
  }
}

async function loadGifScene(url, autoPlay = true) {
  if (gifDecodeAbort) gifDecodeAbort.abort();
  gifDecodeAbort = new AbortController();

  // Check if ImageDecoder is supported (Chromium / Edge WebView2)
  if (typeof ImageDecoder === "undefined") {
    sceneMediaType = "gif";
    sceneGifFallback.src = url;
    sceneGifFallback.classList.remove("hidden");
    sceneDuration = 2.0;
    if (sceneOut <= 0) sceneOut = sceneDuration;
    updateSceneTrimUI();
    return;
  }

  try {
    loadingSpinner.classList.remove("hidden");
    const resp = await fetch(url, { signal: gifDecodeAbort.signal });
    const buf = await resp.arrayBuffer();
    const decoder = new ImageDecoder({ data: buf, type: "image/gif" });
    await decoder.tracks.ready;

    const track = decoder.tracks.selectedTrack;
    const count = track.frameCount || 1;
    let cursor = 0.0;
    const frames = [];

    for (let i = 0; i < count; i++) {
      const result = await decoder.decode({ frameIndex: i });
      const vf = result.image;
      const durSec = Math.max(0.02, (vf.duration || 100000) / 1_000_000);
      const bitmap = await createImageBitmap(vf);
      vf.close();
      frames.push({ bitmap, start: cursor, end: cursor + durSec });
      cursor += durSec;
    }
    decoder.close();

    gifFrames = frames;
    sceneMediaType = "gif";
    sceneDuration = cursor;
    if (sceneOut <= 0 || sceneOut > sceneDuration) sceneOut = sceneDuration;
    sceneIn = Math.min(sceneIn, Math.max(0, sceneOut - 0.05));
    sceneCurrentTime = sceneIn;
    audioTriggeredThisCycle = !autoPlay;
    sceneIsPlaying = autoPlay;

    sceneGifCanvas.classList.remove("hidden");
    if (gifFrames.length > 0) {
      sceneGifCanvas.width = gifFrames[0].bitmap.width;
      sceneGifCanvas.height = gifFrames[0].bitmap.height;
      drawGifFrameAt(sceneIn);
    }
    updateSceneTrimUI();
  } catch (e) {
    if (e.name !== "AbortError") {
      console.warn("GIF ImageDecoder failed, using fallback <img>:", e);
      sceneGifFallback.src = url;
      sceneGifFallback.classList.remove("hidden");
    }
  } finally {
    loadingSpinner.classList.add("hidden");
  }
}

function drawGifFrameAt(t) {
  if (gifFrames.length === 0) return;
  let frame = gifFrames[gifFrames.length - 1];
  for (let i = 0; i < gifFrames.length; i++) {
    if (t >= gifFrames[i].start && t < gifFrames[i].end) {
      frame = gifFrames[i];
      break;
    }
  }
  sceneGifCtx.clearRect(0, 0, sceneGifCanvas.width, sceneGifCanvas.height);
  sceneGifCtx.drawImage(frame.bitmap, 0, 0);
}

function drawSceneTimeline() {
  const w = sceneTimelineCanvas.width;
  const h = sceneTimelineCanvas.height;
  if (!w || !h || sceneDuration <= 0) return;

  sceneTimelineCtx.clearRect(0, 0, w, h);
  const inR = Math.max(0, Math.min(1, sceneIn / sceneDuration));
  const outR = Math.max(inR, Math.min(1, (sceneOut > 0 ? sceneOut : sceneDuration) / sceneDuration));
  const curR = Math.max(0, Math.min(1, sceneCurrentTime / sceneDuration));

  // Background dimmed cut zones
  sceneTimelineCtx.fillStyle = "#181818";
  sceneTimelineCtx.fillRect(0, 0, w, h);

  // Active trimmed window
  sceneTimelineCtx.fillStyle = "rgba(245, 158, 11, 0.35)";
  sceneTimelineCtx.fillRect(inR * w, 0, Math.max(2, (outR - inR) * w), h);
  sceneTimelineCtx.fillStyle = "#1DB954";
  sceneTimelineCtx.fillRect(inR * w, h - 4, Math.max(2, (outR - inR) * w), 4);

  // Sound offset pin if > 0
  if (soundOffset > 0) {
    const trigR = Math.min(1, (sceneIn + soundOffset) / sceneDuration);
    sceneTimelineCtx.fillStyle = "#38bdf8";
    sceneTimelineCtx.fillRect(trigR * w - 1, 0, 2, h);
  }

  // Playhead
  sceneTimelineCtx.fillStyle = "#ffffff";
  sceneTimelineCtx.fillRect(curR * w - 1, 0, 2, h);

  sceneTimeLabel.textContent = `${sceneCurrentTime.toFixed(2)}s / ${sceneDuration.toFixed(2)}s`;
}

function updateSceneTrimUI() {
  const effectiveOut = sceneOut > 0 ? sceneOut : sceneDuration;
  const dur = sceneDuration || 1;
  sceneInHandle.style.left = `${Math.max(0, Math.min(100, (sceneIn / dur) * 100))}%`;
  sceneOutHandle.style.left = `${Math.max(0, Math.min(100, (effectiveOut / dur) * 100))}%`;

  const isFull = sceneIn <= 0.01 && Math.abs(effectiveOut - sceneDuration) <= 0.05;
  sceneTrimLabel.textContent = isFull
    ? "구간: 전체"
    : `${sceneIn.toFixed(2)}s ~ ${effectiveOut.toFixed(2)}s`;
  drawSceneTimeline();
}

function pushSceneUndo() {
  sceneUndoStack.push([sceneIn, sceneOut, soundOffset]);
  if (sceneUndoStack.length > 20) sceneUndoStack.shift();
}

async function saveSceneTrimToBackend() {
  if (!currentSlot) return;
  currentSlot.scene_in = sceneIn;
  currentSlot.scene_out = sceneOut;
  currentSlot.sound_offset = soundOffset;
  try {
    await fetch(`/api/slots/${currentSlot.id}/scene/trim`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        scene_in: Number(sceneIn.toFixed(3)),
        scene_out: Number(sceneOut.toFixed(3)),
        sound_offset: Number(soundOffset.toFixed(3)),
      }),
    });
  } catch (e) {
    console.error("Failed to save scene trim:", e);
  }
}

// Scene Upload (Click + Drag & Drop)
async function uploadSceneFile(file) {
  if (!currentSlot || !file) return;
  const formData = new FormData();
  formData.append("file", file);

  try {
    loadingSpinner.classList.remove("hidden");
    const res = await fetch(`/api/slots/${currentSlot.id}/scene`, {
      method: "POST",
      body: formData,
    });
    const data = await res.json();
    loadingSpinner.classList.add("hidden");

    if (!res.ok) {
      alert(`장면 업로드 실패: ${data.detail || "지원되지 않는 형식입니다."}`);
      return;
    }

    currentSlot.scene_media_type = data.scene_media_type;
    currentSlot.scene_media_url = data.scene_media_url;
    currentSlot.scene_in = 0.0;
    currentSlot.scene_out = 0.0;
    currentSlot.sound_offset = 0.0;
    await loadSceneForSlot(currentSlot);
    await runSceneAwareScout(true);
  } catch (e) {
    loadingSpinner.classList.add("hidden");
    alert(`업로드 오류: ${e}`);
  }
}

async function runSceneAwareScout(isAuto = false) {
  if (!currentSlot || !currentSlot.scene_media_url) return;
  const targetSlotId = currentSlot.id;
  const btnSceneScout = document.getElementById("btnSceneScout");
  const analysisBadge = document.getElementById("sceneAnalysisBadge");

  if (btnSceneScout) {
    btnSceneScout.disabled = true;
    btnSceneScout.textContent = "⏳ 영상 분석 중...";
  }
  if (analysisBadge) {
    analysisBadge.textContent = "🤖 영상/GIF 프레임을 분석해 맞춤 사운드를 찾는 중입니다...";
    analysisBadge.classList.remove("hidden");
  }
  loadingSpinner.classList.remove("hidden");

  try {
    const res = await fetch(`/api/slots/${targetSlotId}/scene_scout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        scene_in: sceneIn,
        scene_out: sceneOut,
        max_dur: maxSearchDuration > 0 ? maxSearchDuration : null,
        use_scene_duration: true,
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      if (!isAuto) alert(`영상 맞춤 찾기 실패: ${data.detail || "오류 발생"}`);
      return;
    }

    sceneAnalysisCache[targetSlotId] = data;
    await loadSlots();
    if (currentSlot && currentSlot.id === targetSlotId) {
      await selectSlot(targetSlotId, true);
    }
    if (!isAuto && data.found_count === 0) {
      alert("영상 기준 조건에 맞는 새 후보를 찾지 못했습니다. 최대 길이 설정을 확인해 보세요.");
    }
  } catch (e) {
    console.error("Scene scout error:", e);
    if (!isAuto) alert(`영상 분석 오류: ${e}`);
  } finally {
    loadingSpinner.classList.add("hidden");
    if (btnSceneScout) {
      btnSceneScout.disabled = false;
      btnSceneScout.textContent = "🎬 영상 맞춤 찾기";
    }
  }
}

const btnSceneScout = document.getElementById("btnSceneScout");
if (btnSceneScout) {
  btnSceneScout.addEventListener("click", () => runSceneAwareScout(false));
}

sceneStage.addEventListener("click", (e) => {
  // Only open file picker if no scene yet (when scene exists, clicking toggles play/pause)
  if (!sceneMediaType) {
    sceneFileInput.click();
  } else {
    sceneIsPlaying = !sceneIsPlaying;
    if (sceneMediaType === "video") {
      if (sceneIsPlaying) sceneVideo.play().catch(() => {});
      else sceneVideo.pause();
    }
  }
});

document.getElementById("btnUploadSceneTop").addEventListener("click", (e) => {
  e.stopPropagation();
  sceneFileInput.click();
});

sceneFileInput.addEventListener("change", (e) => {
  if (e.target.files && e.target.files[0]) {
    uploadSceneFile(e.target.files[0]);
    e.target.value = "";
  }
});

sceneStage.addEventListener("dragover", (e) => {
  e.preventDefault();
  sceneStage.classList.add("drag-over");
});

sceneStage.addEventListener("dragleave", () => {
  sceneStage.classList.remove("drag-over");
});

sceneStage.addEventListener("drop", (e) => {
  e.preventDefault();
  sceneStage.classList.remove("drag-over");
  if (e.dataTransfer.files && e.dataTransfer.files[0]) {
    uploadSceneFile(e.dataTransfer.files[0]);
  }
});

btnDeleteScene.addEventListener("click", async (e) => {
  e.stopPropagation();
  if (!currentSlot) return;
  await fetch(`/api/slots/${currentSlot.id}/scene`, { method: "DELETE" });
  currentSlot.scene_media_url = null;
  currentSlot.scene_media_type = null;
  resetSceneUI();
});

// Scene Timeline Scrub, Handle Drag & Trim Controls
btnScenePlayPause.addEventListener("click", (e) => {
  e.stopPropagation();
  if (!sceneMediaType) return;
  sceneIsPlaying = !sceneIsPlaying;
  if (sceneMediaType === "video") {
    if (sceneIsPlaying) sceneVideo.play().catch(() => {});
    else sceneVideo.pause();
  }
});

sceneInHandle.addEventListener("pointerdown", (e) => {
  e.stopPropagation();
  if (sceneDuration <= 0) return;
  pushSceneUndo();
  isDraggingSceneHandle = "in";
  sceneInHandle.classList.add("dragging");
});

sceneOutHandle.addEventListener("pointerdown", (e) => {
  e.stopPropagation();
  if (sceneDuration <= 0) return;
  pushSceneUndo();
  isDraggingSceneHandle = "out";
  sceneOutHandle.classList.add("dragging");
});

sceneTimelineCanvas.addEventListener("pointerdown", (e) => {
  if (sceneDuration <= 0) return;
  isDraggingSceneHandle = "scrub";
  scrubSceneByEvent(e);
});

window.addEventListener("pointermove", (e) => {
  if (!isDraggingSceneHandle || sceneDuration <= 0) return;
  const rect = sceneTimelineCanvas.getBoundingClientRect();
  const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
  const t = ratio * sceneDuration;
  const effectiveOut = sceneOut > 0 ? sceneOut : sceneDuration;

  if (isDraggingSceneHandle === "in") {
    sceneIn = Math.min(t, Math.max(0, effectiveOut - 0.05));
    sceneCurrentTime = sceneIn;
    if (sceneMediaType === "video") sceneVideo.currentTime = sceneIn;
    else if (sceneMediaType === "gif") drawGifFrameAt(sceneIn);
    updateSceneTrimUI();
  } else if (isDraggingSceneHandle === "out") {
    sceneOut = Math.max(t, Math.min(sceneDuration, sceneIn + 0.05));
    sceneCurrentTime = sceneOut;
    if (sceneMediaType === "video") sceneVideo.currentTime = sceneOut;
    else if (sceneMediaType === "gif") drawGifFrameAt(sceneOut);
    updateSceneTrimUI();
  } else if (isDraggingSceneHandle === "scrub") {
    scrubSceneByEvent(e);
  }
});

window.addEventListener("pointerup", () => {
  if (isDraggingSceneHandle === "in" || isDraggingSceneHandle === "out") {
    sceneInHandle.classList.remove("dragging");
    sceneOutHandle.classList.remove("dragging");
    saveSceneTrimToBackend();
  }
  isDraggingSceneHandle = null;
});

function scrubSceneByEvent(e) {
  if (sceneDuration <= 0) return;
  const rect = sceneTimelineCanvas.getBoundingClientRect();
  const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
  const target = ratio * sceneDuration;
  sceneCurrentTime = target;
  if (sceneMediaType === "video") {
    sceneVideo.currentTime = target;
  } else if (sceneMediaType === "gif") {
    drawGifFrameAt(target);
  }
  drawSceneTimeline();
}

document.getElementById("btnSceneSetIn").addEventListener("click", () => {
  if (sceneDuration <= 0) return;
  pushSceneUndo();
  const effectiveOut = sceneOut > 0 ? sceneOut : sceneDuration;
  sceneIn = Math.min(sceneCurrentTime, Math.max(0, effectiveOut - 0.05));
  updateSceneTrimUI();
  saveSceneTrimToBackend();
});

document.getElementById("btnSceneSetOut").addEventListener("click", () => {
  if (sceneDuration <= 0) return;
  pushSceneUndo();
  sceneOut = Math.max(sceneCurrentTime, Math.min(sceneDuration, sceneIn + 0.05));
  updateSceneTrimUI();
  saveSceneTrimToBackend();
});

document.getElementById("btnSceneApply").addEventListener("click", async () => {
  soundOffset = Math.max(0, parseFloat(soundOffsetInput.value) || 0.0);
  await saveSceneTrimToBackend();
  // Restart scene & audio in sync from sceneIn!
  sceneCurrentTime = sceneIn;
  if (sceneMediaType === "video") sceneVideo.currentTime = sceneIn;
  audioTriggeredThisCycle = false;
  sceneIsPlaying = true;
});

document.getElementById("btnSceneUndo").addEventListener("click", async () => {
  if (sceneUndoStack.length === 0) return;
  const [prevIn, prevOut, prevOffset] = sceneUndoStack.pop();
  sceneIn = prevIn;
  sceneOut = prevOut;
  soundOffset = prevOffset;
  soundOffsetInput.value = soundOffset.toFixed(2);
  updateSceneTrimUI();
  await saveSceneTrimToBackend();
});

document.getElementById("btnSceneReset").addEventListener("click", async () => {
  if (sceneDuration <= 0) return;
  pushSceneUndo();
  sceneIn = 0.0;
  sceneOut = sceneDuration;
  soundOffset = 0.0;
  soundOffsetInput.value = "0.00";
  updateSceneTrimUI();
  if (currentSlot) {
    currentSlot.scene_in = 0.0;
    currentSlot.scene_out = 0.0;
    currentSlot.sound_offset = 0.0;
    await fetch(`/api/slots/${currentSlot.id}/scene/trim`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scene_in: 0.0, scene_out: 0.0, sound_offset: 0.0 }),
    });
  }
});

soundOffsetInput.addEventListener("change", () => {
  pushSceneUndo();
  soundOffset = Math.max(0, parseFloat(soundOffsetInput.value) || 0.0);
  saveSceneTrimToBackend();
  drawSceneTimeline();
});

// =========================================================================
// Original Source Link Opening, Clipboard Copy & AI Provenance Modal
// =========================================================================

const btnCopySourceUrl = document.getElementById("btnCopySourceUrl");
const sourceInfoModal = document.getElementById("sourceInfoModal");
const sourceModalBadge = document.getElementById("sourceModalBadge");
const sourceModalTitle = document.getElementById("sourceModalTitle");
const sourceModalDesc = document.getElementById("sourceModalDesc");
const sourceModalUrlsBox = document.getElementById("sourceModalUrlsBox");
const sourceModalUrlInput = document.getElementById("sourceModalUrlInput");
const sourceModalCatalogRow = document.getElementById("sourceModalCatalogRow");
const sourceModalCatalogInput = document.getElementById("sourceModalCatalogInput");
const btnModalOpenPopup = document.getElementById("btnModalOpenPopup");
const btnModalOpenExternal = document.getElementById("btnModalOpenExternal");

async function copyTextToClipboard(text, btnEl = null) {
  if (!text) return false;
  let ok = false;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      ok = true;
    }
  } catch (_) {}

  if (!ok) {
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      ok = document.execCommand("copy");
      document.body.removeChild(ta);
    } catch (_) {}
  }

  if (ok && btnEl) {
    const prev = btnEl.textContent;
    btnEl.textContent = "✅ 복사됨!";
    setTimeout(() => {
      btnEl.textContent = prev;
    }, 1500);
  }
  return ok;
}

function showSourceInfoModal(cand) {
  if (!cand || !sourceInfoModal) return;
  const kind = cand.source_kind || "external";
  const srcUrl = cand.original_source_url || cand.catalog_url || "";
  const catUrl = cand.catalog_url && cand.catalog_url !== srcUrl ? cand.catalog_url : "";

  sourceModalTitle.textContent = cand.name || "사운드 출처 정보";

  if (kind === "ai_generated") {
    sourceModalBadge.textContent = "🤖 AI / 로컬 자체 생성";
    sourceModalBadge.className = "px-2.5 py-0.5 rounded-full text-xs font-bold bg-purple-950 text-purple-300 border border-purple-700";
    sourceModalDesc.textContent = "이 효과음은 외부 사이트에서 다운로드한 음원이 아니라, AI·절차적 오디오 합성 엔진이 로컬에서 직접 생성한 100% 자체 제작 사운드입니다. 별도 외부 출처 URL이나 원작자 표기가 필요 없습니다.";
    sourceModalUrlsBox.classList.add("hidden");
    btnModalOpenPopup.classList.add("hidden");
    btnModalOpenExternal.classList.add("hidden");
  } else if (kind === "ai_mutated") {
    sourceModalBadge.textContent = "🎲 AI 로컬 변형 + 원곡 출처";
    sourceModalBadge.className = "px-2.5 py-0.5 rounded-full text-xs font-bold bg-indigo-950 text-indigo-300 border border-indigo-700";
    sourceModalDesc.textContent = `기존 CC0 원곡을 기반으로 로컬에서 음높이(피치)를 변형 생성한 사운드입니다. 원곡 저작 정보(${cand.license_type || "CC0"}) 및 출처 주소는 아래에서 확인/복사할 수 있습니다.`;
    sourceModalUrlsBox.classList.remove("hidden");
  } else {
    sourceModalBadge.textContent = `🌐 외부 CC0 출처 (${cand.license_type || "CC0"})`;
    sourceModalBadge.className = "px-2.5 py-0.5 rounded-full text-xs font-bold bg-emerald-950 text-emerald-300 border border-emerald-800";
    sourceModalDesc.textContent = `원작자: ${cand.author || "LotsOfSounds / CC0 Community"} · 라이선스: ${cand.license_type || "CC0"}. 아래 버튼으로 원본 페이지 창을 열거나 주소를 바로 복사할 수 있습니다.`;
    sourceModalUrlsBox.classList.remove("hidden");
  }

  if (srcUrl && kind !== "ai_generated") {
    sourceModalUrlInput.value = srcUrl;
    if (catUrl) {
      sourceModalCatalogInput.value = catUrl;
      sourceModalCatalogRow.classList.remove("hidden");
    } else {
      sourceModalCatalogRow.classList.add("hidden");
    }
    btnModalOpenPopup.classList.remove("hidden");
    btnModalOpenExternal.classList.remove("hidden");
    btnModalOpenPopup.dataset.url = srcUrl;
    btnModalOpenPopup.dataset.title = cand.name || "원본 출처";
    btnModalOpenExternal.dataset.url = srcUrl;
  }

  sourceInfoModal.classList.remove("hidden");
}

function closeSourceInfoModal() {
  if (sourceInfoModal) sourceInfoModal.classList.add("hidden");
}

async function openOriginalSource(url, candObj = null) {
  const targetCand = candObj || (currentIndex >= 0 ? currentCandidates[currentIndex] : null);
  if (targetCand && targetCand.source_kind === "ai_generated") {
    showSourceInfoModal(targetCand);
    return;
  }
  if (!url) {
    if (targetCand) showSourceInfoModal(targetCand);
    return;
  }

  const title = (targetCand && targetCand.name) ? targetCand.name : "원본 출처";

  // 1. Inside PyWebView Desktop App: open a real native popup window directly!
  try {
    if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.open_source_window === "function") {
      const res = await window.pywebview.api.open_source_window(url, title);
      if (res && res.status === "ok") return;
    }
  } catch (e) {
    console.warn("PyWebView native source window failed:", e);
  }

  // 2. Fallback: open via OS browser command + show in-app Source Inspector modal so URL is always visible & copyable!
  try {
    await fetch("/api/open_external_url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
  } catch (e) {
    console.warn("Backend external open failed:", e);
  }
  if (targetCand) showSourceInfoModal(targetCand);
}

btnOpenSourceUrl.addEventListener("click", () => {
  const cand = currentIndex >= 0 ? currentCandidates[currentIndex] : null;
  const url = btnOpenSourceUrl.dataset.url;
  openOriginalSource(url, cand);
});

if (btnCopySourceUrl) {
  btnCopySourceUrl.addEventListener("click", () => {
    const url = btnCopySourceUrl.dataset.url;
    if (url) copyTextToClipboard(url, btnCopySourceUrl);
  });
}

soundBadgeSource.addEventListener("click", () => {
  const cand = currentIndex >= 0 ? currentCandidates[currentIndex] : null;
  if (cand) showSourceInfoModal(cand);
});
soundBadgeSource.style.cursor = "pointer";
soundBadgeSource.title = "출처 상세 정보 및 주소 보기/복사";

if (document.getElementById("btnCloseSourceModal")) {
  document.getElementById("btnCloseSourceModal").addEventListener("click", closeSourceInfoModal);
}
if (document.getElementById("btnModalCloseBottom")) {
  document.getElementById("btnModalCloseBottom").addEventListener("click", closeSourceInfoModal);
}
if (sourceInfoModal) {
  sourceInfoModal.addEventListener("click", (e) => {
    if (e.target === sourceInfoModal) closeSourceInfoModal();
  });
}
if (document.getElementById("btnModalCopyPrimary")) {
  document.getElementById("btnModalCopyPrimary").addEventListener("click", (e) => {
    copyTextToClipboard(sourceModalUrlInput.value, e.currentTarget);
  });
}
if (document.getElementById("btnModalCopyCatalog")) {
  document.getElementById("btnModalCopyCatalog").addEventListener("click", (e) => {
    copyTextToClipboard(sourceModalCatalogInput.value, e.currentTarget);
  });
}
if (btnModalOpenPopup) {
  btnModalOpenPopup.addEventListener("click", async () => {
    const url = btnModalOpenPopup.dataset.url;
    const title = btnModalOpenPopup.dataset.title || "원본 출처";
    if (!url) return;
    if (window.pywebview?.api?.open_source_window) {
      await window.pywebview.api.open_source_window(url, title);
    } else {
      window.open(url, "_blank", "noopener,noreferrer");
    }
  });
}
if (btnModalOpenExternal) {
  btnModalOpenExternal.addEventListener("click", async () => {
    const url = btnModalOpenExternal.dataset.url;
    if (!url) return;
    if (window.pywebview?.api?.open_external_browser) {
      await window.pywebview.api.open_external_browser(url);
    } else {
      await fetch("/api/open_external_url", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
    }
  });
}

// =========================================================================
// Slots & Candidates API Calls
// =========================================================================

async function loadSlots() {
  try {
    const qParam = maxSearchDuration > 0 ? `?max_dur=${encodeURIComponent(maxSearchDuration)}` : "";
    const res = await fetch(`/api/slots${qParam}`);
    const data = await res.json();
    allSlots = data.slots || [];
    if (currentSlot) {
      const refreshed = allSlots.find(s => s.id === currentSlot.id);
      if (refreshed) currentSlot = refreshed;
    }
    renderSlotsList();
    updateProgress();

    if (!currentSlot && allSlots.length > 0) {
      const firstActive = allSlots.find(s => s.candidate_count > 0) || allSlots[0];
      // Do NOT auto-play sound on initial app startup!
      selectSlot(firstActive.id, false);
    }
  } catch (err) {
    console.error("Failed to load slots:", err);
  }
}

async function selectSlot(slotId, autoPlay = true) {
  const slot = allSlots.find(s => s.id === slotId);
  if (!slot) return;
  currentSlot = slot;

  currentSlotName.textContent = slot.name;
  currentSlotCategory.textContent = slot.category.toUpperCase();
  currentSlotMemo.textContent = slot.memo;
  activeSlotBadge.textContent = `${slot.name} (${slot.candidate_count || 0} 후보)`;

  // Load slot's attached scene media if any
  await loadSceneForSlot(slot, autoPlay);

  try {
    loadingSpinner.classList.remove("hidden");
    const qParam = maxSearchDuration > 0 ? `?max_dur=${encodeURIComponent(maxSearchDuration)}` : "";
    const res = await fetch(`/api/slots/${slotId}/candidates${qParam}`);
    const data = await res.json();
    currentCandidates = data.candidates || [];
    loadingSpinner.classList.add("hidden");

    renderCandidatesQueue();
    renderSlotsList();

    if (currentCandidates.length > 0) {
      const confirmedIdx = currentCandidates.findIndex(c => c.candidate_status === "confirmed");
      playCandidate(confirmedIdx >= 0 ? confirmedIdx : 0, autoPlay);
    } else {
      audioPlayer.pause();
      audioPlayer.removeAttribute("src");
      waveformPeaks = [];
      audioDuration = 0;
      btnOpenSourceUrl.classList.add("hidden");
      btnOpenSourceUrl.classList.remove("inline-flex");
      if (btnCopySourceUrl) {
        btnCopySourceUrl.classList.add("hidden");
        btnCopySourceUrl.classList.remove("inline-flex");
      }
      currentSoundName.textContent = maxSearchDuration > 0 ? `${maxSearchDuration}초 이하 후보 없음` : "후보 사운드 없음";
      currentSoundMeta.textContent = maxSearchDuration > 0 ? "'🔍 새로 찾기'를 누르면 해당 길이로 다시 검색합니다" : "LotsOfSounds 검색 중이거나 대기 상태";
      queuePosition.textContent = "후보 0 / 0";
      updateAudioTrimUI();
    }
  } catch (e) {
    loadingSpinner.classList.add("hidden");
    console.error("Failed to load candidates:", e);
  }
}

function playCandidate(index, autoPlay = true) {
  if (index < 0 || index >= currentCandidates.length) return;
  if (autoPlay) initAudioContext();

  currentIndex = index;
  const cand = currentCandidates[index];
  currentNormGain = cand.norm_gain || 1.0;
  applyAudioGainNodes();

  currentSoundName.textContent = cand.name;
  const kind = cand.source_kind || "external";
  if (kind === "ai_generated") {
    currentSoundMeta.textContent = `${cand.duration.toFixed(1)}s · 🤖 AI 자체 생성 음원 (출처/저작권 자유)`;
    soundBadgeSource.textContent = "🤖 AI 자체 생성 · 로컬 합성";
    soundBadgeSource.className = "bg-purple-950/80 text-purple-300 font-semibold px-3 py-1 rounded-full border border-purple-700/70 truncate";
  } else if (kind === "ai_mutated") {
    currentSoundMeta.textContent = `${cand.duration.toFixed(1)}s · 🎲 AI 변형 (${cand.license_type}) · ${cand.author || "LotsOfSounds"}`;
    soundBadgeSource.textContent = `🎲 AI 변형 · ${cand.license_type}`;
    soundBadgeSource.className = "bg-indigo-950/80 text-indigo-300 font-semibold px-3 py-1 rounded-full border border-indigo-700/70 truncate";
  } else {
    currentSoundMeta.textContent = `${cand.duration.toFixed(1)}s · ${cand.license_type} · ${cand.author || "LotsOfSounds"}`;
    soundBadgeSource.textContent = `LotsOfSounds · ${cand.license_type}`;
    soundBadgeSource.className = "bg-[#242424] text-emerald-400 font-semibold px-3 py-1 rounded-full border border-emerald-900/50 truncate";
  }
  soundMatchScore.textContent = `${Math.round(cand.score * 100)}% Match`;
  queuePosition.textContent = `후보 ${index + 1} / ${currentCandidates.length}`;

  // Show Original Source Link + Copy URL OR AI Provenance Button
  const srcUrl = cand.original_source_url || cand.catalog_url;
  if (kind === "ai_generated") {
    btnOpenSourceUrl.dataset.url = "";
    btnOpenSourceUrl.querySelector("span:first-child").textContent = "🤖 AI 자체 제작";
    btnOpenSourceUrl.querySelector("span:last-child").textContent = "ℹ";
    btnOpenSourceUrl.className = "inline-flex items-center gap-1 bg-purple-950/60 hover:bg-purple-900 text-purple-300 font-semibold px-3 py-1 rounded-full border border-purple-700/70 transition cursor-pointer shrink-0";
    if (btnCopySourceUrl) {
      btnCopySourceUrl.classList.add("hidden");
      btnCopySourceUrl.classList.remove("inline-flex");
    }
  } else if (srcUrl) {
    btnOpenSourceUrl.dataset.url = srcUrl;
    const isFs = srcUrl.includes("freesound.org");
    btnOpenSourceUrl.querySelector("span:first-child").textContent = isFs ? "🔗 원본 출처 (Freesound)" : "🔗 원본 출처";
    btnOpenSourceUrl.querySelector("span:last-child").textContent = "↗";
    btnOpenSourceUrl.className = "inline-flex items-center gap-1 bg-[#242424] hover:bg-[#333] text-sky-400 hover:text-sky-300 font-semibold px-3 py-1 rounded-full border border-sky-900/60 transition cursor-pointer shrink-0";
    if (btnCopySourceUrl) {
      btnCopySourceUrl.dataset.url = srcUrl;
      btnCopySourceUrl.title = `주소 복사: ${srcUrl}`;
      btnCopySourceUrl.classList.remove("hidden");
      btnCopySourceUrl.classList.add("inline-flex");
    }
  } else {
    btnOpenSourceUrl.classList.add("hidden");
    btnOpenSourceUrl.classList.remove("inline-flex");
    if (btnCopySourceUrl) {
      btnCopySourceUrl.classList.add("hidden");
      btnCopySourceUrl.classList.remove("inline-flex");
    }
  }

  // Update Like & Confirm button state
  const btnLike = document.getElementById("btnLike");
  if (cand.candidate_status === "liked") {
    btnLike.classList.add("text-yellow-400", "border-yellow-500");
  } else {
    btnLike.classList.remove("text-yellow-400", "border-yellow-500");
  }
  updateConfirmButtonUI(cand);

  document.querySelectorAll(".dock-btn").forEach(b => b.classList.remove("active", "border-[#1DB954]"));

  // Decode bottom waveform & load trim boundaries
  decodeWaveformForCandidate(cand);

  // Enforce normalized gain & master volume before and after loading audio
  audioPlayer.pause();
  applyAudioGainNodes();
  audioPlayer.src = cand.audio_url;
  audioPlayer.onloadedmetadata = () => {
    applyAudioGainNodes();
    const startAt = cand.audio_in || 0.0;
    audioPlayer.currentTime = startAt;
    if (!autoPlay) {
      audioTriggeredThisCycle = true;
      return;
    }
    if (loopMode === "sync" && sceneMediaType) {
      // Restart scene from sceneIn so scene & new sound start together!
      sceneCurrentTime = sceneIn;
      if (sceneMediaType === "video") {
        sceneVideo.currentTime = sceneIn;
        if (sceneIsPlaying) sceneVideo.play().catch(() => {});
      }
      audioTriggeredThisCycle = false;
    } else {
      audioPlayer.play().catch(() => {});
    }
  };
  audioPlayer.load();

  renderCandidatesQueue();
}

// =========================================================================
// Render Methods
// =========================================================================

function renderSlotsList() {
  const container = document.getElementById("tabSlots");
  const previousScroll = container.scrollTop;
  container.innerHTML = "";

  const filtered = allSlots.filter(s => {
    if (showConfirmedOnly && s.status !== "confirmed") return false;
    if (currentCategory !== "all" && s.category !== currentCategory) return false;
    const searchVal = document.getElementById("searchInput").value.toLowerCase();
    if (searchVal && !s.name.toLowerCase().includes(searchVal) && !s.memo.toLowerCase().includes(searchVal)) return false;
    return true;
  });

  if (filtered.length === 0) {
    container.innerHTML = `<div class="text-center text-gray-500 text-xs py-8">${showConfirmedOnly ? "확정 완료된 항목이 아직 없습니다." : "표시할 슬롯이 없습니다."}</div>`;
    return;
  }

  filtered.forEach(s => {
    const item = document.createElement("div");
    const isActive = currentSlot && currentSlot.id === s.id;
    const isConfirmed = s.status === "confirmed";

    let cardStyle = isActive ? "active bg-[#282828]" : "bg-[#181818] hover:bg-[#202020]";
    if (isConfirmed) {
      cardStyle = isActive
        ? "active border-l-4 border-[#1DB954] bg-emerald-900/45 ring-1 ring-[#1DB954]"
        : "border-l-4 border-[#1DB954] bg-emerald-950/35 hover:bg-emerald-950/55";
    }
    item.className = `slot-item p-2.5 rounded-lg flex items-center justify-between cursor-pointer text-[13px] transition ${cardStyle}`;

    let statusBadge = "";
    if (isConfirmed) {
      statusBadge = `<span class="bg-[#1DB954] text-black text-[11px] px-2.5 py-0.5 rounded-full font-black">✅ 확정</span>`;
    } else if (s.status === "on_hold") {
      statusBadge = `<span class="bg-[#242424] text-gray-500 text-xs px-2 py-0.5 rounded">보류</span>`;
    } else if (s.candidate_count > 0) {
      statusBadge = `<span class="bg-[#242424] text-gray-300 text-xs px-2 py-0.5 rounded font-mono">${s.candidate_count} 후보</span>`;
    } else {
      statusBadge = `<span class="bg-[#242424] text-gray-500 text-xs px-2 py-0.5 rounded">탐색 중</span>`;
    }

    const sceneIcon = s.scene_media_url ? "🎬 " : "";
    const titleColor = isConfirmed ? "text-emerald-300" : "text-gray-200";
    item.innerHTML = `
      <div class="truncate flex-1 pr-2 min-w-0">
        <div class="font-bold ${titleColor} truncate">${isConfirmed ? "✅ " : ""}${sceneIcon}${s.name}</div>
        <div class="text-xs text-gray-400 truncate">${s.memo}</div>
      </div>
      <div class="shrink-0">${statusBadge}</div>
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
    const isConfirmed = c.candidate_status === "confirmed";
    const activeCls = idx === currentIndex ? "active bg-[#282828]" : "bg-[#181818] hover:bg-[#222]";
    const confirmedCls = isConfirmed ? "border-l-4 border-[#1DB954] bg-emerald-950/35" : "";
    item.className = `candidate-item p-3 rounded-lg flex items-center justify-between cursor-pointer text-[14px] ${activeCls} ${confirmedCls}`;

    const likeIcon = c.candidate_status === "liked" ? "⭐ " : "";
    const confirmedTag = isConfirmed
      ? `<span class="ml-1.5 bg-[#1DB954] text-black text-[11px] px-2 py-0.5 rounded-full font-black">✅ 확정됨</span>`
      : "";
    const srcUrl = c.original_source_url || c.catalog_url;

    const infoDiv = document.createElement("div");
    infoDiv.className = "truncate flex-1 pr-2 min-w-0 flex items-center gap-2.5";
    infoDiv.innerHTML = `
      <span class="shrink-0 bg-[#242424] text-[#1DB954] font-mono font-bold text-xs px-2 py-1 rounded border border-[#333]">#${idx + 1}</span>
      <div class="truncate min-w-0">
        <div class="font-bold text-gray-100 truncate">${likeIcon}${c.name}${confirmedTag}</div>
        <div class="text-xs text-gray-400 font-mono">${c.duration.toFixed(1)}s · ${Math.round(c.score * 100)}% Match</div>
      </div>
    `;

    const btnsDiv = document.createElement("div");
    btnsDiv.className = "flex items-center gap-1.5 shrink-0";

    if (c.source_kind === "ai_generated") {
      const aiBtn = document.createElement("button");
      aiBtn.title = "AI 자체 제작 음원 정보 보기";
      aiBtn.className = "text-purple-300 hover:text-purple-100 px-2 py-0.5 text-xs bg-purple-950/60 rounded border border-purple-700/60";
      aiBtn.textContent = "🤖 AI";
      aiBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        showSourceInfoModal(c);
      });
      btnsDiv.appendChild(aiBtn);
    } else if (srcUrl) {
      const linkBtn = document.createElement("button");
      linkBtn.title = `원본 출처 창 열기 (${srcUrl})`;
      linkBtn.className = "text-sky-400 hover:text-sky-200 px-2 py-0.5 text-xs bg-[#242424] rounded border border-sky-900/40";
      linkBtn.textContent = "🔗";
      linkBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        openOriginalSource(srcUrl, c);
      });
      btnsDiv.appendChild(linkBtn);

      const copyBtn = document.createElement("button");
      copyBtn.title = `출처 주소 복사 (${srcUrl})`;
      copyBtn.className = "text-gray-300 hover:text-white px-2 py-0.5 text-xs bg-[#242424] rounded border border-[#383838]";
      copyBtn.textContent = "📋";
      copyBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        copyTextToClipboard(srcUrl, copyBtn);
      });
      btnsDiv.appendChild(copyBtn);
    }

    const playBtn = document.createElement("button");
    playBtn.className = "text-gray-300 hover:text-white px-2 py-0.5 text-xs bg-[#242424] rounded";
    playBtn.textContent = "듣기";
    btnsDiv.appendChild(playBtn);

    item.appendChild(infoDiv);
    item.appendChild(btnsDiv);
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

// =========================================================================
// Loop Mode Toggle (Sync Loop <-> Audio-Only Loop <-> Play Once)
// =========================================================================

function cycleLoopMode() {
  if (loopMode === "sync") {
    loopMode = "audio";
    btnLoopMode.textContent = "🔁 🔊 소리만 반복";
    btnLoopMode.classList.remove("text-emerald-400");
    btnLoopMode.classList.add("text-amber-400");
  } else if (loopMode === "audio") {
    loopMode = "once";
    btnLoopMode.textContent = "⏭️ 1회 후 다음";
    btnLoopMode.classList.remove("text-amber-400");
    btnLoopMode.classList.add("text-gray-300");
  } else {
    loopMode = "sync";
    btnLoopMode.textContent = "🔁 🎬+🔊 싱크 반복";
    btnLoopMode.classList.remove("text-gray-300");
    btnLoopMode.classList.add("text-emerald-400");
  }
}

btnLoopMode.addEventListener("click", cycleLoopMode);

// =========================================================================
// Action Handlers (Like, Similar, Mutate, Confirm, Export)
// =========================================================================

async function toggleLikeCurrent() {
  if (currentIndex < 0 || !currentCandidates[currentIndex]) return;
  const cand = currentCandidates[currentIndex];

  try {
    const res = await fetch("/api/actions/like", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slot_id: currentSlot.id, sound_id: cand.sound_id }),
    });
    const data = await res.json();
    cand.candidate_status = data.new_candidate_status;
    if (cand.candidate_status === "liked") addToCompareDock(cand);
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
  if (emptyIdx !== -1) compareDock[emptyIdx] = sound;
  else {
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

async function triggerSimilarCurrent() {
  if (currentIndex < 0 || !currentCandidates[currentIndex] || !currentSlot) return;
  const cand = currentCandidates[currentIndex];
  try {
    const res = await fetch("/api/actions/reorder_similar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slot_id: currentSlot.id, reference_sound_id: cand.sound_id }),
    });
    if (res.ok) {
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

async function triggerMutateCurrent() {
  if (currentIndex < 0 || !currentCandidates[currentIndex] || !currentSlot) return;
  const cand = currentCandidates[currentIndex];
  try {
    loadingSpinner.classList.remove("hidden");
    const res = await fetch("/api/actions/mutate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slot_id: currentSlot.id, sound_id: cand.sound_id }),
    });
    loadingSpinner.classList.add("hidden");
    if (res.ok) {
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

function updateConfirmButtonUI(cand) {
  const btn = document.getElementById("btnConfirm");
  if (!btn) return;
  const isConfirmed = cand && cand.candidate_status === "confirmed";
  if (isConfirmed) {
    btn.className = "flex items-center gap-1.5 px-6 py-2 rounded-full bg-amber-950/90 hover:bg-amber-900 text-amber-300 border border-amber-600 font-black text-[13px] transition shadow-xl active:scale-95";
    btn.title = "이 후보의 확정을 취소합니다";
    btn.innerHTML = `↩ 확정 취소`;
  } else {
    btn.className = "flex items-center gap-1.5 px-6 py-2 rounded-full bg-[#1DB954] hover:bg-[#1ed760] text-black font-black text-[13px] transition shadow-xl active:scale-95";
    btn.title = "슬롯 확정 및 채택 (Enter)";
    btn.innerHTML = `<svg class="w-4 h-4" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"></path></svg> 최종 채택 (Enter)`;
  }
}

async function unconfirmCurrent() {
  if (!currentSlot) return;
  const cand = currentIndex >= 0 ? currentCandidates[currentIndex] : null;

  try {
    const res = await fetch("/api/actions/unconfirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        slot_id: currentSlot.id,
        sound_id: cand && cand.candidate_status === "confirmed" ? cand.sound_id : null,
      }),
    });
    const data = await res.json();
    if (res.ok) {
      if (cand && cand.candidate_status === "confirmed") {
        cand.candidate_status = "pending";
      } else {
        currentCandidates.forEach(c => {
          if (c.candidate_status === "confirmed") c.candidate_status = "pending";
        });
      }
      currentSlot.status = data.new_slot_status || "has_candidates";
      updateConfirmButtonUI(cand);
      renderCandidatesQueue();
      renderSlotsList();
      updateProgress();
    }
  } catch (e) {
    console.error("Unconfirm error:", e);
  }
}

async function confirmCurrent() {
  if (currentIndex < 0 || !currentCandidates[currentIndex]) return;
  const cand = currentCandidates[currentIndex];
  if (cand.candidate_status === "confirmed") {
    await unconfirmCurrent();
    return;
  }
  const unityPath = unityPathInput.value.trim();

  try {
    await fetch("/api/actions/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        slot_id: currentSlot.id,
        sound_id: cand.sound_id,
        unity_project_path: unityPath || null,
      }),
    });
    cand.candidate_status = "confirmed";
    currentSlot.status = "confirmed";
    updateConfirmButtonUI(cand);

    renderCandidatesQueue();
    renderSlotsList();
    updateProgress();

    const nextSlot = allSlots.find(s => s.status !== "confirmed" && s.status !== "on_hold");
    if (nextSlot) selectSlot(nextSlot.id);
  } catch (e) {
    console.error("Confirm error:", e);
  }
}

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
      body: JSON.stringify({ unity_project_path: unityPath }),
    });
    const data = await res.json();
    if (res.ok) {
      alert(`[내보내기 완료!] 총 ${data.exported_count}개의 확정 사운드가 설정한 트림 구간 및 -1dBFS 마스터링 WAV로 복사되었으며 CREDITS.txt가 작성되었습니다.`);
    } else {
      alert(`내보내기 실패: ${data.detail || "오류 발생"}`);
    }
  } catch (e) {
    alert(`내보내기 요청 오류: ${e}`);
  }
});

// =========================================================================
// Player Events & Controls
// =========================================================================

audioPlayer.addEventListener("timeupdate", () => {
  const dur = audioDuration || audioPlayer.duration;
  if (!dur) return;
  timeCurrent.textContent = formatTime(audioPlayer.currentTime);
  timeDuration.textContent = formatTime(dur);
  timeSeek.value = (audioPlayer.currentTime / dur) * 100;
});

audioPlayer.addEventListener("play", () => {
  applyAudioGainNodes();
  playIcon.classList.add("hidden");
  pauseIcon.classList.remove("hidden");
});

audioPlayer.addEventListener("pause", () => {
  playIcon.classList.remove("hidden");
  pauseIcon.classList.add("hidden");
});

audioPlayer.addEventListener("ended", () => {
  if (loopMode === "audio" || (loopMode === "sync" && !sceneMediaType)) {
    audioPlayer.currentTime = audioIn;
    audioPlayer.play().catch(() => {});
  } else if (loopMode === "once") {
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
  const dur = audioDuration || audioPlayer.duration;
  if (!dur) return;
  audioPlayer.currentTime = (timeSeek.value / 100) * dur;
});

timeSeek.addEventListener("pointerup", () => timeSeek.blur());
timeSeek.addEventListener("change", () => timeSeek.blur());
if (volumeSlider) {
  volumeSlider.addEventListener("pointerup", () => volumeSlider.blur());
  volumeSlider.addEventListener("change", () => volumeSlider.blur());
}

function togglePlayback() {
  initAudioContext();
  const isPlayingNow = !audioPlayer.paused || (loopMode === "sync" && sceneMediaType && sceneIsPlaying);
  if (isPlayingNow) {
    audioPlayer.pause();
    if (loopMode === "sync" && sceneMediaType) {
      sceneIsPlaying = false;
      if (sceneMediaType === "video") sceneVideo.pause();
    }
  } else {
    if (loopMode === "sync" && sceneMediaType) {
      sceneCurrentTime = sceneIn;
      if (sceneMediaType === "video") {
        sceneVideo.currentTime = sceneIn;
        sceneVideo.play().catch(() => {});
      }
      sceneIsPlaying = true;
      audioTriggeredThisCycle = false;
    } else {
      if (audioPlayer.currentTime < audioIn || audioPlayer.currentTime >= audioOut) {
        audioPlayer.currentTime = audioIn;
      }
      audioPlayer.play().catch(() => {});
    }
  }
}

document.getElementById("btnPlay").addEventListener("click", togglePlayback);

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

const btnFilterConfirmed = document.getElementById("btnFilterConfirmed");
if (btnFilterConfirmed) {
  btnFilterConfirmed.addEventListener("click", () => {
    showConfirmedOnly = !showConfirmedOnly;
    if (showConfirmedOnly) {
      btnFilterConfirmed.className = "px-2 py-0.5 rounded-full bg-[#1DB954] text-black border border-[#1DB954] text-[11px] font-black transition";
      btnFilterConfirmed.textContent = "✅ 확정만 보기 중";
      document.getElementById("tabSlotsBtn").click();
    } else {
      btnFilterConfirmed.className = "px-2 py-0.5 rounded-full bg-[#242424] hover:bg-[#333] text-gray-300 border border-[#383838] text-[11px] font-semibold transition";
      btnFilterConfirmed.textContent = "확정만 보기";
    }
    renderSlotsList();
  });
}

document.querySelectorAll(".cat-btn").forEach(btn => {
  btn.addEventListener("click", (e) => {
    document.querySelectorAll(".cat-btn").forEach(b => b.classList.remove("active", "text-white", "bg-[#282828]"));
    e.target.classList.add("active", "text-white", "bg-[#282828]");
    currentCategory = e.target.dataset.cat;
    renderSlotsList();
  });
});

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

document.getElementById("toggleLeftBtn").addEventListener("click", () => {
  const isHidden = document.getElementById("leftPanel").classList.toggle("hidden");
  document.getElementById("leftResizer").classList.toggle("hidden", isHidden);
  setTimeout(resizeWaveformCanvas, 20);
});

// =========================================================================
// Resizable Panels (Left, Right, Bottom Footer)
// =========================================================================

function initResizers() {
  const leftPanel = document.getElementById("leftPanel");
  const rightPanel = document.getElementById("rightPanel");
  const bottomFooter = document.getElementById("bottomFooter");
  const leftResizer = document.getElementById("leftResizer");
  const rightResizer = document.getElementById("rightResizer");
  const bottomResizer = document.getElementById("bottomResizer");

  // Restore saved sizes
  const savedLeft = localStorage.getItem("soundreels_left_width");
  if (savedLeft) leftPanel.style.width = `${savedLeft}px`;
  const savedRight = localStorage.getItem("soundreels_right_width");
  if (savedRight) rightPanel.style.width = `${savedRight}px`;
  const savedBottom = localStorage.getItem("soundreels_footer_height");
  if (savedBottom) bottomFooter.style.height = `${savedBottom}px`;

  let activeResizer = null; // 'left' | 'right' | 'bottom' | null

  leftResizer.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    activeResizer = "left";
    leftResizer.classList.add("active");
    document.body.classList.add("resizing");
  });

  rightResizer.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    activeResizer = "right";
    rightResizer.classList.add("active");
    document.body.classList.add("resizing");
  });

  bottomResizer.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    activeResizer = "bottom";
    bottomResizer.classList.add("active");
    document.body.classList.add("resizing");
  });

  window.addEventListener("pointermove", (e) => {
    if (!activeResizer) return;
    if (activeResizer === "left") {
      const w = Math.max(150, Math.min(460, e.clientX));
      leftPanel.style.width = `${w}px`;
      resizeWaveformCanvas();
    } else if (activeResizer === "right") {
      const w = Math.max(220, Math.min(580, window.innerWidth - e.clientX));
      rightPanel.style.width = `${w}px`;
      resizeWaveformCanvas();
    } else if (activeResizer === "bottom") {
      const h = Math.max(54, Math.min(220, window.innerHeight - e.clientY));
      bottomFooter.style.height = `${h}px`;
    }
  });

  window.addEventListener("pointerup", () => {
    if (!activeResizer) return;
    leftResizer.classList.remove("active");
    rightResizer.classList.remove("active");
    bottomResizer.classList.remove("active");
    document.body.classList.remove("resizing");

    if (activeResizer === "left") {
      saveSetting("left_width", parseInt(leftPanel.style.width, 10));
    } else if (activeResizer === "right") {
      saveSetting("right_width", parseInt(rightPanel.style.width, 10));
    } else if (activeResizer === "bottom") {
      saveSetting("footer_height", parseInt(bottomFooter.style.height, 10));
    }
    activeResizer = null;
    resizeWaveformCanvas();
  });
}

initResizers();

// =========================================================================
// Load Persisted Settings from SQLite + Self-Update Checker
// =========================================================================

async function loadSettingsFromServer() {
  try {
    const res = await fetch("/api/settings");
    if (!res.ok) return;
    const data = await res.json();
    const s = data.settings || {};

    if (s.master_volume !== undefined) {
      const v = parseFloat(s.master_volume);
      if (!Number.isNaN(v)) {
        if (v > 0) preMuteVolume = v;
        applyMasterVolume(v, false);
      }
    }
    if (s.norm_enabled !== undefined) {
      isNormEnabled = s.norm_enabled !== "false";
      updateNormToggleUI();
      applyAudioGainNodes();
    }
    if (s.max_search_dur !== undefined) {
      const d = parseFloat(s.max_search_dur);
      maxSearchDuration = !Number.isNaN(d) && d > 0 ? d : 0;
      maxDurationInput.value = maxSearchDuration > 0 ? maxSearchDuration : "";
    }
    if (s.left_width) document.getElementById("leftPanel").style.width = `${s.left_width}px`;
    if (s.right_width) document.getElementById("rightPanel").style.width = `${s.right_width}px`;
    if (s.footer_height) document.getElementById("bottomFooter").style.height = `${s.footer_height}px`;
    if (s.unity_path && !unityPathInput.value) unityPathInput.value = s.unity_path;
    updateGeminiKeyBtnUI(s.has_gemini_key === "true");
    resizeWaveformCanvas();
  } catch (e) {
    console.warn("Could not load server settings:", e);
  }
}

function updateGeminiKeyBtnUI(hasKey) {
  const btn = document.getElementById("btnGeminiKey");
  if (!btn) return;
  if (hasKey) {
    btn.textContent = "🔑 Gemini 활성";
    btn.className = "hover:text-white px-3 py-1.5 rounded-full bg-emerald-950 hover:bg-emerald-900 border border-[#1DB954] text-xs font-bold text-emerald-300";
  } else {
    btn.textContent = "🔑 AI 키 (로컬)";
    btn.className = "hover:text-white px-3 py-1.5 rounded-full bg-[#222] hover:bg-[#303030] border border-[#333] text-xs font-semibold text-gray-300";
  }
}

const btnGeminiKey = document.getElementById("btnGeminiKey");
if (btnGeminiKey) {
  btnGeminiKey.addEventListener("click", async () => {
    const val = prompt(
      "Gemini API 키를 입력하면 영상/GIF 장면을 Gemini Vision으로 더 정밀하게 분석합니다.\n" +
      "(비워 두고 확인을 누르면 무료 로컬 비전 AI 모드로 전환됩니다.)",
      ""
    );
    if (val === null) return;
    await saveSetting("gemini_api_key", val.trim());
    await loadSettingsFromServer();
  });
}

// Max Duration Filter & Live Re-Scout Handlers
maxDurationInput.addEventListener("change", async () => {
  const val = parseFloat(maxDurationInput.value);
  maxSearchDuration = !Number.isNaN(val) && val > 0 ? val : 0;
  saveSetting("max_search_dur", maxSearchDuration > 0 ? String(maxSearchDuration) : "");
  await loadSlots();
  if (currentSlot) {
    await selectSlot(currentSlot.id, false);
  }
});

btnRescoutSlot.addEventListener("click", async () => {
  if (!currentSlot) return;
  const val = parseFloat(maxDurationInput.value);
  maxSearchDuration = !Number.isNaN(val) && val > 0 ? val : 0;
  saveSetting("max_search_dur", maxSearchDuration > 0 ? String(maxSearchDuration) : "");

  btnRescoutSlot.disabled = true;
  btnRescoutSlot.textContent = "⏳ 검색 중...";
  loadingSpinner.classList.remove("hidden");

  try {
    const res = await fetch(`/api/slots/${currentSlot.id}/rescout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ max_dur: maxSearchDuration > 0 ? maxSearchDuration : null }),
    });
    const data = await res.json();
    await loadSlots();
    await selectSlot(currentSlot.id, false);
    if (res.ok && data.found_count === 0) {
      alert(`${maxSearchDuration > 0 ? maxSearchDuration + "초 이하의 " : ""}새 후보를 찾지 못했습니다. 최대 길이를 조금 늘려서 다시 찾아보세요.`);
    }
  } catch (e) {
    alert(`재탐색 오류: ${e}`);
  } finally {
    loadingSpinner.classList.add("hidden");
    btnRescoutSlot.disabled = false;
    btnRescoutSlot.textContent = "🔍 새로 찾기";
  }
});

const btnCheckUpdate = document.getElementById("btnCheckUpdate");
const updateDot = document.getElementById("updateDot");

async function checkForUpdate(silent = true) {
  try {
    const res = await fetch("/api/system/check_update");
    const data = await res.json();

    if (data.has_update) {
      // Only show the update button when a newer version exists!
      btnCheckUpdate.classList.remove("hidden");
      btnCheckUpdate.classList.add("inline-flex");
      updateDot.classList.remove("hidden");
      btnCheckUpdate.querySelector("span:first-child").textContent = `🔄 업데이트 (${data.behind_count})`;

      if (!silent) {
        const msg = `최신 버전이 있습니다! (${data.local_rev} → ${data.remote_rev}, ${data.behind_count}개 커밋)\n\n${(data.changes || []).join("\n")}\n\n지금 최신 판으로 업데이트할까요?`;
        if (confirm(msg)) {
          btnCheckUpdate.querySelector("span:first-child").textContent = "⏳ 업데이트 중...";
          const applyRes = await fetch("/api/system/apply_update", { method: "POST" });
          const applyData = await applyRes.json();
          if (applyRes.ok) {
            alert(`업데이트 완료! (${applyData.local_rev})\n${applyData.needs_restart ? "백엔드 파일도 업데이트되었습니다. 프로그램을 껐다 켜면 백엔드까지 100% 반영됩니다." : "최신 UI로 새로고침합니다."}`);
            window.location.reload();
          } else {
            alert(`업데이트 실패: ${applyData.detail || "오류 발생"}`);
            btnCheckUpdate.querySelector("span:first-child").textContent = `🔄 업데이트 (${data.behind_count})`;
          }
        }
      }
    } else {
      // Hide update button completely when up-to-date
      btnCheckUpdate.classList.add("hidden");
      btnCheckUpdate.classList.remove("inline-flex");
      updateDot.classList.add("hidden");
    }
  } catch (e) {
    if (!silent) alert(`업데이트 확인 중 오류: ${e}`);
  }
}

btnCheckUpdate.addEventListener("click", () => checkForUpdate(false));

document.getElementById("searchInput").addEventListener("input", renderSlotsList);

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
        currentNormGain = sound.norm_gain || 1.0;
        applyAudioGainNodes();
        audioPlayer.src = sound.audio_url || `/api/sounds/${sound.sound_id}/audio`;
        audioPlayer.play().catch(() => {});
        currentSoundName.textContent = sound.name;
      }
    }
  });
});

// =========================================================================
// Keyboard Shortcuts (Reels + Scene Sync + Original Source 'O')
// =========================================================================

function isTextEditingTarget(target) {
  if (!target) return false;
  if (target.isContentEditable || target.tagName === "TEXTAREA" || target.tagName === "SELECT") return true;
  if (target.tagName === "INPUT") {
    const t = (target.type || "").toLowerCase();
    return !["range", "button", "checkbox", "radio", "submit", "reset"].includes(t);
  }
  return false;
}

// Prevent mouse clicks on buttons/range bars from trapping Spacebar focus
document.addEventListener("mouseup", (e) => {
  const active = document.activeElement;
  if (!active || active === document.body) return;
  if (active.tagName === "BUTTON" || (active.tagName === "INPUT" && active.type === "range")) {
    active.blur();
  }
});

window.addEventListener("keydown", (e) => {
  if (isTextEditingTarget(e.target)) return;

  if (e.key === "ArrowDown" || e.key === "j" || e.key === "J") {
    e.preventDefault();
    if (currentIndex + 1 < currentCandidates.length) playCandidate(currentIndex + 1);
  } else if (e.key === "ArrowUp" || e.key === "k" || e.key === "K") {
    e.preventDefault();
    if (currentIndex - 1 >= 0) playCandidate(currentIndex - 1);
  } else if (e.key === " " || e.code === "Space") {
    e.preventDefault();
    e.stopPropagation();
    if (document.activeElement && document.activeElement !== document.body && !isTextEditingTarget(document.activeElement)) {
      document.activeElement.blur();
    }
    if (!e.repeat) togglePlayback();
  } else if (e.key === "s" || e.key === "S") {
    e.preventDefault();
    toggleLikeCurrent();
  } else if (e.key === "f" || e.key === "F") {
    e.preventDefault();
    triggerSimilarCurrent();
  } else if (e.key === "m" || e.key === "M") {
    e.preventDefault();
    triggerMutateCurrent();
  } else if (e.key === "Escape") {
    closeSourceInfoModal();
  } else if (e.key === "o" || e.key === "O") {
    e.preventDefault();
    const cand = currentIndex >= 0 ? currentCandidates[currentIndex] : null;
    const url = btnOpenSourceUrl.dataset.url;
    if (url || cand) openOriginalSource(url, cand);
  } else if (e.key === "Enter") {
    e.preventDefault();
    confirmCurrent();
  } else if (e.key === "l" || e.key === "L") {
    e.preventDefault();
    cycleLoopMode();
  } else if (["1", "2", "3", "4", "5"].includes(e.key)) {
    const dockIdx = parseInt(e.key, 10) - 1;
    const btn = document.querySelector(`.dock-btn[data-dock="${dockIdx}"]`);
    if (btn) btn.click();
  }
}, true);

window.addEventListener("keyup", (e) => {
  if ((e.key === " " || e.code === "Space") && !isTextEditingTarget(e.target)) {
    e.preventDefault();
    e.stopPropagation();
  }
}, true);

// Initial load & poll
loadSettingsFromServer().then(() => loadSlots());
setInterval(loadSlots, 10000);
setTimeout(() => checkForUpdate(true), 2000);
