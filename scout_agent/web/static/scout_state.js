'use strict';
document.getElementById('all-statuses').addEventListener('click', () => {
  document.querySelectorAll('input[name="status"]').forEach(input => { input.checked = true; });
});
document.querySelectorAll('.state-actions').forEach(actions => {
  actions.addEventListener('click', async event => {
    const button = event.target.closest('button[data-status]');
    if (!button) return;
    const buttons = actions.querySelectorAll('button');
    buttons.forEach(item => { item.disabled = true; });
    try {
      const response = await fetch(`/jobs/${actions.dataset.scoutId}/state`, {
        method: 'POST', headers: {'Content-Type': 'application/json',
          'X-CSRF-Token': document.getElementById('daily-run').dataset.csrf},
        body: JSON.stringify({status: button.dataset.status}),
      });
      if (!response.ok) throw new Error(response.status === 409 ? '正在筛选或本地数据库忙，请稍后重试。' : '状态保存失败，请刷新后重试。');
      window.location.reload();
    } catch (error) {
      document.getElementById('state-error').textContent = error instanceof TypeError ? '状态保存失败，请稍后重试。' : error.message;
      buttons.forEach(item => { item.disabled = false; });
    }
  });
});
