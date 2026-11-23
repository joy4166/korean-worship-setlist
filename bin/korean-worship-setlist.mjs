#!/usr/bin/env node

import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import https from 'node:https';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const PKG_ROOT = path.resolve(__dirname, '..');
const SRC_SKILL_DIR = path.join(PKG_ROOT, '.agents', 'skills', 'korean-worship-setlist');
const NPM_LATEST_URL = 'https://registry.npmjs.org/korean-worship-setlist/latest';

const EXCLUDED_DIRS = new Set([
  '.git',
  '.venv',
  'node_modules',
  '__pycache__',
  '.pytest_cache',
  'tests',
  'output',
  '_cache',
]);

function readPkgVersion() {
  try {
    const pkg = JSON.parse(fs.readFileSync(path.join(PKG_ROOT, 'package.json'), 'utf8'));
    return pkg.version || '0.0.0';
  } catch {
    return '0.0.0';
  }
}

function readSkillVersion(skillDir) {
  try {
    const tomlPath = path.join(skillDir, 'pyproject.toml');
    if (!fs.existsSync(tomlPath)) return null;
    const content = fs.readFileSync(tomlPath, 'utf8');
    const match = content.match(/^version\s*=\s*"([^"]+)"/m);
    return match ? match[1].trim() : null;
  } catch {
    return null;
  }
}

function parseSemver(ver) {
  if (!ver) return null;
  const m = String(ver).trim().match(/^v?(\d+)\.(\d+)\.(\d+)$/);
  if (!m) return null;
  return [Number(m[1]), Number(m[2]), Number(m[3])];
}

function compareSemver(a, b) {
  const pa = parseSemver(a);
  const pb = parseSemver(b);
  if (!pa || !pb) return null;
  for (let i = 0; i < 3; i += 1) {
    if (pa[i] > pb[i]) return 1;
    if (pa[i] < pb[i]) return -1;
  }
  return 0;
}

function printHelp() {
  console.log(`korean-worship-setlist v${readPkgVersion()}
Korean church worship setlist planner & sheet stamper skill installer

Usage:
  npx korean-worship-setlist [install|update|check] [options]

Commands:
  install                 Install skill files into target directory (default)
  update                  Upgrade existing skill/plugin while preserving church data (user_config.json, church_local.db)
  check                   Check npm registry for a newer release

Options:
  --global, -g            Install/update globally (~/.gemini/config/skills/ or global tool dir)
  --plugin                Install/update as Antigravity global plugin (~/.gemini/config/plugins/korean-worship-setlist)
  --claude                Target Claude Code skills directory (.claude/skills/korean-worship-setlist)
  --agents                Target generic agent skills directory (.agents/skills/korean-worship-setlist)
  --dest <dir>            Target a custom skill directory path
  --sync                  Run "uv sync --project <dest>" after installing/updating
  --version, -v           Show package version
  --help, -h              Show this help message

Examples:
  npx korean-worship-setlist                         # Install to ./.agents/skills/korean-worship-setlist
  npx korean-worship-setlist install --global        # Install to ~/.gemini/config/skills/korean-worship-setlist
  npx korean-worship-setlist install --plugin        # Install Antigravity plugin to ~/.gemini/config/plugins/korean-worship-setlist
  npx korean-worship-setlist install --claude        # Install to ./.claude/skills/korean-worship-setlist
  npx korean-worship-setlist install --dest ./skills # Install to custom path
  npx korean-worship-setlist@latest update           # Upgrade workspace skill preserving church data
  npx korean-worship-setlist@latest update --global  # Upgrade global skill preserving church data
  npx korean-worship-setlist check                   # Check installed/bundled version against npm latest
`);
}

