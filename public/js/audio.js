let audioCtx = null;

function initAudio() {
    if (!audioCtx) {
        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (audioCtx.state === 'suspended') {
        audioCtx.resume();
    }
}

function playBeep(freq = 660, duration = 0.15, vol = 0.8, type = 'square', isBuzzer = true) {
    try {
        initAudio();
        const gainNode = audioCtx.createGain();

        // 最初から最後まで一定の音量で鳴らす
        gainNode.gain.setValueAtTime(vol, audioCtx.currentTime);
        // 終了時にブツッというノイズが入るのを防ぐため、最後の0.01秒だけ瞬時に音を落とす
        gainNode.gain.setValueAtTime(vol, audioCtx.currentTime + duration - 0.01);
        gainNode.gain.linearRampToValueAtTime(0.001, audioCtx.currentTime + duration);

        gainNode.connect(audioCtx.destination);

        if (isBuzzer) {
            // スポーツタイマー風の重低音ブザー（周波数をわずかにずらして濁らせる）
            const osc1 = audioCtx.createOscillator();
            const osc2 = audioCtx.createOscillator();
            const osc3 = audioCtx.createOscillator();

            osc1.type = 'sawtooth';
            osc2.type = 'square';
            osc3.type = 'sawtooth';

            osc1.frequency.setValueAtTime(freq, audioCtx.currentTime);
            osc2.frequency.setValueAtTime(freq - 5, audioCtx.currentTime);
            osc3.frequency.setValueAtTime(freq + 7, audioCtx.currentTime);

            osc1.connect(gainNode);
            osc2.connect(gainNode);
            osc3.connect(gainNode);

            osc1.start(); osc2.start(); osc3.start();
            osc1.stop(audioCtx.currentTime + duration);
            osc2.stop(audioCtx.currentTime + duration);
            osc3.stop(audioCtx.currentTime + duration);
        } else {
            const osc = audioCtx.createOscillator();
            osc.type = type;
            osc.frequency.setValueAtTime(freq, audioCtx.currentTime);
            osc.connect(gainNode);
            osc.start();
            osc.stop(audioCtx.currentTime + duration);
        }
    } catch(e) {
        console.warn("Audio playback failed", e);
    }
}
