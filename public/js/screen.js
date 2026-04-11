const STATE_KEY = 'robocon_timer_state';

let serverIsActive = false;
let lastStateStr = '';
let lastState = null;

// =======================
// 音の2重再生防止: 複数ウィンドウが開いている場合、同じ音が2つの画面から二重に鳴るのを防ぐ
// localStorageをクロスウィンドウのロックとして使い、500ms以内に同じ音を鳴らしていたらスキップ
// =======================
const AUDIO_DEDUP_KEY = 'robocon_last_audio';
const AUDIO_DEDUP_WINDOW_MS = 500;

function playBeepOnce(eventKey, freq, duration, vol, type, isBuzzer) {
    if (typeof playBeep !== 'function') return;
    const now = Date.now();
    try {
        const last = JSON.parse(localStorage.getItem(AUDIO_DEDUP_KEY) || '{}');
        if (last.key === eventKey && (now - last.t) < AUDIO_DEDUP_WINDOW_MS) {
            return; // 他ウィンドウがすでに鳴らしたのでスキップ
        }
        localStorage.setItem(AUDIO_DEDUP_KEY, JSON.stringify({ key: eventKey, t: now }));
    } catch(e) {
        // localStorageが使えない場合は dedup なしで鳴らす
    }
    playBeep(freq, duration, vol, type, isBuzzer);
}

// =======================
// SSE（Server-Sent Events）による状態受信
// サーバーからのプッシュ通知で状態を受信する（ポーリングより低遅延）
// =======================
function connectSSE() {
    const evtSource = new EventSource('/events');

    evtSource.onopen = () => {
        serverIsActive = true;
    };

    evtSource.onmessage = (e) => {
        serverIsActive = true;
        try {
            const data = JSON.parse(e.data);
            if (data && data.timerType) {
                updateUI(data);
            }
        } catch (err) {
            // JSON parse失敗は無視（pingコメント等）
        }
    };

    evtSource.onerror = () => {
        serverIsActive = false;
        evtSource.close();
        // 接続が切れたら2秒後に再接続を試みる
        setTimeout(connectSSE, 2000);
    };
}

function initScreen() {
    // ユーザーが一度クリックしたら音声コンテキストを初期化（ブラウザのAutoPlay対策）
    document.body.addEventListener('click', () => {
        if (typeof initAudio === 'function') initAudio();
    }, { once: true });

    // 【最優先・遅延ゼロ】BroadcastChannel で同一ブラウザ内の他タブから即時受信
    // OBSブラウザソースは別プロセスなので届かない → SSEがフォールバック
    try {
        const bc = new BroadcastChannel('robocon_timer');
        bc.onmessage = (e) => {
            if (e.data && e.data.timerType) {
                serverIsActive = true; // SSEよりも優先してlocalStorageポーリングを無効化
                updateUI(e.data);
            }
        };
    } catch(e) {}

    // SSEで接続（状態の変化をプッシュ受信） ← OBSブラウザソース等の別プロセス向け
    connectSSE();

    // ローカルでの直接ファイル実行時(file://)などの後方互換用
    const data = localStorage.getItem(STATE_KEY);
    if(data) {
        updateUI(JSON.parse(data));
    }
    
    // 他のウィンドウ(コントロール画面)からの直接ブラウザ変更を受信
    window.addEventListener('storage', (e) => {
        // サーバー通信が生きている場合は競合(ちらつき)を防ぐため無視する
        if (serverIsActive) return;
        if (e.key === STATE_KEY) {
            updateUI(JSON.parse(e.newValue));
        }
    });

    // 万が一storageイベントが発火しない場合（同一タブ確認用等）のためのポーリングバックアップ
    setInterval(() => {
        if (serverIsActive) return;
        const raw = localStorage.getItem(STATE_KEY);
        if(raw) updateUI(JSON.parse(raw));
    }, 500);
}

