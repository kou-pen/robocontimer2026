const STATE_KEY = 'robocon_timer_state';

let state = {
    timerType: 'SETTING', // 'SETTING' or 'MATCH'
    phase: 'IDLE', // 'IDLE', 'PRE_START', 'RUNNING', 'PAUSED', 'END'
    timeRemaining: 60,
    preStartText: '',
    red: { score: 0, name: '' },
    blue: { score: 0, name: '' }
};

let timerInterval = null;
let preStartIntervalId = null;
let preStartTimeoutId = null;
let currentSettings = { setting: 60, match: 180 };

let scoreConfig = [];
let teamsList = [];

// =======================
// Web Audio API によるBEEP音の生成
// =======================
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
}
// =======================

function clearAllTimers() {
    if (timerInterval) clearInterval(timerInterval);
    if (preStartIntervalId) clearInterval(preStartIntervalId);
    if (preStartTimeoutId) clearTimeout(preStartTimeoutId);
    timerInterval = null;
    preStartIntervalId = null;
    preStartTimeoutId = null;
}

async function init() {
    try {
        const teamRes = await fetch('data/teams.json');
        teamsList = await teamRes.json();

        const scoreRes = await fetch('data/scores.json');
        scoreConfig = await scoreRes.json();
    } catch (e) {
        console.error("Failed to load configs", e);
        return;
    }

    // 初期化 (JSON設定から動的にステートを生成)
    scoreConfig.forEach(cfg => {
        if (cfg.type === 'number') {
            state.red[cfg.id] = 0; // 総数
            state.blue[cfg.id] = 0;
            state.red[cfg.id + '_manual'] = 0; // 手動機による数
            state.blue[cfg.id + '_manual'] = 0;
            state.red[cfg.id + '_auto'] = 0; // 自動機による数
            state.blue[cfg.id + '_auto'] = 0;
        } else if (cfg.type === 'toggle') {
            state.red[cfg.id] = false;
            state.blue[cfg.id] = false;
        }
    });

    state.red.name = teamsList[0] || 'RED TEAM';
    state.blue.name = teamsList[1] || 'BLUE TEAM';

    buildUI();
    loadSettingsFromUI();
    state.timeRemaining = currentSettings.setting;
    updateState();
    setupEventListeners();
}

