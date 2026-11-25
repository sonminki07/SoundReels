// SoundReels v1.2 Application Logic
// Features: Scene Preview (Video + Frame-Accurate GIF), Scene/Audio Non-Destructive Trimmer & Undo,
//           Scene-Audio Master Sync + Loop Modes, Original Source Direct Link, Reels Keyboard Flow

let allSlots = [];
let currentCategory = "all";
let slotFilterMode = "all"; // 'all' | 'liked' | 'confirmed'
let hideConfirmedSlots = localStorage.getItem("soundreels_hide_confirmed") === "true";
let currentSlot = null;
let currentCandidates = [];
let currentIndex = -1;
let similarAnchorSoundId = null;
const similarRoundStatusBySlot = {}; // { [slotId]: { roundNo, foundNew, totalRanked, promptUsed, queriesUsed } }
let similarClickTimer = null;
const slotCompareDocks = {}; // { [slotId]: [sound|null, ...] }
let topSearchMode = "sound"; // 'sound' | 'slot'
let currentTrash = [];
let activeAudioPoint = "playhead"; // 'playhead' | 'in' | 'out'
let audioNudgeSaveTimer = null;
let audioNudgeStopTimer = null;
let lastPopupImportSeq = 0;

// Full-Screen Pre-Export Review Mode State
let isReviewMode = false;
let reviewTab = "tasks"; // 'tasks' | 'passed'
let reviewSelectedSlotId = null;
let reviewOffsetTimer = null;

function getActiveCompareDock() {
  if (!currentSlot) return [null, null, null, null, null];
  if (!slotCompareDocks[currentSlot.id]) {
    slotCompareDocks[currentSlot.id] = [null, null, null, null, null];
  }
  return slotCompareDocks[currentSlot.id];
}

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

// Bottom Waveform Trimmer & Speed State
let waveformPeaks = [];
let audioDuration = 0.0;
let audioIn = 0.0;
let audioOut = 0.0;
let currentPlaybackRate = 1.0;
let currentPreservePitch = true;
let currentReverse = false;
let currentPitchSemitones = 0.0;
let currentTailSec = 0.0;
let audioUndoStack = [];
let editingMemoSlotId = null;

function getCandidatePlaybackUrl(cand) {
  if (!cand) return "";
  const base = cand.audio_url || `/api/sounds/${cand.sound_id}/audio`;
  const params = new URLSearchParams();
  if (cand.reverse) params.set("reverse", "1");
  const pitch = Number(cand.pitch_semitones || 0);
  if (Math.abs(pitch) >= 0.1) params.set("pitch", pitch.toFixed(1));
  const tail = Number(cand.tail_sec || 0);
  if (tail >= 0.05) {
    params.set("tail", tail.toFixed(1));
    const outVal = Number(cand.audio_out || 0);
    if (outVal > 0) params.set("out", outVal.toFixed(3));
  }
  const qs = params.toString();
  return qs ? `${base}?${qs}` : base;
}
let audioDecodeAbort = null;
let waveformDecodeSeq = 0;
let isDraggingAudioHandle = null; // 'in' | 'out' | 'scrub' | null

// Shortcut & Playback Settings State
const DEFAULT_KEYMAP_SETTINGS = {
  doubleSpaceReplay: true,
  doubleSpaceWindowMs: 320,
  arrowUpDownAlso: true,
  keys: {
    playPause: "Space",
    replay: "KeyR",
    prevCandidate: "ArrowLeft",
    nextCandidate: "ArrowRight",
    like: "KeyS",
    similar: "KeyF",
    mutate: "KeyM",
    confirm: "Enter",
    loopMode: "KeyL",
    sourceLink: "KeyO",
  },
};
let keymapSettings = JSON.parse(JSON.stringify(DEFAULT_KEYMAP_SETTINGS));
let lastSpacePressAt = 0;
let capturingKeyAction = null;

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
    const baseOut = audioOut > audioIn ? audioOut : audioDuration;
    const effectiveOut = (currentTailSec >= 0.05 && audioOut > audioIn) ? (baseOut + currentTailSec) : baseOut;
    if (audioPlayer.currentTime >= effectiveOut - 0.025) {
      if (loopMode === "audio" || (loopMode === "sync" && !sceneMediaType)) {
        audioPlayer.currentTime = audioIn;
        audioPlayer.play().catch(() => {});
      } else if (loopMode === "sync" && sceneMediaType) {
        // Pause and wait for the scene loop to restart
        audioPlayer.pause();
        audioPlayer.currentTime = audioIn;
      } else if (loopMode === "single") {
        audioPlayer.pause();
        audioPlayer.currentTime = audioIn;
      } else if (loopMode === "single") {
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
  const mySeq = ++waveformDecodeSeq;

  waveformPeaks = [];
  audioDuration = cand.duration || 0;
  audioIn = cand.audio_in || 0.0;
  audioOut = cand.audio_out > 0 ? cand.audio_out : audioDuration;
  currentPlaybackRate = Number(cand.playback_rate) || 1.0;
  currentPreservePitch = cand.preserve_pitch !== 0;
  currentReverse = !!cand.reverse;
  currentPitchSemitones = Number(cand.pitch_semitones) || 0.0;
  currentTailSec = Number(cand.tail_sec) || 0.0;
  audioUndoStack = [];
  applyAudioPlaybackSpeed();
  updateAudioTrimUI();

  try {
    initAudioContext();
    const resp = await fetch(getCandidatePlaybackUrl(cand), { signal: audioDecodeAbort.signal });
    const buf = await resp.arrayBuffer();
    if (mySeq !== waveformDecodeSeq) return;
    const decoded = await audioCtx.decodeAudioData(buf);
    if (mySeq !== waveformDecodeSeq) return;
    if (!currentCandidates[currentIndex] || currentCandidates[currentIndex].sound_id !== cand.sound_id) return;

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

const audioRateSlider = document.getElementById("audioRateSlider");
const audioRateLabel = document.getElementById("audioRateLabel");
const btnPreservePitch = document.getElementById("btnPreservePitch");

function applyAudioPlaybackSpeed() {
  const r = Math.max(0.25, Math.min(2.5, Number(currentPlaybackRate) || 1.0));
  currentPlaybackRate = r;
  audioPlayer.playbackRate = r;
  audioPlayer.preservesPitch = !!currentPreservePitch;
  audioPlayer.mozPreservesPitch = !!currentPreservePitch;
  audioPlayer.webkitPreservesPitch = !!currentPreservePitch;

  if (audioRateSlider) audioRateSlider.value = r;
  if (audioRateLabel) audioRateLabel.textContent = `${r.toFixed(2)}x`;
  document.querySelectorAll(".speed-preset-btn").forEach(btn => {
    const pr = parseFloat(btn.dataset.rate);
    const active = Math.abs(pr - r) < 0.01;
    btn.className = active
      ? "speed-preset-btn px-1.5 py-0.5 rounded text-[11px] bg-[#1DB954] text-black font-bold"
      : "speed-preset-btn px-1.5 py-0.5 rounded text-[11px] text-gray-300 hover:bg-[#2c2c2c]";
  });
  if (btnPreservePitch) {
    btnPreservePitch.textContent = currentPreservePitch ? "🎵 피치유지" : "🎚️ 피치연동";
    btnPreservePitch.className = currentPreservePitch
      ? "px-1.5 py-0.5 rounded bg-emerald-950 text-emerald-300 border border-emerald-800 text-[11px] font-bold hover:bg-emerald-900"
      : "px-1.5 py-0.5 rounded bg-amber-950 text-amber-300 border border-amber-700 text-[11px] font-bold hover:bg-amber-900";
  }
  const btnReverse = document.getElementById("btnToggleReverse");
  if (btnReverse) {
    btnReverse.textContent = currentReverse ? "🔁 역재생 중" : "🔁 역재생";
    btnReverse.className = currentReverse
      ? "px-1.5 py-0.5 rounded bg-cyan-400 text-black border border-cyan-300 text-[11px] font-black transition"
      : "px-1.5 py-0.5 rounded bg-[#242424] text-gray-300 border border-[#383838] text-[11px] font-bold hover:bg-[#333] transition";
  }
  const p = Math.max(-12, Math.min(12, Number(currentPitchSemitones) || 0.0));
  currentPitchSemitones = p;
  const audioPitchSlider = document.getElementById("audioPitchSlider");
  const audioPitchLabel = document.getElementById("audioPitchLabel");
  if (audioPitchSlider) audioPitchSlider.value = p;
  if (audioPitchLabel) audioPitchLabel.textContent = `${p > 0 ? "+" : ""}${Number(p.toFixed(1))}st`;
  const t = Math.max(0, Math.min(3.0, Number(currentTailSec) || 0.0));
  currentTailSec = t;
  const audioTailSlider = document.getElementById("audioTailSlider");
  const audioTailLabel = document.getElementById("audioTailLabel");
  if (audioTailSlider) audioTailSlider.value = t;
  if (audioTailLabel) audioTailLabel.textContent = `${t.toFixed(1)}s`;
}

function updateAudioTrimUI() {
  const dur = audioDuration || 1;
  const inPct = Math.max(0, Math.min(100, (audioIn / dur) * 100));
  const outPct = Math.max(inPct, Math.min(100, (audioOut / dur) * 100));
  audioInHandle.style.left = `${inPct}%`;
  audioOutHandle.style.left = `${outPct}%`;

  const selLen = Math.max(0, audioOut - audioIn);
  const effLen = (selLen + currentTailSec) / Math.max(0.25, currentPlaybackRate || 1.0);
  const isFull = audioIn <= 0.01 && Math.abs(audioOut - audioDuration) <= 0.05;
  const speedSuffix = (Math.abs(currentPlaybackRate - 1.0) >= 0.01 || currentTailSec >= 0.05)
    ? ` → 실효 ${effLen.toFixed(2)}s (${currentPlaybackRate.toFixed(2)}x)`
    : "";
  const pitchSuffix = Math.abs(currentPitchSemitones) >= 0.1
    ? ` · 🎵 ${currentPitchSemitones > 0 ? "+" : ""}${currentPitchSemitones.toFixed(1)}st`
    : "";
  const tailSuffix = currentTailSec >= 0.05
    ? ` · ✨여운 +${currentTailSec.toFixed(1)}s`
    : "";
  const revPrefix = currentReverse ? "🔁 역재생 · " : "";
  audioTrimRangeLabel.textContent = isFull
    ? `${revPrefix}구간: 전체 (0.00s ~ ${audioDuration.toFixed(2)}s)${speedSuffix}${pitchSuffix}${tailSuffix}`
    : `${revPrefix}선택 구간: ${audioIn.toFixed(2)}s ~ ${audioOut.toFixed(2)}s (${selLen.toFixed(2)}s)${speedSuffix}${pitchSuffix}${tailSuffix}`;
}

function pushAudioUndo() {
  audioUndoStack.push([audioIn, audioOut, currentPlaybackRate, currentPreservePitch, currentReverse, currentPitchSemitones, currentTailSec]);
  if (audioUndoStack.length > 20) audioUndoStack.shift();
}

async function saveAudioTrimToBackend() {
  if (!currentSlot || currentIndex < 0 || !currentCandidates[currentIndex]) return;
  const cand = currentCandidates[currentIndex];
  if (cand.modify_ok === false) return;
  cand.audio_in = audioIn;
  cand.audio_out = audioOut;
  cand.playback_rate = Number(currentPlaybackRate.toFixed(2));
  cand.preserve_pitch = currentPreservePitch ? 1 : 0;
  cand.reverse = currentReverse ? 1 : 0;
  cand.pitch_semitones = Number((Number(currentPitchSemitones) || 0.0).toFixed(1));
  cand.tail_sec = Number((Number(currentTailSec) || 0.0).toFixed(1));
  try {
    await fetch("/api/actions/trim_audio", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        slot_id: currentSlot.id,
        sound_id: cand.sound_id,
        audio_in: Number(audioIn.toFixed(3)),
        audio_out: Number(audioOut.toFixed(3)),
        playback_rate: cand.playback_rate,
        preserve_pitch: cand.preserve_pitch,
        reverse: cand.reverse,
        pitch_semitones: cand.pitch_semitones,
        tail_sec: cand.tail_sec,
      }),
    });
  } catch (e) {
    console.error("Failed to save audio trim:", e);
  }
}

function setActiveAudioPoint(point) {
  const isTrimOpen = audioTrimControls && !audioTrimControls.classList.contains("hidden");
  activeAudioPoint = (isTrimOpen && (point === "in" || point === "out")) ? point : "playhead";

  if (audioInHandle) {
    audioInHandle.classList.toggle("ring-2", activeAudioPoint === "in");
    audioInHandle.classList.toggle("ring-cyan-400", activeAudioPoint === "in");
  }
  if (audioOutHandle) {
    audioOutHandle.classList.toggle("ring-2", activeAudioPoint === "out");
    audioOutHandle.classList.toggle("ring-amber-400", activeAudioPoint === "out");
  }

  const badge = document.getElementById("audioNudgePointBadge");
  if (badge) {
    if (activeAudioPoint === "in") {
      badge.textContent = "📍 시작점 이동 (A/D ±0.05s)";
      badge.className = "px-2 py-0.5 rounded bg-emerald-950/70 border border-emerald-700 text-[10px] text-emerald-300 shrink-0";
    } else if (activeAudioPoint === "out") {
      badge.textContent = "🏁 끝점 이동 (A/D ±0.05s)";
      badge.className = "px-2 py-0.5 rounded bg-amber-950/70 border border-amber-700 text-[10px] text-amber-300 shrink-0";
    } else {
      badge.textContent = "🎯 재생위치 (A/D ±0.05s)";
      badge.className = "px-2 py-0.5 rounded bg-[#1d1d1d] border border-[#333] text-[10px] text-cyan-300 shrink-0";
    }
  }
}

function playNudgeSnippetAt(startSec) {
  if (!audioPlayer.src || audioDuration <= 0) return;
  initAudioContext();
  const clamped = Math.max(0, Math.min(audioDuration, startSec));
  audioPlayer.currentTime = clamped;
  if (audioNudgeStopTimer) clearTimeout(audioNudgeStopTimer);
  if (audioPlayer.paused) {
    audioPlayer.play().catch(() => {});
    audioNudgeStopTimer = setTimeout(() => {
      audioPlayer.pause();
    }, 180);
  }
}

function scheduleSaveAudioTrim() {
  if (audioNudgeSaveTimer) clearTimeout(audioNudgeSaveTimer);
  audioNudgeSaveTimer = setTimeout(() => {
    saveAudioTrimToBackend();
  }, 240);
}

function nudgeAudioPoint(direction, isLarge = false) {
  if (audioDuration <= 0 || currentIndex < 0) return;
  const step = isLarge ? 0.25 : 0.05;
  const delta = direction * step;
  const isTrimOpen = audioTrimControls && !audioTrimControls.classList.contains("hidden");

  if (isTrimOpen && activeAudioPoint === "in") {
    pushAudioUndo();
    audioIn = Math.max(0, Math.min(audioOut - 0.05, audioIn + delta));
    updateAudioTrimUI();
    playNudgeSnippetAt(audioIn);
    scheduleSaveAudioTrim();
    return;
  }
  if (isTrimOpen && activeAudioPoint === "out") {
    pushAudioUndo();
    audioOut = Math.min(audioDuration, Math.max(audioIn + 0.05, audioOut + delta));
    updateAudioTrimUI();
    playNudgeSnippetAt(Math.max(audioIn, audioOut - 0.14));
    scheduleSaveAudioTrim();
    return;
  }

  // Default: nudge playhead frame-by-frame
  setActiveAudioPoint("playhead");
  const baseTime = Number.isFinite(audioPlayer.currentTime) ? audioPlayer.currentTime : audioIn;
  const nextTime = Math.max(0, Math.min(audioDuration, baseTime + delta));
  playNudgeSnippetAt(nextTime);
  if (timeCurrent) timeCurrent.textContent = formatTime(nextTime);
  if (timeSeek && audioDuration > 0) timeSeek.value = (nextTime / audioDuration) * 100;
}

