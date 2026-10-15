import { defineConfig } from '@playwright/test'
import { existsSync } from 'node:fs'
import { delimiter, resolve } from 'node:path'

const scriptsDir = resolve('..', '.venv', process.platform === 'win32' ? 'Scripts' : 'bin')
const localPython = resolve(scriptsDir, process.platform === 'win32' ? 'python.exe' : 'python')
const pythonPath = !process.env.FAULTLINE_PYTHON && existsSync(localPython) ? `${scriptsDir}${delimiter}${process.env.PATH || ''}` : (process.env.PATH || '')

export default defineConfig({ testDir: './e2e', use: { baseURL: 'http://127.0.0.1:5173' }, webServer: [
  { command: 'python -m uvicorn faultline.api:app --host 127.0.0.1 --port 8000', cwd: '../backend', env: { PATH: pythonPath }, url: 'http://127.0.0.1:8000/api/health', reuseExistingServer: !process.env.CI, timeout: 30000 },
  { command: 'npm run dev', url: 'http://127.0.0.1:5173', reuseExistingServer: !process.env.CI, timeout: 30000 }
] })