function buildUI() {
    // チーム選択ドロップダウン構築
    const redSelect = document.getElementById('team-red-select');
    const blueSelect = document.getElementById('team-blue-select');

    teamsList.forEach(team => {
        redSelect.add(new Option(team, team));
        blueSelect.add(new Option(team, team));
    });

    redSelect.value = state.red.name;
    blueSelect.value = state.blue.name;

    redSelect.addEventListener('change', (e) => { state.red.name = e.target.value; updateState(); });
    blueSelect.addEventListener('change', (e) => { state.blue.name = e.target.value; updateState(); });

    // スコアパネル自動生成
    const redBreakdown = document.getElementById('red-breakdown');
    const blueBreakdown = document.getElementById('blue-breakdown');

    scoreConfig.forEach(cfg => {
        ['red', 'blue'].forEach(team => {
            const container = team === 'red' ? redBreakdown : blueBreakdown;
            const keyBind = team === 'red' ? cfg.keyRed : cfg.keyBlue;

            const div = document.createElement('div');
            div.className = 'item';

            if (cfg.type === 'toggle') {
                const pt = typeof cfg.points === 'object' ? cfg.points.manual : cfg.points;
                div.innerHTML = `
                    <span>
                        ${cfg.label}${pt > 0 ? '(' + pt + ')' : ''}
                        <span class="key-badge">[${keyBind.toUpperCase()}]</span>
                    </span>
                    <button class="btn op-btn vgoal-btn" data-team="${team}" data-id="${cfg.id}" data-type="toggle" id="btn-${team}-${cfg.id}">${cfg.label} ON/OFF</button>
                `;
            } else {
                let ptsStr = '';
                if (typeof cfg.points === 'object') {
                    ptsStr = `(手動: ${cfg.points.manual} / 自動: ${cfg.points.auto})`;
                } else {
                    ptsStr = `(${cfg.points})`;
                }
                
                div.innerHTML = `
                    <div style="display:flex; flex-direction:column; line-height: 1.2;">
                        <span>
                            ${cfg.label}
                            <span class="key-badge">[${keyBind.toUpperCase()}]</span>
                            <small style="font-size:0.8rem; color:#ccc; margin-left:5px;">${ptsStr}</small>
                        </span>
                        <small style="color:#aaa; font-size:0.9rem; margin-top:2px;">
                            手動:<span id="c-${team}-${cfg.id}_manual">0</span> / 
                            自動:<span id="c-${team}-${cfg.id}_auto">0</span>
                        </small>
                    </div>
                    <div class="btn-group">
                        <button class="btn op-btn" data-team="${team}" data-id="${cfg.id}" data-type="number" data-val="-1">-</button>
                        <span class="count" id="c-${team}-${cfg.id}">0</span>
                        <button class="btn op-btn" data-team="${team}" data-id="${cfg.id}" data-type="number" data-val="1">+</button>
                    </div>
                `;
            }
            container.appendChild(div);
        });
    });

    document.getElementById('red-hints').innerHTML = `<small>💡 キーボードの <b>Ctrl + 対象キー</b> を押すと点数を減らすことができます。</small>`;
    document.getElementById('blue-hints').innerHTML = `<small>💡 キーボードの <b>Ctrl + 対象キー</b> を押すと点数を減らすことができます。</small>`;

    // 生成されたボタンへイベントリスナーを登録
    document.querySelectorAll('.op-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const team = e.target.getAttribute('data-team');
            const id = e.target.getAttribute('data-id');
            const type = e.target.getAttribute('data-type');
            const val = type === 'toggle' ? 'toggle' : parseInt(e.target.getAttribute('data-val'));
            triggerScoreChange(team, id, val);
        });
    });
}

function loadSettingsFromUI() {
    const sMin = parseInt(document.getElementById('setting-min').value) || 0;
    const sSec = parseInt(document.getElementById('setting-sec').value) || 0;
    const mMin = parseInt(document.getElementById('match-min').value) || 0;
    const mSec = parseInt(document.getElementById('match-sec').value) || 0;

    currentSettings.setting = sMin * 60 + sSec;
    currentSettings.match = mMin * 60 + mSec;
}

function updateState() {
    // トータルスコア計算 (設定から動的に)
    let redTotal = 0;
    let blueTotal = 0;

    const getPts = (points, mode) => typeof points === 'object' ? (points[mode] || 0) : points;

    scoreConfig.forEach(cfg => {
        if (cfg.type === 'number') {
            redTotal += state.red[cfg.id + '_manual'] * getPts(cfg.points, 'manual');
            redTotal += state.red[cfg.id + '_auto'] * getPts(cfg.points, 'auto');
            
            blueTotal += state.blue[cfg.id + '_manual'] * getPts(cfg.points, 'manual');
            blueTotal += state.blue[cfg.id + '_auto'] * getPts(cfg.points, 'auto');
        } else if (cfg.type === 'toggle') {
            const pt = getPts(cfg.points, 'manual'); // トグルはマニュアルポイントを一律利用
            if (pt > 0) {
                if (state.red[cfg.id]) redTotal += pt;
                if (state.blue[cfg.id]) blueTotal += pt;
            }
        }
    });

    state.red.score = redTotal;
    state.blue.score = blueTotal;

    // 残り時間警告判定 (3秒前〜終了時)
    state.isWarning = false;
    if (state.phase === 'RUNNING' || state.phase === 'END') {
        let timeLeft = state.timerType === 'SETTING' ? state.timeRemaining : currentSettings.match - state.timeRemaining;
        if (timeLeft <= 3) state.isWarning = true;
    } else if (state.phase === 'PRE_START') {
        // スタート前の 3, 2, 1 カウントダウン時も黄色にする
        if (state.preStartText === '3' || state.preStartText === '2' || state.preStartText === '1') {
            state.isWarning = true;
        }
    }

    // OBS用カスタムサーバーへの同期(POST)
    fetch('/state', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(state)
    }).catch(e => console.error("OBS Sync warning:", e));

    // 従来のローカルストレージ連携（バックアップ）
    localStorage.setItem(STATE_KEY, JSON.stringify(state));
    renderControlUI();
}