// Mouse / Pointer Dragging on Waveform & Handles
audioInHandle.addEventListener("pointerdown", (e) => {
  e.stopPropagation();
  pushAudioUndo();
  isDraggingAudioHandle = "in";
  setActiveAudioPoint("in");
  audioInHandle.classList.add("dragging");
});

audioOutHandle.addEventListener("pointerdown", (e) => {
  e.stopPropagation();
  pushAudioUndo();
  isDraggingAudioHandle = "out";
  setActiveAudioPoint("out");
  audioOutHandle.classList.add("dragging");
});

audioWaveformCanvas.addEventListener("pointerdown", (e) => {
  if (audioDuration <= 0) return;
  isDraggingAudioHandle = "scrub";
  setActiveAudioPoint("playhead");
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
  if (isHidden) setActiveAudioPoint("playhead");
});

// Audio Trim Buttons
document.getElementById("btnAudioSetIn").addEventListener("click", () => {
  if (audioDuration <= 0) return;
  pushAudioUndo();
  audioIn = Math.min(audioPlayer.currentTime, Math.max(0, audioOut - 0.05));
  setActiveAudioPoint("in");
  updateAudioTrimUI();
  saveAudioTrimToBackend();
});

document.getElementById("btnAudioSetOut").addEventListener("click", () => {
  if (audioDuration <= 0) return;
  pushAudioUndo();
  audioOut = Math.max(audioPlayer.currentTime, Math.min(audioDuration, audioIn + 0.05));
  setActiveAudioPoint("out");
  updateAudioTrimUI();
  saveAudioTrimToBackend();
});

document.getElementById("btnAudioApply").addEventListener("click", async () => {
  await saveAudioTrimToBackend();
  triggerAudioFromIn();
});

async function reloadAudioForReverseToggle(cand, autoPlay = true) {
  if (!cand) return;
  await saveAudioTrimToBackend();
  renderCandidatesQueue();
  decodeWaveformForCandidate(cand);
  audioPlayer.pause();
  audioPlayer.src = getCandidatePlaybackUrl(cand);
  audioPlayer.onloadedmetadata = () => {
    if (!currentCandidates[currentIndex] || currentCandidates[currentIndex].sound_id !== cand.sound_id) return;
    applyAudioGainNodes();
    applyAudioPlaybackSpeed();
    audioPlayer.currentTime = audioIn;
    timeCurrent.textContent = formatTime(audioIn);
    if (autoPlay) audioPlayer.play().catch(() => {});
  };
  audioPlayer.load();
}

document.getElementById("btnAudioUndo").addEventListener("click", async () => {
  if (audioUndoStack.length === 0) return;
  const [prevIn, prevOut, prevRate, prevPitch, prevRev, prevPitchSt, prevTail] = audioUndoStack.pop();
  const revChanged = prevRev !== undefined && prevRev !== currentReverse;
  const pitchStChanged = prevPitchSt !== undefined && Math.abs(prevPitchSt - currentPitchSemitones) >= 0.1;
  const tailChanged = prevTail !== undefined && Math.abs(prevTail - currentTailSec) >= 0.05;
  audioIn = prevIn;
  audioOut = prevOut;
  if (prevRate !== undefined) currentPlaybackRate = prevRate;
  if (prevPitch !== undefined) currentPreservePitch = prevPitch;
  if (prevRev !== undefined) currentReverse = !!prevRev;
  if (prevPitchSt !== undefined) currentPitchSemitones = Number(prevPitchSt) || 0.0;
  if (prevTail !== undefined) currentTailSec = Number(prevTail) || 0.0;
  applyAudioPlaybackSpeed();
  updateAudioTrimUI();
  if ((revChanged || pitchStChanged || tailChanged) && currentIndex >= 0 && currentCandidates[currentIndex]) {
    currentCandidates[currentIndex].pitch_semitones = currentPitchSemitones;
    currentCandidates[currentIndex].tail_sec = currentTailSec;
    await reloadAudioForReverseToggle(currentCandidates[currentIndex], false);
  } else {
    await saveAudioTrimToBackend();
  }
});

document.getElementById("btnAudioReset").addEventListener("click", async () => {
  if (audioDuration <= 0) return;
  pushAudioUndo();
  const revChanged = currentReverse;
  const pitchStChanged = Math.abs(currentPitchSemitones) >= 0.1;
  const tailChanged = currentTailSec >= 0.05;
  audioIn = 0.0;
  audioOut = audioDuration;
  currentPlaybackRate = 1.0;
  currentPreservePitch = true;
  currentReverse = false;
  currentPitchSemitones = 0.0;
  currentTailSec = 0.0;
  applyAudioPlaybackSpeed();
  updateAudioTrimUI();
  if ((revChanged || pitchStChanged || tailChanged) && currentIndex >= 0 && currentCandidates[currentIndex]) {
    currentCandidates[currentIndex].pitch_semitones = 0.0;
    currentCandidates[currentIndex].tail_sec = 0.0;
    await reloadAudioForReverseToggle(currentCandidates[currentIndex], false);
  } else {
    await saveAudioTrimToBackend();
  }
});

const btnToggleReverseEl = document.getElementById("btnToggleReverse");
if (btnToggleReverseEl) {
  btnToggleReverseEl.addEventListener("click", async () => {
    if (currentIndex < 0 || !currentCandidates[currentIndex]) return;
    const cand = currentCandidates[currentIndex];
    pushAudioUndo();
    const dur = audioDuration || cand.duration || 0;
    const prevIn = audioIn;
    const prevOut = audioOut;
    currentReverse = !currentReverse;
    if (dur > 0 && (prevIn > 0.01 || Math.abs(prevOut - dur) > 0.05)) {
      audioIn = Math.max(0, Number((dur - prevOut).toFixed(3)));
      audioOut = Math.min(dur, Math.max(audioIn + 0.05, Number((dur - prevIn).toFixed(3))));
    }
    applyAudioPlaybackSpeed();
    updateAudioTrimUI();
    await reloadAudioForReverseToggle(cand, true);
  });
}

document.querySelectorAll(".speed-preset-btn").forEach(btn => {
  btn.addEventListener("click", async () => {
    const rate = parseFloat(btn.dataset.rate);
    if (Number.isNaN(rate)) return;
    pushAudioUndo();
    currentPlaybackRate = rate;
    applyAudioPlaybackSpeed();
    updateAudioTrimUI();
    await saveAudioTrimToBackend();
  });
});

if (audioRateSlider) {
  audioRateSlider.addEventListener("input", () => {
    currentPlaybackRate = parseFloat(audioRateSlider.value) || 1.0;
    applyAudioPlaybackSpeed();
    updateAudioTrimUI();
  });
  audioRateSlider.addEventListener("change", async () => {
    audioRateSlider.blur();
    await saveAudioTrimToBackend();
  });
}

if (btnPreservePitch) {
  btnPreservePitch.addEventListener("click", async () => {
    pushAudioUndo();
    currentPreservePitch = !currentPreservePitch;
    applyAudioPlaybackSpeed();
    await saveAudioTrimToBackend();
  });
}

const audioPitchSliderEl = document.getElementById("audioPitchSlider");
if (audioPitchSliderEl) {
  audioPitchSliderEl.addEventListener("input", () => {
    currentPitchSemitones = parseFloat(audioPitchSliderEl.value) || 0.0;
    applyAudioPlaybackSpeed();
    updateAudioTrimUI();
  });
  audioPitchSliderEl.addEventListener("change", async () => {
    audioPitchSliderEl.blur();
    if (currentIndex < 0 || !currentCandidates[currentIndex]) return;
    const cand = currentCandidates[currentIndex];
    cand.pitch_semitones = Number((parseFloat(audioPitchSliderEl.value) || 0.0).toFixed(1));
    currentPitchSemitones = cand.pitch_semitones;
    await reloadAudioForReverseToggle(cand, !audioPlayer.paused);
  });
}

const btnResetPitchEl = document.getElementById("btnResetPitch");
if (btnResetPitchEl) {
  btnResetPitchEl.addEventListener("click", async () => {
    if (Math.abs(currentPitchSemitones) < 0.1) return;
    pushAudioUndo();
    currentPitchSemitones = 0.0;
    applyAudioPlaybackSpeed();
    updateAudioTrimUI();
    if (currentIndex >= 0 && currentCandidates[currentIndex]) {
      const cand = currentCandidates[currentIndex];
      cand.pitch_semitones = 0.0;
      await reloadAudioForReverseToggle(cand, !audioPlayer.paused);
    }
  });
}

const audioTailSliderEl = document.getElementById("audioTailSlider");
if (audioTailSliderEl) {
  audioTailSliderEl.addEventListener("input", () => {
    currentTailSec = parseFloat(audioTailSliderEl.value) || 0.0;
    applyAudioPlaybackSpeed();
    updateAudioTrimUI();
  });
  audioTailSliderEl.addEventListener("change", async () => {
    audioTailSliderEl.blur();
    if (currentIndex < 0 || !currentCandidates[currentIndex]) return;
    const cand = currentCandidates[currentIndex];
    cand.tail_sec = Number((parseFloat(audioTailSliderEl.value) || 0.0).toFixed(1));
    currentTailSec = cand.tail_sec;
    await reloadAudioForReverseToggle(cand, !audioPlayer.paused);
  });
}

const btnResetTailEl = document.getElementById("btnResetTail");
if (btnResetTailEl) {
  btnResetTailEl.addEventListener("click", async () => {
    if (currentTailSec < 0.05) return;
    pushAudioUndo();
    currentTailSec = 0.0;
    applyAudioPlaybackSpeed();
    updateAudioTrimUI();
    if (currentIndex >= 0 && currentCandidates[currentIndex]) {
      const cand = currentCandidates[currentIndex];
      cand.tail_sec = 0.0;
      await reloadAudioForReverseToggle(cand, !audioPlayer.paused);
    }
  });
}

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

  // Always auto-copy the source URL to clipboard immediately when clicked!
  copyTextToClipboard(url, btnCopySourceUrl);

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

const HANDOFF_STATUS_META = {
  todo: { label: "📝 확인필요", cls: "bg-amber-950/90 text-amber-300 border border-amber-700" },
  in_progress: { label: "🔄 작업중", cls: "bg-sky-950/90 text-sky-300 border border-sky-700" },
  needs_review: { label: "👀 인수요청", cls: "bg-indigo-950/90 text-indigo-200 border border-indigo-600" },
  ready: { label: "✅ 인수완료", cls: "bg-emerald-950/90 text-emerald-300 border border-emerald-700" },
};

function updateCurrentSlotWorkNoteUI() {
  const banner = document.getElementById("currentSlotWorkNoteBanner");
  if (!banner) return;
  if (!currentSlot || (!currentSlot.work_memo && !currentSlot.assignee && !currentSlot.handoff_status)) {
    banner.classList.add("hidden");
    banner.classList.remove("inline-flex");
    banner.textContent = "";
    return;
  }
  const st = HANDOFF_STATUS_META[currentSlot.handoff_status];
  const parts = [];
  if (st) parts.push(st.label);
  if (currentSlot.assignee) parts.push(`👤 ${currentSlot.assignee}`);
  if (currentSlot.work_memo) parts.push(`📝 ${currentSlot.work_memo}`);
  banner.textContent = parts.join(" · ");
  banner.classList.remove("hidden");
  banner.classList.add("inline-flex");
}

function openSlotMemoModal(slotId) {
  const slot = allSlots.find(s => s.id === slotId);
  if (!slot) return;
  editingMemoSlotId = slotId;
  const modal = document.getElementById("slotMemoModal");
  const titleEl = document.getElementById("slotMemoTitle");
  const statusEl = document.getElementById("slotMemoStatusSelect");
  const assigneeEl = document.getElementById("slotMemoAssignee");
  const inputEl = document.getElementById("slotMemoInput");
  if (!modal) return;
  if (titleEl) titleEl.textContent = `${slot.name} — 작업 인수인계 메모`;
  if (statusEl) statusEl.value = slot.handoff_status || "";
  if (assigneeEl) assigneeEl.value = slot.assignee || "";
  if (inputEl) inputEl.value = slot.work_memo || "";
  modal.classList.remove("hidden");
  if (inputEl) setTimeout(() => inputEl.focus(), 20);
}

function closeSlotMemoModal() {
  editingMemoSlotId = null;
  const modal = document.getElementById("slotMemoModal");
  if (modal) modal.classList.add("hidden");
}

async function saveSlotMemoFromModal(clearAll = false) {
  if (!editingMemoSlotId) return;
  const statusEl = document.getElementById("slotMemoStatusSelect");
  const assigneeEl = document.getElementById("slotMemoAssignee");
  const inputEl = document.getElementById("slotMemoInput");
  const payload = clearAll
    ? { work_memo: "", assignee: "", handoff_status: "" }
    : {
        work_memo: (inputEl?.value || "").trim(),
        assignee: (assigneeEl?.value || "").trim(),
        handoff_status: statusEl?.value || "",
      };
  try {
    const res = await fetch(`/api/slots/${editingMemoSlotId}/work_note`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      alert(`메모 저장 실패: ${data.detail || "오류"}`);
      return;
    }
    const target = allSlots.find(s => s.id === editingMemoSlotId);
    if (target) {
      target.work_memo = data.work_memo;
      target.assignee = data.assignee;
      target.handoff_status = data.handoff_status;
    }
    if (currentSlot && currentSlot.id === editingMemoSlotId) {
      currentSlot.work_memo = data.work_memo;
      currentSlot.assignee = data.assignee;
      currentSlot.handoff_status = data.handoff_status;
      updateCurrentSlotWorkNoteUI();
    }
    closeSlotMemoModal();
    renderSlotsList();
    if (isReviewMode) renderReviewChecklist();
  } catch (e) {
    alert(`메모 저장 오류: ${e}`);
  }
}

async function deleteEditingSlotFromModal() {
  if (!editingMemoSlotId) return;
  const targetSlot = allSlots.find(s => s.id === editingMemoSlotId);
  const label = targetSlot ? `${targetSlot.name} (${targetSlot.id})` : editingMemoSlotId;
  if (!confirm(`정말 '${label}' 슬롯을 삭제할까요?\n연결된 후보/찜/확정 기록이 함께 제거됩니다.`)) return;
  const deletingId = editingMemoSlotId;
  try {
    const res = await fetch(`/api/slots/${deletingId}`, { method: "DELETE" });
    const data = await res.json();
    if (!res.ok) {
      alert(`슬롯 삭제 실패: ${data.detail || "오류"}`);
      return;
    }
    closeSlotMemoModal();
    if (currentSlot && currentSlot.id === deletingId) {
      currentSlot = null;
      currentCandidates = [];
      currentIndex = -1;
    }
    await loadSlots();
    if (isReviewMode) renderReviewChecklist();
  } catch (e) {
    alert(`슬롯 삭제 오류: ${e}`);
  }
}

function openAddSlotModal() {
  const modal = document.getElementById("addSlotModal");
  if (!modal) return;
  const nameEl = document.getElementById("addSlotNameInput");
  const catEl = document.getElementById("addSlotCategorySelect");
  const durEl = document.getElementById("addSlotMaxDurInput");
  const memoEl = document.getElementById("addSlotMemoInput");
  const tagsEl = document.getElementById("addSlotTagsInput");
  const chkEl = document.getElementById("addSlotAutoScoutChk");
  if (nameEl) nameEl.value = "";
  if (catEl && currentCategory !== "all") catEl.value = currentCategory;
  if (durEl) durEl.value = "3.0";
  if (memoEl) memoEl.value = "";
  if (tagsEl) tagsEl.value = "";
  if (chkEl) chkEl.checked = true;
  modal.classList.remove("hidden");
  if (nameEl) setTimeout(() => nameEl.focus(), 20);
}

function closeAddSlotModal() {
  const modal = document.getElementById("addSlotModal");
  if (modal) modal.classList.add("hidden");
}

