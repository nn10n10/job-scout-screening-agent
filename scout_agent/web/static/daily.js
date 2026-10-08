(() => {
  const button = document.getElementById('daily-run');
  const status = document.getElementById('daily-status');
  const checks = [...document.querySelectorAll('#daily-platforms input[type="checkbox"]')];
  const preset = document.getElementById('daily-all');
  const progress = document.getElementById('daily-platform-status');
  let latest = {state: 'idle'};
  const selected = () => checks.filter(input => input.checked).map(input => input.value);
  function lock(running) {
    checks.forEach(input => { input.disabled = running || posting; });
    preset.disabled = running || posting;
    button.disabled = running || posting || selected().length === 0;
  }
  checks.forEach(input => input.addEventListener('change', () => lock(latest.state === 'running')));
  preset.addEventListener('click', () => {
    checks.forEach(input => { input.checked = true; });
    lock(latest.state === 'running');
  });
  let wasRunning = false;
  let posting = false;
  function render(data) {
    const running = data.state === 'running';
    latest = data;
    lock(running);
    button.textContent = running ? '筛选中…' : '▶ 运行所选平台';
    const labels = {idle: '待机', running: '筛选中…', completed: 'completed · 筛选完成', failed: 'failed · 筛选失败'};
    status.textContent = [labels[data.state], data.started_at && `开始：${data.started_at}`,
      data.finished_at && `结束：${data.finished_at}`, data.safe_error,
      data.latest_summary && `KEEP ${data.latest_summary.KEEP} · MAYBE ${data.latest_summary.MAYBE} · SKIP ${data.latest_summary.SKIP}`].filter(Boolean).join(' · ');
    const names = {green: 'Green', type: 'type', doda: 'doda', mynavi: 'マイナビ転職'};
    const states = {idle: '待机', running: '运行中…', completed: '完成', failed: '失败', skipped: '未选择'};
    progress.replaceChildren(...checks.map(input => {
      const row = document.createElement('div');
      row.textContent = names[input.value] + ' · ' + (states[data.platform_status?.[input.value]] || '待机');
      return row;
    }));
    if (wasRunning && !running) window.location.reload();
    wasRunning = running;
  }
  async function poll() {
    try {
      const response = await fetch('/api/daily/status');
      if (!response.ok) throw new Error();
      render(await response.json());
    } catch (_) { status.textContent = '无法获取运行状态，请稍后重试。'; }
    setTimeout(poll, 2000);
  }
  button.addEventListener('click', async () => {
    if (!selected().length || posting || latest.state === "running") return;
    posting = true;
    lock(false);
    try {
      const response = await fetch('/api/daily/run', {method: 'POST', headers: {'x-csrf-token': button.dataset.csrf, 'Content-Type': 'application/json'}, body: JSON.stringify({platforms: selected()})});
      if (response.status === 409) { wasRunning = true; return; }
      if (response.status !== 202) throw new Error();
      wasRunning = true;
      posting = false;
      render(await response.json());
    } catch (_) { status.textContent = '启动失败，请稍后重试。'; }
    finally { posting = false; lock(wasRunning || latest.state === 'running'); }
  });
  poll();
})();