function updateUI(state) {
    const currentStateStr = JSON.stringify(state);
    if (lastStateStr === currentStateStr) return; // 状態に変化がなければスキップ
    
    // 音の同期処理: 前回の状態と比較して特定のタイミングで音を鳴らす
    if (lastState) {
        // カウントダウン (PRE_START)
        if (state.phase === 'PRE_START' && state.preStartText !== lastState.preStartText) {
            const text = state.preStartText;
            if (text === '3' || text === '2' || text === '1') {
                playBeepOnce(`prestart_${text}`, 500, 0.25, 1.0, 'triangle', false);
            } else if (text === 'START') {
                playBeepOnce('prestart_START', 1000, 0.8, 1.0, 'triangle', false);
            }
        }
        
        // 通常のカウントダウン/カウントアップ中 (3秒前、2秒前、1秒前)
        if (state.phase === 'RUNNING' && state.timeRemaining !== lastState.timeRemaining) {
            if (state.timerType === 'SETTING') {
                if (state.timeRemaining === 3 || state.timeRemaining === 2 || state.timeRemaining === 1) {
                    playBeepOnce(`setting_${state.timeRemaining}`, 500, 0.25, 1.0, 'triangle', false);
                }
            } else if (state.timerType === 'MATCH' && state.settings) {
                const timeLeft = state.settings.match - state.timeRemaining;
                if (timeLeft === 3 || timeLeft === 2 || timeLeft === 1) {
                    playBeepOnce(`match_${timeLeft}`, 500, 0.25, 1.0, 'triangle', false);
                }
            }
        }
        
        // 終了時 (ENDになった瞬間)
        if (state.phase === 'END' && lastState.phase !== 'END') {
            playBeepOnce('end', 1000, 0.8, 1.0, 'triangle', false);
        }
    }
    
    // 状態を保存
    lastStateStr = currentStateStr;
    lastState = state;

    // 1. 時間表示の更新
    const timeDisplay = document.getElementById('time-display');
    const phaseLabel = document.getElementById('phase-label');
    const timerBg = document.getElementById('timer-bg');

    const min = Math.floor(state.timeRemaining / 60).toString().padStart(2, '0');
    const sec = (state.timeRemaining % 60).toString().padStart(2, '0');
    
    // PRE_START状態などの特別な表示
    if (state.phase === 'PRE_START') {
        timeDisplay.innerHTML = state.preStartText || "READY";
        timerBg.classList.remove('timeup');
    } else {
        const timeStr = `${min}:${sec}`;
        timeDisplay.innerHTML = timeStr.split('').map(c => `<span style="display:inline-block; width: ${c === ':' ? '0.5ch' : '1.3ch'}; text-align: center;">${c}</span>`).join('');
    }

    // フェーズラベル
    phaseLabel.innerText = state.timerType === 'SETTING' ? 'SETTING TIME' : 'MATCH TIME';

    // 警告・時間切れエフェクト (文字の黄色化)
    if (state.phase === 'END' || state.isWarning) {
        timerBg.classList.add('timeup');
    } else {
        timerBg.classList.remove('timeup');
    }

    // 2. スコア表示の更新
    document.getElementById('score-red').innerText = state.red.score;
    // チーム名の更新
    document.getElementById('name-red').innerText = state.red.name;
    document.getElementById('name-blue').innerText = state.blue.name;

    // 3. Vゴールエフェクト
    const bgRed = document.getElementById('bg-red');
    const bgBlue = document.getElementById('bg-blue');

    if (state.red.vgoal) {
        bgRed.classList.add('is-vgoal');
        const vt = state.red.vgoal_time;
        if (vt !== undefined && vt !== null) {
            const vMin = Math.floor(vt / 60).toString().padStart(2, '0');
            const vSec = (vt % 60).toString().padStart(2, '0');
            document.getElementById('vgoal-red').innerHTML = `V-GOAL!<br><span style="font-size:0.5em; display:block; margin-top:-10px; letter-spacing:0.05em;">TIME ${vMin}:${vSec}</span>`;
        } else {
            document.getElementById('vgoal-red').innerHTML = 'V-GOAL!';
        }
    } else {
        bgRed.classList.remove('is-vgoal');
        document.getElementById('vgoal-red').innerHTML = 'V-GOAL!';
    }

    if (state.blue.vgoal) {
        bgBlue.classList.add('is-vgoal');
        const vt = state.blue.vgoal_time;
        if (vt !== undefined && vt !== null) {
            const vMin = Math.floor(vt / 60).toString().padStart(2, '0');
            const vSec = (vt % 60).toString().padStart(2, '0');
            document.getElementById('vgoal-blue').innerHTML = `V-GOAL!<br><span style="font-size:0.5em; display:block; margin-top:-10px; letter-spacing:0.05em;">TIME ${vMin}:${vSec}</span>`;
        } else {
            document.getElementById('vgoal-blue').innerHTML = 'V-GOAL!';
        }
    } else {
        bgBlue.classList.remove('is-vgoal');
        document.getElementById('vgoal-blue').innerHTML = 'V-GOAL!';
    }
}

// 初期化実行
initScreen();