async function submitAddSlotFromModal() {
  const nameEl = document.getElementById("addSlotNameInput");
  const catEl = document.getElementById("addSlotCategorySelect");
  const durEl = document.getElementById("addSlotMaxDurInput");
  const memoEl = document.getElementById("addSlotMemoInput");
  const tagsEl = document.getElementById("addSlotTagsInput");
  const chkEl = document.getElementById("addSlotAutoScoutChk");
  const btnEl = document.getElementById("btnSubmitAddSlot");

  const name = (nameEl?.value || "").trim();
  if (!name) {
    alert("슬롯 이름을 입력해주세요.");
    if (nameEl) nameEl.focus();
    return;
  }
  const category = (catEl?.value || "gimmick").trim();
  const maxDur = Math.max(0.5, parseFloat(durEl?.value || "3.0") || 3.0);
  const memo = (memoEl?.value || "").trim();
  const searchTags = (tagsEl?.value || "").split(",").map(s => s.trim()).filter(Boolean);
  const autoScout = !!(chkEl ? chkEl.checked : true);

  if (btnEl) {
    btnEl.disabled = true;
    btnEl.textContent = autoScout ? "생성 및 탐색 중..." : "추가 중...";
  }
  try {
    const res = await fetch("/api/slots", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        category,
        memo,
        min_dur: 0.2,
        max_dur: maxDur,
        search_tags: searchTags,
        auto_scout: autoScout,
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      alert(`슬롯 추가 실패: ${data.detail || "오류"}`);
      return;
    }
    closeAddSlotModal();
    await loadSlots();
    if (data.slot && data.slot.id) {
      await selectSlot(data.slot.id);
    }
  } catch (e) {
    alert(`슬롯 추가 오류: ${e}`);
  } finally {
    if (btnEl) {
      btnEl.disabled = false;
      btnEl.textContent = "➕ 추가";
    }
  }
}

async function selectSlot(slotId, autoPlay = true) {
  const slot = allSlots.find(s => s.id === slotId);
  if (!slot) return;
  currentSlot = slot;
  similarAnchorSoundId = null;
  renderSimilarStatusBanner();
  fetch("/api/system/active_slot", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ slot_id: slotId }),
  }).catch(() => {});

  currentSlotName.textContent = slot.name;
  currentSlotCategory.textContent = slot.category.toUpperCase();
  currentSlotMemo.textContent = slot.memo;
  updateCurrentSlotWorkNoteUI();
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

    // Sync this slot's isolated Compare Dock [1~5] strictly with THIS slot's liked candidates!
    syncActiveSlotCompareDockFromCandidates();
    renderCandidatesQueue();
    renderSlotsList();
    loadTrashForCurrentSlot();

    if (currentCandidates.length > 0) {
      const confirmedIdx = currentCandidates.findIndex(c => c.candidate_status === "confirmed");
      const likedIdx = currentCandidates.findIndex(c => c.candidate_status === "liked");
      const initialIdx = confirmedIdx >= 0 ? confirmedIdx : (likedIdx >= 0 ? likedIdx : 0);
      playCandidate(initialIdx, autoPlay);
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
    const srcLabel = cand.source === "freesound" ? "Freesound" : "LotsOfSounds";
    currentSoundMeta.textContent = `${cand.duration.toFixed(1)}s · ${cand.license_type} · ${cand.author || srcLabel}`;
    soundBadgeSource.textContent = `${srcLabel} · ${cand.license_type}`;
    soundBadgeSource.className = "bg-[#242424] text-emerald-400 font-semibold px-3 py-1 rounded-full border border-emerald-900/50 truncate";
  }
  setActiveAudioPoint("playhead");
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
  applyCandidateLicenseLock(cand);

  const dock = getActiveCompareDock();
  document.querySelectorAll(".dock-btn").forEach((b, dIdx) => {
    const docked = dock[dIdx];
    const isThis = docked && docked.sound_id === cand.sound_id;
    b.classList.toggle("active", !!isThis);
    b.classList.toggle("border-[#1DB954]", !!isThis);
  });

  // Decode bottom waveform & load trim boundaries
  decodeWaveformForCandidate(cand);

  // Enforce normalized gain, playback speed & master volume before and after loading audio
  audioPlayer.pause();
  applyAudioGainNodes();
  applyAudioPlaybackSpeed();
  audioPlayer.src = getCandidatePlaybackUrl(cand);
  audioPlayer.onloadedmetadata = () => {
    if (!currentCandidates[currentIndex] || currentCandidates[currentIndex].sound_id !== cand.sound_id) return;
    applyAudioGainNodes();
    applyAudioPlaybackSpeed();
    if (Number.isFinite(audioPlayer.duration) && audioPlayer.duration > 0) {
      if (!waveformPeaks.length) {
        audioDuration = audioPlayer.duration;
        if (cand.audio_out <= 0 || cand.audio_out > audioDuration) {
          audioOut = audioDuration;
        }
        updateAudioTrimUI();
      }
      timeDuration.textContent = formatTime(audioDuration || audioPlayer.duration);
    }
    const startAt = cand.audio_in || 0.0;
    audioPlayer.currentTime = startAt;
    timeCurrent.textContent = formatTime(startAt);
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
    if (slotFilterMode === "confirmed" && s.status !== "confirmed") return false;
    if (slotFilterMode === "liked" && !(s.liked_count > 0)) return false;
    if (hideConfirmedSlots && slotFilterMode !== "confirmed" && s.status === "confirmed") return false;
    if (currentCategory !== "all" && s.category !== currentCategory) return false;
    const searchVal = topSearchMode === "slot" ? document.getElementById("searchInput").value.toLowerCase() : "";
    if (searchVal && !s.name.toLowerCase().includes(searchVal) && !s.memo.toLowerCase().includes(searchVal)) return false;
    return true;
  });

  if (filtered.length === 0) {
    const emptyMsg = slotFilterMode === "confirmed"
      ? "확정 완료된 항목이 아직 없습니다."
      : (slotFilterMode === "liked" ? "찜해둔 후보가 있는 슬롯이 아직 없습니다." : "표시할 슬롯이 없습니다.");
    container.innerHTML = `<div class="text-center text-gray-500 text-xs py-8">${emptyMsg}</div>`;
    return;
  }

  filtered.forEach(s => {
    const item = document.createElement("div");
    const isActive = currentSlot && currentSlot.id === s.id;
    const isConfirmed = s.status === "confirmed";
    const isPassed = !!s.review_passed;
    const likedCount = Number(s.liked_count || 0);

    let cardStyle = isActive ? "active bg-[#282828]" : "bg-[#181818] hover:bg-[#202020]";
    if (isConfirmed) {
      cardStyle = isActive
        ? "active border-l-4 border-[#1DB954] bg-emerald-900/45 ring-1 ring-[#1DB954]"
        : "border-l-4 border-[#1DB954] bg-emerald-950/35 hover:bg-emerald-950/55";
    } else if (likedCount > 0) {
      cardStyle = isActive
        ? "active border-l-4 border-amber-400 bg-amber-950/35"
        : "border-l-4 border-amber-500/70 bg-[#1a1812] hover:bg-[#222018]";
    }
    item.className = `slot-item p-2.5 rounded-lg flex items-center justify-between cursor-pointer text-[13px] transition ${cardStyle}`;

    const badges = [];
    if (s.is_exported) {
      badges.push(`<span class="bg-sky-950/90 text-sky-300 border border-sky-700/80 text-[10px] px-1.5 py-0.5 rounded-full font-bold" title="추출 완료 (${s.exported_at || ''})">📦 추출✓</span>`);
    }
    if (likedCount > 0) {
      badges.push(`<span class="bg-amber-950/80 text-amber-300 border border-amber-700/70 text-[10px] px-1.5 py-0.5 rounded-full font-bold">⭐ 찜${likedCount}</span>`);
    }
    if (isPassed) {
      badges.push(`<span class="bg-[#1DB954] text-black text-[11px] px-2 py-0.5 rounded-full font-black">✅ 패스</span>`);
    } else if (isConfirmed) {
      badges.push(`<span class="bg-[#1DB954] text-black text-[11px] px-2 py-0.5 rounded-full font-black">✅ 확정</span>`);
    } else if (s.status === "on_hold") {
      badges.push(`<span class="bg-[#242424] text-gray-500 text-xs px-2 py-0.5 rounded">보류</span>`);
    } else if (s.candidate_count > 0) {
      badges.push(`<span class="bg-[#242424] text-gray-300 text-xs px-2 py-0.5 rounded font-mono">${s.candidate_count} 후보</span>`);
    } else {
      badges.push(`<span class="bg-[#242424] text-gray-500 text-xs px-2 py-0.5 rounded">탐색 중</span>`);
    }

    const sceneIcon = s.scene_media_url ? "🎬 " : "";
    const titleColor = isConfirmed ? "text-emerald-300" : (likedCount > 0 ? "text-amber-200" : "text-gray-200");
    const handoffMeta = HANDOFF_STATUS_META[s.handoff_status];
    if (handoffMeta) {
      badges.unshift(`<span class="${handoffMeta.cls} text-[10px] px-1.5 py-0.5 rounded-full font-bold">${handoffMeta.label}</span>`);
    }
    const noteLine = (s.work_memo || s.assignee)
      ? `<div class="text-[11px] text-indigo-300 truncate mt-0.5">📝 ${s.assignee ? `[${s.assignee}] ` : ""}${s.work_memo || "담당자 지정됨"}</div>`
      : "";
    item.title = "클릭: 슬롯 열기 · 우클릭: 인수인계 메모 / 진행 표시";
    item.innerHTML = `
      <div class="truncate flex-1 pr-2 min-w-0">
        <div class="font-bold ${titleColor} truncate">${isConfirmed ? "✅ " : ""}${sceneIcon}${s.name}</div>
        <div class="text-xs text-gray-400 truncate">${s.memo}</div>
        ${noteLine}
      </div>
      <div class="shrink-0 flex items-center gap-1 flex-wrap justify-end max-w-[130px]">${badges.join("")}</div>
    `;

    item.addEventListener("click", () => selectSlot(s.id));
    item.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      openSlotMemoModal(s.id);
    });
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
    const anchorTag = (similarAnchorSoundId && c.sound_id === similarAnchorSoundId)
      ? `<span class="ml-1.5 bg-cyan-950 text-cyan-300 border border-cyan-700 text-[10px] px-1.5 py-0.5 rounded font-bold">🎯 기준음</span>`
      : "";
    const srcUrl = c.original_source_url || c.catalog_url;

    const infoDiv = document.createElement("div");
    infoDiv.className = "truncate flex-1 pr-2 min-w-0 flex items-center gap-2.5";
    const trimPill = ((c.audio_in && c.audio_in > 0) || (c.audio_out && c.audio_out > 0))
      ? `<span class="ml-1.5 text-emerald-300 font-bold">✂️${Number(c.audio_in || 0).toFixed(2)}~${Number(c.audio_out || c.duration).toFixed(2)}s</span>`
      : "";
    const speedPill = (c.playback_rate && Math.abs(c.playback_rate - 1.0) >= 0.01)
      ? `<span class="ml-1.5 text-amber-300 font-bold">⚡${Number(c.playback_rate).toFixed(2)}x</span>`
      : "";
    const revPill = c.reverse
      ? `<span class="ml-1.5 text-cyan-300 font-bold">🔁역재생</span>`
      : "";
    const pitchPill = (c.pitch_semitones && Math.abs(c.pitch_semitones) >= 0.1)
      ? `<span class="ml-1.5 text-violet-300 font-bold">🎵${c.pitch_semitones > 0 ? "+" : ""}${Number(c.pitch_semitones).toFixed(1)}st</span>`
      : "";
    const tailPill = (c.tail_sec && Number(c.tail_sec) >= 0.05)
      ? `<span class="ml-1.5 text-teal-300 font-bold">✨+${Number(c.tail_sec).toFixed(1)}s</span>`
      : "";
    const lockPill = (c.commercial_ok === false)
      ? `<span class="ml-1.5 text-red-300 font-bold">🔒사용불가</span>`
      : (c.modify_ok === false
          ? `<span class="ml-1.5 text-red-300 font-bold">🔒수정금지</span>`
          : (c.attribution_required ? `<span class="ml-1.5 text-amber-300 font-bold">⚠️표기필수</span>` : ""));
    infoDiv.innerHTML = `
      <span class="shrink-0 bg-[#242424] text-[#1DB954] font-mono font-bold text-xs px-2 py-1 rounded border border-[#333]">#${idx + 1}</span>
      <div class="truncate min-w-0">
        <div class="font-bold text-gray-100 truncate">${likeIcon}${c.name}${confirmedTag}${anchorTag}</div>
        <div class="text-xs text-gray-400 font-mono">${c.duration.toFixed(1)}s · ${Math.round(c.score * 100)}% Match${trimPill}${speedPill}${revPill}${pitchPill}${tailPill}${lockPill}</div>
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

    const delBtn = document.createElement("button");
    delBtn.title = "이 후보 삭제 (목록에서 제외)";
    delBtn.className = "text-gray-400 hover:text-red-400 px-1.5 py-0.5 text-xs bg-[#242424] hover:bg-red-950/60 rounded border border-[#333] hover:border-red-800/60 transition";
    delBtn.textContent = "🗑️";
    delBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      skipCandidateById(c.sound_id);
    });
    btnsDiv.appendChild(delBtn);

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
  btnLoopMode.classList.remove("text-emerald-400", "text-amber-400", "text-cyan-300", "text-gray-300");
  if (loopMode === "sync") {
    loopMode = "audio";
    btnLoopMode.textContent = "🔁 🔊 소리만 반복";
    btnLoopMode.classList.add("text-amber-400");
  } else if (loopMode === "audio") {
    loopMode = "single";
    btnLoopMode.textContent = "1️⃣ 1회 정지";
    btnLoopMode.classList.add("text-cyan-300");
  } else if (loopMode === "single") {
    loopMode = "once";
    btnLoopMode.textContent = "⏭️ 1회 후 다음";
    btnLoopMode.classList.add("text-gray-300");
  } else {
    loopMode = "sync";
    btnLoopMode.textContent = "🔁 🎬+🔊 싱크 반복";
    btnLoopMode.classList.add("text-emerald-400");
  }
}

btnLoopMode.addEventListener("click", cycleLoopMode);

// =========================================================================
// Action Handlers (Like, Similar, Mutate, Confirm, Export)
// =========================================================================

function sortCandidatesPrioritizeLiked(preserveSoundId = null) {
  const prio = (st) => (st === "confirmed" ? 0 : st === "liked" ? 1 : 2);
  currentCandidates.sort(
    (a, b) => prio(a.candidate_status) - prio(b.candidate_status) || (Number(a.rank || 0) - Number(b.rank || 0))
  );
  if (preserveSoundId) {
    const newIdx = currentCandidates.findIndex(c => c.sound_id === preserveSoundId);
    if (newIdx >= 0) currentIndex = newIdx;
  }
}

async function toggleLikeCurrent() {
  if (currentIndex < 0 || !currentCandidates[currentIndex] || !currentSlot) return;
  const cand = currentCandidates[currentIndex];

  try {
    const res = await fetch("/api/actions/like", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slot_id: currentSlot.id, sound_id: cand.sound_id }),
    });
    const data = await res.json();
    cand.candidate_status = data.new_candidate_status;
    if (cand.candidate_status === "liked") {
      addToCompareDock(cand);
    } else {
      removeFromCompareDock(cand.sound_id);
    }
    sortCandidatesPrioritizeLiked(cand.sound_id);
    currentSlot.liked_count = currentCandidates.filter(c => c.candidate_status === "liked").length;
    renderCandidatesQueue();
    renderSlotsList();
    playCandidate(currentIndex);
  } catch (e) {
    console.error("Like error:", e);
  }
}

function applyCandidateLicenseLock(cand) {
  const banner = document.getElementById("candLicenseLockBanner");
  const modifyOk = !cand || cand.modify_ok !== false;
  const commercialOk = !cand || cand.commercial_ok !== false;
  const attrReq = !!(cand && cand.attribution_required);
  const notice = (cand && cand.license_notice) || "";
  const lockReason = (cand && cand.lock_reason) || "";

  if (banner) {
    banner.classList.add("hidden");
    banner.className = "hidden text-[11px] font-bold px-3 py-1.5 rounded-lg border truncate shrink-0";
    if (cand && (!commercialOk || !modifyOk)) {
      banner.textContent = lockReason || "🔒 저작권 제한으로 인해 사용 또는 수정이 잠겨 있습니다.";
      banner.classList.remove("hidden");
      banner.classList.add("bg-red-950/85", "border-red-700", "text-red-200");
    } else if (cand && (attrReq || notice)) {
      banner.textContent = notice || "⚠️ CC BY 라이선스: 게임 크레딧에 원작자 표기가 필요합니다.";
      banner.classList.remove("hidden");
      banner.classList.add("bg-amber-950/75", "border-amber-700/80", "text-amber-200");
    }
  }

  const editControlIds = [
    "audioRateSlider", "audioPitchSlider", "audioTailSlider",
    "btnPreservePitch", "btnResetPitch", "btnResetTail", "btnToggleReverse",
    "btnAudioSetIn", "btnAudioSetOut", "btnAudioApply", "btnAudioUndo", "btnAudioReset",
    "btnMutate",
  ];
  editControlIds.forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.disabled = !modifyOk;
    el.classList.toggle("opacity-40", !modifyOk);
    el.classList.toggle("cursor-not-allowed", !modifyOk);
    if (!modifyOk) el.title = lockReason || "저작권으로 인해 수정이 불가합니다.";
  });
  document.querySelectorAll(".speed-preset-btn").forEach((btn) => {
    btn.disabled = !modifyOk;
    btn.classList.toggle("opacity-40", !modifyOk);
    btn.classList.toggle("cursor-not-allowed", !modifyOk);
  });

  ["btnConfirm", "btnExportSingleSound"].forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.disabled = !commercialOk;
    el.classList.toggle("opacity-40", !commercialOk);
    el.classList.toggle("cursor-not-allowed", !commercialOk);
    if (!commercialOk) el.title = lockReason || "저작권으로 인해 확정/추출이 불가합니다.";
  });
}

function syncActiveSlotCompareDockFromCandidates() {
  if (!currentSlot) return;
  const dock = [null, null, null, null, null];
  const prevDock = slotCompareDocks[currentSlot.id] || [];
  const candMap = new Map(currentCandidates.map(c => [c.sound_id, c]));

  // Keep existing dock order for liked sounds still in this slot
  let writeIdx = 0;
  for (const prev of prevDock) {
    if (prev && candMap.has(prev.sound_id)) {
      const fresh = candMap.get(prev.sound_id);
      if (fresh.candidate_status === "liked" && writeIdx < 5) {
        dock[writeIdx++] = fresh;
      }
    }
  }
  // Fill remaining liked sounds of this slot
  for (const c of currentCandidates) {
    if (writeIdx >= 5) break;
    if (c.candidate_status === "liked" && !dock.some(d => d && d.sound_id === c.sound_id)) {
      dock[writeIdx++] = c;
    }
  }
  slotCompareDocks[currentSlot.id] = dock;
  updateCompareDockUI();
}

function addToCompareDock(sound) {
  if (!currentSlot || !sound) return;
  const dock = getActiveCompareDock();
  const existingIdx = dock.findIndex(s => s && s.sound_id === sound.sound_id);
  if (existingIdx !== -1) {
    dock[existingIdx] = sound;
    updateCompareDockUI();
    return;
  }
  const emptyIdx = dock.findIndex(s => s === null);
  if (emptyIdx !== -1) dock[emptyIdx] = sound;
  else {
    dock.shift();
    dock.push(sound);
  }
  updateCompareDockUI();
}

function removeFromCompareDock(soundId) {
  if (!currentSlot) return;
  const dock = getActiveCompareDock();
  for (let i = 0; i < dock.length; i++) {
    if (dock[i] && dock[i].sound_id === soundId) {
      dock[i] = null;
    }
  }
  updateCompareDockUI();
}

function updateCompareDockUI() {
  const dock = getActiveCompareDock();
  const ownerEl = document.getElementById("dockSlotOwner");
  if (ownerEl) ownerEl.textContent = currentSlot ? currentSlot.name : "현재 슬롯";
  const dockBtns = document.querySelectorAll(".dock-btn");
  dockBtns.forEach((btn, idx) => {
    const s = dock[idx];
    const spanText = btn.querySelector("span:last-child");
    if (s) {
      spanText.textContent = s.name;
      spanText.classList.add("text-emerald-400");
      btn.title = `[${currentSlot ? currentSlot.name : ""}] ${s.name} (${idx + 1}번 키)`;
    } else {
      spanText.textContent = "비어있음";
      spanText.classList.remove("text-emerald-400");
      btn.title = `비어있음 (${idx + 1}번 키)`;
    }
  });
}

function renderSimilarStatusBanner() {
  const banner = document.getElementById("similarStatusBanner");
  if (!banner) return;
  if (!currentSlot || !similarRoundStatusBySlot[currentSlot.id]) {
    banner.classList.add("hidden");
    banner.textContent = "";
    return;
  }
  const st = similarRoundStatusBySlot[currentSlot.id];
  const promptPart = st.promptUsed ? ` · 조건: "${st.promptUsed}"` : "";
  const filterPart = st.filteredOut > 0 ? ` · 불량/초과 ${st.filteredOut}개 제외` : "";
  const kwPart = Array.isArray(st.queriesUsed) && st.queriesUsed.length > 0
    ? ` · 탐색 키워드: ${st.queriesUsed.join(", ")}`
    : "";
  banner.textContent = `🎯 유사 탐색 #${st.roundNo}회차 완료: +${st.foundNew}개 늘어남 (총 ${st.totalRanked}개)${filterPart}${promptPart}${kwPart}`;
  banner.classList.remove("hidden");
}

