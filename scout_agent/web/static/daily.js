(() => {
  const button = document.getElementById('daily-run');
  const status = document.getElementById('daily-status');
  let wasRunning = false;
  let posting = false;
  function render(data) {
    const running = data.state === 'running';
    button.disabled = running || posting;
    button.textContent = running ? '筛选中…' : '▶ 立即运行 Scout 筛选';
    const labels = {idle: '待机', running: '筛选中…', completed: 'completed · 筛选完成', failed: 'failed · 筛选失败'};
    status.textContent = [labels[data.state], data.started_at && `开始：${data.started_at}`,
      data.finished_at && `结束：${data.finished_at}`, data.safe_error,
      data.latest_summary && `KEEP ${data.latest_summary.KEEP} · MAYBE ${data.latest_summary.MAYBE} · SKIP ${data.latest_summary.SKIP}`].filter(Boolean).join(' · ');
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
    posting = true;
    button.disabled = true;
    try {
      const response = await fetch('/api/daily/run', {method: 'POST', headers: {'x-csrf-token': button.dataset.csrf}});
      if (response.status === 409) { wasRunning = true; return; }
      if (response.status !== 202) throw new Error();
      wasRunning = true;
      posting = false;
      render(await response.json());
    } catch (_) { status.textContent = '启动失败，请稍后重试。'; }
    finally { posting = false; }
  });
  poll();
})();