function parseArgs(argv) {
  const opts = {
    command: '',
    global: false,
    plugin: false,
    claude: false,
    agents: false,
    sync: false,
    dest: null,
    help: false,
    version: false,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--help' || arg === '-h') {
      opts.help = true;
    } else if (arg === '--version' || arg === '-v') {
      opts.version = true;
    } else if (arg === '--global' || arg === '-g') {
      opts.global = true;
    } else if (arg === '--plugin') {
      opts.plugin = true;
    } else if (arg === '--claude') {
      opts.claude = true;
    } else if (arg === '--agents') {
      opts.agents = true;
    } else if (arg === '--sync') {
      opts.sync = true;
    } else if (arg === '--dest') {
      const next = argv[i + 1];
      if (!next || next.startsWith('-')) {
        console.error('[korean-worship-setlist] Error: --dest requires a directory path.');
        process.exit(1);
      }
      opts.dest = next;
      i += 1;
    } else if (arg.startsWith('--dest=')) {
      const val = arg.slice(7);
      if (!val) {
        console.error('[korean-worship-setlist] Error: --dest= requires a directory path.');
        process.exit(1);
      }
      opts.dest = val;
    } else if (arg.startsWith('-')) {
      console.error(`[korean-worship-setlist] Error: Unknown option "${arg}". Run --help for usage.`);
      process.exit(1);
    } else if (!opts.command) {
      opts.command = arg;
    } else {
      console.error(`[korean-worship-setlist] Error: Unexpected argument "${arg}". Run --help for usage.`);
      process.exit(1);
    }
  }

  return opts;
}

function resolveTarget(opts) {
  const home = os.homedir();
  const cwd = process.cwd();

  if (opts.dest) {
    return { mode: 'skill', skillDir: path.resolve(opts.dest), pluginDir: null };
  }
  if (opts.plugin) {
    const pluginDir = path.join(home, '.gemini', 'config', 'plugins', 'korean-worship-setlist');
    return {
      mode: 'plugin',
      pluginDir,
      skillDir: path.join(pluginDir, 'skills', 'korean-worship-setlist'),
    };
  }
  if (opts.claude) {
    const base = opts.global ? home : cwd;
    return {
      mode: 'skill',
      pluginDir: null,
      skillDir: path.join(base, '.claude', 'skills', 'korean-worship-setlist'),
    };
  }
  if (opts.agents) {
    const base = opts.global ? home : cwd;
    return {
      mode: 'skill',
      pluginDir: null,
      skillDir: path.join(base, '.agents', 'skills', 'korean-worship-setlist'),
    };
  }
  if (opts.global) {
    return {
      mode: 'skill',
      pluginDir: null,
      skillDir: path.join(home, '.gemini', 'config', 'skills', 'korean-worship-setlist'),
    };
  }
  return {
    mode: 'skill',
    pluginDir: null,
    skillDir: path.join(cwd, '.agents', 'skills', 'korean-worship-setlist'),
  };
}

function buildUpdateHintArgs(opts) {
  const parts = ['npx', 'korean-worship-setlist@latest', 'update'];
  if (opts.plugin) parts.push('--plugin');
  if (opts.claude) parts.push('--claude');
  if (opts.agents) parts.push('--agents');
  if (opts.global && !opts.plugin) parts.push('--global');
  if (opts.dest) parts.push('--dest', `"${opts.dest}"`);
  return parts.join(' ');
}

function shouldCopySkillEntry(src) {
  const rel = path.relative(SRC_SKILL_DIR, src);
  if (!rel) return true;
  const parts = rel.split(path.sep);
  if (parts.some((seg) => EXCLUDED_DIRS.has(seg))) {
    return false;
  }
  const base = path.basename(src);
  if (base.startsWith('church_local.db') || base === 'user_config.json') {
    return false;
  }
  return true;
}

function fetchLatestMetadata(url, timeoutMs = 3000) {
  return new Promise((resolve, reject) => {
    const req = https.get(
      url,
      { headers: { Accept: 'application/json', Connection: 'close' }, timeout: timeoutMs },
      (res) => {
        if (res.statusCode && (res.statusCode < 200 || res.statusCode >= 300)) {
          res.resume();
          reject(new Error(`HTTP ${res.statusCode}`));
          return;
        }
        let body = '';
        res.setEncoding('utf8');
        res.on('data', (chunk) => {
          body += chunk;
        });
        res.on('end', () => {
          try {
            resolve(JSON.parse(body));
          } catch (err) {
            reject(err);
          }
        });
      }
    );
    req.on('timeout', () => {
      req.destroy(new Error('Request timed out'));
    });
    req.on('error', reject);
  });
}