function openSimilarPromptModal() {
  if (currentIndex < 0 || !currentCandidates[currentIndex] || !currentSlot) return;
  const modal = document.getElementById("similarPromptModal");
  const input = document.getElementById("similarPromptInput");
  if (!modal) {
    executeSimilarSearch("");
    return;
  }
  modal.classList.remove("hidden");
  if (input) {
    input.value = "";
    setTimeout(() => input.focus(), 20);
  }
}

function closeSimilarPromptModal() {
  const modal = document.getElementById("similarPromptModal");
  if (modal) modal.classList.add("hidden");
}

async function executeSimilarSearch(customPrompt = "") {
  if (currentIndex < 0 || !currentCandidates[currentIndex] || !currentSlot) return;
  closeSimilarPromptModal();
  const cand = currentCandidates[currentIndex];
  const btnSimilar = document.getElementById("btnSimilar");
  const prevLabel = btnSimilar ? btnSimilar.innerHTML : "";
  if (btnSimilar) {
    btnSimilar.disabled = true;
    btnSimilar.textContent = "⏳ 비슷한 소리 찾는 중...";
  }
  loadingSpinner.classList.remove("hidden");

  try {
    const cleanPrompt = (customPrompt || "").trim();
    const res = await fetch("/api/actions/reorder_similar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        slot_id: currentSlot.id,
        reference_sound_id: cand.sound_id,
        max_dur: maxSearchDuration > 0 ? maxSearchDuration : null,
        custom_prompt: cleanPrompt || null,
      }),
    });
    const simData = await res.json();
    if (res.ok) {
      similarAnchorSoundId = cand.sound_id;
      similarRoundStatusBySlot[currentSlot.id] = {
        roundNo: simData.round_no || 1,
        foundNew: simData.found_new || 0,
        filteredOut: simData.filtered_out || 0,
        totalRanked: simData.total_ranked || 0,
        promptUsed: simData.prompt_used || "",
        queriesUsed: simData.queries_used || [],
      };
      renderSimilarStatusBanner();

      const qParam = maxSearchDuration > 0 ? `?max_dur=${encodeURIComponent(maxSearchDuration)}` : "";
      const cRes = await fetch(`/api/slots/${currentSlot.id}/candidates${qParam}`);
      const cData = await cRes.json();
      currentCandidates = cData.candidates || [];
      await loadSlots();
      renderCandidatesQueue();

      // Play the top NEW/OTHER acoustically similar sound (#2, index 1), NOT replaying the anchor (#1)!
      const targetIdx = currentCandidates.length > 1
        ? Math.min(currentCandidates.length - 1, Math.max(1, simData.top_similar_index || 1))
        : 0;
      playCandidate(targetIdx, true);
    }
  } catch (e) {
    console.error("Similar error:", e);
  } finally {
    loadingSpinner.classList.add("hidden");
    if (btnSimilar) {
      btnSimilar.disabled = false;
      btnSimilar.innerHTML = prevLabel;
    }
  }
}

async function triggerSimilarCurrent() {
  openSimilarPromptModal();
}

function clearEmptySlotPlayerUI() {
  audioPlayer.pause();
  audioPlayer.removeAttribute("src");
  waveformPeaks = [];
  audioDuration = 0;
  currentIndex = -1;
  btnOpenSourceUrl.classList.add("hidden");
  btnOpenSourceUrl.classList.remove("inline-flex");
  if (btnCopySourceUrl) {
    btnCopySourceUrl.classList.add("hidden");
    btnCopySourceUrl.classList.remove("inline-flex");
  }
  currentSoundName.textContent = "남은 후보 사운드 없음";
  currentSoundMeta.textContent = "'🔍 새로 찾기'로 후보를 다시 탐색할 수 있습니다";
  queuePosition.textContent = "후보 0 / 0";
  updateAudioTrimUI();
  updateConfirmButtonUI(null);
}

async function skipCandidateById(soundId) {
  if (!currentSlot || !soundId) return;
  const idx = currentCandidates.findIndex(c => c.sound_id === soundId);
  if (idx < 0) return;

  try {
    const res = await fetch("/api/actions/skip", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slot_id: currentSlot.id, sound_id: soundId }),
    });
    if (!res.ok) return;

    const wasPlayingRemoved = idx === currentIndex;
    currentCandidates.splice(idx, 1);
    removeFromCompareDock(soundId);
    if (similarAnchorSoundId === soundId) similarAnchorSoundId = null;

    await loadSlots();
    const freshSlot = allSlots.find(s => s.id === currentSlot.id);
    if (freshSlot) currentSlot = freshSlot;
    activeSlotBadge.textContent = `${currentSlot.name} (${currentCandidates.length} 후보)`;

    renderCandidatesQueue();
    renderSlotsList();
    loadTrashForCurrentSlot();

    if (currentCandidates.length === 0) {
      clearEmptySlotPlayerUI();
    } else if (wasPlayingRemoved) {
      const nextIdx = Math.min(idx, currentCandidates.length - 1);
      playCandidate(nextIdx, true);
    } else if (idx < currentIndex) {
      currentIndex -= 1;
      queuePosition.textContent = `후보 ${currentIndex + 1} / ${currentCandidates.length}`;
    } else {
      queuePosition.textContent = `후보 ${currentIndex + 1} / ${currentCandidates.length}`;
    }
  } catch (e) {
    console.error("Skip candidate error:", e);
  }
}

async function pruneUnlikedCandidates() {
  if (!currentSlot) return;
  const removableCount = currentCandidates.filter(
    c => c.candidate_status !== "liked" && c.candidate_status !== "confirmed"
  ).length;
  if (removableCount === 0) {
    alert("정리할 미찜 후보가 없습니다. (찜⭐ / 확정✅ 후보는 보호됩니다)");
    return;
  }
  if (!confirm(`[${currentSlot.name}] 슬롯에서 찜(⭐)/확정(✅)하지 않은 후보 ${removableCount}개를 일괄 삭제할까요?`)) {
    return;
  }

  try {
    loadingSpinner.classList.remove("hidden");
    const res = await fetch(`/api/slots/${currentSlot.id}/candidates/prune_unliked`, {
      method: "POST",
    });
    const data = await res.json();
    if (res.ok) {
      const activeSoundId = (currentIndex >= 0 && currentCandidates[currentIndex]) ? currentCandidates[currentIndex].sound_id : null;
      const qParam = maxSearchDuration > 0 ? `?max_dur=${encodeURIComponent(maxSearchDuration)}` : "";
      const cRes = await fetch(`/api/slots/${currentSlot.id}/candidates${qParam}`);
      const cData = await cRes.json();
      currentCandidates = cData.candidates || [];
      await loadSlots();
      const freshSlot = allSlots.find(s => s.id === currentSlot.id);
      if (freshSlot) currentSlot = freshSlot;
      activeSlotBadge.textContent = `${currentSlot.name} (${currentCandidates.length} 후보)`;
      renderCandidatesQueue();
      renderSlotsList();
      loadTrashForCurrentSlot();

      if (currentCandidates.length === 0) {
        clearEmptySlotPlayerUI();
      } else {
        const keptIdx = activeSoundId ? currentCandidates.findIndex(c => c.sound_id === activeSoundId) : -1;
        playCandidate(keptIdx >= 0 ? keptIdx : 0, false);
      }

      const banner = document.getElementById("similarStatusBanner");
    if (banner) {
      banner.textContent = `🧹 미찜 후보 ${data.removed_count || removableCount}개 정리 완료 (삭제함으로 이동 · 남은 후보 ${currentCandidates.length}개)`;
      banner.classList.remove("hidden");
    }
    }
  } catch (e) {
    console.error("Prune unliked error:", e);
  } finally {
    loadingSpinner.classList.add("hidden");
  }
}

async function loadTrashForCurrentSlot() {
  const btnTrash = document.getElementById("tabTrashBtn");
  if (!currentSlot) {
    currentTrash = [];
    if (btnTrash) btnTrash.textContent = "🗑️ 삭제함 (0)";
    renderTrashList();
    return;
  }
  try {
    const res = await fetch(`/api/slots/${currentSlot.id}/trash`);
    if (!res.ok) return;
    const data = await res.json();
    currentTrash = data.trash || [];
    if (btnTrash) btnTrash.textContent = `🗑️ 삭제함 (${currentTrash.length})`;
    renderTrashList();
  } catch (e) {
    console.warn("Failed to load trash:", e);
  }
}

function renderTrashList() {
  const container = document.getElementById("trashListContainer");
  if (!container) return;
  container.innerHTML = "";
  if (!currentTrash.length) {
    container.innerHTML = `<div class="text-center text-gray-500 text-xs py-8">삭제한 후보가 없습니다.</div>`;
    return;
  }
  currentTrash.forEach((t) => {
    const row = document.createElement("div");
    row.className = "p-2.5 rounded-lg bg-[#181818] border border-[#2a2a2a] flex items-center justify-between gap-2 text-xs";
    row.innerHTML = `
      <div class="min-w-0 flex-1">
        <div class="font-bold text-gray-200 truncate">${t.name}</div>
        <div class="text-[11px] text-gray-400 font-mono truncate">${Number(t.duration || 0).toFixed(1)}s · ${t.license_type || "CC0"}</div>
      </div>
    `;
    const actions = document.createElement("div");
    actions.className = "flex items-center gap-1 shrink-0";

    const previewBtn = document.createElement("button");
    previewBtn.className = "px-2 py-1 rounded bg-[#242424] hover:bg-[#333] text-gray-200 text-[11px]";
    previewBtn.textContent = "듣기";
    previewBtn.addEventListener("click", () => {
      initAudioContext();
      audioPlayer.pause();
      audioPlayer.src = t.audio_url;
      audioPlayer.play().catch(() => {});
    });

    const restoreBtn = document.createElement("button");
    restoreBtn.className = "px-2 py-1 rounded bg-emerald-950 hover:bg-[#1DB954] hover:text-black text-emerald-300 border border-emerald-700 text-[11px] font-bold";
    restoreBtn.textContent = "↩ 복구";
    restoreBtn.addEventListener("click", () => restoreCandidateById(t.sound_id));

    actions.appendChild(previewBtn);
    actions.appendChild(restoreBtn);
    row.appendChild(actions);
    container.appendChild(row);
  });
}

