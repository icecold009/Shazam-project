const fileInput = document.getElementById('fileInput');
const uploadBtn = document.getElementById('uploadBtn');
const recBtn = document.getElementById('recBtn');
const stopBtn = document.getElementById('stopBtn');
const resultDiv = document.getElementById('result');
const statusDiv = document.getElementById('status');

let mediaRecorder;
let chunks = [];
let stopTimer = null;
let mediaStream = null;
let audioContext = null;
let analyser = null;
let sourceNode = null;
let animationFrameId = null;
let lastFile = null;
let isSubmitting = false;
let isListening = false;
let isProcessing = false;

const HISTORY_KEY = 'shazam_guest_history';
const SAVE_HISTORY_KEY = 'shazam_save_history_enabled';

function getStorageArea(name) {
    try {
        return window[name];
    } catch {
        return null;
    }
}

function readStorage(name, key) {
    const storage = getStorageArea(name);
    if (!storage) return null;
    try {
        return storage.getItem(key);
    } catch {
        return null;
    }
}

function writeStorage(name, key, value) {
    const storage = getStorageArea(name);
    if (!storage) return;
    try {
        storage.setItem(key, value);
    } catch {
        // Browser storage is optional; recognition must still work without it.
    }
}

function removeStorage(name, key) {
    const storage = getStorageArea(name);
    if (!storage) return;
    try {
        storage.removeItem(key);
    } catch {
        // Browser storage is optional; recognition must still work without it.
    }
}

function setThemeFromStorage() {
    const root = document.documentElement;
    const saved = readStorage('localStorage', 'shazam_theme');
    if (saved === 'light' || saved === 'dark') {
        root.setAttribute('data-theme', saved);
    }
}

function isHistoryEnabled() {
    const raw = readStorage('sessionStorage', SAVE_HISTORY_KEY);
    return raw === null ? true : raw === 'true';
}

function setHistoryEnabled(value) {
    writeStorage('sessionStorage', SAVE_HISTORY_KEY, String(value));
}

function loadHistory() {
    try {
        return JSON.parse(sessionStorage.getItem(HISTORY_KEY) || '[]');
    } catch {
        return [];
    }
}

function saveHistory(history) {
    writeStorage('sessionStorage', HISTORY_KEY, JSON.stringify(history));
}

function clearHistory() {
    removeStorage('sessionStorage', HISTORY_KEY);
}

function addToHistory(item) {
    if (!isHistoryEnabled()) return;
    const history = loadHistory();
    history.unshift(item);
    saveHistory(history.slice(0, 50));
}

function buildStreamingLink(title, artist) {
    const q = encodeURIComponent(`${title || ''} ${artist || ''}`.trim());
    return `https://open.spotify.com/search/${q}`;
}

function setMicLabel() {
    if (recBtn) {
        recBtn.textContent = isProcessing ? 'Identifying…' : (isListening ? 'Listening…' : 'Start Recording');
        recBtn.setAttribute(
            'aria-label',
            isProcessing ? 'Identifying song' : (isListening ? 'Stop listening' : 'Identify song')
        );
    }
    if (stopBtn) {
        stopBtn.setAttribute('aria-label', 'Stop recording');
    }
}

function setSubmittingState(loading) {
    isSubmitting = loading;
    isProcessing = loading;
    uploadBtn.disabled = loading || isListening;
    recBtn.disabled = loading || (mediaRecorder && mediaRecorder.state !== 'inactive');
    stopBtn.disabled = !loading && (!mediaRecorder || mediaRecorder.state === 'inactive');
    setMicLabel();
}

function setListeningState(active) {
    isListening = active;
    if (active) {
        recBtn.disabled = true;
        stopBtn.disabled = false;
    } else {
        recBtn.disabled = false;
        stopBtn.disabled = true;
    }
    setMicLabel();
}

function ensureVisualizerNode(stream) {
    try {
        audioContext = new (window.AudioContext || window.webkitAudioContext)();
        analyser = audioContext.createAnalyser();
        analyser.fftSize = 256;
        sourceNode = audioContext.createMediaStreamSource(stream);
        sourceNode.connect(analyser);
    } catch {
        audioContext = null;
        analyser = null;
        sourceNode = null;
    }
}

function stopVisualizer() {
    if (animationFrameId) cancelAnimationFrame(animationFrameId);
    animationFrameId = null;
    if (sourceNode) {
        try { sourceNode.disconnect(); } catch { }
    }
    if (analyser) {
        try { analyser.disconnect(); } catch { }
    }
    if (audioContext && audioContext.state !== 'closed') {
        audioContext.close().catch(() => { });
    }
    audioContext = null;
    analyser = null;
    sourceNode = null;
}

