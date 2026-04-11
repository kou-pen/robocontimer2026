let state = {
    timerType: 'SETTING',
    phase: 'IDLE',
    timeRemaining: 60,
    preStartText: '',
    red: { score: 0, name: '' },
    blue: { score: 0, name: '' },
    settings: { setting: 60, match: 180 },
    isWarning: false
};

let scoreConfig = [];
let teamsList = [];

// APIコマンド送信ユーティリティ
function sendCommand(cmdData) {
    fetch('/api/command', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(cmdData)
    }).catch(e => console.error("Command error:", e));
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

    buildUI();
    setupEventListeners();
    connectSSE();
}

function connectSSE() {
    const evtSource = new EventSource('/events');
    const connLabel = document.getElementById('obs-status'); // control.htmlに存在しないかもしれないが念のため
    
    evtSource.onmessage = (e) => {
        if (!e.data.trim()) return;
        try {
            state = JSON.parse(e.data);
            if (state.timerType) renderControlUI();
        } catch (err) {
            console.error("SSE parse error", err);
        }
    };
    evtSource.onerror = () => {
        if (connLabel) connLabel.innerText = "⚠ 切断中...";
    };
    evtSource.onopen = () => {
        if (connLabel) connLabel.innerText = "● OBS同期中";
    };
}

function buildUI() {
    // チーム選択ドロップダウン構築
    const redSelect = document.getElementById('team-red-select');
    const blueSelect = document.getElementById('team-blue-select');

    teamsList.forEach(team => {
        redSelect.add(new Option(team, team));
        blueSelect.add(new Option(team, team));
    });

    redSelect.addEventListener('change', (e) => { 
        sendCommand({cmd: 'set_name', team: 'red', name: e.target.value});
    });
    blueSelect.addEventListener('change', (e) => { 
        sendCommand({cmd: 'set_name', team: 'blue', name: e.target.value});
    });

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

    document.getElementById('red-hints').innerHTML = `<small>💡 キーボードの <b>Shift + 対象キー</b> を押すと点数を減らすことができます。</small>`;
    document.getElementById('blue-hints').innerHTML = `<small>💡 キーボードの <b>Shift + 対象キー</b> を押すと点数を減らすことができます。</small>`;

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

function renderControlUI() {
    // チーム名選択の同期
    const redSelect = document.getElementById('team-red-select');
    const blueSelect = document.getElementById('team-blue-select');
    if (state.red.name && redSelect.value !== state.red.name) redSelect.value = state.red.name;
    if (state.blue.name && blueSelect.value !== state.blue.name) blueSelect.value = state.blue.name;

    // 時間表示
    const min = Math.floor(state.timeRemaining / 60).toString().padStart(2, '0');
    const sec = (state.timeRemaining % 60).toString().padStart(2, '0');
    
    if (state.phase === 'PRE_START') {
        const text = state.preStartText || "READY";
        document.getElementById('ctrl-time').innerHTML = `<span style="display:inline-block; min-width: 4.8ch; text-align: center;">${text}</span>`;
    } else {
        const timeStr = `${min}:${sec}`;
        document.getElementById('ctrl-time').innerHTML = timeStr.split('').map(c => `<span style="display:inline-block; width: ${c === ':' ? '0.5ch' : '1.2ch'}; text-align: center;">${c}</span>`).join('');
    }

    const ctrlTime = document.getElementById('ctrl-time');
    if (state.phase === 'END' || state.isWarning) {
        ctrlTime.style.color = '#fdd835';
    } else {
        ctrlTime.style.color = '';
    }

    // フェーズ表示
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
                if (el) el.innerText = state[team][cfg.id] || 0;
                if (elMan) elMan.innerText = state[team][cfg.id + '_manual'] || 0;
                if (elAuto) elAuto.innerText = state[team][cfg.id + '_auto'] || 0;
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
    
    // 設定時間の表示を同期（UIが上書きされないように、フォーカスが外れている時だけ同期するなどの工夫もあるが、ここではシンプルに）
    const sMinEl = document.getElementById('setting-min');
    if (document.activeElement !== sMinEl && state.settings) {
        document.getElementById('setting-min').value = Math.floor(state.settings.setting / 60);
        document.getElementById('setting-sec').value = state.settings.setting % 60;
        document.getElementById('match-min').value = Math.floor(state.settings.match / 60);
        document.getElementById('match-sec').value = state.settings.match % 60;
    }
    
    // ボタンのラベル更新
    const switchBtn = document.getElementById('btn-switch-match');
    if (switchBtn) {
        if (state.timerType === 'SETTING') switchBtn.innerText = "MATCHへ切替";
        else switchBtn.innerText = "SETTINGへ切替";
    }
}

function updateSettings() {
    const sMin = parseInt(document.getElementById('setting-min').value) || 0;
    const sSec = parseInt(document.getElementById('setting-sec').value) || 0;
    const mMin = parseInt(document.getElementById('match-min').value) || 0;
    const mSec = parseInt(document.getElementById('match-sec').value) || 0;

    sendCommand({
        cmd: 'set_settings',
        setting_min: sMin, setting_sec: sSec,
        match_min: mMin, match_sec: mSec
    });
}

function setupEventListeners() {
    // 時間設定の変更
    ['setting-min', 'setting-sec', 'match-min', 'match-sec'].forEach(id => {
        document.getElementById(id).addEventListener('change', updateSettings);
    });

    document.getElementById('btn-start').addEventListener('click', () => {
        sendCommand({cmd: 'start'});
    });

    document.getElementById('btn-pause').addEventListener('click', () => {
        sendCommand({cmd: 'pause'});
    });

    document.getElementById('btn-reset').addEventListener('click', () => {
        sendCommand({cmd: 'reset'});
    });

    document.getElementById('btn-switch-match').addEventListener('click', () => {
        sendCommand({cmd: 'switch_match'});
    });

    // 動的なキーボードバインド
    document.addEventListener('keydown', (e) => {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
        const key = e.key.toLowerCase();
        const isMinus = e.shiftKey;
        const val = isMinus ? -1 : 1;
        scoreConfig.forEach(cfg => {
            if (key === cfg.keyRed.toLowerCase()) triggerScoreChange('red', cfg.id, cfg.type === 'toggle' ? 'toggle' : val);
            if (key === cfg.keyBlue.toLowerCase()) triggerScoreChange('blue', cfg.id, cfg.type === 'toggle' ? 'toggle' : val);
        });
    });
}

function triggerScoreChange(team, id, amt) {
    const isAuto = document.getElementById(`chk-${team}-auto`)?.checked || false;
    sendCommand({
        cmd: 'score',
        team: team,
        id: id,
        amt: amt,
        isAuto: isAuto
    });
}

init();
