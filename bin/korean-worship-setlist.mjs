#!/usr/bin/env node

import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const PKG_ROOT = path.resolve(__dirname, '..');
const SRC_SKILL_DIR = path.join(PKG_ROOT, '.agents', 'skills', 'korean-worship-setlist');

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

function printHelp() {
  console.log(`korean-worship-setlist v${readPkgVersion()}
Korean church worship setlist planner & sheet stamper skill installer

Usage:
  npx korean-worship-setlist [install] [options]

Commands:
  install                 Install skill files into target directory (default)

Options:
  --global, -g            Install globally (~/.gemini/config/skills/ or global tool dir)
  --plugin                Install as Antigravity global plugin (~/.gemini/config/plugins/korean-worship-setlist)
  --claude                Target Claude Code skills directory (.claude/skills/korean-worship-setlist)
  --agents                Target generic agent skills directory (.agents/skills/korean-worship-setlist)
  --dest <dir>            Install skill directly to a custom directory path
  --sync                  Run "uv sync --project <dest>" after installing
  --version, -v           Show package version
  --help, -h              Show this help message

Examples:
  npx korean-worship-setlist                         # Install to ./.agents/skills/korean-worship-setlist
  npx korean-worship-setlist install --global        # Install to ~/.gemini/config/skills/korean-worship-setlist
  npx korean-worship-setlist install --plugin        # Install Antigravity plugin to ~/.gemini/config/plugins/korean-worship-setlist
  npx korean-worship-setlist install --claude        # Install to ./.claude/skills/korean-worship-setlist
  npx korean-worship-setlist install --dest ./skills # Install to custom path
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

const opts = parseArgs(process.argv.slice(2));

if (opts.version) {
  console.log(`korean-worship-setlist v${readPkgVersion()}`);
  process.exit(0);
}

if (opts.help) {
  printHelp();
  process.exit(0);
}

if (opts.command && opts.command !== 'install') {
  console.error(`[korean-worship-setlist] Error: Unknown command "${opts.command}". Expected "install".`);
  process.exit(1);
}

if (!fs.existsSync(SRC_SKILL_DIR)) {
  console.error(`[korean-worship-setlist] Error: Bundled skill directory not found at ${SRC_SKILL_DIR}`);
  process.exit(1);
}

const target = resolveTarget(opts);

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
  console.log(
    `[korean-worship-setlist] Installed ${target.mode === 'plugin' ? 'Antigravity plugin' : 'skill'} to:\n  ${
      target.pluginDir || target.skillDir
    }`
  );
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
    process.exit(1);
  }
  if (res.status !== 0) {
    process.exit(res.status ?? 1);
  }
} else {
  console.log('');
  console.log('Next steps:');
  console.log(`  1. Install Python runtime dependencies:`);
  console.log(`     uv sync --project "${target.skillDir}"`);
  console.log(`  2. Initialize your church worship profile inside your workspace:`);
  console.log(`     /korean-worship-setlist init`);
}