function stopMediaStream() {
    const stream = mediaStream;
    mediaStream = null;
    if (!stream) return;
    try {
        stream.getTracks().forEach(track => track.stop());
    } catch {
        // Cleanup is best effort when a browser stream is already unavailable.
    }
}

function startVisualizerLoop() {
    const bars = Array.from(document.querySelectorAll('[data-wave-bar]'));
    if (!bars.length || !analyser) return;

    const bufferLength = analyser.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);

    const draw = () => {
        animationFrameId = requestAnimationFrame(draw);
        analyser.getByteFrequencyData(dataArray);

        for (let i = 0; i < bars.length; i++) {
            const value = dataArray[Math.floor((i / bars.length) * bufferLength)] || 0;
            const height = Math.max(6, Math.round((value / 255) * 64));
            bars[i].style.height = `${height}px`;
            bars[i].style.opacity = String(0.35 + (value / 255) * 0.65);
        }
    };

    draw();
}

function showDetailModal(item) {
    const existing = document.getElementById('detail-modal');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'detail-modal';
    overlay.style.position = 'fixed';
    overlay.style.inset = '0';
    overlay.style.background = 'rgba(0,0,0,0.55)';
    overlay.style.display = 'flex';
    overlay.style.alignItems = 'center';
    overlay.style.justifyContent = 'center';
    overlay.style.padding = '16px';
    overlay.style.zIndex = '9999';

    const modal = document.createElement('div');
    modal.className = 'panel';
    modal.style.width = 'min(100%, 520px)';
    modal.style.maxHeight = '90vh';
    modal.style.overflowY = 'auto';
    modal.style.display = 'grid';
    modal.style.gap = '12px';

    const closeBtn = document.createElement('button');
    closeBtn.textContent = 'Close';
    closeBtn.style.width = 'fit-content';
    closeBtn.addEventListener('click', () => overlay.remove());

    const heading = document.createElement('h2');
    heading.textContent = 'Song Details';

    const title = document.createElement('div');
    title.textContent = `Title: ${item.title || '(unknown)'}`;

    const artist = document.createElement('div');
    artist.textContent = `Artist: ${item.artist || '(unknown)'}`;

    const album = document.createElement('div');
    album.textContent = `Album: ${item.album || '(unknown)'}`;

    const genre = document.createElement('div');
    genre.textContent = `Genre: ${item.genre || '(not available)'}`;

    const releaseDate = document.createElement('div');
    releaseDate.textContent = `Release Date: ${item.release_date || '(not available)'}`;

    const link = document.createElement('a');
    link.href = item.streaming_url || buildStreamingLink(item.title, item.artist);
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = 'Open in Spotify';

    modal.appendChild(closeBtn);
    modal.appendChild(heading);

    if (item.image) {
        const img = document.createElement('img');
        img.src = item.image;
        img.alt = item.title || 'Album art';
        img.style.maxWidth = '220px';
        img.style.borderRadius = '12px';
        modal.appendChild(img);
    }

    modal.appendChild(title);
    modal.appendChild(artist);
    modal.appendChild(album);
    modal.appendChild(genre);
    modal.appendChild(releaseDate);
    modal.appendChild(link);

    overlay.appendChild(modal);
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) overlay.remove();
    });

    document.body.appendChild(overlay);
}

function renderSettings() {
    const existing = document.getElementById('settings-section');
    if (existing) existing.remove();

    const section = document.createElement('section');
    section.id = 'settings-section';
    section.className = 'panel stack';
    section.style.marginTop = '20px';

    const heading = document.createElement('h2');
    heading.textContent = 'Privacy';

    const label = document.createElement('label');
    label.style.display = 'flex';
    label.style.alignItems = 'center';
    label.style.gap = '10px';
    label.style.fontWeight = '600';

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = isHistoryEnabled();

    const text = document.createElement('span');
    text.textContent = 'Save history in this session';

    checkbox.addEventListener('change', () => {
        setHistoryEnabled(checkbox.checked);
        if (!checkbox.checked) {
            clearHistory();
        }
        renderHistory();
    });

    label.appendChild(checkbox);
    label.appendChild(text);

    const note = document.createElement('div');
    note.textContent = 'When off, recognized songs will not be stored in session history.';

    section.appendChild(heading);
    section.appendChild(label);
    section.appendChild(note);

    document.querySelector('.app-wrap').appendChild(section);
}