function renderControlUI() {
    // 時間表示 (横揺れ防止のためspanで強制固定幅)
    const min = Math.floor(state.timeRemaining / 60).toString().padStart(2, '0');
    const sec = (state.timeRemaining % 60).toString().padStart(2, '0');
    
    // PRE_START状態ではREADYや5, 4...のカウントダウン文字を表示
    if (state.phase === 'PRE_START') {
        const text = state.preStartText || "READY";
        // 1文字になっても横幅が0:00(約4.8ch)以下に潰れないよう保護
        document.getElementById('ctrl-time').innerHTML = `<span style="display:inline-block; min-width: 4.8ch; text-align: center;">${text}</span>`;
    } else {
        const timeStr = `${min}:${sec}`;
        document.getElementById('ctrl-time').innerHTML = timeStr.split('').map(c => `<span style="display:inline-block; width: ${c === ':' ? '0.5ch' : '1.2ch'}; text-align: center;">${c}</span>`).join('');
    }

    // コントロール画面でも文字の黄色化を反映させる
    const ctrlTime = document.getElementById('ctrl-time');
    if (state.phase === 'END' || state.isWarning) {
        ctrlTime.style.color = '#fdd835'; // var(--yellow-alert)
    } else {
        ctrlTime.style.color = '';
    }

    // フェーズと種類表示
    let phaseText = state.timerType === 'SETTING' ? 'SETTING TIME' : 'MATCH TIME';
    if (state.phase === 'PRE_START') phaseText += ' [COUNTDOWN]';
    if (state.phase === 'READY') phaseText += ' [READY]';
    if (state.phase === 'END') phaseText += ' [TIME UP]';
    if (state.phase === 'PAUSED') phaseText += ' [PAUSED]';
    document.getElementById('ctrl-phase').innerText = phaseText;

    // スコアと詳細の表示更新
    document.getElementById('ctrl-score-red').innerText = state.red.score;
    document.getElementById('ctrl-score-blue').innerText = state.blue.score;

    scoreConfig.forEach(cfg => {
        ['red', 'blue'].forEach(team => {
            if (cfg.type === 'number') {
                const el = document.getElementById(`c-${team}-${cfg.id}`);
                const elMan = document.getElementById(`c-${team}-${cfg.id}_manual`);
                const elAuto = document.getElementById(`c-${team}-${cfg.id}_auto`);
                if (el) el.innerText = state[team][cfg.id];
                if (elMan) elMan.innerText = state[team][cfg.id + '_manual'];
                if (elAuto) elAuto.innerText = state[team][cfg.id + '_auto'];
            } else if (cfg.type === 'toggle') {
                const btn = document.getElementById(`btn-${team}-${cfg.id}`);
                if (btn) {
                    if (state[team][cfg.id]) {
                        btn.classList.add('active');
                        btn.innerText = `${cfg.label} (ON)`;
                    } else {
                        btn.classList.remove('active');
                        btn.innerText = `${cfg.label} OFF`;
                    }
                }
            }
        });
    });
}

