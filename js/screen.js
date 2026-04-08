const STATE_KEY = 'robocon_timer_state';

let serverIsActive = false;
let isFetching = false;

async function fetchStateFromServer() {
    if (isFetching) return;
    isFetching = true;
    try {
        const res = await fetch('/state');
        if (res.ok) {
            serverIsActive = true;
            const data = await res.json();
            if (data && data.timerType) {
                updateUI(data);
            }
        } else {
            serverIsActive = false;
        }
    } catch(e) {
        serverIsActive = false;
    } finally {
        isFetching = false;
    }
}

function initScreen() {
    // サーバーからのポーリング同期 (約60fps・通信終了後に次を予約することで100%オーバーラップ防止)
    async function pollLoop() {
        if (!isFetching) await fetchStateFromServer();
        setTimeout(pollLoop, 15);
    }
    pollLoop();

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
    document.getElementById('score-blue').innerText = state.blue.score;

    // チーム名の更新
    document.getElementById('name-red').innerText = state.red.name;
    document.getElementById('name-blue').innerText = state.blue.name;

    // 3. Vゴールエフェクト (scores.jsonのIDが'vgoal'のトグル状態をチェック)
    const bgRed = document.getElementById('bg-red');
    const bgBlue = document.getElementById('bg-blue');

    if (state.red.vgoal) {
        bgRed.classList.add('is-vgoal');
    } else {
        bgRed.classList.remove('is-vgoal');
    }

    if (state.blue.vgoal) {
        bgBlue.classList.add('is-vgoal');
    } else {
        bgBlue.classList.remove('is-vgoal');
    }
}

// 初期化実行
initScreen();