function renderHistory() {
    const history = loadHistory();
    const existing = document.getElementById('history-section');
    if (existing) existing.remove();

    const section = document.createElement('section');
    section.id = 'history-section';
    section.className = 'stack';
    section.style.marginTop = '20px';

    const h2 = document.createElement('h2');
    h2.className = 'section-heading';
    h2.textContent = 'History';
    section.appendChild(h2);

    if (!isHistoryEnabled()) {
        const disabled = document.createElement('div');
        disabled.textContent = 'History is turned off for this session.';
        section.appendChild(disabled);
    } else if (!history.length) {
        const empty = document.createElement('div');
        empty.textContent = 'No history yet.';
        section.appendChild(empty);
    } else {
        history.forEach((item, index) => {
            const card = document.createElement('div');
            card.className = 'panel';
            card.style.display = 'grid';
            card.style.gap = '6px';
            card.style.marginBottom = '12px';

            const title = document.createElement('div');
            title.textContent = `Song: ${item.title || '(unknown)'}`;

            const artist = document.createElement('div');
            artist.textContent = `Artist: ${item.artist || '(unknown)'}`;

            const album = document.createElement('div');
            album.textContent = `Album: ${item.album || '(unknown)'}`;

            const time = document.createElement('div');
            time.textContent = `Recognized: ${new Date(item.timestamp).toLocaleString()}`;

            const actions = document.createElement('div');
            actions.style.display = 'flex';
            actions.style.gap = '10px';
            actions.style.flexWrap = 'wrap';

            const viewBtn = document.createElement('button');
            viewBtn.textContent = 'View Details';
            viewBtn.addEventListener('click', () => showDetailModal(item));

            const delBtn = document.createElement('button');
            delBtn.textContent = 'Remove';
            delBtn.addEventListener('click', () => {
                const updated = loadHistory();
                updated.splice(index, 1);
                saveHistory(updated);
                renderHistory();
            });

            actions.appendChild(viewBtn);
            actions.appendChild(delBtn);

            card.appendChild(title);
            card.appendChild(artist);
            card.appendChild(album);
            card.appendChild(time);

            if (item.image) {
                const img = document.createElement('img');
                img.src = item.image;
                img.alt = item.title || 'Album art';
                img.style.maxWidth = '120px';
                img.style.borderRadius = '12px';
                card.appendChild(img);
            }

            card.appendChild(actions);
            section.appendChild(card);
        });
    }

    document.querySelector('.app-wrap').appendChild(section);
}

async function refreshStatus() {
    try {
        const res = await fetch('/api/status');
        const s = await res.json();
        statusDiv.textContent =
            `RapidAPI: ${s.rapidapi_configured ? 'configured' : 'not configured'}; ` +
            `AcoustID: ${s.acoustid_configured ? 'configured' : 'not configured'}; ` +
            `fpcalc on PATH: ${s.fpcalc_on_path ? 'yes' : 'no'}; ` +
            `FP_CALC_PATH exists: ${s.fpcalc_path_exists ? 'yes' : 'no'}; ` +
            `ffmpeg on PATH: ${s.ffmpeg_on_path ? 'yes' : 'no'}; ` +
            `AudD token: ${s.audd_configured ? 'configured' : 'not configured'}; ` +
            `Supabase: ${s.supabase_configured ? 'configured' : 'not configured'}; ` +
            `Quota: ${s.quota_mode || 'unknown'}; ` +
            `Production-grade: ${s.production_grade_quotas_enabled ? 'enabled' : 'disabled'}; ` +
            `Limits: ${s.daily_limit}/day, ${s.monthly_limit}/month; ` +
            `Cooldown: ${s.cooldown_seconds}s`;
    } catch (err) {
        statusDiv.innerText = 'Failed to fetch status: ' + err;
    }
}

function renderRetryButton() {
    const retryBtn = document.createElement('button');
    retryBtn.textContent = 'Retry';
    retryBtn.style.marginTop = '10px';
    retryBtn.disabled = isSubmitting;
    retryBtn.addEventListener('click', () => {
        if (lastFile) postFile(lastFile);
        else resultDiv.innerText = 'Nothing to retry yet.';
    });
    resultDiv.appendChild(retryBtn);
}

async function postFile(file) {
    if (isSubmitting) return;

    lastFile = file;
    setSubmittingState(true);

    const fd = new FormData();
    fd.append('file', file, file.name || 'upload.audio');
    resultDiv.innerText = 'Uploading...';

    try {
        const res = await fetch('/api/match', { method: 'POST', body: fd });
        const data = await res.json();
        renderResult(data);
        await refreshStatus();
    } catch (err) {
        resultDiv.innerText = 'Upload error: ' + err;
        renderRetryButton();
    } finally {
        setSubmittingState(false);
    }
}