function calculateTime() {
    if (state.timerType === 'SETTING') {
        if (state.timeRemaining > 0) {
            state.timeRemaining--;
            
            // 終了直前の3, 2, 1
            if (state.timeRemaining === 3 || state.timeRemaining === 2 || state.timeRemaining === 1) {
                playBeep(500, 0.25, 1.0, 'triangle', false);
            }
            
            // 0に到達したピッタリの瞬間
            if (state.timeRemaining === 0) {
                clearAllTimers();
                state.phase = 'END';
                // スタート時と全く同じ音(0用)
                playBeep(1000, 0.8, 1.0, 'triangle', false);
            }
            updateState();
        }
    } else {
        // MATCH時のカウントアップ
        if (state.timeRemaining < currentSettings.match) {
            state.timeRemaining++;
            
            // 終了直前の3, 2, 1
            const timeLeft = currentSettings.match - state.timeRemaining;
            if (timeLeft === 3 || timeLeft === 2 || timeLeft === 1) {
                playBeep(500, 0.25, 1.0, 'triangle', false);
            }
            
            // 制限時間に到達したピッタリの瞬間
            if (timeLeft === 0) {
                clearAllTimers();
                state.phase = 'END';
                // スタート時と全く同じ音(0用)
                playBeep(1000, 0.8, 1.0, 'triangle', false);
            }
            updateState();
        }
    }
}