async function runCheck(opts) {
  const target = resolveTarget(opts);
  const installed = readSkillVersion(target.skillDir) || readPkgVersion();
  const hasTarget = fs.existsSync(path.join(target.skillDir, 'pyproject.toml'));
  const scopeLabel = hasTarget ? `installed at ${target.skillDir}` : 'bundled package';

  let latest;
  try {
    const data = await fetchLatestMetadata(NPM_LATEST_URL, 3000);
    latest = data?.version ? String(data.version).trim() : null;
  } catch (err) {
    console.error(`[korean-worship-setlist] Unable to check npm registry (${err?.message || err}).`);
    process.exitCode = 1;
    return;
  }

  if (!latest) {
    console.error('[korean-worship-setlist] Unexpected npm registry response (missing version).');
    process.exitCode = 1;
    return;
  }

  const cmp = compareSemver(latest, installed);
  console.log(`[korean-worship-setlist] Version check (${scopeLabel}): v${installed} | npm latest: v${latest}`);
  if (cmp === 1) {
    console.log(`UPDATE_AVAILABLE: v${latest} is available. Run: ${buildUpdateHintArgs(opts)}`);
  } else {
    console.log('Up to date.');
  }
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));

  if (opts.version) {
    console.log(`korean-worship-setlist v${readPkgVersion()}`);
    return;
  }

  if (opts.help) {
    printHelp();
    return;
  }

  if (opts.command && !['install', 'update', 'check'].includes(opts.command)) {
    console.error(
      `[korean-worship-setlist] Error: Unknown command "${opts.command}". Expected "install", "update", or "check".`
    );
    process.exitCode = 1;
    return;
  }

  if (opts.command === 'check') {
    await runCheck(opts);
    return;
  }

  if (!fs.existsSync(SRC_SKILL_DIR)) {
    console.error(`[korean-worship-setlist] Error: Bundled skill directory not found at ${SRC_SKILL_DIR}`);
    process.exitCode = 1;
    return;
  }

  const target = resolveTarget(opts);
  const newVer = readPkgVersion();
  const prevVer = readSkillVersion(target.skillDir);

  if (target.mode === 'plugin' && target.pluginDir) {
    fs.mkdirSync(target.pluginDir, { recursive: true });
    for (const fileName of ['plugin.json', 'README.md', 'LICENSE']) {
      const srcFile = path.join(PKG_ROOT, fileName);
      if (fs.existsSync(srcFile)) {
        fs.copyFileSync(srcFile, path.join(target.pluginDir, fileName));
      }
    }
  }

  if (path.resolve(SRC_SKILL_DIR) === path.resolve(target.skillDir)) {
    console.log(`[korean-worship-setlist] Skill files are already in place at:\n  ${target.skillDir}`);
  } else {
    fs.mkdirSync(target.skillDir, { recursive: true });
    fs.cpSync(SRC_SKILL_DIR, target.skillDir, {
      recursive: true,
      force: true,
      filter: shouldCopySkillEntry,
    });
    const kindLabel = target.mode === 'plugin' ? 'Antigravity plugin' : 'skill';
    if (opts.command === 'update') {
      const verSummary = prevVer && prevVer !== newVer ? ` (v${prevVer} -> v${newVer})` : ` (v${newVer})`;
      console.log(
        `[korean-worship-setlist] Updated ${kindLabel}${verSummary}, preserved local user_config.json & church_local.db:\n  ${
          target.pluginDir || target.skillDir
        }`
      );
    } else {
      console.log(
        `[korean-worship-setlist] Installed ${kindLabel} to:\n  ${target.pluginDir || target.skillDir}`
      );
    }
    if (target.mode === 'plugin') {
      console.log(`  Skill path: ${target.skillDir}`);
    }
  }

  if (opts.sync) {
    console.log(`[korean-worship-setlist] Running uv sync --project "${target.skillDir}"...`);
    const res = spawnSync('uv', ['sync', '--project', target.skillDir], {
      stdio: 'inherit',
      shell: process.platform === 'win32',
    });
    if (res.error) {
      console.error(`[korean-worship-setlist] Failed to launch uv: ${res.error.message}`);
      process.exitCode = 1;
      return;
    }
    if (res.status !== 0) {
      process.exitCode = res.status ?? 1;
      return;
    }
  } else {
    console.log('');
    console.log('Next steps:');
    console.log(`  1. Install Python runtime dependencies:`);
    console.log(`     uv sync --project "${target.skillDir}"`);
    console.log(`  2. Initialize your church worship profile inside your workspace:`);
    console.log(`     /korean-worship-setlist init`);
  }
}

await main();
