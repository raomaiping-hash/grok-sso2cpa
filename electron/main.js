const { app, BrowserWindow, dialog, ipcMain } = require('electron');
const { autoUpdater } = require('electron-updater');
const crypto = require('node:crypto');
const net = require('node:net');
const path = require('node:path');
const { spawn } = require('node:child_process');

let mainWindow = null;
let backendProcess = null;
let backendPort = null;
let backendToken = null;
let quitting = false;

function findFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      const port = typeof address === 'object' && address ? address.port : null;
      server.close(() => resolve(port));
    });
  });
}

function packagedBackendPath() {
  const folder = path.join(process.resourcesPath, 'backend', 'sso-bridge-backend');
  return path.join(folder, process.platform === 'win32' ? 'sso-bridge-backend.exe' : 'sso-bridge-backend');
}

function backendConfig(port, token) {
  const isPackaged = app.isPackaged;
  const projectRoot = path.join(__dirname, '..');
  const staticDir = isPackaged ? path.join(process.resourcesPath, 'static') : path.join(projectRoot, 'static');
  const env = {
    ...process.env,
    SSO_BRIDGE_PORT: String(port),
    SSO_BRIDGE_LOCAL_TOKEN: token,
    SSO_BRIDGE_DATA_DIR: app.getPath('userData'),
    SSO_BRIDGE_STATIC_DIR: staticDir,
  };
  if (isPackaged) {
    return { command: packagedBackendPath(), args: [], cwd: process.resourcesPath, env };
  }
  return {
    command: process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3'),
    args: ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(port)],
    cwd: projectRoot,
    env,
  };
}

async function waitForBackend(port, token) {
  const url = `http://127.0.0.1:${port}/api/health`;
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try {
      const response = await fetch(url, { headers: { 'X-SSO-Bridge-Token': token } });
      if (response.ok) {
        const payload = await response.json();
        if (payload.service === 'sso-bridge') return;
      }
    } catch (_) {
      // The backend may still be starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error('本地转换引擎启动超时');
}

async function startBackend() {
  backendPort = await findFreePort();
  backendToken = crypto.randomBytes(32).toString('hex');
  const config = backendConfig(backendPort, backendToken);
  backendProcess = spawn(config.command, config.args, {
    cwd: config.cwd,
    env: config.env,
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  backendProcess.stdout?.on('data', (chunk) => console.log(`[backend] ${chunk.toString().trim()}`));
  backendProcess.stderr?.on('data', (chunk) => console.warn(`[backend] ${chunk.toString().trim()}`));
  backendProcess.once('error', (error) => console.error('Backend process error:', error));
  backendProcess.once('exit', (code) => {
    if (!quitting && code !== 0) console.error(`Backend exited with code ${code}`);
  });
  await waitForBackend(backendPort, backendToken);
}

function sendUpdateStatus(payload) {
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('update-status', payload);
}

function configureUpdater() {
  autoUpdater.autoDownload = false;
  autoUpdater.on('checking-for-update', () => sendUpdateStatus({ status: 'checking' }));
  autoUpdater.on('update-available', (info) => sendUpdateStatus({ status: 'available', version: info.version }));
  autoUpdater.on('update-not-available', (info) => sendUpdateStatus({ status: 'not-available', version: info.version }));
  autoUpdater.on('download-progress', (progress) => sendUpdateStatus({ status: 'downloading', percent: progress.percent }));
  autoUpdater.on('update-downloaded', (info) => sendUpdateStatus({ status: 'downloaded', version: info.version }));
  autoUpdater.on('error', (error) => sendUpdateStatus({ status: 'error', message: error.message }));
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 980,
    minHeight: 700,
    backgroundColor: '#101216',
    icon: path.join(__dirname, '..', 'assets', 'icon.png'),
    autoHideMenuBar: true,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, 'preload.js'),
    },
  });
  mainWindow.webContents.session.webRequest.onBeforeSendHeaders(
    { urls: [`http://127.0.0.1:${backendPort}/*`] },
    (details, callback) => {
      details.requestHeaders['X-SSO-Bridge-Token'] = backendToken;
      callback({ requestHeaders: details.requestHeaders });
    },
  );
  mainWindow.loadURL(`http://127.0.0.1:${backendPort}/`);
  mainWindow.on('closed', () => { mainWindow = null; });
}

ipcMain.handle('app:get-version', () => app.getVersion());
ipcMain.handle('app:check-for-updates', async () => {
  if (!app.isPackaged) return { status: 'dev', version: app.getVersion() };
  try {
    const result = await autoUpdater.checkForUpdates();
    return { status: 'checked', version: result?.updateInfo?.version || null };
  } catch (error) {
    return { status: 'error', message: error.message };
  }
});
ipcMain.handle('app:download-update', async () => {
  try {
    await autoUpdater.downloadUpdate();
    return { status: 'downloading' };
  } catch (error) {
    return { status: 'error', message: error.message };
  }
});
ipcMain.handle('app:install-update', () => {
  if (app.isPackaged) autoUpdater.quitAndInstall();
  return { status: 'installing' };
});

app.whenReady().then(async () => {
  configureUpdater();
  try {
    await startBackend();
    createWindow();
    if (app.isPackaged) setTimeout(() => autoUpdater.checkForUpdates().catch(() => {}), 3000);
  } catch (error) {
    dialog.showErrorBox('SSO Bridge 启动失败', error.message);
    app.quit();
  }
});

app.on('before-quit', () => {
  quitting = true;
  if (backendProcess && !backendProcess.killed) backendProcess.kill();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