function setupEventListeners() {
    // 初回にAudioContextを初期化する
    document.body.addEventListener('click', initAudio, { once: true });

    document.getElementById('test-audio').addEventListener('click', () => {
        // 三角波のテスト (500Hz 3回, 1000Hz 1回)
        playBeep(500, 0.3, 1.0, 'triangle', false);
        setTimeout(() => playBeep(1000, 1.0, 1.0, 'triangle', false), 500);
    });

    document.getElementById('btn-start').addEventListener('click', () => {
        if (state.phase === 'RUNNING') return;

        // ポーズ中からの再開
        if (state.phase === 'PAUSED') {
            state.phase = 'RUNNING';
            updateState();
            timerInterval = setInterval(calculateTime, 1000);
            return;
        }

        loadSettingsFromUI();

        if (state.timerType === 'SETTING') {
            if (state.phase === 'IDLE' || state.phase === 'END') {
                state.timeRemaining = currentSettings.setting;
            }
            state.phase = 'RUNNING';
            updateState();
            if (timerInterval) clearInterval(timerInterval);
            timerInterval = setInterval(calculateTime, 1000);
            return;
        }

        // MATCH時のカウントダウン(PRE_START) シーケンス
        // 複数回クリックでの多重始動を防止
        if (preStartIntervalId || preStartTimeoutId) return;

        state.phase = 'PRE_START';
        state.preStartText = 'READY';
        state.timeRemaining = 0; // カウントアップ用に0リセット
        updateState();

        let preStartCount = 5;
        let expectedNextTick = Date.now() + 1000;

        const tickPreStart = () => {
            if (preStartCount <= 5 && preStartCount >= 1) {
                state.preStartText = preStartCount.toString();
                // 3秒前(3, 2, 1)から音を鳴らす
                if (preStartCount <= 3) {
                    playBeep(500, 0.25, 1.0, 'triangle', false);
                }
            } else if (preStartCount === 0) {
                state.preStartText = "START";
                playBeep(1000, 0.8, 1.0, 'triangle', false);
            }

            updateState();

            if (preStartCount === 0) {
                preStartIntervalId = null;

                // START表示と同時に「裏で」タイマー進行（カウントアップ）を開始する
                if (timerInterval) clearInterval(timerInterval);
                timerInterval = setInterval(calculateTime, 1000);

                // STARTの文字を2秒間表示したのち、画面上のタイマー（RUNNING）へ切り替え
                preStartTimeoutId = setTimeout(() => {
                    preStartTimeoutId = null;
                    state.phase = 'RUNNING';
                    updateState();
                }, 2000);
                
                return; // ここでカウントダウンのループは終了
            }

            preStartCount--;
            
            // ブラウザの実行遅延によるズレを防ぐための自己補正タイマー（正確な1000msを刻む）
            const now = Date.now();
            const delay = Math.max(0, expectedNextTick - now);
            expectedNextTick += 1000;
            preStartIntervalId = setTimeout(tickPreStart, delay);
        };

        // 即座に最初の5を鳴らして表示
        tickPreStart();
    });

    document.getElementById('btn-pause').addEventListener('click', () => {
        if (state.phase === 'RUNNING' || state.phase === 'PRE_START') {
            clearAllTimers();
            state.phase = 'PAUSED';
            state.preStartText = 'PAUSED';
            updateState();
        }
    });

    document.getElementById('btn-reset').addEventListener('click', () => {
        clearAllTimers();
        loadSettingsFromUI();
        
        if (state.timerType === 'SETTING') {
            state.phase = 'IDLE';
            state.timeRemaining = currentSettings.setting;
        } else {
            state.phase = 'PRE_START';
            state.preStartText = 'READY';
            state.timeRemaining = 0; // 最新ルール: MATCHリセット時は0:00(カウントアップ)で待機
        }

        // 得点リセット
        scoreConfig.forEach(cfg => {
            if (cfg.type === 'number') {
                state.red[cfg.id] = 0;
                state.blue[cfg.id] = 0;
                state.red[cfg.id + '_manual'] = 0;
                state.blue[cfg.id + '_manual'] = 0;
                state.red[cfg.id + '_auto'] = 0;
                state.blue[cfg.id + '_auto'] = 0;
            } else if (cfg.type === 'toggle') {
                state.red[cfg.id] = false;
                state.blue[cfg.id] = false;
            }
        });

        updateState();
    });

    document.getElementById('btn-switch-match').addEventListener('click', () => {
        clearAllTimers();
        loadSettingsFromUI();
        if (state.timerType === 'SETTING') {
            state.timerType = 'MATCH';
            state.timeRemaining = 0; // カウントアップ仕様のため0始まり
            document.getElementById('btn-switch-match').innerText = "SETTINGへ切替";
            state.phase = 'PRE_START';
            state.preStartText = 'READY';
        } else {
            state.timerType = 'SETTING';
            state.timeRemaining = currentSettings.setting;
            document.getElementById('btn-switch-match').innerText = "MATCHへ切替";
            state.phase = 'IDLE';
        }
        updateState();
    });

    // 動的なキーボードバインド
    document.addEventListener('keydown', (e) => {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;

        const key = e.key.toLowerCase();
        const isMinus = e.ctrlKey;
        const val = isMinus ? -1 : 1;

        scoreConfig.forEach(cfg => {
            if (key === cfg.keyRed) triggerScoreChange('red', cfg.id, cfg.type === 'toggle' ? 'toggle' : val);
            if (key === cfg.keyBlue) triggerScoreChange('blue', cfg.id, cfg.type === 'toggle' ? 'toggle' : val);
        });
    });
}

function triggerScoreChange(team, id, amt) {
    if (amt === 'toggle') {
        state[team][id] = !state[team][id];

        // V-GOALが有効になった場合、進行中のタイマーを自動的にストップ（PAUSE）する
        if (id === 'vgoal' && state[team][id] === true) {
            if (state.phase === 'RUNNING' || state.phase === 'PRE_START') {
                clearAllTimers();
                state.phase = 'PAUSED';
                state.preStartText = 'PAUSED';
            }
        }
    } else {
        const isAuto = document.getElementById(`chk-${team}-auto`)?.checked;
        const modeKey = isAuto ? id + '_auto' : id + '_manual';
        
        if (typeof state[team][modeKey] === 'number') {
            state[team][modeKey] += amt;
            if (state[team][modeKey] < 0) state[team][modeKey] = 0;
            
            // UI表示用の総計を同期
            state[team][id] = state[team][id + '_manual'] + state[team][id + '_auto'];
        }
    }
    updateState();
}

init();
