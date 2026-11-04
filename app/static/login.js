'use strict';
const form = document.getElementById('form-login');
const errorBox = document.getElementById('login-error');
const button = document.getElementById('login-btn');

form.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  errorBox.hidden = true;
  button.disabled = true;
  try {
    const res = await fetch('/api/v1/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: document.getElementById('username').value, password: document.getElementById('password').value }),
    });
    if (res.ok) {
      window.location.assign('/');
      return;
    }
    let json = null;
    try { json = await res.json(); } catch (e) { /* not JSON */ }
    errorBox.textContent = json && json.error ? json.error.message : 'Fehler ' + res.status;
  } catch (e) {
    errorBox.textContent = 'Keine Verbindung zum Server.';
  }
  errorBox.hidden = false;
  button.disabled = false;
  document.getElementById('password').select();
});