async function restoreCandidateById(soundId) {
  if (!currentSlot || !soundId) return;
  try {
    const res = await fetch("/api/actions/restore", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slot_id: currentSlot.id, sound_id: soundId }),
    });
    if (!res.ok) return;
    const qParam = maxSearchDuration > 0 ? `?max_dur=${encodeURIComponent(maxSearchDuration)}` : "";
    const cRes = await fetch(`/api/slots/${currentSlot.id}/candidates${qParam}`);
    const cData = await cRes.json();
    currentCandidates = cData.candidates || [];
    await loadSlots();
    await loadTrashForCurrentSlot();
    renderCandidatesQueue();
    renderSlotsList();
    const restoredIdx = currentCandidates.findIndex(c => c.sound_id === soundId);
    if (restoredIdx >= 0) playCandidate(restoredIdx, true);
  } catch (e) {
    console.error("Restore candidate error:", e);
  }
}

async function restoreAllTrash() {
  if (!currentSlot || !currentTrash.length) return;
  try {
    const res = await fetch(`/api/slots/${currentSlot.id}/trash/restore_all`, { method: "POST" });
    if (!res.ok) return;
    const qParam = maxSearchDuration > 0 ? `?max_dur=${encodeURIComponent(maxSearchDuration)}` : "";
    const cRes = await fetch(`/api/slots/${currentSlot.id}/candidates${qParam}`);
    const cData = await cRes.json();
    currentCandidates = cData.candidates || [];
    await loadSlots();
    await loadTrashForCurrentSlot();
    renderCandidatesQueue();
    renderSlotsList();
    if (currentCandidates.length > 0 && currentIndex < 0) playCandidate(0, false);
  } catch (e) {
    console.error("Restore all trash error:", e);
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
      if (currentSlot.status !== "confirmed") currentSlot.review_passed = false;
      updateConfirmButtonUI(cand);
  applyCandidateLicenseLock(cand);
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
    sortCandidatesPrioritizeLiked(cand.sound_id);
    updateConfirmButtonUI(cand);
  applyCandidateLicenseLock(cand);

    renderCandidatesQueue();
    renderSlotsList();
    updateProgress();

    // Stay in the SAME slot and advance cleanly to the NEXT sound in queue
    if (currentCandidates.length > 1) {
      const nextIdx = (currentIndex + 1) % currentCandidates.length;
      playCandidate(nextIdx, true);
    }
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
      await loadSlots();
      openReceiptModal(data.receipt_text || "", `저장 위치: ${data.receipt_path || unityPath} (총 ${data.exported_count}개 WAV 내보냄)`);
    } else {
      alert(`내보내기 실패: ${data.detail || "오류 발생"}`);
    }
  } catch (e) {
    alert(`내보내기 요청 오류: ${e}`);
  }
});

async function exportConfirmedBundle(passedOnly = false, btnEl = null) {
  const prevText = btnEl ? btnEl.textContent : "";
  if (btnEl) {
    btnEl.disabled = true;
    btnEl.textContent = "⏳ WAV 추출 중...";
  }
  try {
    const res = await fetch("/api/export/confirmed_bundle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ passed_only: passedOnly, open_folder: true }),
    });
    const data = await res.json();
    if (res.ok) {
      await loadSlots();
      openReceiptModal(
        data.receipt_text || "",
        `추출 폴더: ${data.destination} (총 ${data.exported_count}개 WAV + 주의사항 문서 저장 완료)`
      );
    } else {
      alert(`확정 WAV 추출 실패: ${data.detail || "오류 발생"}`);
    }
  } catch (e) {
    alert(`확정 WAV 추출 오류: ${e}`);
  } finally {
    if (btnEl) {
      btnEl.disabled = false;
      btnEl.textContent = prevText;
    }
  }
}

const btnExportConfirmedBundle = document.getElementById("btnExportConfirmedBundle");
if (btnExportConfirmedBundle) {
  btnExportConfirmedBundle.addEventListener("click", () => exportConfirmedBundle(false, btnExportConfirmedBundle));
}
const btnReviewExportConfirmedBundle = document.getElementById("btnReviewExportConfirmedBundle");
if (btnReviewExportConfirmedBundle) {
  btnReviewExportConfirmedBundle.addEventListener("click", () => exportConfirmedBundle(false, btnReviewExportConfirmedBundle));
}

const btnExportSingleSound = document.getElementById("btnExportSingleSound");
if (btnExportSingleSound) {
  btnExportSingleSound.addEventListener("click", async () => {
    if (!currentSlot) {
      alert("먼저 슬롯을 선택해주세요.");
      return;
    }
    const cand = currentIndex >= 0 ? currentCandidates[currentIndex] : null;
    const prevText = btnExportSingleSound.textContent;
    btnExportSingleSound.disabled = true;
    btnExportSingleSound.textContent = "⏳ 추출 중...";
    try {
      await saveAudioTrimToBackend();
      const res = await fetch("/api/export/single", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          slot_id: currentSlot.id,
          sound_id: cand ? cand.sound_id : null,
          open_folder: true,
        }),
      });
      const data = await res.json();
      if (res.ok) {
        await loadSlots();
        openReceiptModal(
          data.receipt_text || "",
          `단일 추출 완료: ${data.destination}\\${(data.files || []).join(", ")}`
        );
      } else {
        alert(`단일 추출 실패: ${data.detail || "오류 발생"}`);
      }
    } catch (e) {
      alert(`단일 추출 오류: ${e}`);
    } finally {
      btnExportSingleSound.disabled = false;
      btnExportSingleSound.textContent = prevText;
    }
  });
}

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
  applyAudioPlaybackSpeed();
  playIcon.classList.add("hidden");
  pauseIcon.classList.remove("hidden");
});

audioPlayer.addEventListener("pause", () => {
  playIcon.classList.remove("hidden");
  pauseIcon.classList.add("hidden");
});

audioPlayer.addEventListener("ended", () => {
  if (isReviewMode) return;
  if (loopMode === "audio" || (loopMode === "sync" && !sceneMediaType)) {
    audioPlayer.currentTime = audioIn;
    audioPlayer.play().catch(() => {});
  } else if (loopMode === "single") {
    audioPlayer.pause();
    audioPlayer.currentTime = audioIn;
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
  applyAudioPlaybackSpeed();
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

function replayFromStart() {
  initAudioContext();
  applyAudioGainNodes();
  applyAudioPlaybackSpeed();
  if (loopMode === "sync" && sceneMediaType) {
    sceneCurrentTime = sceneIn;
    if (sceneMediaType === "video") {
      sceneVideo.currentTime = sceneIn;
      sceneVideo.play().catch(() => {});
    }
    sceneIsPlaying = true;
    if (soundOffset <= 0.01) {
      audioPlayer.currentTime = audioIn;
      audioPlayer.play().catch(() => {});
      audioTriggeredThisCycle = true;
    } else {
      audioPlayer.pause();
      audioPlayer.currentTime = audioIn;
      audioTriggeredThisCycle = false;
    }
  } else {
    audioPlayer.currentTime = audioIn;
    audioPlayer.play().catch(() => {});
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

const btnSimilarEl = document.getElementById("btnSimilar");
if (btnSimilarEl) {
  btnSimilarEl.addEventListener("click", (e) => {
    if (e.detail >= 2) {
      if (similarClickTimer) {
        clearTimeout(similarClickTimer);
        similarClickTimer = null;
      }
      return;
    }
    if (similarClickTimer) return;
    similarClickTimer = setTimeout(() => {
      similarClickTimer = null;
      openSimilarPromptModal();
    }, 240);
  });
  btnSimilarEl.addEventListener("dblclick", (e) => {
    e.preventDefault();
    if (similarClickTimer) {
      clearTimeout(similarClickTimer);
      similarClickTimer = null;
    }
    executeSimilarSearch("");
  });
}

document.getElementById("btnMutate").addEventListener("click", triggerMutateCurrent);

const btnDeleteCandidateEl = document.getElementById("btnDeleteCandidate");
if (btnDeleteCandidateEl) {
  btnDeleteCandidateEl.addEventListener("click", () => {
    if (currentIndex >= 0 && currentCandidates[currentIndex]) {
      skipCandidateById(currentCandidates[currentIndex].sound_id);
    }
  });
}

const btnPruneUnlikedEl = document.getElementById("btnPruneUnliked");
if (btnPruneUnlikedEl) {
  btnPruneUnlikedEl.addEventListener("click", pruneUnlikedCandidates);
}

const similarPromptModalEl = document.getElementById("similarPromptModal");
const similarPromptInputEl = document.getElementById("similarPromptInput");
if (document.getElementById("btnCloseSimilarModal")) {
  document.getElementById("btnCloseSimilarModal").addEventListener("click", closeSimilarPromptModal);
}
if (document.getElementById("btnCancelSimilarModal")) {
  document.getElementById("btnCancelSimilarModal").addEventListener("click", closeSimilarPromptModal);
}
if (document.getElementById("btnRunSimilarModal")) {
  document.getElementById("btnRunSimilarModal").addEventListener("click", () => {
    executeSimilarSearch(similarPromptInputEl ? similarPromptInputEl.value : "");
  });
}
if (similarPromptModalEl) {
  similarPromptModalEl.addEventListener("click", (e) => {
    if (e.target === similarPromptModalEl) closeSimilarPromptModal();
  });
}
document.querySelectorAll(".similar-chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    const val = chip.dataset.chip || "";
    if (!similarPromptInputEl) return;
    const prev = similarPromptInputEl.value.trim();
    similarPromptInputEl.value = prev ? `${prev}, ${val}` : val;
    similarPromptInputEl.focus();
  });
});
if (similarPromptInputEl) {
  similarPromptInputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      executeSimilarSearch(similarPromptInputEl.value);
    } else if ((e.code === keymapSettings.keys.similar) && !similarPromptInputEl.value.trim() && !e.repeat) {
      // Pressing 'F' again while input is empty works like double-tap quick auto-search
      e.preventDefault();
      executeSimilarSearch("");
    }
  });
}

document.getElementById("btnConfirm").addEventListener("click", confirmCurrent);

const btnFilterLiked = document.getElementById("btnFilterLiked");
const btnFilterConfirmed = document.getElementById("btnFilterConfirmed");
const btnToggleHideConfirmed = document.getElementById("btnToggleHideConfirmed");

function updateSlotFilterButtonsUI() {
  if (btnFilterLiked) {
    const active = slotFilterMode === "liked";
    btnFilterLiked.className = active
      ? "px-2 py-0.5 rounded-full bg-amber-400 text-black border border-amber-400 text-[11px] font-black transition"
      : "px-2 py-0.5 rounded-full bg-[#242424] hover:bg-[#333] text-amber-300 border border-[#383838] text-[11px] font-semibold transition";
    btnFilterLiked.textContent = active ? "⭐ 찜만 중" : "⭐ 찜만";
  }
  if (btnFilterConfirmed) {
    const active = slotFilterMode === "confirmed";
    btnFilterConfirmed.className = active
      ? "px-2 py-0.5 rounded-full bg-[#1DB954] text-black border border-[#1DB954] text-[11px] font-black transition"
      : "px-2 py-0.5 rounded-full bg-[#242424] hover:bg-[#333] text-gray-300 border border-[#383838] text-[11px] font-semibold transition";
    btnFilterConfirmed.textContent = active ? "✅ 확정만 중" : "✅ 확정만";
  }
  if (btnToggleHideConfirmed) {
    btnToggleHideConfirmed.className = hideConfirmedSlots
      ? "px-2 py-0.5 rounded-full bg-zinc-200 text-black border border-white text-[11px] font-black transition"
      : "px-2 py-0.5 rounded-full bg-[#242424] hover:bg-[#333] text-zinc-300 border border-[#383838] text-[11px] font-semibold transition";
    btnToggleHideConfirmed.textContent = hideConfirmedSlots ? "🙈 확정 숨김 중" : "🙈 확정 숨김";
  }
}

if (btnFilterLiked) {
  btnFilterLiked.addEventListener("click", () => {
    slotFilterMode = slotFilterMode === "liked" ? "all" : "liked";
    updateSlotFilterButtonsUI();
    document.getElementById("tabSlotsBtn").click();
    renderSlotsList();
  });
}

if (btnFilterConfirmed) {
  btnFilterConfirmed.addEventListener("click", () => {
    slotFilterMode = slotFilterMode === "confirmed" ? "all" : "confirmed";
    if (slotFilterMode === "confirmed" && hideConfirmedSlots) {
      hideConfirmedSlots = false;
      localStorage.setItem("soundreels_hide_confirmed", "false");
    }
    updateSlotFilterButtonsUI();
    document.getElementById("tabSlotsBtn").click();
    renderSlotsList();
  });
}