function renderResult(data) {
    if (!data) {
        resultDiv.innerText = 'No response';
        renderRetryButton();
        return;
    }
    if (data.status === 'rate_limited') {
        resultDiv.innerText = data.error || 'Too many requests. Please wait and try again.';
        return;
    }
    if (data.status === 'not_configured') {
        resultDiv.innerText = data.error || 'No recognition backend is configured.';
        renderRetryButton();
        return;
    }
    if (data.status === 'invalid_audio') {
        resultDiv.innerText = `${data.error_code || 'invalid_audio'}: ${data.error || 'Audio was rejected.'}`;
        renderRetryButton();
        return;
    }
    if (data.status === 'error') {
        resultDiv.innerText = 'Server error: ' + (data.error || 'unknown');
        renderRetryButton();
        return;
    }
    if (data.status === 'no_match') {
        resultDiv.innerText = 'No match found.';
        renderRetryButton();
        return;
    }

    resultDiv.innerHTML = '';

    const title = document.createElement('div');
    title.textContent = `Song: ${data.title || '(unknown)'}`;

    const artist = document.createElement('div');
    artist.textContent = `Artist: ${data.artist || '(unknown)'}`;

    const album = document.createElement('div');
    album.textContent = `Album: ${data.album || '(unknown)'}`;

    const detailBtn = document.createElement('button');
    detailBtn.textContent = 'View Details';
    detailBtn.style.marginTop = '10px';

    const item = {
        title: data.title || '',
        artist: data.artist || '',
        album: data.album || '',
        genre: data.genre || '',
        release_date: data.release_date || '',
        image: data.image || '',
        streaming_url: data.streaming_url || buildStreamingLink(data.title, data.artist),
        timestamp: Date.now()
    };

    detailBtn.addEventListener('click', () => showDetailModal(item));

    resultDiv.appendChild(title);
    resultDiv.appendChild(artist);
    resultDiv.appendChild(album);

    if (data.image) {
        const img = document.createElement('img');
        img.src = data.image;
        img.alt = data.title || 'Album art';
        resultDiv.appendChild(img);
    }

    resultDiv.appendChild(detailBtn);

    addToHistory(item);
    renderHistory();
}

refreshStatus();
setThemeFromStorage();
renderSettings();
renderHistory();
setMicLabel();

uploadBtn.addEventListener('click', () => {
    const f = fileInput.files[0];
    if (!f) {
        resultDiv.innerText = 'Select a file first.';
        return;
    }
    postFile(f);
});

recBtn.addEventListener('click', async () => {
    if (isSubmitting || isListening) return;

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        resultDiv.innerText = 'Microphone not supported in this browser.';
        renderRetryButton();
        return;
    }

    try {
        mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        chunks = [];
        mediaRecorder = new MediaRecorder(mediaStream);

        ensureVisualizerNode(mediaStream);
        startVisualizerLoop();

        mediaRecorder.ondataavailable = (e) => {
            if (e.data && e.data.size > 0) chunks.push(e.data);
        };

        mediaRecorder.onstop = async () => {
            if (stopTimer) {
                clearTimeout(stopTimer);
                stopTimer = null;
            }

            stopMediaStream();

            stopVisualizer();
            setListeningState(false);
            isProcessing = true;
            setMicLabel();

            const blob = new Blob(chunks, { type: chunks[0]?.type || 'audio/webm' });
            chunks = [];

            if (blob.size === 0) {
                resultDiv.innerText = 'No audio recorded.';
                renderRetryButton();
                recBtn.disabled = false;
                stopBtn.disabled = true;
                isProcessing = false;
                setMicLabel();
                return;
            }

            const file = new File([blob], 'recording.webm', { type: blob.type });
            recBtn.disabled = true;
            stopBtn.disabled = true;
            await postFile(file);
            isProcessing = false;
            setMicLabel();
        };

        mediaRecorder.start(1000);
        setListeningState(true);
        resultDiv.innerText = 'Recording... (auto-stops after 10 seconds)';

        stopTimer = setTimeout(() => {
            if (mediaRecorder && mediaRecorder.state !== 'inactive') {
                mediaRecorder.stop();
            }
            recBtn.disabled = false;
            stopBtn.disabled = true;
            resultDiv.innerText = 'Recording stopped automatically after 10 seconds.';
        }, 10000);
    } catch (err) {
        if (mediaRecorder && mediaRecorder.state !== 'inactive') {
            try { mediaRecorder.stop(); } catch { }
        }
        mediaRecorder = null;
        stopMediaStream();
        stopVisualizer();
        setListeningState(false);
        resultDiv.innerText = 'Microphone error: ' + err;
        renderRetryButton();
        recBtn.disabled = false;
        stopBtn.disabled = true;
    }
});

stopBtn.addEventListener('click', () => {
    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
        mediaRecorder.stop();
        recBtn.disabled = true;
        stopBtn.disabled = true;
    }
});