if (btnToggleHideConfirmed) {
  btnToggleHideConfirmed.addEventListener("click", () => {
    hideConfirmedSlots = !hideConfirmedSlots;
    localStorage.setItem("soundreels_hide_confirmed", hideConfirmedSlots ? "true" : "false");
    if (hideConfirmedSlots && slotFilterMode === "confirmed") {
      slotFilterMode = "all";
    }
    updateSlotFilterButtonsUI();
    document.getElementById("tabSlotsBtn").click();
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

function switchRightPanelTab(tabName) {
  const tabSlots = document.getElementById("tabSlots");
  const tabCandidates = document.getElementById("tabCandidates");
  const tabTrash = document.getElementById("tabTrash");
  const btnSlots = document.getElementById("tabSlotsBtn");
  const btnCandidates = document.getElementById("tabCandidatesBtn");
  const btnTrash = document.getElementById("tabTrashBtn");

  [tabSlots, tabCandidates, tabTrash].forEach(el => el && el.classList.add("hidden"));
  [btnSlots, btnCandidates, btnTrash].forEach(btn => btn && btn.classList.remove("border-[#1DB954]", "text-white"));

  if (tabName === "candidates") {
    if (tabCandidates) tabCandidates.classList.remove("hidden");
    if (btnCandidates) btnCandidates.classList.add("border-[#1DB954]", "text-white");
  } else if (tabName === "trash") {
    if (tabTrash) tabTrash.classList.remove("hidden");
    if (btnTrash) btnTrash.classList.add("border-[#1DB954]", "text-white");
    loadTrashForCurrentSlot();
  } else {
    if (tabSlots) tabSlots.classList.remove("hidden");
    if (btnSlots) btnSlots.classList.add("border-[#1DB954]", "text-white");
  }
}

document.getElementById("tabSlotsBtn").addEventListener("click", () => switchRightPanelTab("slots"));
document.getElementById("tabCandidatesBtn").addEventListener("click", () => switchRightPanelTab("candidates"));
if (document.getElementById("tabTrashBtn")) {
  document.getElementById("tabTrashBtn").addEventListener("click", () => switchRightPanelTab("trash"));
}
if (document.getElementById("btnRestoreAllTrash")) {
  document.getElementById("btnRestoreAllTrash").addEventListener("click", restoreAllTrash);
}

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
    if (s.keymap_settings) {
      try {
        applyKeymapSettingsObject(JSON.parse(s.keymap_settings));
      } catch (_) {}
    } else {
      try {
        const localKm = localStorage.getItem("soundreels_keymap_settings");
        if (localKm) applyKeymapSettingsObject(JSON.parse(localKm));
      } catch (_) {}
    }
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

async function runSlotScoutOrBulk(includeFreesound = false, targetCount = 24) {
  if (!currentSlot) return;
  const val = parseFloat(maxDurationInput.value);
  maxSearchDuration = !Number.isNaN(val) && val > 0 ? val : 0;
  saveSetting("max_search_dur", maxSearchDuration > 0 ? String(maxSearchDuration) : "");

  const btnBulk = document.getElementById("btnBulkPrefetchSlot");
  btnRescoutSlot.disabled = true;
  if (btnBulk) btnBulk.disabled = true;
  btnRescoutSlot.textContent = "⏳ 중...";
  if (btnBulk && includeFreesound) btnBulk.textContent = "⏳ 확보 중...";
  loadingSpinner.classList.remove("hidden");

  try {
    const res = await fetch(`/api/slots/${currentSlot.id}/rescout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        max_dur: maxSearchDuration > 0 ? maxSearchDuration : null,
        include_freesound: includeFreesound,
        target_count: targetCount,
      }),
    });
    const data = await res.json();
    await loadSlots();
    await selectSlot(currentSlot.id, false);
    if (includeFreesound) switchRightPanelTab("candidates");
    const banner = document.getElementById("similarStatusBanner");
    if (banner && res.ok) {
      banner.textContent = includeFreesound
        ? `📦 대량 확보 완료: +${data.found_count || 0}개 추가 반영 (총 ${currentCandidates.length}개 후보)`
        : `🔍 재탐색 완료: +${data.found_count || 0}개 반영 (총 ${currentCandidates.length}개 후보)`;
      banner.classList.remove("hidden");
    }
    if (res.ok && data.found_count === 0) {
      alert(`${maxSearchDuration > 0 ? maxSearchDuration + "초 이하의 " : ""}새 후보를 찾지 못했습니다. 최대 길이를 조금 늘려서 다시 찾아보세요.`);
    }
  } catch (e) {
    alert(`재탐색 오류: ${e}`);
  } finally {
    loadingSpinner.classList.add("hidden");
    btnRescoutSlot.disabled = false;
    btnRescoutSlot.textContent = "🔍 찾기";
    if (btnBulk) {
      btnBulk.disabled = false;
      btnBulk.textContent = "📦 대량";
    }
  }
}

btnRescoutSlot.addEventListener("click", () => runSlotScoutOrBulk(false, 24));
const btnBulkPrefetchEl = document.getElementById("btnBulkPrefetchSlot");
if (btnBulkPrefetchEl) {
  btnBulkPrefetchEl.addEventListener("click", () => runSlotScoutOrBulk(true, 40));
}

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

const topSearchInput = document.getElementById("searchInput");
const btnSearchModeToggle = document.getElementById("btnSearchModeToggle");
const btnRunTopSearch = document.getElementById("btnRunTopSearch");
const btnOpenFreesoundPopup = document.getElementById("btnOpenFreesoundPopup");
const btnImportFromPopup = document.getElementById("btnImportFromPopup");

function updateTopSearchModeUI() {
  if (btnSearchModeToggle) {
    if (topSearchMode === "sound") {
      btnSearchModeToggle.textContent = "🔊 사운드/URL";
      btnSearchModeToggle.className = "px-3 py-1.5 rounded-full bg-emerald-950 text-emerald-300 border border-emerald-700 text-xs font-bold shrink-0 hover:bg-emerald-900 transition";
    } else {
      btnSearchModeToggle.textContent = "📋 슬롯 필터";
      btnSearchModeToggle.className = "px-3 py-1.5 rounded-full bg-[#242424] text-gray-200 border border-[#444] text-xs font-bold shrink-0 hover:bg-[#333] transition";
    }
  }
  if (topSearchInput) {
    topSearchInput.placeholder = topSearchMode === "sound"
      ? "사운드 검색 (예: 엘리베이터 닫힘, bell) 또는 Freesound/LotsOfSounds URL 붙여넣기..."
      : "슬롯 필터 (예: 철문, 엘리베이터, 발소리...) · URL 붙여넣기도 가능";
  }
  if (btnRunTopSearch) {
    btnRunTopSearch.classList.toggle("hidden", topSearchMode === "slot" && !/^https?:\/\//i.test((topSearchInput?.value || "").trim()));
  }
}

async function importUrlIntoCurrentSlot(rawUrl) {
  if (!currentSlot) {
    alert("먼저 왼쪽/오른쪽에서 대상 사운드 슬롯을 선택해주세요.");
    return;
  }
  const cleanUrl = (rawUrl || "").trim();
  if (!cleanUrl) return;
  loadingSpinner.classList.remove("hidden");
  if (btnRunTopSearch) {
    btnRunTopSearch.disabled = true;
    btnRunTopSearch.textContent = "⏳...";
  }
  try {
    const res = await fetch(`/api/slots/${currentSlot.id}/import_url`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: cleanUrl }),
    });
    const data = await res.json();
    if (!res.ok) {
      alert(`URL 가져오기 실패: ${data.detail || "지원되지 않는 주소입니다."}`);
      return;
    }
    if (topSearchInput) topSearchInput.value = "";
    await refreshSlotAfterCandidateMutation(data.first_sound_id, `🔗 URL에서 ${data.imported_count}개 사운드를 후보 맨 위에 추가했습니다.`);
  } catch (e) {
    alert(`URL 가져오기 오류: ${e}`);
  } finally {
    loadingSpinner.classList.add("hidden");
    if (btnRunTopSearch) {
      btnRunTopSearch.disabled = false;
      btnRunTopSearch.textContent = "검색/추가";
    }
  }
}

async function refreshSlotAfterCandidateMutation(targetSoundId = null, bannerText = "") {
  if (!currentSlot) return;
  const qParam = maxSearchDuration > 0 ? `?max_dur=${encodeURIComponent(maxSearchDuration)}` : "";
  const cRes = await fetch(`/api/slots/${currentSlot.id}/candidates${qParam}`);
  const cData = await cRes.json();
  currentCandidates = cData.candidates || [];
  await loadSlots();
  await loadTrashForCurrentSlot();
  renderCandidatesQueue();
  renderSlotsList();
  switchRightPanelTab("candidates");

  if (currentCandidates.length > 0) {
    const idx = targetSoundId ? currentCandidates.findIndex(c => c.sound_id === targetSoundId) : 0;
    playCandidate(idx >= 0 ? idx : 0, true);
  }
  const banner = document.getElementById("similarStatusBanner");
  if (banner && bannerText) {
    banner.textContent = bannerText;
    banner.classList.remove("hidden");
  }
}

async function executeTopSearchOrUrl() {
  const val = (topSearchInput ? topSearchInput.value : "").trim();
  if (!val) {
    if (topSearchMode === "slot") renderSlotsList();
    return;
  }
  if (/^https?:\/\//i.test(val)) {
    await importUrlIntoCurrentSlot(val);
    return;
  }
  if (topSearchMode === "slot") {
    switchRightPanelTab("slots");
    renderSlotsList();
    return;
  }
  if (!currentSlot) return;

  loadingSpinner.classList.remove("hidden");
  if (btnRunTopSearch) {
    btnRunTopSearch.disabled = true;
    btnRunTopSearch.textContent = "⏳...";
  }
  try {
    const res = await fetch(`/api/slots/${currentSlot.id}/manual_search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query: val,
        max_dur: maxSearchDuration > 0 ? maxSearchDuration : null,
        limit: 30,
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      alert(`사운드 검색 실패: ${data.detail || "오류"}`);
      return;
    }
    if (data.added_count === 0) {
      alert(`'${val}' 검색 결과가 없습니다. 다른 키워드(영문 포함)를 시도하거나 Freesound 창에서 직접 찾아보세요.`);
      return;
    }
    await refreshSlotAfterCandidateMutation(
      data.first_sound_id,
      `🔊 '${val}' 수동 검색 완료: ${data.added_count}개 사운드를 대기열에 추가했습니다 (총 ${currentCandidates.length}개)`
    );
  } catch (e) {
    alert(`사운드 검색 오류: ${e}`);
  } finally {
    loadingSpinner.classList.add("hidden");
    if (btnRunTopSearch) {
      btnRunTopSearch.disabled = false;
      btnRunTopSearch.textContent = "검색/추가";
    }
  }
}

if (btnSearchModeToggle) {
  btnSearchModeToggle.addEventListener("click", () => {
    topSearchMode = topSearchMode === "sound" ? "slot" : "sound";
    updateTopSearchModeUI();
    renderSlotsList();
    if (topSearchInput) topSearchInput.focus();
  });
}

if (topSearchInput) {
  topSearchInput.addEventListener("input", () => {
    if (topSearchMode === "slot") {
      renderSlotsList();
    }
    updateTopSearchModeUI();
  });
  topSearchInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      executeTopSearchOrUrl();
    }
  });
}

if (btnRunTopSearch) {
  btnRunTopSearch.addEventListener("click", executeTopSearchOrUrl);
}

if (btnOpenFreesoundPopup) {
  btnOpenFreesoundPopup.addEventListener("click", () => {
    const typed = (topSearchInput ? topSearchInput.value : "").trim();
    const rawTags = currentSlot && Array.isArray(currentSlot.search_tags) ? currentSlot.search_tags.slice(0, 2).join(" ") : "";
    const q = (!/^https?:\/\//i.test(typed) && typed) ? typed : (rawTags || (currentSlot ? currentSlot.name : "game sfx"));
    const targetUrl = `https://freesound.org/search/?q=${encodeURIComponent(q)}&f=license%3A%22Creative+Commons+0%22`;
    openOriginalSource(targetUrl, { name: `Freesound 탐색 (${q})`, source_kind: "external" });
  });
}

if (btnImportFromPopup) {
  btnImportFromPopup.addEventListener("click", async () => {
    if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.get_popup_url === "function") {
      const info = await window.pywebview.api.get_popup_url();
      if (info && info.url && /^https?:\/\//i.test(info.url)) {
        await importUrlIntoCurrentSlot(info.url);
        return;
      }
    }
    const manual = prompt("열려 있는 창이 없거나 주소를 읽지 못했습니다. Freesound/LotsOfSounds URL을 직접 붙여넣으세요:");
    if (manual && manual.trim()) await importUrlIntoCurrentSlot(manual.trim());
  });
}

async function pollDesktopPopupBridge() {
  try {
    const res = await fetch("/api/desktop/popup_state");
    if (!res.ok) return;
    const st = await res.json();
    if (st.import_seq && st.import_seq > lastPopupImportSeq) {
      lastPopupImportSeq = st.import_seq;
      const imp = st.last_imported;
      if (imp && currentSlot && imp.slot_id === currentSlot.id) {
        await refreshSlotAfterCandidateMutation(
          imp.first_sound_id,
          `📥 팝업 창에서 ${imp.imported_count}개 사운드를 바로 후보에 담았습니다!`
        );
      } else {
        await loadSlots();
      }
    }
  } catch (_) {}
}

updateTopSearchModeUI();
setInterval(pollDesktopPopupBridge, 2000);

document.querySelectorAll(".dock-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    const idx = parseInt(btn.dataset.dock, 10);
    const sound = getActiveCompareDock()[idx];
    if (sound) {
      const dock = getActiveCompareDock();
  document.querySelectorAll(".dock-btn").forEach((b, dIdx) => {
    const docked = dock[dIdx];
    const isThis = docked && docked.sound_id === cand.sound_id;
    b.classList.toggle("active", !!isThis);
    b.classList.toggle("border-[#1DB954]", !!isThis);
  });
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
// Shortcut & Playback Settings Modal + Customizable Keyboard Dispatcher
// =========================================================================

const KEYMAP_LABELS = [
  ["playPause", "재생 / 일시정지"],
  ["replay", "처음부터 다시 재생 (단독 키 · 스페이스 2연타와 병행)"],
  ["prevCandidate", "이전 사운드로 넘기기 (앞)"],
  ["nextCandidate", "다음 사운드로 넘기기 (뒤)"],
  ["like", "찜하기"],
  ["similar", "비슷한 소리 찾기"],
  ["mutate", "즉석 변형"],
  ["confirm", "최종 채택 / 확정 취소"],
  ["loopMode", "반복 모드 전환"],
  ["sourceLink", "원본 출처 창 열기 + 복사"],
];

function applyKeymapSettingsObject(rawObj) {
  if (!rawObj || typeof rawObj !== "object") return;
  keymapSettings = {
    doubleSpaceReplay: rawObj.doubleSpaceReplay !== undefined ? !!rawObj.doubleSpaceReplay : DEFAULT_KEYMAP_SETTINGS.doubleSpaceReplay,
    doubleSpaceWindowMs: Math.max(150, Math.min(800, parseInt(rawObj.doubleSpaceWindowMs || DEFAULT_KEYMAP_SETTINGS.doubleSpaceWindowMs, 10))),
    arrowUpDownAlso: rawObj.arrowUpDownAlso !== undefined ? !!rawObj.arrowUpDownAlso : DEFAULT_KEYMAP_SETTINGS.arrowUpDownAlso,
    keys: { ...DEFAULT_KEYMAP_SETTINGS.keys, ...(rawObj.keys || {}) },
  };
  updateShortcutTooltips();
}

function formatKeyCodeLabel(code) {
  if (!code) return "-";
  const map = {
    Space: "Spacebar",
    ArrowLeft: "← Left",
    ArrowRight: "→ Right",
    ArrowUp: "↑ Up",
    ArrowDown: "↓ Down",
    Enter: "Enter",
  };
  if (map[code]) return map[code];
  if (code.startsWith("Key")) return code.slice(3);
  if (code.startsWith("Digit")) return code.slice(5);
  return code;
}

function updateShortcutTooltips() {
  const k = keymapSettings.keys;
  const btnPlayEl = document.getElementById("btnPlay");
  if (btnPlayEl) {
    const dbl = keymapSettings.doubleSpaceReplay ? " · 2연타: 처음부터 다시 재생" : "";
    btnPlayEl.title = `재생 / 일시정지 (${formatKeyCodeLabel(k.playPause)}${dbl})`;
  }
  const btnPrevEl = document.getElementById("btnPrev");
  if (btnPrevEl) btnPrevEl.title = `이전 사운드 (${formatKeyCodeLabel(k.prevCandidate)})`;
  const btnNextEl = document.getElementById("btnNext");
  if (btnNextEl) btnNextEl.title = `다음 사운드 (${formatKeyCodeLabel(k.nextCandidate)})`;
}

const settingsModal = document.getElementById("settingsModal");
const chkDoubleSpaceReplay = document.getElementById("chkDoubleSpaceReplay");
const doubleSpaceWindowMsInput = document.getElementById("doubleSpaceWindowMs");
const chkArrowUpDownAlso = document.getElementById("chkArrowUpDownAlso");
const keymapRowsEl = document.getElementById("keymapRows");
const keyCaptureHintEl = document.getElementById("keyCaptureHint");
let draftKeymapSettings = null;

function renderKeymapRows() {
  if (!keymapRowsEl || !draftKeymapSettings) return;
  keymapRowsEl.innerHTML = "";
  KEYMAP_LABELS.forEach(([actionKey, label]) => {
    const row = document.createElement("div");
    row.className = "flex items-center justify-between bg-[#121212] border border-[#2a2a2a] rounded-lg px-3 py-2 text-xs";

    const span = document.createElement("span");
    span.className = "text-gray-200 font-medium";
    span.textContent = label;

    const btn = document.createElement("button");
    const isCapturing = capturingKeyAction === actionKey;
    btn.className = isCapturing
      ? "px-3 py-1 rounded bg-amber-500 text-black font-black min-w-[96px] text-center animate-pulse"
      : "px-3 py-1 rounded bg-[#242424] hover:bg-[#333] text-emerald-300 border border-[#383838] font-mono font-bold min-w-[96px] text-center";
    btn.textContent = isCapturing ? "입력 대기 중..." : formatKeyCodeLabel(draftKeymapSettings.keys[actionKey]);
    btn.addEventListener("click", () => {
      capturingKeyAction = actionKey;
      if (keyCaptureHintEl) keyCaptureHintEl.classList.remove("hidden");
      renderKeymapRows();
    });

    row.appendChild(span);
    row.appendChild(btn);
    keymapRowsEl.appendChild(row);
  });
}

function openSettingsModal() {
  if (!settingsModal) return;
  draftKeymapSettings = JSON.parse(JSON.stringify(keymapSettings));
  capturingKeyAction = null;
  if (keyCaptureHintEl) keyCaptureHintEl.classList.add("hidden");
  if (chkDoubleSpaceReplay) chkDoubleSpaceReplay.checked = !!draftKeymapSettings.doubleSpaceReplay;
  if (doubleSpaceWindowMsInput) doubleSpaceWindowMsInput.value = draftKeymapSettings.doubleSpaceWindowMs || 320;
  if (chkArrowUpDownAlso) chkArrowUpDownAlso.checked = !!draftKeymapSettings.arrowUpDownAlso;
  renderKeymapRows();
  settingsModal.classList.remove("hidden");
}

function closeSettingsModal() {
  capturingKeyAction = null;
  if (keyCaptureHintEl) keyCaptureHintEl.classList.add("hidden");
  if (settingsModal) settingsModal.classList.add("hidden");
}

if (document.getElementById("btnOpenSettings")) {
  document.getElementById("btnOpenSettings").addEventListener("click", openSettingsModal);
}
if (document.getElementById("btnCloseSettingsModal")) {
  document.getElementById("btnCloseSettingsModal").addEventListener("click", closeSettingsModal);
}
if (document.getElementById("btnCloseSettingsBottom")) {
  document.getElementById("btnCloseSettingsBottom").addEventListener("click", closeSettingsModal);
}
if (settingsModal) {
  settingsModal.addEventListener("click", (e) => {
    if (e.target === settingsModal) closeSettingsModal();
  });
}
if (document.getElementById("btnResetSettings")) {
  document.getElementById("btnResetSettings").addEventListener("click", () => {
    draftKeymapSettings = JSON.parse(JSON.stringify(DEFAULT_KEYMAP_SETTINGS));
    if (chkDoubleSpaceReplay) chkDoubleSpaceReplay.checked = draftKeymapSettings.doubleSpaceReplay;
    if (doubleSpaceWindowMsInput) doubleSpaceWindowMsInput.value = draftKeymapSettings.doubleSpaceWindowMs;
    if (chkArrowUpDownAlso) chkArrowUpDownAlso.checked = draftKeymapSettings.arrowUpDownAlso;
    capturingKeyAction = null;
    if (keyCaptureHintEl) keyCaptureHintEl.classList.add("hidden");
    renderKeymapRows();
  });
}
if (document.getElementById("btnSaveSettingsModal")) {
  document.getElementById("btnSaveSettingsModal").addEventListener("click", async () => {
    if (!draftKeymapSettings) return;
    draftKeymapSettings.doubleSpaceReplay = chkDoubleSpaceReplay ? !!chkDoubleSpaceReplay.checked : true;
    draftKeymapSettings.doubleSpaceWindowMs = Math.max(150, Math.min(800, parseInt(doubleSpaceWindowMsInput?.value || "320", 10) || 320));
    draftKeymapSettings.arrowUpDownAlso = chkArrowUpDownAlso ? !!chkArrowUpDownAlso.checked : true;
    applyKeymapSettingsObject(draftKeymapSettings);
    const serialized = JSON.stringify(keymapSettings);
    localStorage.setItem("soundreels_keymap_settings", serialized);
    await saveSetting("keymap_settings", serialized);
    closeSettingsModal();
  });
}

// =========================================================================
// Full-Screen Pre-Export Review Mode (#reviewView) & Receipt Viewer Modal
// =========================================================================

const workspaceView = document.getElementById("workspaceView");
const bottomResizerEl = document.getElementById("bottomResizer");
const bottomFooterEl = document.getElementById("bottomFooter");
const reviewView = document.getElementById("reviewView");
const reviewUnityPathInput = document.getElementById("reviewUnityPathInput");
const reviewChecklist = document.getElementById("reviewChecklist");
const revTabTasksBtn = document.getElementById("revTabTasksBtn");
const revTabPassedBtn = document.getElementById("revTabPassedBtn");
const reviewSummaryCounts = document.getElementById("reviewSummaryCounts");
const revSlotBadge = document.getElementById("revSlotBadge");
const revSlotTitle = document.getElementById("revSlotTitle");
const revSlotMemo = document.getElementById("revSlotMemo");
const reviewVideo = document.getElementById("reviewVideo");
const reviewGifImg = document.getElementById("reviewGifImg");
const reviewNoSceneBox = document.getElementById("reviewNoSceneBox");
const revOutputPathLabel = document.getElementById("revOutputPathLabel");
const revSpecBody = document.getElementById("revSpecBody");
const btnReviewTogglePass = document.getElementById("btnReviewTogglePass");
const btnReviewEditInWorkspace = document.getElementById("btnReviewEditInWorkspace");
const receiptModal = document.getElementById("receiptModal");
const receiptTextContent = document.getElementById("receiptTextContent");
const receiptSavedPathNote = document.getElementById("receiptSavedPathNote");

function openReceiptModal(text, pathNote = "") {
  if (!receiptModal) return;
  receiptTextContent.textContent = text || "(내용 없음)";
  if (pathNote) {
    receiptSavedPathNote.textContent = `📄 ${pathNote}`;
    receiptSavedPathNote.classList.remove("hidden");
  } else {
    receiptSavedPathNote.classList.add("hidden");
  }
  receiptModal.classList.remove("hidden");
}

function closeReceiptModal() {
  if (receiptModal) receiptModal.classList.add("hidden");
}

if (document.getElementById("btnCloseReceiptModal")) {
  document.getElementById("btnCloseReceiptModal").addEventListener("click", closeReceiptModal);
}
if (document.getElementById("btnCloseReceiptBottom")) {
  document.getElementById("btnCloseReceiptBottom").addEventListener("click", closeReceiptModal);
}
if (receiptModal) {
  receiptModal.addEventListener("click", (e) => {
    if (e.target === receiptModal) closeReceiptModal();
  });
}
if (document.getElementById("btnCopyReceiptText")) {
  document.getElementById("btnCopyReceiptText").addEventListener("click", (e) => {
    copyTextToClipboard(receiptTextContent.textContent, e.currentTarget);
  });
}

function stopReviewPlayback() {
  if (reviewOffsetTimer) {
    clearTimeout(reviewOffsetTimer);
    reviewOffsetTimer = null;
  }
  audioPlayer.pause();
  if (reviewVideo) reviewVideo.pause();
}

function getReviewListForActiveTab() {
  if (reviewTab === "passed") {
    return allSlots.filter(s => !!s.review_passed);
  }
  // Pending/Task tab: show Confirmed-needing-pass FIRST, then Liked-only, then remaining unpassed slots
  return allSlots
    .filter(s => !s.review_passed)
    .sort((a, b) => {
      const rank = (s) => (s.status === "confirmed" ? 0 : (s.liked_count > 0 ? 1 : 2));
      return rank(a) - rank(b);
    });
}

function renderReviewChecklist() {
  if (!reviewChecklist) return;
  const tasksList = allSlots.filter(s => !s.review_passed);
  const passedList = allSlots.filter(s => !!s.review_passed);

  if (revTabTasksBtn) revTabTasksBtn.textContent = `📋 점검 테스크 (${tasksList.length})`;
  if (revTabPassedBtn) revTabPassedBtn.textContent = `✅ 패스 완료함 (${passedList.length})`;
  if (reviewSummaryCounts) reviewSummaryCounts.textContent = `패스 완료 ${passedList.length} / ${allSlots.length}`;

  if (revTabTasksBtn && revTabPassedBtn) {
    if (reviewTab === "tasks") {
      revTabTasksBtn.className = "flex-1 py-3 border-b-2 border-amber-400 text-amber-300";
      revTabPassedBtn.className = "flex-1 py-3 border-b-2 border-transparent text-gray-400 hover:text-white";
    } else {
      revTabPassedBtn.className = "flex-1 py-3 border-b-2 border-[#1DB954] text-[#1DB954]";
      revTabTasksBtn.className = "flex-1 py-3 border-b-2 border-transparent text-gray-400 hover:text-white";
    }
  }

  const list = getReviewListForActiveTab();
  reviewChecklist.innerHTML = "";
  if (list.length === 0) {
    reviewChecklist.innerHTML = `<div class="text-center text-gray-500 text-xs py-10">${reviewTab === "passed" ? "패스 완료된 슬롯이 아직 없습니다." : "모든 슬롯을 패스 완료했습니다! 🎉"}</div>`;
    return;
  }

  list.forEach(s => {
    const isSel = s.id === reviewSelectedSlotId;
    const isConfirmed = s.status === "confirmed";
    const row = document.createElement("div");
    let borderCls = "border-[#2a2a2a] bg-[#1c1c1c] hover:bg-[#242424]";
    if (s.review_passed) {
      borderCls = isSel
        ? "border-[#1DB954] bg-emerald-950/60 ring-1 ring-[#1DB954]"
        : "border-emerald-800/70 bg-emerald-950/30 hover:bg-emerald-950/45";
    } else if (isConfirmed) {
      borderCls = isSel
        ? "border-amber-400 bg-amber-950/40 ring-1 ring-amber-400"
        : "border-amber-800/60 bg-[#1f1e18] hover:bg-[#26241b]";
    } else if (isSel) {
      borderCls = "border-gray-400 bg-[#2a2a2a]";
    }
    row.className = `p-3 rounded-xl border cursor-pointer transition space-y-1 ${borderCls}`;

    const statePill = s.review_passed
      ? `<span class="bg-[#1DB954] text-black text-[10px] px-2 py-0.5 rounded-full font-black">✅ 패스됨</span>`
      : (isConfirmed
          ? `<span class="bg-amber-400 text-black text-[10px] px-2 py-0.5 rounded-full font-black">점검 대기</span>`
          : `<span class="bg-[#2c2c2c] text-gray-400 text-[10px] px-2 py-0.5 rounded-full">⚠️ 미확정</span>`);

    const sndLabel = s.confirmed_sound
      ? `🔊 ${s.confirmed_sound.name}${s.confirmed_sound.reverse ? " (🔁역재생)" : ""}`
      : (s.liked_count > 0 ? `⭐ 찜 후보 ${s.liked_count}개` : "확정된 소리 없음");
    const hMeta = HANDOFF_STATUS_META[s.handoff_status];
    const hLine = (hMeta || s.work_memo || s.assignee)
      ? `<div class="text-[11px] text-indigo-300 truncate">${hMeta ? hMeta.label + " " : ""}${s.assignee ? `[${s.assignee}] ` : ""}${s.work_memo || ""}</div>`
      : "";

    row.innerHTML = `
      <div class="flex items-center justify-between gap-2">
        <span class="font-bold text-xs text-white truncate">${s.scene_media_url ? "🎬 " : ""}${s.name}</span>
        ${statePill}
      </div>
      <div class="text-[11px] text-gray-400 truncate">${sndLabel}</div>
      ${hLine}
    `;
    row.addEventListener("click", () => selectReviewSlot(s.id, true));
    row.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      openSlotMemoModal(s.id);
    });
    reviewChecklist.appendChild(row);
  });
}

function selectReviewSlot(slotId, autoPlay = true) {
  const slot = allSlots.find(s => s.id === slotId);
  if (!slot) return;
  reviewSelectedSlotId = slot.id;
  stopReviewPlayback();
  renderReviewChecklist();

  revSlotTitle.textContent = slot.name;
  revSlotMemo.textContent = slot.memo || "";
  if (slot.review_passed) {
    revSlotBadge.textContent = "✅ 패스 완료";
    revSlotBadge.className = "text-xs px-2.5 py-0.5 rounded-full bg-[#1DB954] text-black font-black font-mono";
  } else if (slot.status === "confirmed") {
    revSlotBadge.textContent = "🔎 확정됨 · 점검 대기";
    revSlotBadge.className = "text-xs px-2.5 py-0.5 rounded-full bg-amber-950 text-amber-300 border border-amber-700 font-bold font-mono";
  } else {
    revSlotBadge.textContent = "⚠️ 미확정 (작업 필요)";
    revSlotBadge.className = "text-xs px-2.5 py-0.5 rounded-full bg-red-950/60 text-red-300 border border-red-800 font-bold font-mono";
  }

  // Scene Media Display
  reviewVideo.classList.add("hidden");
  reviewVideo.removeAttribute("src");
  reviewGifImg.classList.add("hidden");
  reviewGifImg.removeAttribute("src");
  reviewNoSceneBox.classList.add("hidden");

  if (slot.scene_media_url) {
    if (slot.scene_media_type === "video") {
      reviewVideo.src = slot.scene_media_url;
      reviewVideo.currentTime = slot.scene_in || 0.0;
      reviewVideo.classList.remove("hidden");
    } else {
      reviewGifImg.src = slot.scene_media_url;
      reviewGifImg.classList.remove("hidden");
    }
  } else {
    reviewNoSceneBox.classList.remove("hidden");
  }

  // Receipt Card & Pass Button
  const cs = slot.confirmed_sound;
  revOutputPathLabel.textContent = `Assets/Audio/SFX/${slot.id}/${slot.id}_01.wav`;
  if (cs) {
    const srcLabel = cs.source_kind === "ai_generated"
      ? "🤖 AI / 로컬 자체 생성 (출처 표기 불필요)"
      : (cs.source_kind === "ai_mutated"
          ? `🎲 AI 변형 (${cs.original_source_url || ""})`
          : `🌐 ${cs.original_source_url || cs.catalog_url || "CC0"}`);
    const trimEnd = cs.audio_out > cs.audio_in ? cs.audio_out : cs.duration;
    const hasTrim = (cs.audio_in > 0) || (cs.audio_out > 0);
    const trimStr = hasTrim ? `${Number(cs.audio_in).toFixed(2)}s ~ ${Number(trimEnd).toFixed(2)}s` : `전체 자동 무음제거 (${Number(cs.duration).toFixed(2)}s)`;
    const rate = Number(cs.playback_rate || 1.0);
    const rateStr = `${cs.reverse ? "🔁 역재생 · " : ""}${rate.toFixed(2)}x (${cs.preserve_pitch ? "피치유지" : "피치연동"})`;
    const handoffSummary = (slot.work_memo || slot.assignee || slot.handoff_status)
      ? `<div class="col-span-2 text-indigo-200 bg-indigo-950/40 border border-indigo-800/60 rounded-lg px-2.5 py-1.5 mt-1">📝 <b>인수인계:</b> ${HANDOFF_STATUS_META[slot.handoff_status]?.label || ""} ${slot.assignee ? `[${slot.assignee}] ` : ""}${slot.work_memo || ""}</div>`
      : "";

    revSpecBody.innerHTML = `
      <div><span class="text-gray-400">확정 음원:</span> <span class="text-white font-bold">${cs.name}</span></div>
      <div><span class="text-gray-400">라이선스:</span> <span class="text-emerald-300 font-semibold">${cs.license_type} · ${cs.author}</span></div>
      <div class="col-span-2 truncate"><span class="text-gray-400">원본 출처:</span> <span class="text-sky-300 font-mono">${srcLabel}</span></div>
      <div><span class="text-gray-400">자르기 구간:</span> <span class="text-gray-200 font-mono">${trimStr}</span></div>
      <div><span class="text-gray-400">배속 / 피치:</span> <span class="text-amber-300 font-mono font-bold">${rateStr}</span></div>
      ${handoffSummary}
    `;
    btnReviewTogglePass.disabled = false;
    if (slot.review_passed) {
      btnReviewTogglePass.className = "px-5 py-2 rounded-lg bg-amber-950 hover:bg-amber-900 text-amber-300 border border-amber-700 text-xs font-black";
      btnReviewTogglePass.textContent = "↩ 불만족 · 점검 테스크로 되돌리기";
    } else {
      btnReviewTogglePass.className = "px-6 py-2 rounded-lg bg-[#1DB954] hover:bg-[#1ed760] text-black text-xs font-black";
      btnReviewTogglePass.textContent = "✅ 이상 없음 · 패스 완료함으로 보내기";
    }
  } else {
    revSpecBody.innerHTML = `<div class="col-span-2 text-amber-300">아직 확정된 사운드가 없습니다. '🛠️ 작업실에서 수정하기'를 눌러 사운드를 확정한 뒤 패스해 주세요.</div>`;
    btnReviewTogglePass.disabled = true;
    btnReviewTogglePass.className = "px-5 py-2 rounded-lg bg-[#242424] text-gray-500 border border-[#333] text-xs font-bold cursor-not-allowed";
    btnReviewTogglePass.textContent = "✅ 확정 후 패스 가능";
  }

  if (autoPlay && cs) {
    playReviewSynced();
  }
}

function playReviewSynced() {
  const slot = allSlots.find(s => s.id === reviewSelectedSlotId);
  if (!slot) return;
  stopReviewPlayback();

  if (slot.scene_media_url) {
    if (slot.scene_media_type === "video") {
      reviewVideo.currentTime = slot.scene_in || 0.0;
      reviewVideo.play().catch(() => {});
    } else {
      // Restart GIF cleanly from frame 0
      const baseSrc = slot.scene_media_url;
      reviewGifImg.src = "";
      reviewGifImg.src = baseSrc;
    }
  }

  const cs = slot.confirmed_sound;
  if (!cs) return;
  initAudioContext();
  currentNormGain = Number(cs.norm_gain || 1.0);
  applyAudioGainNodes();
  currentPlaybackRate = Number(cs.playback_rate || 1.0);
  currentPreservePitch = cs.preserve_pitch !== 0;
  currentReverse = !!cs.reverse;
  currentPitchSemitones = Number(cs.pitch_semitones || 0.0);
  currentTailSec = Number(cs.tail_sec || 0.0);
  applyAudioPlaybackSpeed();
  audioIn = Number(cs.audio_in || 0.0);
  audioOut = Number(cs.audio_out || 0.0) || Number(cs.duration || 10.0);

  audioPlayer.src = getCandidatePlaybackUrl(cs);
  const triggerSnd = () => {
    applyAudioPlaybackSpeed();
    audioPlayer.currentTime = audioIn;
    audioPlayer.play().catch(() => {});
  };
  const offsetMs = Math.max(0, Number(slot.sound_offset || 0.0) * 1000);
  audioPlayer.onloadedmetadata = () => {
    if (offsetMs > 10) {
      reviewOffsetTimer = setTimeout(triggerSnd, offsetMs);
    } else {
      triggerSnd();
    }
  };
  audioPlayer.load();
}

function navigateReviewSlot(delta) {
  const list = getReviewListForActiveTab();
  if (list.length === 0) return;
  const idx = list.findIndex(s => s.id === reviewSelectedSlotId);
  const nextIdx = idx < 0 ? 0 : Math.max(0, Math.min(list.length - 1, idx + delta));
  selectReviewSlot(list[nextIdx].id, true);
}

async function switchToReviewView() {
  stopReviewPlayback();
  audioPlayer.pause();
  sceneIsPlaying = false;
  if (sceneVideo) sceneVideo.pause();

  isReviewMode = true;
  if (reviewUnityPathInput && unityPathInput) {
    reviewUnityPathInput.value = unityPathInput.value;
  }
  workspaceView.classList.add("hidden");
  if (bottomResizerEl) bottomResizerEl.classList.add("hidden");
  if (bottomFooterEl) bottomFooterEl.classList.add("hidden");
  reviewView.classList.remove("hidden");

  await loadSlots();
  renderReviewChecklist();
  const list = getReviewListForActiveTab();
  if (list.length > 0) {
    selectReviewSlot(list[0].id, false);
  }
}

function switchToWorkspaceView(targetSlotId = null) {
  stopReviewPlayback();
  isReviewMode = false;
  reviewView.classList.add("hidden");
  workspaceView.classList.remove("hidden");
  if (bottomResizerEl) bottomResizerEl.classList.remove("hidden");
  if (bottomFooterEl) bottomFooterEl.classList.remove("hidden");
  resizeWaveformCanvas();
  if (targetSlotId) {
    selectSlot(targetSlotId, false);
  }
}

if (document.getElementById("btnOpenReviewView")) {
  document.getElementById("btnOpenReviewView").addEventListener("click", switchToReviewView);
}
if (document.getElementById("btnBackToWorkspace")) {
  document.getElementById("btnBackToWorkspace").addEventListener("click", () => switchToWorkspaceView());
}
if (revTabTasksBtn) {
  revTabTasksBtn.addEventListener("click", () => {
    reviewTab = "tasks";
    renderReviewChecklist();
    const list = getReviewListForActiveTab();
    if (list.length > 0) selectReviewSlot(list[0].id, false);
  });
}
if (revTabPassedBtn) {
  revTabPassedBtn.addEventListener("click", () => {
    reviewTab = "passed";
    renderReviewChecklist();
    const list = getReviewListForActiveTab();
    if (list.length > 0) selectReviewSlot(list[0].id, false);
  });
}
if (document.getElementById("btnRevReplay")) {
  document.getElementById("btnRevReplay").addEventListener("click", playReviewSynced);
}
if (document.getElementById("btnRevStop")) {
  document.getElementById("btnRevStop").addEventListener("click", stopReviewPlayback);
}
if (document.getElementById("btnRevPrev")) {
  document.getElementById("btnRevPrev").addEventListener("click", () => navigateReviewSlot(-1));
}
if (document.getElementById("btnRevNext")) {
  document.getElementById("btnRevNext").addEventListener("click", () => navigateReviewSlot(1));
}
if (btnReviewEditInWorkspace) {
  btnReviewEditInWorkspace.addEventListener("click", () => {
    const sid = reviewSelectedSlotId;
    switchToWorkspaceView(sid);
  });
}
if (btnReviewTogglePass) {
  btnReviewTogglePass.addEventListener("click", async () => {
    const slot = allSlots.find(s => s.id === reviewSelectedSlotId);
    if (!slot) return;
    const nextPassed = !slot.review_passed;
    try {
      const res = await fetch(`/api/slots/${slot.id}/review_pass`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ passed: nextPassed }),
      });
      const data = await res.json();
      if (!res.ok) {
        alert(data.detail || "패스 처리 실패");
        return;
      }
      slot.review_passed = !!data.review_passed;
      renderSlotsList();
      renderReviewChecklist();
      const remaining = getReviewListForActiveTab();
      if (remaining.length > 0) {
        selectReviewSlot(remaining[0].id, false);
      } else {
        selectReviewSlot(slot.id, false);
      }
    } catch (e) {
      alert(`오류: ${e}`);
    }
  });
}
if (document.getElementById("btnPreviewReceipt")) {
  document.getElementById("btnPreviewReceipt").addEventListener("click", async () => {
    const hasPassed = allSlots.some(s => s.review_passed);
    const res = await fetch(`/api/export/receipt_preview?passed_only=${hasPassed}`);
    const data = await res.json();
    openReceiptModal(data.receipt_text, hasPassed ? "총점검 패스 완료 항목 기준 미리보기" : "전체 확정 완료 항목 기준 미리보기");
  });
}
if (document.getElementById("btnReviewExportPassed")) {
  document.getElementById("btnReviewExportPassed").addEventListener("click", async () => {
    const pathVal = (reviewUnityPathInput?.value || unityPathInput?.value || "").trim();
    if (!pathVal) {
      alert("상단 입력란에 Unity 프로젝트 경로를 입력해주세요. (예: E:/MyGame/)");
      if (reviewUnityPathInput) reviewUnityPathInput.focus();
      return;
    }
    unityPathInput.value = pathVal;
    saveSetting("unity_path", pathVal);

    const hasPassed = allSlots.some(s => s.review_passed);
    try {
      const res = await fetch("/api/export/batch_unity", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ unity_project_path: pathVal, passed_only: hasPassed }),
      });
      const data = await res.json();
      if (res.ok) {
        openReceiptModal(data.receipt_text, `영수증 파일 저장됨: ${data.receipt_path} (${data.exported_count}개 WAV 내보내기 완료)`);
      } else {
        alert(`내보내기 실패: ${data.detail || "오류 발생"}`);
      }
    } catch (e) {
      alert(`내보내기 오류: ${e}`);
    }
  });
}

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
document.addEventListener("mouseup", () => {
  const active = document.activeElement;
  if (!active || active === document.body) return;
  if (active.tagName === "BUTTON" || (active.tagName === "INPUT" && active.type === "range")) {
    active.blur();
  }
});

window.addEventListener("keydown", (e) => {
  // Handle shortcut key capture inside Settings Modal first
  if (capturingKeyAction && settingsModal && !settingsModal.classList.contains("hidden")) {
    e.preventDefault();
    e.stopPropagation();
    if (e.key === "Escape") {
      capturingKeyAction = null;
      if (keyCaptureHintEl) keyCaptureHintEl.classList.add("hidden");
      renderKeymapRows();
      return;
    }
    if (!["ShiftLeft", "ShiftRight", "ControlLeft", "ControlRight", "AltLeft", "AltRight", "MetaLeft", "MetaRight"].includes(e.code)) {
      draftKeymapSettings.keys[capturingKeyAction] = e.code;
      capturingKeyAction = null;
      if (keyCaptureHintEl) keyCaptureHintEl.classList.add("hidden");
      renderKeymapRows();
    }
    return;
  }

  if (e.key === "Escape") {
    closeAddSlotModal();
    closeSlotMemoModal();
    closeSimilarPromptModal();
    closeReceiptModal();
    closeSettingsModal();
    closeSourceInfoModal();
    return;
  }

  const addSlotModalEl = document.getElementById("addSlotModal");
  if (addSlotModalEl && !addSlotModalEl.classList.contains("hidden")) return;
  const slotMemoModalEl = document.getElementById("slotMemoModal");
  if (slotMemoModalEl && !slotMemoModalEl.classList.contains("hidden")) return;
  const simModal = document.getElementById("similarPromptModal");
  if (simModal && !simModal.classList.contains("hidden")) return;
  if (receiptModal && !receiptModal.classList.contains("hidden")) return;
  if (settingsModal && !settingsModal.classList.contains("hidden")) return;
  if (isTextEditingTarget(e.target)) return;

  if (isReviewMode) {
    if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
      e.preventDefault();
      navigateReviewSlot(-1);
    } else if (e.key === "ArrowRight" || e.key === "ArrowDown") {
      e.preventDefault();
      navigateReviewSlot(1);
    } else if (e.key === " " || e.code === "Space") {
      e.preventDefault();
      e.stopPropagation();
      if (!e.repeat) {
        if (!audioPlayer.paused) stopReviewPlayback();
        else playReviewSynced();
      }
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (btnReviewTogglePass && !btnReviewTogglePass.disabled) btnReviewTogglePass.click();
    }
    return;
  }

  const code = e.code || e.key;
  const k = keymapSettings.keys;

  if (code === k.playPause || (k.playPause === "Space" && (e.key === " " || e.code === "Space"))) {
    e.preventDefault();
    e.stopPropagation();
    if (document.activeElement && document.activeElement !== document.body && !isTextEditingTarget(document.activeElement)) {
      document.activeElement.blur();
    }
    if (!e.repeat) {
      const now = performance.now();
      const winMs = keymapSettings.doubleSpaceWindowMs || 320;
      if (keymapSettings.doubleSpaceReplay && lastSpacePressAt > 0 && (now - lastSpacePressAt) <= winMs) {
        lastSpacePressAt = 0;
        replayFromStart();
      } else {
        lastSpacePressAt = now;
        togglePlayback();
      }
    }
  } else if (code === k.replay) {
    e.preventDefault();
    if (!e.repeat) replayFromStart();
  } else if (code === k.nextCandidate || (keymapSettings.arrowUpDownAlso && (e.key === "ArrowDown" || e.code === "KeyJ"))) {
    e.preventDefault();
    if (currentIndex + 1 < currentCandidates.length) playCandidate(currentIndex + 1);
  } else if (code === k.prevCandidate || (keymapSettings.arrowUpDownAlso && (e.key === "ArrowUp" || e.code === "KeyK"))) {
    e.preventDefault();
    if (currentIndex - 1 >= 0) playCandidate(currentIndex - 1);
  } else if (code === k.like) {
    e.preventDefault();
    toggleLikeCurrent();
  } else if (code === k.similar) {
    e.preventDefault();
    triggerSimilarCurrent();
  } else if (code === k.mutate) {
    e.preventDefault();
    triggerMutateCurrent();
  } else if (e.key === "Delete" || e.key === "Backspace") {
    e.preventDefault();
    if (currentIndex >= 0 && currentCandidates[currentIndex]) {
      skipCandidateById(currentCandidates[currentIndex].sound_id);
    }
  } else if (e.code === "KeyA") {
    e.preventDefault();
    nudgeAudioPoint(-1, e.shiftKey);
  } else if (e.code === "KeyD") {
    e.preventDefault();
    nudgeAudioPoint(1, e.shiftKey);
  } else if (code === k.sourceLink) {
    e.preventDefault();
    const cand = currentIndex >= 0 ? currentCandidates[currentIndex] : null;
    const url = btnOpenSourceUrl.dataset.url;
    if (url || cand) openOriginalSource(url, cand);
  } else if (code === k.confirm) {
    e.preventDefault();
    confirmCurrent();
  } else if (code === k.loopMode) {
    e.preventDefault();
    cycleLoopMode();
  } else if (["1", "2", "3", "4", "5"].includes(e.key)) {
    const dockIdx = parseInt(e.key, 10) - 1;
    const btn = document.querySelector(`.dock-btn[data-dock="${dockIdx}"]`);
    if (btn) btn.click();
  }
}, true);

window.addEventListener("keyup", (e) => {
  const playCode = keymapSettings.keys.playPause;
  if ((e.key === " " || e.code === "Space" || e.code === playCode) && !isTextEditingTarget(e.target)) {
    e.preventDefault();
    e.stopPropagation();
  }
}, true);

// Initial load & poll
if (document.getElementById("currentSlotWorkNoteBanner")) {
  document.getElementById("currentSlotWorkNoteBanner").addEventListener("click", () => {
    if (currentSlot) openSlotMemoModal(currentSlot.id);
  });
}
if (document.getElementById("btnCloseSlotMemo")) {
  document.getElementById("btnCloseSlotMemo").addEventListener("click", closeSlotMemoModal);
}
if (document.getElementById("btnCancelSlotMemo")) {
  document.getElementById("btnCancelSlotMemo").addEventListener("click", closeSlotMemoModal);
}
if (document.getElementById("btnSaveSlotMemo")) {
  document.getElementById("btnSaveSlotMemo").addEventListener("click", () => saveSlotMemoFromModal(false));
}
if (document.getElementById("btnClearSlotMemo")) {
  document.getElementById("btnClearSlotMemo").addEventListener("click", () => saveSlotMemoFromModal(true));
}
if (document.getElementById("btnDeleteSlotFromModal")) {
  document.getElementById("btnDeleteSlotFromModal").addEventListener("click", deleteEditingSlotFromModal);
}
if (document.getElementById("slotMemoModal")) {
  document.getElementById("slotMemoModal").addEventListener("click", (e) => {
    if (e.target.id === "slotMemoModal") closeSlotMemoModal();
  });
}
if (document.getElementById("btnOpenAddSlot")) {
  document.getElementById("btnOpenAddSlot").addEventListener("click", openAddSlotModal);
}
if (document.getElementById("btnCloseAddSlot")) {
  document.getElementById("btnCloseAddSlot").addEventListener("click", closeAddSlotModal);
}
if (document.getElementById("btnCancelAddSlot")) {
  document.getElementById("btnCancelAddSlot").addEventListener("click", closeAddSlotModal);
}
if (document.getElementById("btnSubmitAddSlot")) {
  document.getElementById("btnSubmitAddSlot").addEventListener("click", submitAddSlotFromModal);
}
if (document.getElementById("addSlotModal")) {
  document.getElementById("addSlotModal").addEventListener("click", (e) => {
    if (e.target.id === "addSlotModal") closeAddSlotModal();
  });
}

updateSlotFilterButtonsUI();
loadSettingsFromServer().then(() => loadSlots());
setInterval(loadSlots, 10000);
setTimeout(() => checkForUpdate(true), 2000);
