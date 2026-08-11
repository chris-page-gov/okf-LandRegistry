#!/usr/bin/env node

/**
 * Record exact-candidate authored-site browser-quality observations.
 *
 * This runner deliberately does not decide Land Registry G6, perform a human
 * accessibility audit, claim WCAG conformance, or replace the pinned
 * Explorer product and search-calibration receipts.
 */

import { createHash } from 'node:crypto';
import { execFile } from 'node:child_process';
import { createServer } from 'node:http';
import {
  lstat,
  mkdir,
  readFile,
  readdir,
  realpath,
  stat,
  writeFile
} from 'node:fs/promises';
import { createRequire } from 'node:module';
import { arch, platform, release } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { promisify } from 'node:util';
import { gunzipSync, gzipSync } from 'node:zlib';

const execFileAsync = promisify(execFile);
const RUNNER_PATH = fileURLToPath(import.meta.url);
const SHA256_PATTERN = /^[0-9a-f]{64}$/;
const COMMIT_PATTERN = /^[0-9a-f]{40}$/;
const ROOT_MARKER = '# release-root-sha256: ';
const EXPECTED_PLAYWRIGHT_VERSION = '1.62.1';
const EXPECTED_AXE_VERSION = '4.12.1';
const ACCEPTANCE_EXECUTABLE_NAMES = Object.freeze([
  'runner',
  'wrapper',
  'invocation_lock_module',
  'contract_module',
  'app_build_manifest_module',
  'deterministic_build_script'
]);
const REQUIRED_ROUTES = [
  '/',
  '/accessibility.html',
  '/catalogue-index.html',
  '/404.html'
];
const RICH_RELATIONSHIP_SOURCE_PATH =
  'apps/okf-explorer/src/lib/sources/largeCorpus.ts';
const RICH_RELATIONSHIP_LIMIT_NAMES = [
  'maximum_json_bytes',
  'maximum_relationship_rows',
  'maximum_rich_relationship_route_chunks',
  'maximum_rich_relationship_route_rows',
  'maximum_rich_relationship_chunk_rows',
  'maximum_rich_relationship_chunk_bytes',
  'maximum_rich_relationship_decoded_chunk_bytes',
  'maximum_rich_relationship_hydration_compressed_bytes',
  'maximum_rich_relationship_retained_text_units',
  'maximum_rich_relationship_row_text_units',
  'maximum_rich_relationship_evidence_items',
  'maximum_rich_relationship_supporting_assertions',
  'maximum_rich_relationship_cached_chunks',
  'maximum_rich_relationship_planes',
  'maximum_rich_relationship_chunks'
];
const EXPLORER_V061_RICH_RELATIONSHIP_LIMITS = Object.freeze({
  maximum_json_bytes: 67_108_864,
  maximum_relationship_rows: 300_000,
  maximum_rich_relationship_route_chunks: 64,
  maximum_rich_relationship_route_rows: 100_000,
  maximum_rich_relationship_chunk_rows: 50_000,
  maximum_rich_relationship_chunk_bytes: 8_388_608,
  maximum_rich_relationship_decoded_chunk_bytes: 67_108_864,
  maximum_rich_relationship_hydration_compressed_bytes: 67_108_864,
  maximum_rich_relationship_retained_text_units: 33_554_432,
  maximum_rich_relationship_row_text_units: 32_768,
  maximum_rich_relationship_evidence_items: 16,
  maximum_rich_relationship_supporting_assertions: 128,
  maximum_rich_relationship_cached_chunks: 16,
  maximum_rich_relationship_planes: 16,
  maximum_rich_relationship_chunks: 10_000
});
const ABSOLUTE_IRI_PATTERN = /^[A-Za-z][A-Za-z0-9+.-]*:[^\s]+$/;
const LOCAL_RELATIONSHIP_ROUTE_PATTERN =
  /^[a-z][a-z0-9-]*(?:\/[A-Za-z0-9._~-]+)+$/;
const RICH_RELATIONSHIP_AUTHORITY_CLASSES = new Set([
  'official',
  'derived',
  'model-assisted',
  'synthetic',
  'unclassified'
]);
const RICH_RELATIONSHIP_ASSERTION_STATUSES = new Set([
  'official',
  'normalized',
  'inferred',
  'model-derived'
]);
const RICH_RELATIONSHIP_ASSERTION_SCOPES = new Set([
  'real-world',
  'synthetic-fixture'
]);
const RICH_RELATIONSHIP_PLANE_LIFECYCLES = new Set([
  'active',
  'historical',
  'rejected'
]);
const RICH_RELATIONSHIP_MAXIMUM_NAMES = [
  'row_retained_text_units',
  'row_evidence_items',
  'row_supporting_assertions',
  'chunk_rows',
  'chunk_compressed_bytes',
  'chunk_decoded_bytes',
  'chunk_retained_text_units',
  'locator_bucket_compressed_bytes',
  'locator_bucket_decoded_bytes',
  'locator_manifest_bytes',
  'runtime_manifest_bytes',
  'route_chunks',
  'route_declared_rows',
  'route_incident_rows',
  'route_compressed_bytes',
  'route_retained_text_units',
  'full_hydration_chunks',
  'full_hydration_declared_rows',
  'full_hydration_compressed_bytes',
  'full_hydration_retained_text_units',
  'total_chunks',
  'total_rows',
  'total_planes'
];
const AXE_TAGS = [
  'wcag2a',
  'wcag2aa',
  'wcag21a',
  'wcag21aa',
  'wcag22aa'
];
const TEXT_EXTENSIONS = new Set([
  '.css',
  '.csv',
  '.html',
  '.js',
  '.json',
  '.jsonl',
  '.jsonld',
  '.md',
  '.mjs',
  '.svg',
  '.ttl',
  '.txt',
  '.webmanifest',
  '.yaml',
  '.yamlld',
  '.yml'
]);
const COMPRESSIBLE_EXTENSIONS = new Set([
  ...TEXT_EXTENSIONS,
  '.xml'
]);
const SENSITIVE_QUERY_KEYS = new Set([
  'accesskey',
  'accesstoken',
  'apikey',
  'authenticationtoken',
  'authtoken',
  'clientsecret',
  'credential',
  'downloadtoken',
  'idtoken',
  'keypairid',
  'password',
  'passwd',
  'refreshtoken',
  'sas',
  'sessiontoken',
  'securitytoken',
  'sharedaccesssignature',
  'sig',
  'signature',
  'token',
  'xamzcredential',
  'xamzsecuritytoken',
  'xamzsignature',
  'xgoogcredential',
  'xgoogsignature'
]);
const HELP = `Usage:
  node scripts/run_authored_site_browser_quality.mjs \\
    --repository-root <absolute candidate repository> \\
    --bundle-root <absolute exact bundle directory> \\
    --explorer-checkout <absolute clean Explorer v0.6.1 checkout> \\
    --candidate-commit <40-character lowercase commit> \\
    --release-root <64-character lowercase bundle root> \\
    --output <absolute new JSON evidence path> \\
    [--incomplete-review <absolute reviewed axe-incomplete JSON>] \\
    [--preflight-only] [--headed]

The full run covers the authored site only. Explorer product and search
calibration remain separate required receipts. The output is automated
candidate evidence, never a Land Registry G6 decision, human audit or WCAG
conformance claim. --preflight-only performs identity and checksum checks
without launching a browser and cannot serve as browser-quality evidence.
`;

function invariant(condition, message) {
  if (!condition) throw new Error(message);
}

function sha256(bytes) {
  return createHash('sha256').update(bytes).digest('hex');
}

function jsonObject(value, label) {
  invariant(value && typeof value === 'object' && !Array.isArray(value), `${label} must be an object`);
  return value;
}

function canonicalValue(value) {
  if (Array.isArray(value)) return value.map(canonicalValue);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.keys(value).sort().map((key) => [key, canonicalValue(value[key])])
    );
  }
  return value;
}

function canonicalJson(value) {
  return `${JSON.stringify(canonicalValue(value), null, 2)}\n`;
}

function compareCodePoints(left, right) {
  const leftPoints = [...left];
  const rightPoints = [...right];
  const length = Math.min(leftPoints.length, rightPoints.length);
  for (let index = 0; index < length; index += 1) {
    const difference = leftPoints[index].codePointAt(0) - rightPoints[index].codePointAt(0);
    if (difference !== 0) return difference < 0 ? -1 : 1;
  }
  return leftPoints.length - rightPoints.length;
}

function safeRelativePath(value, label = 'path') {
  invariant(typeof value === 'string' && value.length > 0 && value.length <= 4096, `${label} must be a bounded non-empty string`);
  invariant(!value.includes('\\') && !path.posix.isAbsolute(value), `${label} must be a relative POSIX path`);
  invariant(!/[\u0000-\u001f\u007f]/.test(value), `${label} contains control characters`);
  const normalised = path.posix.normalize(value);
  invariant(normalised === value, `${label} is not canonical: ${value}`);
  invariant(normalised.split('/').every((part) => part && part !== '.' && part !== '..'), `${label} contains an unsafe segment: ${value}`);
  return normalised;
}

// Keep this predicate in exact behavioural parity with Explorer v0.6.1's
// safeRelativeResourcePath. Runtime resource names are URLs as well as local
// filesystem paths, so ordinary POSIX normalisation is not sufficient: a
// query, fragment or encoded path separator can resolve to a different HTTP
// resource from the file measured by this observer.
function safeRelativeResourcePath(value, label = 'release data-plane path') {
  if (
    typeof value !== 'string' ||
    !value ||
    value.trim() !== value ||
    value.startsWith('/') ||
    value.includes('\\')
  ) {
    throw new Error(`${label} is unsafe`);
  }
  if (
    value.includes('?') ||
    value.includes('#') ||
    /^[A-Za-z][A-Za-z0-9+.-]*:/.test(value)
  ) {
    throw new Error(`${label} is unsafe`);
  }
  const segments = value.split('/');
  if (segments.some((segment) => !segment)) throw new Error(`${label} is unsafe`);
  for (const segment of segments) {
    let decoded;
    try {
      decoded = decodeURIComponent(segment);
    } catch {
      throw new Error(`${label} is unsafe`);
    }
    if (
      !decoded ||
      decoded === '.' ||
      decoded === '..' ||
      decoded.includes('/') ||
      decoded.includes('\\') ||
      decoded.includes('\0')
    ) {
      throw new Error(`${label} is unsafe`);
    }
  }
  return value;
}

function resolveUnder(root, relative, label = 'path') {
  const safe = safeRelativePath(relative, label);
  const resolvedRoot = path.resolve(root);
  const resolved = path.resolve(resolvedRoot, ...safe.split('/'));
  invariant(resolved.startsWith(`${resolvedRoot}${path.sep}`), `${label} escapes its root: ${relative}`);
  return resolved;
}

function normaliseQueryKey(value) {
  return value
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .replace(/[^A-Za-z0-9]/g, '')
    .toLowerCase();
}

function describedUrl(raw) {
  try {
    const parsed = new URL(raw);
    return {
      origin: parsed.origin,
      path: parsed.pathname,
      query_names: [...new Set([...parsed.searchParams.keys()])].sort(compareCodePoints),
      sha256: sha256(Buffer.from(raw, 'utf8'))
    };
  } catch {
    return {
      origin: null,
      path: null,
      query_names: [],
      sha256: sha256(Buffer.from(String(raw), 'utf8'))
    };
  }
}

function credentialUrlFinding(raw) {
  let parsed;
  try {
    parsed = new URL(raw);
  } catch {
    return null;
  }
  if (!['http:', 'https:'].includes(parsed.protocol)) return null;
  if (parsed.username || parsed.password) return 'embedded-user-information';
  for (const key of parsed.searchParams.keys()) {
    if (SENSITIVE_QUERY_KEYS.has(normaliseQueryKey(key))) {
      return `sensitive-query-key:${normaliseQueryKey(key)}`;
    }
  }
  return null;
}

function boundedTextEvidence(text) {
  const value = String(text);
  return {
    characters: value.length,
    sha256: sha256(Buffer.from(value, 'utf8'))
  };
}

function safeDiagnostic(text) {
  return String(text)
    .replace(/-----BEGIN [^-]+ PRIVATE KEY-----[\s\S]*?-----END [^-]+ PRIVATE KEY-----/giu, '[redacted private key]')
    .replace(/([?&][A-Za-z0-9_.~-]{1,128}=)[^&#\s]*/gu, '$1[redacted]')
    .replace(/\bgh[pousr]_[A-Za-z0-9]{20,}\b/gu, '[redacted GitHub credential]')
    .replace(/\bAKIA[0-9A-Z]{16}\b/gu, '[redacted AWS credential]')
    .slice(0, 1000);
}

function parseArguments(argv) {
  if (argv.includes('--help') || argv.includes('-h')) return { help: true };
  const valueOptions = new Set([
    '--repository-root',
    '--bundle-root',
    '--explorer-checkout',
    '--candidate-commit',
    '--release-root',
    '--output',
    '--incomplete-review'
  ]);
  const flagOptions = new Set(['--preflight-only', '--headed']);
  const parsed = { help: false, preflightOnly: false, headed: false };
  const seen = new Set();
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    invariant(valueOptions.has(token) || flagOptions.has(token), `unknown option: ${token}`);
    invariant(!seen.has(token), `option may be supplied only once: ${token}`);
    seen.add(token);
    if (flagOptions.has(token)) {
      if (token === '--preflight-only') parsed.preflightOnly = true;
      if (token === '--headed') parsed.headed = true;
      continue;
    }
    invariant(index + 1 < argv.length && !argv[index + 1].startsWith('--'), `${token} requires a value`);
    parsed[token.slice(2).replace(/-([a-z])/g, (_match, letter) => letter.toUpperCase())] = argv[index + 1];
    index += 1;
  }
  for (const name of [
    'repositoryRoot',
    'bundleRoot',
    'explorerCheckout',
    'candidateCommit',
    'releaseRoot',
    'output'
  ]) {
    invariant(parsed[name], `--${name.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`)} is required`);
  }
  for (const name of ['repositoryRoot', 'bundleRoot', 'explorerCheckout', 'output']) {
    invariant(path.isAbsolute(parsed[name]), `--${name.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`)} must be absolute`);
    parsed[name] = path.resolve(parsed[name]);
  }
  if (parsed.incompleteReview) {
    invariant(path.isAbsolute(parsed.incompleteReview), '--incomplete-review must be absolute');
    parsed.incompleteReview = path.resolve(parsed.incompleteReview);
  }
  invariant(COMMIT_PATTERN.test(parsed.candidateCommit), '--candidate-commit must be 40 lowercase hexadecimal characters');
  invariant(SHA256_PATTERN.test(parsed.releaseRoot), '--release-root must be 64 lowercase hexadecimal characters');
  return parsed;
}

async function requireRealDirectory(directory, label) {
  const metadata = await lstat(directory);
  invariant(metadata.isDirectory() && !metadata.isSymbolicLink(), `${label} must be a real directory: ${directory}`);
  return realpath(directory);
}

async function regularFiles(root, relative = '') {
  const directory = relative ? resolveUnder(root, relative) : root;
  const metadata = await lstat(directory);
  invariant(metadata.isDirectory() && !metadata.isSymbolicLink(), `directory input is not a real directory: ${relative || '.'}`);
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries.sort((left, right) => compareCodePoints(left.name, right.name))) {
    const child = relative ? `${relative}/${entry.name}` : entry.name;
    const absolute = resolveUnder(root, child);
    const childMetadata = await lstat(absolute);
    invariant(!childMetadata.isSymbolicLink(), `symbolic links are not accepted: ${child}`);
    if (childMetadata.isDirectory()) files.push(...await regularFiles(root, child));
    else if (childMetadata.isFile()) files.push(child);
    else throw new Error(`non-regular filesystem entry is not accepted: ${child}`);
  }
  return files.sort(compareCodePoints);
}

async function readRegularFile(absolute, label) {
  const before = await lstat(absolute);
  invariant(before.isFile() && !before.isSymbolicLink(), `${label} must be a regular file`);
  const bytes = await readFile(absolute);
  const after = await lstat(absolute);
  invariant(
    before.dev === after.dev && before.ino === after.ino && before.size === after.size && before.mtimeMs === after.mtimeMs,
    `${label} changed while it was read`
  );
  return bytes;
}

async function readJson(absolute, label) {
  const bytes = await readRegularFile(absolute, label);
  let parsed;
  try {
    parsed = JSON.parse(bytes.toString('utf8'));
  } catch (error) {
    throw new Error(`${label} is not valid UTF-8 JSON: ${error.message}`);
  }
  return { bytes, value: jsonObject(parsed, label) };
}

async function git(repository, args, label) {
  const result = await execFileAsync('git', ['-C', repository, ...args], {
    encoding: 'utf8',
    maxBuffer: 8 * 1024 * 1024
  });
  invariant(!result.stderr.trim(), `${label} git command wrote to stderr: ${result.stderr.trim()}`);
  return result.stdout.trim();
}

async function verifyGitIdentity(repository, expectedCommit, label) {
  const [topLevel, head, statusText] = await Promise.all([
    git(repository, ['rev-parse', '--show-toplevel'], label),
    git(repository, ['rev-parse', 'HEAD'], label),
    git(repository, ['status', '--porcelain=v1', '--untracked-files=all'], label)
  ]);
  invariant(await realpath(topLevel) === await realpath(repository), `${label} path is not its Git top level`);
  invariant(head === expectedCommit, `${label} HEAD ${head} does not match required commit ${expectedCommit}`);
  invariant(statusText === '', `${label} working tree is not clean`);
  return { commit_sha: head, source_dirty: false };
}

async function verifyBundleChecksums(bundleRoot, expectedRoot) {
  const checksumPath = path.join(bundleRoot, 'CHECKSUMS.sha256');
  const bytes = await readRegularFile(checksumPath, 'bundle checksum manifest');
  const text = bytes.toString('utf8');
  invariant(text.endsWith('\n'), 'bundle checksum manifest must end with a newline');
  const lines = text.slice(0, -1).split('\n');
  const digestLines = [];
  const declaredRoots = [];
  const rows = [];
  const seen = new Set();
  for (const [offset, line] of lines.entries()) {
    const lineNumber = offset + 1;
    invariant(line.length > 0, `bundle checksum manifest line ${lineNumber} is blank`);
    if (line.startsWith(ROOT_MARKER)) {
      declaredRoots.push(line.slice(ROOT_MARKER.length));
      continue;
    }
    invariant(!line.startsWith('#'), `bundle checksum manifest line ${lineNumber} has an unsupported comment`);
    const separator = line.indexOf('  ');
    invariant(separator === 64, `bundle checksum manifest line ${lineNumber} is malformed`);
    const digest = line.slice(0, separator);
    const relative = safeRelativePath(line.slice(separator + 2), `checksum path on line ${lineNumber}`);
    invariant(SHA256_PATTERN.test(digest), `bundle checksum manifest line ${lineNumber} has an invalid digest`);
    invariant(!seen.has(relative), `bundle checksum manifest repeats ${relative}`);
    seen.add(relative);
    const artifact = resolveUnder(bundleRoot, relative, 'checksummed bundle path');
    const artifactBytes = await readRegularFile(artifact, `checksummed bundle file ${relative}`);
    invariant(sha256(artifactBytes) === digest, `bundle checksum mismatch for ${relative}`);
    digestLines.push(line);
    rows.push({ path: relative, bytes: artifactBytes.length, sha256: digest });
  }
  invariant(declaredRoots.length === 1 && SHA256_PATTERN.test(declaredRoots[0]), 'bundle checksum manifest must declare exactly one valid release root');
  invariant(lines.at(-1) === `${ROOT_MARKER}${declaredRoots[0]}`, 'release-root marker must be the final checksum line');
  const calculatedRoot = sha256(Buffer.from(`${digestLines.join('\n')}\n`, 'utf8'));
  invariant(calculatedRoot === declaredRoots[0], `bundle release-root marker ${declaredRoots[0]} does not match calculated root ${calculatedRoot}`);
  invariant(calculatedRoot === expectedRoot, `bundle release root ${calculatedRoot} does not match required root ${expectedRoot}`);
  const actualFiles = await regularFiles(bundleRoot);
  const expectedFiles = [...seen, 'CHECKSUMS.sha256'].sort(compareCodePoints);
  invariant(JSON.stringify(actualFiles) === JSON.stringify(expectedFiles), 'bundle checksum manifest does not cover the exact bundle file set');
  return {
    release_root_sha256: calculatedRoot,
    checksums_sha256: sha256(bytes),
    file_count: actualFiles.length,
    checksummed_file_count: rows.length,
    raw_bytes: rows.reduce((total, row) => total + row.bytes, bytes.length),
    rows
  };
}

function validateBuildMaterial(row, index) {
  jsonObject(row, `Explorer build material ${index}`);
  invariant(
    JSON.stringify(Object.keys(row).sort()) === JSON.stringify(['bytes', 'path', 'sha256']),
    `Explorer build material ${index} has an unexpected key set`
  );
  const relative = safeRelativePath(row.path, `Explorer build material ${index} path`);
  invariant(relative !== 'okf-explorer-build-manifest.json', 'Explorer build manifest must exclude itself');
  invariant(Number.isSafeInteger(row.bytes) && row.bytes > 0, `Explorer build material ${index} has invalid bytes`);
  invariant(SHA256_PATTERN.test(row.sha256), `Explorer build material ${index} has invalid SHA-256`);
  return { path: relative, bytes: row.bytes, sha256: row.sha256 };
}

async function verifyExplorerBuild(explorerRoot, locked) {
  const buildRoot = path.join(explorerRoot, 'apps', 'okf-explorer', 'build');
  await requireRealDirectory(buildRoot, 'Explorer build directory');
  const manifestPath = path.join(buildRoot, 'okf-explorer-build-manifest.json');
  const { bytes: manifestBytes, value: manifest } = await readJson(manifestPath, 'Explorer build manifest');
  invariant(manifest.schema === 'okf-explorer-app-build-manifest.v1', 'Explorer build manifest schema is not supported');
  invariant(manifest.algorithm === 'sha256-canonical-json-materials-v1', 'Explorer build manifest algorithm is not supported');
  invariant(Array.isArray(manifest.materials) && manifest.materials.length > 0, 'Explorer build manifest has no materials');
  const materials = manifest.materials.map(validateBuildMaterial);
  const paths = materials.map((row) => row.path);
  invariant(new Set(paths).size === paths.length, 'Explorer build material paths are not unique');
  invariant(paths.every((value, index) => index === 0 || compareCodePoints(paths[index - 1], value) < 0), 'Explorer build materials are not strictly path-sorted');
  invariant(manifest.file_count === materials.length, 'Explorer build file_count differs from materials');
  for (const row of materials) {
    const materialBytes = await readRegularFile(resolveUnder(buildRoot, row.path), `Explorer build material ${row.path}`);
    invariant(materialBytes.length === row.bytes, `Explorer build material byte count mismatch: ${row.path}`);
    invariant(sha256(materialBytes) === row.sha256, `Explorer build material digest mismatch: ${row.path}`);
  }
  const buildFiles = await regularFiles(buildRoot);
  const expectedBuildFiles = [...paths, 'okf-explorer-build-manifest.json'].sort(compareCodePoints);
  invariant(JSON.stringify(buildFiles) === JSON.stringify(expectedBuildFiles), 'Explorer build manifest does not cover the exact build directory');
  const tree = sha256(Buffer.from(`${JSON.stringify(materials)}\n`, 'utf8'));
  invariant(tree === manifest.tree_sha256, 'Explorer build tree differs from canonical material identities');
  invariant(locked.algorithm === manifest.algorithm, 'consumer lock build algorithm differs from Explorer build');
  invariant(locked.files === materials.length, 'consumer lock build file count differs from Explorer build');
  invariant(locked.tree_sha256 === tree, 'consumer lock build tree differs from Explorer build');
  invariant(locked.build_manifest_sha256 === sha256(manifestBytes), 'consumer lock build-manifest digest differs from Explorer build');
  const indexRow = materials.find((row) => row.path === 'index.html');
  invariant(indexRow && indexRow.sha256 === locked.index_sha256, 'consumer lock index digest differs from Explorer build');
  return {
    algorithm: manifest.algorithm,
    files: materials.length,
    tree_sha256: tree,
    build_manifest_sha256: sha256(manifestBytes),
    index_sha256: indexRow.sha256
  };
}

async function verifyContractSources(explorerRoot, sources) {
  invariant(Array.isArray(sources) && sources.length > 0, 'consumer lock contract_sources must be a non-empty array');
  const rows = [];
  const seen = new Set();
  for (const [index, source] of sources.entries()) {
    jsonObject(source, `consumer contract source ${index}`);
    const relative = safeRelativePath(source.path, `consumer contract source ${index} path`);
    invariant(!seen.has(relative), `consumer contract source is duplicated: ${relative}`);
    invariant(SHA256_PATTERN.test(source.sha256), `consumer contract source ${relative} has an invalid digest`);
    seen.add(relative);
    const bytes = await readRegularFile(resolveUnder(explorerRoot, relative), `consumer contract source ${relative}`);
    invariant(sha256(bytes) === source.sha256, `consumer contract source digest mismatch: ${relative}`);
    rows.push({ path: relative, bytes: bytes.length, sha256: source.sha256 });
  }
  return rows;
}

function verifyAcceptanceExecutableMaterials(consumer, contractSources) {
  const declared = jsonObject(
    consumer.acceptance_executable_materials,
    'consumer acceptance executable materials'
  );
  invariant(
    JSON.stringify(Object.keys(declared)) ===
      JSON.stringify(ACCEPTANCE_EXECUTABLE_NAMES),
    'consumer acceptance executable material names or order differ'
  );
  const sourcesByPath = new Map(
    contractSources.map((row) => [row.path, row])
  );
  const verified = {};
  for (const name of ACCEPTANCE_EXECUTABLE_NAMES) {
    const row = jsonObject(
      declared[name],
      `consumer acceptance executable material ${name}`
    );
    invariant(
      JSON.stringify(Object.keys(row).sort()) ===
        JSON.stringify(['bytes', 'path', 'sha256']),
      `consumer acceptance executable material ${name} has an unexpected key set`
    );
    const relative = safeRelativePath(
      row.path,
      `consumer acceptance executable material ${name} path`
    );
    const bytes = positiveSafeInteger(
      row.bytes,
      `consumer acceptance executable material ${name} bytes`
    );
    invariant(
      SHA256_PATTERN.test(row.sha256),
      `consumer acceptance executable material ${name} has an invalid digest`
    );
    const source = sourcesByPath.get(relative);
    invariant(
      source && source.bytes === bytes && source.sha256 === row.sha256,
      `consumer acceptance executable material ${name} differs from its contract source`
    );
    verified[name] = { path: relative, bytes, sha256: row.sha256 };
  }
  return verified;
}

function positiveSafeInteger(value, label) {
  invariant(
    Number.isSafeInteger(value) && value > 0,
    `${label} must be a positive safe integer`
  );
  return value;
}

function nonNegativeSafeInteger(value, label) {
  invariant(
    Number.isSafeInteger(value) && value >= 0,
    `${label} must be a non-negative safe integer`
  );
  return value;
}

function exactResourceReference(value, label) {
  const reference = jsonObject(value, label);
  const relative = safeRelativePath(reference.path, `${label} path`);
  invariant(
    SHA256_PATTERN.test(reference.sha256),
    `${label} has an invalid SHA-256 digest`
  );
  const bytes = positiveSafeInteger(reference.bytes, `${label} bytes`);
  return { path: relative, sha256: reference.sha256, bytes };
}

function exactRuntimeResourceReference(value, label) {
  const reference = jsonObject(value, label);
  const relative = safeRelativeResourcePath(reference.path, `${label} path`);
  invariant(
    SHA256_PATTERN.test(reference.sha256),
    `${label} has an invalid SHA-256 digest`
  );
  const bytes = positiveSafeInteger(reference.bytes, `${label} bytes`);
  return { path: relative, sha256: reference.sha256, bytes };
}

function sameResourceReference(left, right, label) {
  invariant(
    left.path === right.path &&
      left.sha256 === right.sha256 &&
      left.bytes === right.bytes,
    `${label} path, digest or byte count differs`
  );
}

async function readBoundJsonResource(bundleRoot, reference, label) {
  const { bytes, value } = await readJson(
    resolveUnder(bundleRoot, reference.path, `${label} path`),
    label
  );
  invariant(bytes.length === reference.bytes, `${label} byte count differs`);
  invariant(sha256(bytes) === reference.sha256, `${label} digest differs`);
  return { bytes, value };
}

function exactSnapshot(value, label) {
  invariant(
    typeof value === 'string' && value.length > 0 && value.trim() === value,
    `${label} must be bounded non-empty text`
  );
  return value;
}

async function resolveAdvertisedRichRelationshipRuntime(bundleRoot, descriptor) {
  const entrypoints = jsonObject(
    descriptor.entrypoints,
    'bundle descriptor entrypoints'
  );
  const integrity = jsonObject(
    descriptor.entrypoint_integrity,
    'bundle descriptor entrypoint integrity'
  );
  const dataManifestPath = safeRelativePath(
    entrypoints.data_manifest,
    'bundle descriptor data-manifest entrypoint'
  );
  const dataManifestReference = exactResourceReference(
    integrity.data_manifest,
    'bundle descriptor data-manifest integrity'
  );
  invariant(
    dataManifestPath === dataManifestReference.path,
    'bundle descriptor data-manifest entrypoint and integrity paths differ'
  );
  const dataManifestResource = await readBoundJsonResource(
    bundleRoot,
    dataManifestReference,
    'advertised Explorer data manifest'
  );
  const dataManifest = jsonObject(
    dataManifestResource.value,
    'advertised Explorer data manifest'
  );
  invariant(
    dataManifest.schema === 'okf-explorer-data-manifest.v1',
    'advertised Explorer data-manifest schema is unsupported'
  );

  const descriptorRuntime = exactRuntimeResourceReference(
    entrypoints.relationship_runtime,
    'bundle descriptor relationship-runtime entrypoint'
  );
  const descriptorRuntimeIntegrity = exactRuntimeResourceReference(
    integrity.relationship_runtime,
    'bundle descriptor relationship-runtime integrity'
  );
  sameResourceReference(
    descriptorRuntime,
    descriptorRuntimeIntegrity,
    'bundle descriptor relationship-runtime entrypoint and integrity'
  );
  const manifestRuntime = exactRuntimeResourceReference(
    jsonObject(dataManifest.indexes, 'Explorer data-manifest indexes')
      .relationship_runtime,
    'Explorer data-manifest relationship-runtime index'
  );
  sameResourceReference(
    descriptorRuntime,
    manifestRuntime,
    'descriptor and data-manifest relationship-runtime references'
  );
  const runtimeResource = await readBoundJsonResource(
    bundleRoot,
    descriptorRuntime,
    'advertised relationship runtime manifest'
  );
  const runtime = jsonObject(
    runtimeResource.value,
    'advertised relationship runtime manifest'
  );
  const descriptorSnapshot = exactSnapshot(
    descriptor.snapshot,
    'bundle descriptor snapshot'
  );
  const manifestSnapshot = exactSnapshot(
    dataManifest.snapshot,
    'Explorer data-manifest snapshot'
  );
  const runtimeSnapshot = exactSnapshot(
    runtime.snapshot,
    'relationship runtime snapshot'
  );
  invariant(
    descriptorSnapshot === manifestSnapshot &&
      descriptorSnapshot === runtimeSnapshot,
    'descriptor, data-manifest and relationship-runtime snapshots differ'
  );
  return {
    snapshot: descriptorSnapshot,
    data_manifest: {
      reference: dataManifestReference,
      document: dataManifest
    },
    relationship_runtime: {
      reference: descriptorRuntime,
      document: runtime
    }
  };
}

function exactRichRelationshipLimits(lock) {
  const contract = jsonObject(
    lock.rich_relationship_runtime,
    'consumer lock rich_relationship_runtime'
  );
  invariant(
    contract.manifest_schema === 'okf-rich-relationship-runtime-manifest.v1' &&
      contract.row_schema === 'okf-relationship-runtime-row.v1' &&
      contract.route_locator_schema === 'okf-rich-relationship-route-locator.v1' &&
      contract.route_bucket_schema === 'okf-rich-relationship-route-locator-bucket.v1' &&
      contract.route_hash_algorithm === 'sha256-utf8-first-byte-hex' &&
      contract.content_encoding === 'gzip',
    'consumer lock rich relationship runtime identity is unsupported'
  );
  const declared = jsonObject(lock.limits, 'consumer lock limits');
  const limits = {};
  for (const name of RICH_RELATIONSHIP_LIMIT_NAMES) {
    limits[name] = positiveSafeInteger(
      declared[name],
      `consumer lock limit ${name}`
    );
    invariant(
      limits[name] === EXPLORER_V061_RICH_RELATIONSHIP_LIMITS[name],
      `consumer lock limit ${name} differs from the executable Explorer v0.6.1 contract`
    );
  }
  invariant(
    limits.maximum_rich_relationship_decoded_chunk_bytes ===
      limits.maximum_json_bytes,
    'consumer lock decoded rich relationship ceiling differs from maximum_json_bytes'
  );
  invariant(
    limits.maximum_rich_relationship_cached_chunks <=
      limits.maximum_rich_relationship_route_chunks,
    'consumer lock rich relationship cache ceiling exceeds its route ceiling'
  );
  return limits;
}

function validateRichRuntimeBuildReceipt(
  lock,
  contractSources,
  semanticValidation,
  descriptorCounts
) {
  const limits = exactRichRelationshipLimits(lock);
  const sourceRows = contractSources.filter(
    (row) => row.path === RICH_RELATIONSHIP_SOURCE_PATH
  );
  invariant(
    sourceRows.length === 1,
    `consumer lock must bind exactly one ${RICH_RELATIONSHIP_SOURCE_PATH}`
  );
  const runtime = jsonObject(
    semanticValidation.rich_relationship_runtime,
    'build receipt rich relationship runtime validation'
  );
  invariant(
    runtime.status === 'passed',
    'build receipt rich relationship runtime validation did not pass'
  );
  const rows = nonNegativeSafeInteger(
    runtime.rows,
    'build receipt rich relationship runtime rows'
  );
  const chunks = nonNegativeSafeInteger(
    runtime.chunks,
    'build receipt rich relationship runtime chunks'
  );
  const routes = positiveSafeInteger(
    runtime.routes,
    'build receipt rich relationship runtime routes'
  );
  const buckets = positiveSafeInteger(
    runtime.buckets,
    'build receipt rich relationship runtime buckets'
  );
  invariant(
    rows === descriptorCounts.relationships,
    'build receipt rich relationship runtime rows differ from descriptor relationships'
  );
  invariant(
    Array.isArray(runtime.default_planes) &&
      runtime.default_planes.length > 0 &&
      runtime.default_planes.length <= limits.maximum_rich_relationship_planes &&
      runtime.default_planes.every(
        (value) => typeof value === 'string' && value.length > 0
      ) &&
      new Set(runtime.default_planes).size === runtime.default_planes.length,
    'build receipt rich relationship runtime default planes are malformed'
  );
  const validation = jsonObject(
    runtime.consumer_limits,
    'build receipt rich relationship consumer-limit validation'
  );
  invariant(
    validation.status === 'passed',
    'build receipt rich relationship consumer-limit validation did not pass'
  );
  const consumer = jsonObject(
    validation.consumer,
    'build receipt rich relationship consumer identity'
  );
  invariant(
    consumer.version === lock.consumer.version &&
      consumer.commit_sha === lock.consumer.commit_sha &&
      consumer.large_corpus_source_sha256 === sourceRows[0].sha256,
    'build receipt rich relationship consumer identity differs from the pinned source'
  );
  const receiptLimits = jsonObject(
    validation.limits,
    'build receipt rich relationship limits'
  );
  invariant(
    JSON.stringify(Object.keys(receiptLimits).sort(compareCodePoints)) ===
      JSON.stringify([...RICH_RELATIONSHIP_LIMIT_NAMES].sort(compareCodePoints)),
    'build receipt rich relationship limit names differ from the pinned consumer lock'
  );
  for (const name of RICH_RELATIONSHIP_LIMIT_NAMES) {
    invariant(
      receiptLimits[name] === limits[name],
      `build receipt rich relationship limit ${name} differs from the pinned consumer lock`
    );
  }
  const maxima = jsonObject(
    validation.maxima,
    'build receipt rich relationship maxima'
  );
  for (const name of RICH_RELATIONSHIP_MAXIMUM_NAMES) {
    nonNegativeSafeInteger(
      maxima[name],
      `build receipt rich relationship maximum ${name}`
    );
  }
  invariant(maxima.total_rows === rows, 'build receipt runtime row maximum does not reconcile');
  invariant(maxima.total_chunks === chunks, 'build receipt runtime chunk maximum does not reconcile');
  const cachePolicy = jsonObject(
    validation.cache_policy,
    'build receipt rich relationship cache policy'
  );
  invariant(
    cachePolicy.maximum_cached_chunks ===
      limits.maximum_rich_relationship_cached_chunks &&
      cachePolicy.interpretation ===
        'consumer eviction ceiling, not a producer route-chunk ceiling',
    'build receipt rich relationship cache policy differs from the pinned consumer'
  );
  return {
    limits,
    source: sourceRows[0],
    runtime: {
      rows,
      chunks,
      routes,
      buckets,
      default_planes: [...runtime.default_planes]
    },
    validation,
    maxima
  };
}

async function verifySemanticProfile(repositoryRoot, consumerProfile) {
  jsonObject(consumerProfile, 'consumer semantic profile lock');
  const sourceRelease = jsonObject(
    consumerProfile.source_release,
    'consumer semantic profile source release'
  );
  invariant(
    sourceRelease.repository ===
      'https://github.com/chris-page-gov/okf-explorer' &&
      sourceRelease.version === '0.6.0' &&
      sourceRelease.tag === 'v0.6.0' &&
      sourceRelease.tag_object ===
        'd256a74419c2593c2bf2f3f5749c606fad5daf9d' &&
      sourceRelease.commit ===
        '4bb7b92a64b7ba69bde9b1e86786217338cd166d' &&
      sourceRelease.git_tree ===
        'd26ae9a818041ff74c469e653ec714632ddbfc2a',
    'Bundle Wiki v1 source release differs from its frozen v0.6.0 identity'
  );
  invariant(
    consumerProfile.profile ===
      'https://chris-page-gov.github.io/okf-explorer/profile/bundle-wiki/v1/' &&
      consumerProfile.files === 16 &&
      consumerProfile.local_vendor_lock_sha256 ===
        '979af714974abb093ac9d4b1b7e289597c61d33c24bb6959d9914c2f74dc6a09' &&
      consumerProfile.aggregate_identity_sha256 ===
        '854d1853b71ec8bda3424924f0f0985fe24aa7bca4c180d15f359fe259ef4c7e',
    'Bundle Wiki v1 lock differs from its frozen v0.6.0 bytes'
  );
  const vendorRelative = safeRelativePath(consumerProfile.local_vendor_lock, 'local semantic profile vendor lock');
  const vendorPath = resolveUnder(repositoryRoot, vendorRelative);
  const { bytes, value: vendor } = await readJson(vendorPath, 'local semantic profile vendor lock');
  invariant(sha256(bytes) === consumerProfile.local_vendor_lock_sha256, 'local semantic profile vendor-lock digest differs from consumer lock');
  invariant(vendor.schema === 'okf-profile-vendor-lock.v1', 'local semantic profile vendor-lock schema is not supported');
  invariant(vendor.profile === consumerProfile.profile, 'local semantic profile IRI differs from consumer lock');
  invariant(
    JSON.stringify(canonicalValue(vendor.release)) ===
      JSON.stringify(canonicalValue(sourceRelease)),
    'local semantic profile release differs from its independent source-release lock'
  );
  invariant(vendor.release?.git_tree === consumerProfile.git_tree, 'local semantic profile git tree differs from consumer lock');
  invariant(Array.isArray(vendor.files) && vendor.files.length === vendor.file_count, 'local semantic profile file count is inconsistent');
  invariant(vendor.file_count === consumerProfile.files, 'local semantic profile file count differs from consumer lock');
  const vendorSuffix = '.vendor-lock.json';
  invariant(path.basename(vendorPath).endsWith(vendorSuffix), 'semantic profile vendor-lock filename must end with .vendor-lock.json');
  const profileRoot = path.join(
    path.dirname(vendorPath),
    path.basename(vendorPath).slice(0, -vendorSuffix.length)
  );
  await requireRealDirectory(profileRoot, 'local semantic profile material directory');
  const identityLines = [];
  const seen = new Set();
  for (const [index, row] of vendor.files.entries()) {
    jsonObject(row, `semantic profile material ${index}`);
    const relative = safeRelativePath(row.path, `semantic profile material ${index} path`);
    invariant(!seen.has(relative), `semantic profile material is duplicated: ${relative}`);
    invariant(Number.isSafeInteger(row.bytes) && row.bytes > 0, `semantic profile material has invalid bytes: ${relative}`);
    invariant(SHA256_PATTERN.test(row.sha256), `semantic profile material has invalid digest: ${relative}`);
    seen.add(relative);
    const material = await readRegularFile(resolveUnder(profileRoot, relative), `semantic profile material ${relative}`);
    invariant(material.length === row.bytes, `semantic profile material byte count mismatch: ${relative}`);
    invariant(sha256(material) === row.sha256, `semantic profile material digest mismatch: ${relative}`);
    identityLines.push(`${relative}\t${row.bytes}\t${row.sha256}\n`);
  }
  const aggregate = sha256(Buffer.from(identityLines.join(''), 'utf8'));
  invariant(vendor.identity?.algorithm === 'sha256', 'semantic profile identity algorithm is not supported');
  invariant(vendor.identity?.sha256 === aggregate, 'semantic profile vendor-lock aggregate is invalid');
  invariant(consumerProfile.aggregate_identity_sha256 === aggregate, 'semantic profile aggregate differs from consumer lock');
  return {
    profile: consumerProfile.profile,
    source_release: sourceRelease,
    git_tree: consumerProfile.git_tree,
    files: vendor.file_count,
    vendor_lock_sha256: sha256(bytes),
    aggregate_identity_sha256: aggregate
  };
}

async function verifyPredicateRegistryProfile(repositoryRoot, contract, consumer) {
  jsonObject(contract, 'consumer predicate-registry profile lock');
  invariant(
    JSON.stringify(contract.supported_schemas) === JSON.stringify([
      'okf-predicate-registry.v1',
      'okf-predicate-registry.v2'
    ]) &&
      contract.required_projection_schema === 'okf-predicate-registry.v2' &&
      contract.profile ===
        'https://chris-page-gov.github.io/okf-explorer/profile/predicate-registry/v2/',
    'predicate-registry v2 capability contract is unsupported'
  );
  const sourceRelease = jsonObject(
    contract.source_release,
    'predicate-registry v2 source release'
  );
  invariant(
      sourceRelease.repository ===
      'https://github.com/chris-page-gov/okf-explorer' &&
      sourceRelease.version === consumer.version &&
      sourceRelease.tag === consumer.release_tag &&
      sourceRelease.annotated_tag_object_sha ===
        consumer.annotated_tag_object_sha &&
      sourceRelease.commit_sha === consumer.commit_sha &&
      sourceRelease.immutable_release_id === consumer.immutable_release.id &&
      sourceRelease.published_at === consumer.immutable_release.published_at,
    'predicate-registry v2 source release differs from Explorer v0.6.1'
  );

  const lockReference = jsonObject(
    contract.profile_lock,
    'predicate-registry v2 profile-lock reference'
  );
  const lockRelative = safeRelativePath(
    lockReference.local_path,
    'predicate-registry v2 local profile-lock path'
  );
  invariant(
    lockReference.url ===
      'https://chris-page-gov.github.io/okf-explorer/profile/predicate-registry/v2.lock.json' &&
      lockReference.bytes === 744 &&
      lockReference.sha256 ===
        '3d1f7cdbb423628f3938e5aef299ae09013f56be515ff2155475c5325ffd0110' &&
      lockReference.identity_sha256 ===
        '75e444a35fdfe28fc111b6f0490cb8a0d569d20c1e4b62410174ead2608d86c6',
    'predicate-registry v2 public profile-lock identity differs'
  );
  const lockPath = resolveUnder(repositoryRoot, lockRelative);
  invariant(
    path.basename(lockPath) === 'v2.lock.json',
    'predicate-registry v2 local profile-lock filename differs'
  );
  const { bytes: lockBytes, value: lock } = await readJson(
    lockPath,
    'predicate-registry v2 local profile lock'
  );
  invariant(
    lockBytes.length === lockReference.bytes &&
      sha256(lockBytes) === lockReference.sha256,
    'predicate-registry v2 local and public profile-lock bytes differ'
  );
  invariant(
    lock.schema === 'okf-profile-extension-lock.v1' &&
      lock.profile === contract.profile &&
      lock.file_count === 2 &&
      Array.isArray(lock.files) &&
      lock.files.length === 2 &&
      lock.identity?.algorithm === 'sha256',
    'predicate-registry v2 local profile lock is malformed'
  );
  const profileRoot = path.join(path.dirname(lockPath), 'v2');
  await requireRealDirectory(
    profileRoot,
    'predicate-registry v2 local profile material directory'
  );
  const expectedPaths = ['index.md', 'predicate-registry.schema.json'];
  const identityLines = [];
  for (const [index, expectedPath] of expectedPaths.entries()) {
    const row = jsonObject(
      lock.files[index],
      `predicate-registry v2 profile material ${index}`
    );
    invariant(
      row.path === expectedPath &&
        Number.isSafeInteger(row.bytes) &&
        row.bytes > 0 &&
        SHA256_PATTERN.test(row.sha256),
      `predicate-registry v2 profile material ${index} is invalid`
    );
    const material = await readRegularFile(
      resolveUnder(profileRoot, expectedPath),
      `predicate-registry v2 profile material ${expectedPath}`
    );
    invariant(
      material.length === row.bytes && sha256(material) === row.sha256,
      `predicate-registry v2 profile material differs: ${expectedPath}`
    );
    identityLines.push(`${row.path}\t${row.bytes}\t${row.sha256}\n`);
  }
  const aggregate = sha256(Buffer.from(identityLines.join(''), 'utf8'));
  invariant(
    lock.identity.sha256 === aggregate &&
      lockReference.identity_sha256 === aggregate,
    'predicate-registry v2 aggregate identity differs'
  );

  const schemaReference = jsonObject(
    contract.schema,
    'predicate-registry v2 schema reference'
  );
  const schemaRelative = safeRelativePath(
    schemaReference.local_path,
    'predicate-registry v2 local schema path'
  );
  const schemaRow = lock.files[1];
  invariant(
    schemaReference.url === `${contract.profile}predicate-registry.schema.json` &&
      schemaReference.bytes === schemaRow.bytes &&
      schemaReference.sha256 === schemaRow.sha256 &&
      resolveUnder(repositoryRoot, schemaRelative) ===
        resolveUnder(profileRoot, schemaRow.path),
    'predicate-registry v2 schema reference differs from the profile lock'
  );
  const { value: schema } = await readJson(
    resolveUnder(repositoryRoot, schemaRelative),
    'predicate-registry v2 schema'
  );
  invariant(
    schema.$id === schemaReference.url &&
      schema.properties?.schema?.const === 'okf-predicate-registry.v2' &&
      schema.properties?.profile?.const === contract.profile,
    'predicate-registry v2 schema identity is unsupported'
  );
  return {
    profile: contract.profile,
    required_projection_schema: contract.required_projection_schema,
    supported_schemas: [...contract.supported_schemas],
    source_release: sourceRelease,
    profile_lock: {
      url: lockReference.url,
      bytes: lockBytes.length,
      sha256: sha256(lockBytes),
      identity_sha256: aggregate
    },
    schema: {
      url: schemaReference.url,
      bytes: schemaReference.bytes,
      sha256: schemaReference.sha256
    }
  };
}

async function locatePackage(appPackagePath, packageName, expectedVersion, explorerRoot) {
  const resolver = createRequire(appPackagePath);
  const entry = resolver.resolve(packageName);
  const realEntry = await realpath(entry);
  const realExplorer = await realpath(explorerRoot);
  invariant(realEntry.startsWith(`${realExplorer}${path.sep}`), `${packageName} resolved outside the supplied Explorer checkout`);
  let directory = path.dirname(realEntry);
  let packagePath = null;
  for (;;) {
    const candidate = path.join(directory, 'package.json');
    try {
      const parsed = JSON.parse((await readFile(candidate)).toString('utf8'));
      if (parsed.name === packageName) {
        packagePath = candidate;
        invariant(parsed.version === expectedVersion, `${packageName} ${parsed.version} does not match required ${expectedVersion}`);
        break;
      }
    } catch (error) {
      if (error?.code !== 'ENOENT' && !(error instanceof SyntaxError)) throw error;
    }
    const parent = path.dirname(directory);
    invariant(parent !== directory && parent.startsWith(realExplorer), `could not locate ${packageName} package metadata inside Explorer checkout`);
    directory = parent;
  }
  return {
    name: packageName,
    version: expectedVersion,
    entry,
    entry_label: path.relative(realExplorer, realEntry).split(path.sep).join('/'),
    package_json_sha256: sha256(await readRegularFile(packagePath, `${packageName} package metadata`))
  };
}

async function verifyToolchain(explorerRoot) {
  const appPackagePath = path.join(explorerRoot, 'apps', 'okf-explorer', 'package.json');
  const appPackage = JSON.parse((await readRegularFile(appPackagePath, 'Explorer application package metadata')).toString('utf8'));
  invariant(appPackage.name === '@okf/explorer' && appPackage.version === '0.6.1', 'Explorer application package identity is not @okf/explorer 0.6.1');
  const [playwright, axe] = await Promise.all([
    locatePackage(appPackagePath, '@playwright/test', EXPECTED_PLAYWRIGHT_VERSION, explorerRoot),
    locatePackage(appPackagePath, '@axe-core/playwright', EXPECTED_AXE_VERSION, explorerRoot)
  ]);
  return { appPackagePath, playwright, axe };
}

async function performPreflight(options) {
  const repositoryRoot = await requireRealDirectory(options.repositoryRoot, 'candidate repository');
  const bundleRoot = await requireRealDirectory(options.bundleRoot, 'candidate bundle');
  const explorerRoot = await requireRealDirectory(options.explorerCheckout, 'Explorer checkout');
  invariant(bundleRoot === path.join(repositoryRoot, 'bundle'), 'candidate bundle must be the repository bundle directory');
  invariant(!options.output.startsWith(`${bundleRoot}${path.sep}`), 'evidence output must not be written inside the checksummed bundle');
  try {
    await lstat(options.output);
    throw new Error(`evidence output already exists: ${options.output}`);
  } catch (error) {
    if (error?.code !== 'ENOENT') throw error;
  }
  const candidateGit = await verifyGitIdentity(repositoryRoot, options.candidateCommit, 'candidate repository');
  const checksums = await verifyBundleChecksums(bundleRoot, options.releaseRoot);
  const lockPath = path.join(repositoryRoot, 'contracts', 'okf-explorer.consumer-lock.json');
  const [{ bytes: lockBytes, value: lock }, { value: buildReceipt }, { value: descriptor }] = await Promise.all([
    readJson(lockPath, 'Explorer consumer lock'),
    readJson(path.join(bundleRoot, 'build-receipt.json'), 'bundle build receipt'),
    readJson(path.join(bundleRoot, 'okf-explorer.json'), 'bundle descriptor')
  ]);
  invariant(lock.schema === 'okf-explorer-consumer-lock.v1', 'Explorer consumer lock schema is not supported');
  const consumer = jsonObject(lock.consumer, 'Explorer consumer identity');
  invariant(consumer.name === '@okf/explorer', 'Explorer consumer package identity differs from @okf/explorer');
  invariant(
    consumer.version === '0.6.1' &&
      consumer.release_tag === 'v0.6.1' &&
      COMMIT_PATTERN.test(consumer.commit_sha) &&
      COMMIT_PATTERN.test(consumer.annotated_tag_object_sha),
    'Explorer consumer lock must pin the exact v0.6.1 release identities'
  );
  const immutableRelease = jsonObject(
    consumer.immutable_release,
    'Explorer immutable release identity'
  );
  invariant(
    Number.isSafeInteger(immutableRelease.id) &&
      immutableRelease.id > 0 &&
      immutableRelease.immutable === true &&
      typeof immutableRelease.published_at === 'string' &&
      immutableRelease.published_at.length > 0 &&
      immutableRelease.url ===
        'https://github.com/chris-page-gov/okf-explorer/releases/tag/v0.6.1',
    'Explorer immutable release identity differs from v0.6.1'
  );
  invariant(lock.compatibility_window?.policy === 'exact-version-only', 'Explorer compatibility window is not exact-version-only');
  invariant(lock.compatibility_window?.minimum_version === '0.6.1' && lock.compatibility_window?.maximum_version === '0.6.1', 'Explorer compatibility window differs from v0.6.1');
  invariant(lock.runtime_harness?.browser === 'chromium', 'Explorer runtime harness is not locked to Chromium');
  const explorerGit = await verifyGitIdentity(explorerRoot, consumer.commit_sha, 'Explorer checkout');
  const [contractSources, explorerBuild, semanticProfile, predicateRegistry, toolchain] = await Promise.all([
    verifyContractSources(explorerRoot, consumer.contract_sources),
    verifyExplorerBuild(explorerRoot, jsonObject(consumer.executable_build, 'consumer executable build lock')),
    verifySemanticProfile(repositoryRoot, jsonObject(consumer.semantic_profile, 'consumer semantic profile lock')),
    verifyPredicateRegistryProfile(repositoryRoot, jsonObject(consumer.predicate_registry, 'consumer predicate-registry profile lock'), consumer),
    verifyToolchain(explorerRoot)
  ]);
  const acceptanceExecutableMaterials =
    verifyAcceptanceExecutableMaterials(consumer, contractSources);
  invariant(Array.isArray(buildReceipt.governed_inputs), 'bundle build receipt has no governed_inputs array');
  const lockRows = buildReceipt.governed_inputs.filter((row) => row.path === 'contracts/okf-explorer.consumer-lock.json');
  invariant(lockRows.length === 1, 'bundle build receipt must bind exactly one Explorer consumer lock');
  invariant(lockRows[0].bytes === lockBytes.length, 'bundle build receipt consumer-lock byte count differs');
  invariant(lockRows[0].sha256 === sha256(lockBytes), 'bundle build receipt consumer-lock digest differs');
  invariant(buildReceipt.schema === 'okf-hmlr-build-receipt.v1', 'bundle build receipt schema is not okf-hmlr-build-receipt.v1');
  invariant(descriptor.schema === 'okf-explorer-large-corpus.v1' && descriptor.kind === 'okf-large-corpus', 'bundle descriptor is not the required large-corpus contract');
  invariant(descriptor.version === '0.3.0', 'G6 candidate runner requires the Land Registry v0.3.0 descriptor');
  invariant(descriptor.status === 'ai-generated-proof-of-concept', 'bundle descriptor status is not ai-generated-proof-of-concept');
  invariant(buildReceipt.status === 'ai-generated-proof-of-concept', 'bundle build receipt status is not ai-generated-proof-of-concept');
  invariant(descriptor.publication_state === 'digest-bound-external-evidence', 'bundle descriptor publication_state is not digest-bound-external-evidence');
  invariant(buildReceipt.publication_state === 'digest-bound-external-evidence', 'bundle build receipt publication_state is not digest-bound-external-evidence');
  invariant(descriptor.release_at === null, 'G6 candidate runner requires an unreleased descriptor with release_at null');
  invariant(buildReceipt.release_at === null, 'G6 candidate runner requires a build receipt with release_at null');
  invariant(buildReceipt.network_access === false, 'bundle build receipt must record network_access false');
  const descriptorCounts = jsonObject(descriptor.counts, 'bundle descriptor counts');
  invariant(Number.isSafeInteger(descriptorCounts.records) && descriptorCounts.records >= 0, 'bundle descriptor record count is invalid');
  invariant(Number.isSafeInteger(descriptorCounts.relationships) && descriptorCounts.relationships >= 0, 'bundle descriptor relationship count is invalid');
  invariant(buildReceipt.record_count === descriptorCounts.records, 'bundle build receipt record count differs from the descriptor');
  const advertisedRichRuntime = await resolveAdvertisedRichRelationshipRuntime(
    bundleRoot,
    descriptor
  );
  const semanticValidation = jsonObject(buildReceipt.semantic_assertion_validation, 'semantic assertion validation');
  const semanticCounts = jsonObject(semanticValidation.counts, 'semantic assertion validation counts');
  invariant(semanticValidation.status === 'conformant', 'semantic assertion validation is not conformant');
  invariant(semanticCounts.validation_failures === 0, 'semantic assertion validation records failures');
  invariant(semanticCounts.semantic_assertions_validated === descriptorCounts.relationships, 'semantic assertion validation count differs from the descriptor');
  const richRuntimeValidation = validateRichRuntimeBuildReceipt(
    lock,
    contractSources,
    semanticValidation,
    descriptorCounts
  );
  const receiptChecksum = checksums.rows.find((row) => row.path === 'build-receipt.json');
  const descriptorChecksum = checksums.rows.find((row) => row.path === 'okf-explorer.json');
  invariant(receiptChecksum && descriptorChecksum, 'bundle checksum manifest must cover build-receipt.json and okf-explorer.json');
  const evidence = {
    candidate: {
      candidate_commit_sha: candidateGit.commit_sha,
      source_dirty: candidateGit.source_dirty,
      release_root_sha256: checksums.release_root_sha256,
      checksums_sha256: checksums.checksums_sha256,
      file_count: checksums.file_count,
      raw_bytes: checksums.raw_bytes,
      descriptor: {
        id: descriptor['@id'] ?? null,
        schema: descriptor.schema,
        kind: descriptor.kind,
        version: descriptor.version,
        snapshot: descriptor.snapshot ?? null,
        status: descriptor.status,
        publication_state: descriptor.publication_state,
        release_at: descriptor.release_at,
        data_manifest: advertisedRichRuntime.data_manifest.reference,
        relationship_runtime: advertisedRichRuntime.relationship_runtime.reference
      },
      build_receipt: {
        schema: buildReceipt.schema,
        status: buildReceipt.status,
        publication_state: buildReceipt.publication_state,
        release_at: buildReceipt.release_at,
        network_access: buildReceipt.network_access,
        record_count: buildReceipt.record_count,
        semantic_validation_status: semanticValidation.status,
        semantic_assertions_validated: semanticCounts.semantic_assertions_validated,
        semantic_validation_failures: semanticCounts.validation_failures,
        rich_relationship_runtime: {
          status: richRuntimeValidation.validation.status,
          consumer: richRuntimeValidation.validation.consumer,
          rows: richRuntimeValidation.runtime.rows,
          chunks: richRuntimeValidation.runtime.chunks,
          routes: richRuntimeValidation.runtime.routes,
          buckets: richRuntimeValidation.runtime.buckets,
          default_planes: richRuntimeValidation.runtime.default_planes,
          limits: richRuntimeValidation.limits,
          maxima: richRuntimeValidation.maxima,
          cache_policy: richRuntimeValidation.validation.cache_policy
        }
      },
      build_receipt_sha256: receiptChecksum.sha256,
      descriptor_sha256: descriptorChecksum.sha256
    },
    consumer: {
      package: consumer.name,
      version: consumer.version,
      release_tag: consumer.release_tag,
      annotated_tag_object_sha: consumer.annotated_tag_object_sha,
      immutable_release: immutableRelease,
      source_commit: explorerGit.commit_sha,
      source_dirty: explorerGit.source_dirty,
      consumer_lock_sha256: sha256(lockBytes),
      contract_sources: contractSources,
      acceptance_executable_materials: acceptanceExecutableMaterials,
      executable_build: explorerBuild,
      semantic_profile: semanticProfile,
      predicate_registry: predicateRegistry
    },
    toolchain: {
      playwright: {
        name: toolchain.playwright.name,
        version: toolchain.playwright.version,
        entry_label: toolchain.playwright.entry_label,
        package_json_sha256: toolchain.playwright.package_json_sha256
      },
      axe_core_playwright: {
        name: toolchain.axe.name,
        version: toolchain.axe.version,
        entry_label: toolchain.axe.entry_label,
        package_json_sha256: toolchain.axe.package_json_sha256
      }
    }
  };
  return {
    evidence,
    internal: {
      repositoryRoot,
      bundleRoot,
      explorerRoot,
      lock,
      checksums,
      toolchain,
      descriptor,
      advertisedRichRuntime,
      richRuntimeValidation
    }
  };
}

function contentType(filePath) {
  return {
    '.css': 'text/css; charset=utf-8',
    '.csv': 'text/csv; charset=utf-8',
    '.gz': 'application/json',
    '.html': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.jsonld': 'application/ld+json; charset=utf-8',
    '.md': 'text/markdown; charset=utf-8',
    '.svg': 'image/svg+xml',
    '.ttl': 'text/turtle; charset=utf-8',
    '.webmanifest': 'application/manifest+json; charset=utf-8',
    '.yamlld': 'application/ld+yaml; charset=utf-8'
  }[path.extname(filePath).toLowerCase()] || 'application/octet-stream';
}

function staticServer(bundleRoot, requestLog) {
  let origin = null;
  const server = createServer(async (request, response) => {
    const started = process.hrtime.bigint();
    const observationId = String(request.headers['x-okf-observation'] || 'unlabelled');
    const phase = String(request.headers['x-okf-phase'] || 'unlabelled');
    let requestUrl;
    try {
      requestUrl = new URL(request.url || '/', origin);
      invariant(['GET', 'HEAD'].includes(request.method || ''), 'method is not accepted');
      let pathname = decodeURIComponent(requestUrl.pathname);
      if (pathname.startsWith('/okf-LandRegistry/')) pathname = `/${pathname.slice('/okf-LandRegistry/'.length)}`;
      let relative = pathname.replace(/^\/+/, '');
      if (!relative) relative = 'index.html';
      let filePath;
      let statusCode = 200;
      try {
        filePath = resolveUnder(bundleRoot, relative, 'served bundle path');
        const metadata = await stat(filePath);
        invariant(metadata.isFile(), 'served path is not a regular file');
      } catch {
        filePath = path.join(bundleRoot, '404.html');
        statusCode = 404;
      }
      const raw = await readRegularFile(filePath, `served file ${relative}`);
      const extension = path.extname(filePath).toLowerCase();
      const alreadyGzipped = extension === '.gz';
      const acceptsGzip = /(?:^|,)\s*gzip\s*(?:,|$)/i.test(String(request.headers['accept-encoding'] || ''));
      const compress = alreadyGzipped || (acceptsGzip && COMPRESSIBLE_EXTENSIONS.has(extension));
      const body = alreadyGzipped ? raw : compress ? gzipSync(raw, { level: 9, mtime: 0 }) : raw;
      const headers = {
        'cache-control': 'no-store',
        'content-length': String(body.length),
        'content-type': contentType(filePath),
        'x-content-type-options': 'nosniff',
        'x-okf-raw-bytes': String(raw.length)
      };
      if (compress) headers['content-encoding'] = 'gzip';
      response.writeHead(statusCode, headers);
      response.end(request.method === 'HEAD' ? undefined : body);
      requestLog.push({
        sequence: requestLog.length + 1,
        observation_id: observationId,
        phase,
        method: request.method,
        path: requestUrl.pathname,
        status: statusCode,
        raw_bytes: raw.length,
        transferred_body_bytes: request.method === 'HEAD' ? 0 : body.length,
        content_encoding: compress ? 'gzip' : 'identity',
        elapsed_microseconds: Number((process.hrtime.bigint() - started) / 1000n)
      });
    } catch (error) {
      const body = Buffer.from('Not found\n', 'utf8');
      response.writeHead(404, {
        'cache-control': 'no-store',
        'content-length': String(body.length),
        'content-type': 'text/plain; charset=utf-8',
        'x-content-type-options': 'nosniff'
      });
      response.end(body);
      requestLog.push({
        sequence: requestLog.length + 1,
        observation_id: observationId,
        phase,
        method: request.method || null,
        path: requestUrl?.pathname || null,
        status: 404,
        raw_bytes: body.length,
        transferred_body_bytes: body.length,
        content_encoding: 'identity',
        error: boundedTextEvidence(error instanceof Error ? error.message : String(error)),
        elapsed_microseconds: Number((process.hrtime.bigint() - started) / 1000n)
      });
    }
  });
  return {
    server,
    setOrigin(value) {
      origin = value;
    }
  };
}

function extractHttpUrls(text) {
  const matches = text.match(/https?:\/\/[^\s"'<>\\]+/giu) || [];
  return [...new Set(matches.map((value) => value.replace(/[),.;\]}]+$/u, '')))];
}

function htmlAttributes(tag) {
  const attributes = {};
  const pattern = /([^\s"'<>/=]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+))/gu;
  for (const match of tag.matchAll(pattern)) {
    const name = match[1].toLowerCase();
    invariant(!Object.hasOwn(attributes, name), `HTML tag repeats attribute ${name}`);
    attributes[name] = match[2] ?? match[3] ?? match[4] ?? '';
  }
  return attributes;
}

function cspDirectives(policy) {
  const directives = {};
  for (const segment of policy.split(';')) {
    const tokens = segment.trim().split(/\s+/u).filter(Boolean);
    if (!tokens.length) continue;
    const [name, ...values] = tokens;
    invariant(!directives[name.toLowerCase()], `CSP repeats directive ${name}`);
    directives[name.toLowerCase()] = values;
  }
  return directives;
}

function cspBaseline(directives) {
  const allTokens = Object.values(directives).flat();
  return {
    default_src_self: directives['default-src']?.includes("'self'") === true,
    object_src_none: JSON.stringify(directives['object-src']) === JSON.stringify(["'none'"]),
    base_uri_none: JSON.stringify(directives['base-uri']) === JSON.stringify(["'none'"]),
    unsafe_inline_absent: !allTokens.includes("'unsafe-inline'"),
    unsafe_eval_absent: !allTokens.includes("'unsafe-eval'"),
    wildcard_absent: !allTokens.includes('*')
  };
}

function boundedGunzip(bytes, maximumOutputLength, label = 'gzip member') {
  invariant(
    Number.isSafeInteger(maximumOutputLength) && maximumOutputLength > 0,
    `${label} output ceiling must be a positive safe integer`
  );
  try {
    return gunzipSync(bytes, { maxOutputLength: maximumOutputLength });
  } catch (error) {
    throw new Error(`${label} is invalid or exceeds ${maximumOutputLength} decompressed bytes: ${error.message}`);
  }
}

async function staticSecurityScan(bundleRoot, checksumRows, maximumDecompressedBytes) {
  invariant(
    Number.isSafeInteger(maximumDecompressedBytes) && maximumDecompressedBytes > 0,
    'static security gzip ceiling must be a positive safe integer'
  );
  const findings = [];
  const scannedUrls = new Set();
  const cspObservations = [];
  let textFiles = 0;
  let decompressedMembers = 0;
  for (const row of checksumRows) {
    const extension = path.extname(row.path).toLowerCase();
    if (!TEXT_EXTENSIONS.has(extension) && extension !== '.gz') continue;
    let bytes = await readRegularFile(resolveUnder(bundleRoot, row.path), `static security input ${row.path}`);
    if (extension === '.gz') {
      try {
        bytes = boundedGunzip(bytes, maximumDecompressedBytes, `static security gzip member ${row.path}`);
        decompressedMembers += 1;
      } catch (error) {
        findings.push({ code: 'STATIC-GZIP-INVALID-OR-OVERSIZE', severity: 'blocker', path: row.path, maximum_decompressed_bytes: maximumDecompressedBytes, detail: boundedTextEvidence(error.message) });
        continue;
      }
    }
    const text = bytes.toString('utf8');
    textFiles += 1;
    const secretPatterns = [
      ['private-key', /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/iu],
      ['github-token', /\bgh[pousr]_[A-Za-z0-9]{30,}\b/u],
      ['aws-access-key', /\bAKIA[0-9A-Z]{16}\b/u]
    ];
    for (const [kind, pattern] of secretPatterns) {
      if (pattern.test(text)) findings.push({ code: 'CREDENTIAL-MATERIAL', severity: 'blocker', path: row.path, kind });
    }
    for (const url of extractHttpUrls(text)) {
      scannedUrls.add(url);
      const reason = credentialUrlFinding(url);
      if (reason) findings.push({ code: 'CREDENTIAL-OR-SIGNED-URL', severity: 'blocker', path: row.path, reason, url: describedUrl(url) });
    }
    if (['.html', '.js', '.mjs'].includes(extension)) {
      const sinkPatterns = [
        ['javascript-url', /(?:href|src)\s*=\s*["']\s*javascript:/iu],
        ['inline-event-handler', /\son[a-z]+\s*=/iu],
        ['eval', /\beval\s*\(/u],
        ['function-constructor', /\bnew\s+Function\s*\(/u],
        ['document-write', /\bdocument\s*\.\s*write(?:ln)?\s*\(/u],
        ['inner-html-assignment', /\.innerHTML\s*=/u],
        ['outer-html-assignment', /\.outerHTML\s*=/u],
        ['insert-adjacent-html', /\.insertAdjacentHTML\s*\(/u]
      ];
      for (const [sink, pattern] of sinkPatterns) {
        if (pattern.test(text)) findings.push({ code: 'UNSAFE-DOM-SINK', severity: 'blocker', path: row.path, sink });
      }
    }
    if (extension === '.html') {
      const policies = [...text.matchAll(/<meta\b[^>]*>/giu)]
        .map((match) => htmlAttributes(match[0]))
        .filter((attributes) => (attributes['http-equiv'] || '').toLowerCase() === 'content-security-policy')
        .map((attributes) => attributes.content)
        .filter((policy) => typeof policy === 'string' && policy.length > 0);
      if (policies.length !== 1) {
        findings.push({ code: 'CSP-MISSING-OR-DUPLICATED', severity: 'blocker', path: row.path, policies: policies.length });
        cspObservations.push({ path: row.path, policies: policies.length, directives: null, baseline_complete: false });
      } else {
        let directives;
        try {
          directives = cspDirectives(policies[0]);
        } catch (error) {
          findings.push({ code: 'CSP-MALFORMED', severity: 'blocker', path: row.path, detail: boundedTextEvidence(error.message) });
          directives = {};
        }
        const baseline = cspBaseline(directives);
        const baselineComplete = Object.values(baseline).every(Boolean);
        cspObservations.push({
          path: row.path,
          policies: 1,
          policy_sha256: sha256(Buffer.from(policies[0], 'utf8')),
          directives,
          baseline,
          baseline_complete: baselineComplete
        });
        if (!baselineComplete) findings.push({ code: 'CSP-BASELINE-INCOMPLETE', severity: 'blocker', path: row.path, baseline });
      }
      const runtimeAttributes = [...text.matchAll(/<(?:script|img|iframe|source)\b[^>]*\bsrc=["']([^"']+)["']/giu)].map((match) => match[1]);
      const stylesheets = [...text.matchAll(/<link\b[^>]*\brel=["'][^"']*stylesheet[^"']*["'][^>]*\bhref=["']([^"']+)["']/giu)].map((match) => match[1]);
      for (const target of [...runtimeAttributes, ...stylesheets]) {
        if (/^https?:\/\//iu.test(target) || target.startsWith('//')) {
          findings.push({ code: 'EXTERNAL-RUNTIME-DEPENDENCY', severity: 'blocker', path: row.path, target: describedUrl(target.startsWith('//') ? `https:${target}` : target) });
        }
      }
    }
  }
  const requiredCspPaths = REQUIRED_ROUTES.map((route) => route === '/' ? 'index.html' : route.slice(1));
  for (const requiredPath of requiredCspPaths) {
    if (!cspObservations.some((row) => row.path === requiredPath)) {
      findings.push({ code: 'CSP-ROUTE-COVERAGE-MISSING', severity: 'blocker', path: requiredPath });
    }
  }
  return {
    files_scanned: textFiles,
    gzip_members_decompressed: decompressedMembers,
    maximum_gzip_output_bytes: maximumDecompressedBytes,
    http_urls_examined: scannedUrls.size,
    csp_observations: cspObservations.sort((left, right) => compareCodePoints(left.path, right.path)),
    findings: findings.sort((left, right) => compareCodePoints(JSON.stringify(left), JSON.stringify(right)))
  };
}

function gzipBytes(bytes) {
  return gzipSync(bytes, { level: 9, mtime: 0 }).length;
}

function requiredRichText(value, label) {
  invariant(
    typeof value === 'string' && value.length > 0 && value.trim() === value,
    `${label} must be non-empty text`
  );
  return value;
}

function requiredRichIri(value, label) {
  const iri = requiredRichText(value, label);
  invariant(ABSOLUTE_IRI_PATTERN.test(iri), `${label} must be an absolute IRI`);
  return iri;
}

function requiredRichRoute(value, label) {
  const route = requiredRichText(value, label);
  invariant(
    LOCAL_RELATIONSHIP_ROUTE_PATTERN.test(route),
    `${label} must be a safe local route`
  );
  return route;
}

function requiredRichHash(value, label) {
  const digest = requiredRichText(value, label).toLowerCase();
  invariant(SHA256_PATTERN.test(digest), `${label} must be a SHA-256 digest`);
  return digest;
}

function requiredRichHttpUrl(value, label) {
  invariant(typeof value === 'string' && value.length > 0, `${label} must be a URL`);
  invariant(value.trim() === value, `${label} must be canonical`);
  invariant(!/[^\x21-\x7e]/.test(value), `${label} contains non-ASCII or whitespace characters`);
  invariant(!/[\s"'<>\\^`{|}]/.test(value), `${label} contains unsafe delimiters`);
  invariant(!/%(?![0-9A-Fa-f]{2})/.test(value), `${label} contains a malformed escape`);
  invariant(
    /^https?:\/\/(?:\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?)(?::(?:[1-9]|[1-9][0-9]{1,3}|[1-5][0-9]{4}|6[0-4][0-9]{3}|65[0-4][0-9]{2}|655[0-2][0-9]|6553[0-5]))?(?:[/?#]|$)/i.test(value),
    `${label} must be a canonical credential-free HTTP(S) URL`
  );
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error(`${label} must be a canonical credential-free HTTP(S) URL`);
  }
  invariant(
    ['http:', 'https:'].includes(parsed.protocol) &&
      !parsed.username &&
      !parsed.password,
    `${label} must be a canonical credential-free HTTP(S) URL`
  );
  return value;
}

function requiredRichUnitNumber(value, label) {
  invariant(
    typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1,
    `${label} must be a finite number from 0 to 1`
  );
  return value;
}

function optionalRichText(source, field, label) {
  if (source[field] === undefined) return undefined;
  invariant(typeof source[field] === 'string', `${label} must be text`);
  return source[field];
}

function retainedTextUnits(value) {
  if (typeof value === 'string') return value.length;
  if (Array.isArray(value)) {
    return value.reduce((total, item) => total + retainedTextUnits(item), 0);
  }
  if (value && typeof value === 'object') {
    return Object.values(value).reduce(
      (total, item) => total + retainedTextUnits(item),
      0
    );
  }
  return 0;
}

function projectRichRelationshipEvidence(value, label) {
  const evidence = jsonObject(value, label);
  const projected = {
    '@id': requiredRichIri(evidence['@id'], `${label} id`),
    type: requiredRichText(evidence.type, `${label} type`),
    url: requiredRichHttpUrl(evidence.url, `${label} URL`),
    source_field: requiredRichText(evidence.source_field, `${label} source field`),
    source_value_sha256: requiredRichHash(
      evidence.source_value_sha256,
      `${label} source-value SHA-256`
    ),
    retrieved_at: requiredRichText(evidence.retrieved_at, `${label} retrieval time`)
  };
  if (evidence.resource !== undefined) {
    projected.resource = requiredRichHttpUrl(evidence.resource, `${label} resource`);
  }
  for (const field of ['normalization', 'rule_id']) {
    if (evidence[field] !== undefined) {
      projected[field] = requiredRichIri(evidence[field], `${label} ${field}`);
    }
  }
  for (const field of ['source_sha256', 'literal_sha256']) {
    if (evidence[field] !== undefined) {
      projected[field] = requiredRichHash(evidence[field], `${label} ${field}`);
    }
  }
  for (const field of [
    'source_artifact',
    'field_provenance',
    'source_value',
    'source_value_hash_canonicalization',
    'value',
    'rationale',
    'locator',
    'source_locator'
  ]) {
    const text = optionalRichText(evidence, field, `${label} ${field}`);
    if (text !== undefined) projected[field] = text;
  }
  return projected;
}

function projectRichRelationshipRow(value, plane, label) {
  const row = jsonObject(value, label);
  invariant(
    row.schema === 'okf-relationship-runtime-row.v1',
    `${label} schema is unsupported`
  );
  const id = requiredRichIri(row.id, `${label} id`);
  const assertionId = requiredRichIri(row.assertion_id, `${label} assertion id`);
  invariant(!/[^\x00-\x7f]/.test(id), `${label} assertion identity must be ASCII`);
  invariant(assertionId === id, `${label} assertion identity differs`);
  const source = requiredRichRoute(row.source, `${label} source`);
  const target = requiredRichRoute(row.target, `${label} target`);
  invariant(
    (row.source_route === undefined ||
      requiredRichRoute(row.source_route, `${label} source route`) === source) &&
      (row.target_route === undefined ||
        requiredRichRoute(row.target_route, `${label} target route`) === target),
    `${label} route aliases differ`
  );
  const predicate = requiredRichIri(row.predicate, `${label} predicate`);
  invariant(
    requiredRichIri(row.predicate_iri, `${label} predicate IRI`) === predicate,
    `${label} predicate aliases differ`
  );
  invariant(
    row.direction === 'source-to-target' &&
      row.active === plane.active &&
      requiredRichIri(row.plane, `${label} plane`) === plane.id,
    `${label} direction or plane binding differs`
  );
  const assertionStatus = requiredRichText(
    row.assertion_status,
    `${label} assertion status`
  );
  invariant(
    RICH_RELATIONSHIP_ASSERTION_STATUSES.has(assertionStatus),
    `${label} assertion status is unsupported`
  );
  const assertionScope = requiredRichText(
    row.assertion_scope,
    `${label} assertion scope`
  );
  invariant(
    RICH_RELATIONSHIP_ASSERTION_SCOPES.has(assertionScope),
    `${label} assertion scope is unsupported`
  );
  const authority = jsonObject(row.authority, `${label} authority`);
  const authorityClass = requiredRichText(
    authority.class,
    `${label} authority class`
  );
  invariant(
    RICH_RELATIONSHIP_AUTHORITY_CLASSES.has(authorityClass) &&
      Array.isArray(plane.authority_classes) &&
      plane.authority_classes.includes(authorityClass),
    `${label} authority is outside its declared plane`
  );
  const expectedAuthorityClass = assertionScope === 'synthetic-fixture'
    ? 'synthetic'
    : {
        official: 'official',
        normalized: 'derived',
        inferred: 'derived',
        'model-derived': 'model-assisted'
      }[assertionStatus];
  invariant(
    authorityClass === expectedAuthorityClass,
    `${label} authority conflicts with its assertion status and scope`
  );
  invariant(
    Array.isArray(row.evidence) &&
      row.evidence.length > 0 &&
      row.evidence.length <=
        EXPLORER_V061_RICH_RELATIONSHIP_LIMITS.maximum_rich_relationship_evidence_items,
    `${label} evidence is absent or exceeds the Explorer ceiling`
  );
  const projectedEvidence = row.evidence.map((item, index) =>
    projectRichRelationshipEvidence(item, `${label} evidence ${index}`)
  );
  const evidenceIds = projectedEvidence.map((item) => item['@id']);
  invariant(
    new Set(evidenceIds).size === evidenceIds.length,
    `${label} repeats an evidence identity`
  );
  const rights = jsonObject(row.rights, `${label} rights`);
  const reviewStatus =
    row.review_status === undefined
      ? undefined
      : requiredRichText(row.review_status, `${label} review status`);
  const projected = {
    schema: 'okf-relationship-runtime-row.v1',
    id,
    assertion_id: id,
    source,
    target,
    source_route: source,
    target_route: target,
    source_iri: requiredRichIri(row.source_iri, `${label} source IRI`),
    target_iri: requiredRichIri(row.target_iri, `${label} target IRI`),
    predicate,
    predicate_iri: predicate,
    kind: requiredRichText(row.kind, `${label} kind`),
    label: requiredRichText(row.label, `${label} label`),
    inverse_label: requiredRichText(row.inverse_label, `${label} inverse label`),
    direction: 'source-to-target',
    assertion_status: assertionStatus,
    assertion_scope: assertionScope,
    authority: {
      class: authorityClass,
      label: requiredRichText(authority.label, `${label} authority label`),
      source: requiredRichHttpUrl(authority.source, `${label} authority source`)
    },
    derivation: requiredRichIri(row.derivation, `${label} derivation`),
    observed_at: requiredRichText(row.observed_at, `${label} observation time`),
    evidence: projectedEvidence,
    rights: {
      source: requiredRichHttpUrl(rights.source, `${label} rights source`),
      assertion: requiredRichText(rights.assertion, `${label} rights assertion`)
    },
    plane: plane.id,
    lifecycle: plane.lifecycle,
    active: plane.active
  };
  if (reviewStatus !== undefined) projected.review_status = reviewStatus;
  let supportingAssertions = 0;
  if (assertionStatus === 'inferred') {
    projected.rule = requiredRichIri(row.rule, `${label} inference rule`);
    projected.derivation_activity = requiredRichIri(
      row.derivation_activity,
      `${label} derivation activity`
    );
    projected.confidence_score = requiredRichUnitNumber(
      row.confidence_score,
      `${label} confidence score`
    );
    invariant(
      Array.isArray(row.supporting_assertions) &&
        row.supporting_assertions.length > 0 &&
        row.supporting_assertions.length <=
          EXPLORER_V061_RICH_RELATIONSHIP_LIMITS.maximum_rich_relationship_supporting_assertions,
      `${label} inferred assertion has no supporting assertions`
    );
    projected.supporting_assertions = row.supporting_assertions.map((item, index) =>
      requiredRichIri(item, `${label} supporting assertion ${index}`)
    );
    invariant(
      new Set(projected.supporting_assertions).size ===
        projected.supporting_assertions.length,
      `${label} repeats a supporting assertion`
    );
    supportingAssertions = projected.supporting_assertions.length;
  }
  if (assertionStatus === 'model-derived') {
    projected.derivation_activity = requiredRichIri(
      row.derivation_activity,
      `${label} derivation activity`
    );
    projected.confidence_score = requiredRichUnitNumber(
      row.confidence_score,
      `${label} confidence score`
    );
    invariant(
      reviewStatus !== undefined,
      `${label} model-derived assertion requires review status`
    );
  }
  for (const field of ['stale_after', 'freshness', 'support_profile']) {
    const text = optionalRichText(row, field, `${label} ${field}`);
    if (text !== undefined) projected[field] = text;
  }
  if (row.confidence !== undefined) {
    invariant(
      typeof row.confidence === 'string' ||
        (typeof row.confidence === 'number' && Number.isFinite(row.confidence)),
      `${label} confidence is invalid`
    );
    projected.confidence = row.confidence;
  }
  for (const field of ['strength', 'count']) {
    if (row[field] !== undefined) {
      invariant(
        typeof row[field] === 'number' && Number.isFinite(row[field]),
        `${label} ${field} is invalid`
      );
      projected[field] = row[field];
    }
  }
  if (row.official_legal_classification !== undefined) {
    invariant(
      typeof row.official_legal_classification === 'boolean',
      `${label} official legal classification is invalid`
    );
    projected.official_legal_classification = row.official_legal_classification;
  }
  return {
    id,
    source,
    target,
    plane: plane.name,
    retained_text_units: retainedTextUnits(projected),
    evidence_items: projectedEvidence.length,
    supporting_assertions: supportingAssertions
  };
}

function runtimeLimitFinding(findings, code, measured, maximum, detail = {}) {
  if (measured > maximum) {
    findings.push({ code, severity: 'blocker', ...detail, measured, maximum });
  }
}

function reconcileObservedRuntimeMaxima(observed, receiptMaxima) {
  for (const name of RICH_RELATIONSHIP_MAXIMUM_NAMES) {
    invariant(
      observed[name] === receiptMaxima[name],
      `observed rich relationship maximum ${name} differs from the build receipt`
    );
  }
}

async function measureRichRelationshipRuntime(
  bundleRoot,
  lock,
  receiptValidation,
  advertisedRichRuntime
) {
  const limits = receiptValidation.limits;
  const findings = [];
  const advertised = jsonObject(
    advertisedRichRuntime,
    'advertised rich relationship resources'
  );
  const runtimeReference = exactRuntimeResourceReference(
    jsonObject(
      advertised.relationship_runtime,
      'advertised relationship-runtime resource'
    ).reference,
    'advertised relationship-runtime reference'
  );
  const { bytes: runtimeBytes, value: runtime } = await readBoundJsonResource(
    bundleRoot,
    runtimeReference,
    'advertised relationship runtime manifest'
  );
  invariant(
    runtime.schema === 'okf-rich-relationship-runtime-manifest.v1',
    'relationship runtime manifest schema is unsupported'
  );
  requiredRichIri(runtime['@id'], 'relationship runtime manifest id');
  exactSnapshot(runtime.snapshot, 'relationship runtime manifest snapshot');
  requiredRichText(runtime.generated_at, 'relationship runtime generation time');
  safeRelativeResourcePath(runtime.semantic_manifest, 'relationship runtime semantic-manifest path');
  safeRelativeResourcePath(runtime.assertion_contract, 'relationship runtime assertion-contract path');
  safeRelativeResourcePath(runtime.row_contract, 'relationship runtime row-contract path');
  requiredRichText(runtime.loading_policy, 'relationship runtime loading policy');
  invariant(Array.isArray(runtime.planes) && runtime.planes.length > 0, 'relationship runtime manifest has no planes');
  invariant(Array.isArray(runtime.default_planes) && runtime.default_planes.length > 0, 'relationship runtime manifest has no default planes');
  invariant(
    runtime.planes.length <= limits.maximum_rich_relationship_planes &&
      runtime.default_planes.length <= limits.maximum_rich_relationship_planes,
    'relationship runtime exceeds the Explorer plane ceiling'
  );
  runtime.default_planes.forEach((name, index) =>
    requiredRichText(name, `relationship runtime default plane ${index}`)
  );
  invariant(
    new Set(runtime.default_planes).size === runtime.default_planes.length,
    'relationship runtime repeats a default plane'
  );
  const planeNames = runtime.planes.map((plane, index) =>
    requiredRichText(jsonObject(plane, `relationship plane ${index}`).name, `relationship plane ${index} name`)
  );
  invariant(new Set(planeNames).size === planeNames.length, 'relationship runtime repeats a plane name');
  const planeIds = runtime.planes.map((plane, index) =>
    requiredRichIri(plane.id, `relationship plane ${index} id`)
  );
  invariant(new Set(planeIds).size === planeIds.length, 'relationship runtime repeats a plane id');
  const activeNames = runtime.planes
    .filter((plane) => plane.active === true)
    .map((plane) => plane.name);
  invariant(
    JSON.stringify(runtime.default_planes) === JSON.stringify(activeNames),
    'relationship runtime defaults differ from active planes'
  );
  invariant(
    JSON.stringify(runtime.default_planes) ===
      JSON.stringify(receiptValidation.runtime.default_planes),
    'relationship runtime defaults differ from the build receipt'
  );
  runtimeLimitFinding(
    findings,
    'RELATIONSHIP-PLANE-LIMIT',
    runtime.planes.length,
    limits.maximum_rich_relationship_planes
  );
  runtimeLimitFinding(
    findings,
    'RELATIONSHIP-RUNTIME-MANIFEST-BYTE-LIMIT',
    runtimeBytes.length,
    limits.maximum_json_bytes,
    { path: runtimeReference.path }
  );

  const runtimeChunks = [];
  const runtimeByPath = new Map();
  const runtimeChunkIds = new Set();
  const assertionIds = new Set();
  let totalRows = 0;
  for (const [planeIndex, rawPlane] of runtime.planes.entries()) {
    const plane = jsonObject(rawPlane, `relationship plane ${planeIndex}`);
    plane.id = requiredRichIri(plane.id, `relationship plane ${plane.name} id`);
    plane.lifecycle = requiredRichText(
      plane.lifecycle,
      `relationship plane ${plane.name} lifecycle`
    );
    invariant(
      typeof plane.active === 'boolean' &&
        RICH_RELATIONSHIP_PLANE_LIFECYCLES.has(plane.lifecycle) &&
        plane.active === (plane.lifecycle === 'active'),
      `relationship plane ${plane.name} lifecycle differs from active state`
    );
    invariant(
      Array.isArray(plane.authority_classes) && plane.authority_classes.length > 0,
      `relationship plane ${plane.name} has no authority classes`
    );
    plane.authority_classes = plane.authority_classes.map((value, index) => {
      const authorityClass = requiredRichText(
        value,
        `relationship plane ${plane.name} authority class ${index}`
      );
      invariant(
        RICH_RELATIONSHIP_AUTHORITY_CLASSES.has(authorityClass),
        `relationship plane ${plane.name} has an unsupported authority class`
      );
      return authorityClass;
    });
    invariant(
      new Set(plane.authority_classes).size === plane.authority_classes.length,
      `relationship plane ${plane.name} repeats an authority class`
    );
    invariant(Array.isArray(plane.chunks), `relationship plane ${plane.name} has no chunks`);
    let planeRows = 0;
    for (const [chunkIndex, rawChunk] of plane.chunks.entries()) {
      const chunk = jsonObject(rawChunk, `relationship plane ${plane.name} chunk ${chunkIndex}`);
      const relative = safeRelativeResourcePath(chunk.path, 'relationship runtime chunk path');
      invariant(!runtimeByPath.has(relative), `relationship runtime repeats chunk path: ${relative}`);
      const chunkId = requiredRichIri(chunk.id, `relationship runtime chunk ${relative} id`);
      invariant(!runtimeChunkIds.has(chunkId), `relationship runtime repeats chunk id: ${chunkId}`);
      runtimeChunkIds.add(chunkId);
      invariant(
        chunk.content_encoding === 'gzip' && chunk.media_type === 'application/json',
        `relationship runtime chunk is not gzip JSON: ${relative}`
      );
      const declaredRows = nonNegativeSafeInteger(
        chunk.count,
        `relationship runtime chunk ${relative} count`
      );
      invariant(
        declaredRows <= limits.maximum_rich_relationship_chunk_rows,
        `relationship runtime chunk exceeds its row ceiling: ${relative}`
      );
      invariant(
        chunk.records === declaredRows,
        `relationship runtime chunk count and records differ: ${relative}`
      );
      const compressed = await readRegularFile(
        resolveUnder(bundleRoot, relative),
        `relationship runtime chunk ${relative}`
      );
      invariant(
        positiveSafeInteger(chunk.bytes, `relationship runtime chunk ${relative} bytes`) <=
          limits.maximum_rich_relationship_chunk_bytes &&
          compressed.length === chunk.bytes &&
          sha256(compressed) === requiredRichHash(
            chunk.sha256,
            `relationship runtime chunk ${relative} SHA-256`
          ),
        `relationship runtime chunk identity differs: ${relative}`
      );
      const raw = boundedGunzip(
        compressed,
        limits.maximum_rich_relationship_decoded_chunk_bytes,
        `relationship runtime chunk ${relative}`
      );
      let parsed;
      try {
        parsed = JSON.parse(raw.toString('utf8'));
      } catch (error) {
        throw new Error(`relationship runtime chunk is not valid gzip JSON (${relative}): ${error.message}`);
      }
      invariant(Array.isArray(parsed), `relationship runtime chunk must contain an array: ${relative}`);
      invariant(parsed.length === declaredRows, `relationship runtime chunk row count differs: ${relative}`);
      const projectedRows = parsed.map((row, rowIndex) =>
        projectRichRelationshipRow(
          row,
          plane,
          `relationship runtime chunk ${relative} row ${rowIndex}`
        )
      );
      const identifiers = projectedRows.map((row) => row.id);
      invariant(new Set(identifiers).size === identifiers.length, `relationship runtime chunk repeats an assertion: ${relative}`);
      for (const identifier of identifiers) {
        invariant(
          !assertionIds.has(identifier),
          `relationship runtime repeats an assertion across chunks: ${identifier}`
        );
        assertionIds.add(identifier);
      }
      const retained = projectedRows.reduce(
        (total, row) => total + row.retained_text_units,
        0
      );
      const measured = {
        plane: plane.name,
        plane_active: plane.active,
        path: relative,
        rows: parsed.length,
        decoded_bytes: raw.length,
        compressed_bytes: compressed.length,
        retained_text_units: retained,
        projected_rows: projectedRows
      };
      runtimeChunks.push(measured);
      runtimeByPath.set(relative, measured);
      planeRows += parsed.length;
      totalRows += parsed.length;
      runtimeLimitFinding(findings, 'RELATIONSHIP-CHUNK-ROW-LIMIT', parsed.length, limits.maximum_rich_relationship_chunk_rows, { path: relative });
      runtimeLimitFinding(findings, 'RELATIONSHIP-CHUNK-COMPRESSED-BYTE-LIMIT', compressed.length, limits.maximum_rich_relationship_chunk_bytes, { path: relative });
      runtimeLimitFinding(findings, 'RELATIONSHIP-CHUNK-DECODED-BYTE-LIMIT', raw.length, limits.maximum_rich_relationship_decoded_chunk_bytes, { path: relative });
      runtimeLimitFinding(findings, 'RELATIONSHIP-CHUNK-RETAINED-TEXT-LIMIT', retained, limits.maximum_rich_relationship_retained_text_units, { path: relative });
      for (const row of projectedRows) {
        runtimeLimitFinding(findings, 'RELATIONSHIP-ROW-RETAINED-TEXT-LIMIT', row.retained_text_units, limits.maximum_rich_relationship_row_text_units, { assertion_sha256: sha256(Buffer.from(row.id, 'utf8')) });
        runtimeLimitFinding(findings, 'RELATIONSHIP-ROW-EVIDENCE-LIMIT', row.evidence_items, limits.maximum_rich_relationship_evidence_items, { assertion_sha256: sha256(Buffer.from(row.id, 'utf8')) });
        runtimeLimitFinding(findings, 'RELATIONSHIP-ROW-SUPPORTING-ASSERTION-LIMIT', row.supporting_assertions, limits.maximum_rich_relationship_supporting_assertions, { assertion_sha256: sha256(Buffer.from(row.id, 'utf8')) });
      }
    }
    const planeAssertions = nonNegativeSafeInteger(
      plane.assertions,
      `relationship plane ${plane.name} assertion total`
    );
    invariant(
      planeRows === planeAssertions,
      `relationship plane ${plane.name} assertion total differs from its chunks`
    );
  }
  runtimeLimitFinding(findings, 'RELATIONSHIP-CHUNK-COUNT-LIMIT', runtimeChunks.length, limits.maximum_rich_relationship_chunks);
  runtimeLimitFinding(findings, 'RELATIONSHIP-ROW-TOTAL-LIMIT', totalRows, limits.maximum_relationship_rows);
  invariant(totalRows === receiptValidation.runtime.rows, 'relationship runtime rows differ from the build receipt');
  invariant(runtimeChunks.length === receiptValidation.runtime.chunks, 'relationship runtime chunks differ from the build receipt');

  const totals = jsonObject(runtime.totals, 'relationship runtime totals');
  const expectedTotals = {
    active_assertions: runtime.planes
      .filter((plane) => plane.lifecycle === 'active')
      .reduce((total, plane) => total + plane.assertions, 0),
    historical_assertions: runtime.planes
      .filter((plane) => plane.lifecycle === 'historical')
      .reduce((total, plane) => total + plane.assertions, 0),
    rejected_assertions: runtime.planes
      .filter((plane) => plane.lifecycle === 'rejected')
      .reduce((total, plane) => total + plane.assertions, 0),
    all_assertions: totalRows,
    chunks: runtimeChunks.length
  };
  for (const [name, expected] of Object.entries(expectedTotals)) {
    invariant(
      nonNegativeSafeInteger(totals[name], `relationship runtime total ${name}`) === expected,
      `relationship runtime total ${name} does not reconcile`
    );
  }
  invariant(
    expectedTotals.active_assertions +
      expectedTotals.historical_assertions +
      expectedTotals.rejected_assertions === expectedTotals.all_assertions,
    'relationship runtime lifecycle totals do not reconcile'
  );

  // Derive the complete routing contract independently from every parsed row.
  // Locator self-consistency alone cannot show that an endpoint was omitted:
  // its manifest, buckets and receipt could all repeat the same smaller count.
  const expectedRoutes = new Map();
  for (const chunk of runtimeChunks) {
    for (const row of chunk.projected_rows) {
      for (const routeName of new Set([row.source, row.target])) {
        let route = expectedRoutes.get(routeName);
        if (!route) {
          route = new Map();
          expectedRoutes.set(routeName, route);
        }
        let plane = route.get(row.plane);
        if (!plane) {
          plane = { assertion_ids: new Set(), chunks: new Set() };
          route.set(row.plane, plane);
        }
        plane.assertion_ids.add(row.id);
        plane.chunks.add(chunk.path);
      }
    }
  }
  invariant(
    expectedRoutes.size > 0,
    'relationship runtime rows derive no endpoint routes'
  );
  const expectedBucketPrefixes = new Set(
    [...expectedRoutes.keys()].map((routeName) =>
      sha256(Buffer.from(routeName, 'utf8')).slice(0, 2)
    )
  );
  const expectedChunkReferences = [...expectedRoutes.values()].reduce(
    (total, routePlanes) =>
      total + new Set(
        [...routePlanes.values()].flatMap((plane) => [...plane.chunks])
      ).size,
    0
  );

  const locatorReference = jsonObject(runtime.route_locator, 'relationship route-locator reference');
  const locatorRelative = safeRelativeResourcePath(locatorReference.path, 'relationship route-locator manifest path');
  requiredRichIri(locatorReference.id, 'relationship route-locator reference id');
  const locatorRouteTotal = positiveSafeInteger(
    locatorReference.routes,
    'relationship route-locator reference route total'
  );
  const locatorBucketTotal = positiveSafeInteger(
    locatorReference.buckets,
    'relationship route-locator reference bucket total'
  );
  const locatorDigest = requiredRichHash(
    locatorReference.sha256,
    'relationship route-locator reference SHA-256'
  );
  const { bytes: locatorBytes, value: locator } = await readJson(
    resolveUnder(bundleRoot, locatorRelative),
    'relationship route-locator manifest'
  );
  invariant(
    sha256(locatorBytes) === locatorDigest,
    'relationship route-locator manifest digest differs from its runtime reference'
  );
  invariant(
    locator.schema === 'okf-rich-relationship-route-locator.v1' &&
      locator.hash_algorithm === 'sha256-utf8-first-byte-hex',
    'relationship route-locator manifest identity is unsupported'
  );
  requiredRichText(locator.generated_at, 'relationship route-locator generation time');
  invariant(Array.isArray(locator.buckets), 'relationship route-locator buckets must be an array');
  const bucketTemplate = safeRelativeResourcePath(
    locator.bucket_path_template,
    'relationship route-locator bucket path template'
  );
  invariant(
    bucketTemplate.includes('{prefix}'),
    'relationship route-locator bucket template has no prefix token'
  );
  const locatorCounts = jsonObject(locator.counts, 'relationship route-locator counts');
  const locatorCountRoutes = positiveSafeInteger(
    locatorCounts.routes,
    'relationship route-locator route count'
  );
  const locatorCountBuckets = positiveSafeInteger(
    locatorCounts.buckets,
    'relationship route-locator bucket count'
  );
  const locatorCountChunkReferences = nonNegativeSafeInteger(
    locatorCounts.chunk_references,
    'relationship route-locator chunk-reference count'
  );
  invariant(
    locatorCountRoutes === locatorRouteTotal &&
      locatorCountBuckets === locatorBucketTotal &&
      locatorCountBuckets === locator.buckets.length &&
      locatorCountBuckets <= 256,
    'relationship route-locator counts differ from its runtime reference'
  );
  runtimeLimitFinding(findings, 'RELATIONSHIP-LOCATOR-MANIFEST-BYTE-LIMIT', locatorBytes.length, limits.maximum_json_bytes, { path: locatorRelative });
  const routeMeasurements = [];
  let locatorBucketCompressedMaximum = 0;
  let locatorBucketDecodedMaximum = 0;
  const observedRoutes = new Set();
  const observedBucketPrefixes = new Set();
  const observedBucketPaths = new Set();
  let observedChunkReferences = 0;
  for (const rawBucket of locator.buckets) {
    const bucket = jsonObject(rawBucket, 'relationship route-locator bucket metadata');
    invariant(/^[0-9a-f]{2}$/.test(bucket.bucket), 'relationship route-locator bucket prefix is invalid');
    const relative = safeRelativeResourcePath(bucket.path, 'relationship route-locator bucket path');
    invariant(
      relative === bucketTemplate.replace('{prefix}', bucket.bucket),
      `relationship route-locator bucket path differs from its template: ${relative}`
    );
    invariant(
      !observedBucketPrefixes.has(bucket.bucket) && !observedBucketPaths.has(relative),
      'relationship route-locator repeats a bucket prefix or path'
    );
    observedBucketPrefixes.add(bucket.bucket);
    observedBucketPaths.add(relative);
    invariant(
      bucket.content_encoding === 'gzip',
      `relationship route-locator bucket is not gzip encoded: ${relative}`
    );
    const bucketRoutes = nonNegativeSafeInteger(
      bucket.routes,
      `relationship route-locator bucket ${relative} routes`
    );
    const bucketChunkReferences = nonNegativeSafeInteger(
      bucket.chunk_references,
      `relationship route-locator bucket ${relative} chunk references`
    );
    const compressed = await readRegularFile(
      resolveUnder(bundleRoot, relative),
      `relationship route-locator bucket ${relative}`
    );
    invariant(
      positiveSafeInteger(bucket.bytes, `relationship route-locator bucket ${relative} bytes`) <=
        limits.maximum_rich_relationship_chunk_bytes &&
        compressed.length === bucket.bytes &&
        sha256(compressed) === requiredRichHash(
          bucket.sha256,
          `relationship route-locator bucket ${relative} SHA-256`
        ),
      `relationship route-locator bucket identity differs: ${relative}`
    );
    const raw = boundedGunzip(
      compressed,
      limits.maximum_rich_relationship_decoded_chunk_bytes,
      `relationship route-locator bucket ${relative}`
    );
    const document = JSON.parse(raw.toString('utf8'));
    invariant(
      document.schema === 'okf-rich-relationship-route-locator-bucket.v1' &&
        document.hash_algorithm === 'sha256-utf8-first-byte-hex' &&
        document.bucket === bucket.bucket,
      `relationship route-locator bucket identity differs: ${relative}`
    );
    requiredRichText(
      document.generated_at,
      `relationship route-locator bucket ${relative} generation time`
    );
    invariant(Array.isArray(document.routes), `relationship route-locator bucket has no routes: ${relative}`);
    const documentCounts = jsonObject(
      document.counts,
      `relationship route-locator bucket ${relative} counts`
    );
    invariant(document.routes.length === bucketRoutes, `relationship route-locator bucket route count differs: ${relative}`);
    const chunkReferenceCount = document.routes.reduce(
      (total, route) => total + (Array.isArray(route.chunks) ? route.chunks.length : 0),
      0
    );
    invariant(
      nonNegativeSafeInteger(
        documentCounts.routes,
        `relationship route-locator bucket ${relative} document route count`
      ) === bucketRoutes &&
        nonNegativeSafeInteger(
          documentCounts.chunk_references,
          `relationship route-locator bucket ${relative} document chunk references`
        ) === bucketChunkReferences &&
        chunkReferenceCount === bucketChunkReferences,
      `relationship route-locator bucket counts differ: ${relative}`
    );
    observedChunkReferences += chunkReferenceCount;
    locatorBucketCompressedMaximum = Math.max(locatorBucketCompressedMaximum, compressed.length);
    locatorBucketDecodedMaximum = Math.max(locatorBucketDecodedMaximum, raw.length);
    runtimeLimitFinding(findings, 'RELATIONSHIP-LOCATOR-BUCKET-COMPRESSED-BYTE-LIMIT', compressed.length, limits.maximum_rich_relationship_chunk_bytes, { path: relative });
    runtimeLimitFinding(findings, 'RELATIONSHIP-LOCATOR-BUCKET-DECODED-BYTE-LIMIT', raw.length, limits.maximum_rich_relationship_decoded_chunk_bytes, { path: relative });
    for (const rawRoute of document.routes) {
      const route = jsonObject(rawRoute, 'relationship route-locator route');
      const routeName = requiredRichRoute(route.route, 'relationship route-locator route');
      invariant(!observedRoutes.has(routeName), 'relationship route-locator repeats a route');
      invariant(sha256(Buffer.from(routeName, 'utf8')).slice(0, 2) === bucket.bucket, 'relationship route-locator route is in the wrong bucket');
      observedRoutes.add(routeName);
      const expectedRoutePlanes = expectedRoutes.get(routeName);
      invariant(
        expectedRoutePlanes,
        `relationship route-locator advertises a route absent from runtime rows: ${routeName}`
      );
      invariant(Array.isArray(route.chunks) && route.chunks.length > 0, `relationship route has no chunks: ${routeName}`);
      const routeChunkPaths = route.chunks.map((chunkPath, index) =>
        safeRelativeResourcePath(chunkPath, `relationship route ${routeName} chunk ${index}`)
      );
      invariant(new Set(routeChunkPaths).size === routeChunkPaths.length, `relationship route repeats a chunk: ${routeName}`);
      const expectedRouteChunks = new Set(
        [...expectedRoutePlanes.values()].flatMap((plane) => [...plane.chunks])
      );
      invariant(
        expectedRouteChunks.size === routeChunkPaths.length &&
          routeChunkPaths.every((chunkPath) => expectedRouteChunks.has(chunkPath)),
        `relationship route chunks differ from runtime row endpoints: ${routeName}`
      );
      const allMembers = routeChunkPaths.map((chunkPath) => {
        invariant(runtimeByPath.has(chunkPath), `relationship route references an unknown chunk: ${chunkPath}`);
        return runtimeByPath.get(chunkPath);
      });
      invariant(Array.isArray(route.planes) && route.planes.length > 0, `relationship route has no plane commitments: ${routeName}`);
      const commitmentNames = new Set();
      const commitments = route.planes.map((rawCommitment, index) => {
        const commitment = jsonObject(
          rawCommitment,
          `relationship route ${routeName} plane commitment ${index}`
        );
        const name = requiredRichText(
          commitment.name,
          `relationship route ${routeName} plane commitment ${index} name`
        );
        invariant(
          planeNames.includes(name) &&
            expectedRoutePlanes.has(name) &&
            !commitmentNames.has(name),
          `relationship route has an unknown or repeated plane commitment: ${routeName}`
        );
        commitmentNames.add(name);
        invariant(
          Array.isArray(commitment.chunks) && commitment.chunks.length > 0,
          `relationship route plane has no chunks: ${routeName}`
        );
        const chunks = commitment.chunks.map((chunkPath, chunkIndex) =>
          safeRelativeResourcePath(
            chunkPath,
            `relationship route ${routeName} plane ${name} chunk ${chunkIndex}`
          )
        );
        invariant(
          new Set(chunks).size === chunks.length,
          `relationship route plane repeats a chunk: ${routeName}`
        );
        const expectedPlane = expectedRoutePlanes.get(name);
        invariant(
          expectedPlane.chunks.size === chunks.length &&
            chunks.every((chunkPath) => expectedPlane.chunks.has(chunkPath)),
          `relationship route plane chunks differ from runtime row endpoints: ${routeName}`
        );
        for (const chunkPath of chunks) {
          invariant(
            runtimeByPath.get(chunkPath)?.plane === name,
            `relationship route plane names an unknown or cross-plane chunk: ${routeName}`
          );
        }
        const assertionIds = [...expectedPlane.assertion_ids].sort(compareCodePoints);
        const assertions = positiveSafeInteger(
          commitment.assertions,
          `relationship route ${routeName} plane ${name} assertions`
        );
        const assertionIdsSha256 = requiredRichHash(
          commitment.assertion_ids_sha256,
          `relationship route ${routeName} plane ${name} assertion digest`
        );
        invariant(
          assertions === assertionIds.length &&
            assertionIdsSha256 ===
              sha256(Buffer.from(JSON.stringify(assertionIds), 'utf8')),
          `relationship route assertion commitment differs from runtime row endpoints: ${routeName}`
        );
        return {
          name,
          chunks,
          assertions,
          assertion_ids_sha256: assertionIdsSha256
        };
      });
      invariant(
        commitmentNames.size === expectedRoutePlanes.size,
        `relationship route plane commitments differ from runtime row endpoints: ${routeName}`
      );
      const allCommittedPaths = new Set(commitments.flatMap((plane) => plane.chunks));
      invariant(
        allCommittedPaths.size === routeChunkPaths.length &&
          routeChunkPaths.every((chunkPath) => allCommittedPaths.has(chunkPath)),
        `relationship route chunk union differs from its plane commitments: ${routeName}`
      );
      const activeCommitments = commitments.filter((plane) => runtime.default_planes.includes(plane.name));
      const activeMembers = allMembers.filter((member) => member.plane_active);
      const activePaths = new Set(activeMembers.map((member) => member.path));
      const committedPaths = new Set(activeCommitments.flatMap((plane) => plane.chunks));
      invariant(
        activePaths.size === committedPaths.size &&
          [...activePaths].every((chunkPath) => committedPaths.has(chunkPath)),
        `relationship route active plane commitments differ from selected chunks: ${routeName}`
      );
      const selectedRows = activeMembers.reduce((total, member) => total + member.rows, 0);
      const allIncidentRows = allMembers
        .flatMap((member) => member.projected_rows)
        .filter((row) => row.source === routeName || row.target === routeName);
      invariant(new Set(allIncidentRows.map((row) => row.id)).size === allIncidentRows.length, `relationship route repeats an incident assertion: ${routeName}`);
      for (const commitment of commitments) {
        const committedAssertionIds = allIncidentRows
          .filter((row) => row.plane === commitment.name)
          .map((row) => row.id)
          .sort(compareCodePoints);
        invariant(
          committedAssertionIds.length === commitment.assertions &&
            sha256(Buffer.from(JSON.stringify(committedAssertionIds), 'utf8')) === commitment.assertion_ids_sha256,
          `relationship route assertion commitment differs: ${routeName}`
        );
      }
      const incidentRows = allIncidentRows.filter((row) =>
        runtime.default_planes.includes(row.plane)
      );
      const expectedIncidentRows = activeCommitments.reduce(
        (total, commitment) => total + commitment.assertions,
        0
      );
      invariant(incidentRows.length === expectedIncidentRows, `relationship route incident assertions do not reconcile: ${routeName}`);
      const measured = {
        route_sha256: sha256(Buffer.from(routeName, 'utf8')),
        chunks: activeMembers.length,
        declared_rows: selectedRows,
        incident_rows: incidentRows.length,
        compressed_bytes: activeMembers.reduce((total, member) => total + member.compressed_bytes, 0),
        retained_text_units: activeMembers.reduce((total, member) => total + member.retained_text_units, 0)
      };
      routeMeasurements.push(measured);
      runtimeLimitFinding(findings, 'RELATIONSHIP-ROUTE-CHUNK-LIMIT', measured.chunks, limits.maximum_rich_relationship_route_chunks, { route_sha256: measured.route_sha256 });
      runtimeLimitFinding(findings, 'RELATIONSHIP-ROUTE-DECLARED-ROW-LIMIT', measured.declared_rows, limits.maximum_rich_relationship_route_rows, { route_sha256: measured.route_sha256 });
      runtimeLimitFinding(findings, 'RELATIONSHIP-ROUTE-INCIDENT-ROW-LIMIT', measured.incident_rows, limits.maximum_rich_relationship_route_rows, { route_sha256: measured.route_sha256 });
      runtimeLimitFinding(findings, 'RELATIONSHIP-ROUTE-COMPRESSED-BYTE-LIMIT', measured.compressed_bytes, limits.maximum_rich_relationship_hydration_compressed_bytes, { route_sha256: measured.route_sha256 });
      runtimeLimitFinding(findings, 'RELATIONSHIP-ROUTE-RETAINED-TEXT-LIMIT', measured.retained_text_units, limits.maximum_rich_relationship_retained_text_units, { route_sha256: measured.route_sha256 });
    }
  }
  invariant(
    observedRoutes.size === locatorRouteTotal &&
      observedRoutes.size === locatorCountRoutes &&
      observedRoutes.size === expectedRoutes.size &&
      [...expectedRoutes.keys()].every((routeName) => observedRoutes.has(routeName)),
    'relationship route-locator route total differs from runtime manifest'
  );
  invariant(
    locator.buckets.length === locatorBucketTotal &&
      locator.buckets.length === locatorCountBuckets &&
      locator.buckets.length === expectedBucketPrefixes.size &&
      [...expectedBucketPrefixes].every((prefix) => observedBucketPrefixes.has(prefix)),
    'relationship route-locator bucket total differs from runtime manifest'
  );
  invariant(
    observedChunkReferences === locatorCountChunkReferences &&
      observedChunkReferences === expectedChunkReferences,
    'relationship route-locator chunk-reference total does not reconcile'
  );
  invariant(observedRoutes.size === receiptValidation.runtime.routes, 'relationship route-locator routes differ from build receipt');
  invariant(locator.buckets.length === receiptValidation.runtime.buckets, 'relationship route-locator buckets differ from build receipt');

  const routeMaxima = routeMeasurements.reduce(
    (maximum, row) => ({
      chunks: Math.max(maximum.chunks, row.chunks),
      declared_rows: Math.max(maximum.declared_rows, row.declared_rows),
      incident_rows: Math.max(maximum.incident_rows, row.incident_rows),
      compressed_bytes: Math.max(maximum.compressed_bytes, row.compressed_bytes),
      retained_text_units: Math.max(maximum.retained_text_units, row.retained_text_units)
    }),
    { chunks: 0, declared_rows: 0, incident_rows: 0, compressed_bytes: 0, retained_text_units: 0 }
  );
  const activeChunks = runtimeChunks.filter((chunk) => chunk.plane_active);
  const fullSelected = [];
  let fullDeclaredRows = 0;
  for (const chunk of activeChunks) {
    if (fullDeclaredRows >= limits.maximum_relationship_rows) break;
    fullSelected.push(chunk);
    fullDeclaredRows += chunk.rows;
  }
  const fullHydration = {
    chunks: fullSelected.length,
    declared_rows: fullDeclaredRows,
    compressed_bytes: fullSelected.reduce((total, chunk) => total + chunk.compressed_bytes, 0),
    retained_text_units: fullSelected.reduce((total, chunk) => total + chunk.retained_text_units, 0)
  };
  runtimeLimitFinding(findings, 'RELATIONSHIP-FULL-HYDRATION-COMPRESSED-BYTE-LIMIT', fullHydration.compressed_bytes, limits.maximum_rich_relationship_hydration_compressed_bytes);
  runtimeLimitFinding(findings, 'RELATIONSHIP-FULL-HYDRATION-RETAINED-TEXT-LIMIT', fullHydration.retained_text_units, limits.maximum_rich_relationship_retained_text_units);

  const observedMaxima = {
    row_retained_text_units: Math.max(0, ...runtimeChunks.flatMap((chunk) => chunk.projected_rows.map((row) => row.retained_text_units))),
    row_evidence_items: Math.max(0, ...runtimeChunks.flatMap((chunk) => chunk.projected_rows.map((row) => row.evidence_items))),
    row_supporting_assertions: Math.max(0, ...runtimeChunks.flatMap((chunk) => chunk.projected_rows.map((row) => row.supporting_assertions))),
    chunk_rows: Math.max(0, ...runtimeChunks.map((chunk) => chunk.rows)),
    chunk_compressed_bytes: Math.max(0, ...runtimeChunks.map((chunk) => chunk.compressed_bytes)),
    chunk_decoded_bytes: Math.max(0, ...runtimeChunks.map((chunk) => chunk.decoded_bytes)),
    chunk_retained_text_units: Math.max(0, ...runtimeChunks.map((chunk) => chunk.retained_text_units)),
    locator_bucket_compressed_bytes: locatorBucketCompressedMaximum,
    locator_bucket_decoded_bytes: locatorBucketDecodedMaximum,
    locator_manifest_bytes: locatorBytes.length,
    runtime_manifest_bytes: runtimeBytes.length,
    route_chunks: routeMaxima.chunks,
    route_declared_rows: routeMaxima.declared_rows,
    route_incident_rows: routeMaxima.incident_rows,
    route_compressed_bytes: routeMaxima.compressed_bytes,
    route_retained_text_units: routeMaxima.retained_text_units,
    full_hydration_chunks: fullHydration.chunks,
    full_hydration_declared_rows: fullHydration.declared_rows,
    full_hydration_compressed_bytes: fullHydration.compressed_bytes,
    full_hydration_retained_text_units: fullHydration.retained_text_units,
    total_chunks: runtimeChunks.length,
    total_rows: totalRows,
    total_planes: runtime.planes.length
  };
  reconcileObservedRuntimeMaxima(observedMaxima, receiptValidation.maxima);
  return {
    measurement: {
      chunks: runtimeChunks.map(({ projected_rows: _projectedRows, plane_active: _planeActive, ...chunk }) => chunk),
      chunk_count: runtimeChunks.length,
      rows: totalRows,
      compressed_bytes: runtimeChunks.reduce((total, row) => total + row.compressed_bytes, 0),
      decoded_bytes: runtimeChunks.reduce((total, row) => total + row.decoded_bytes, 0),
      retained_text_units: runtimeChunks.reduce((total, row) => total + row.retained_text_units, 0),
      route_count: routeMeasurements.length,
      maximum_incident_route: routeMaxima,
      full_default_hydration: fullHydration,
      observed_maxima: observedMaxima,
      consumer_identity: receiptValidation.validation.consumer,
      consumer_limits: limits,
      cache_policy: receiptValidation.validation.cache_policy
    },
    findings
  };
}

async function measureBundle(
  bundleRoot,
  checksumRows,
  lock,
  receiptValidation,
  advertisedRichRuntime
) {
  const limits = jsonObject(lock.limits, 'consumer lock limits');
  const checksumManifestBytes = (
    await readRegularFile(
      path.join(bundleRoot, 'CHECKSUMS.sha256'),
      'bundle checksum manifest for size measurement'
    )
  ).length;
  const shell = [];
  for (const route of REQUIRED_ROUTES) {
    const relative = route === '/' ? 'index.html' : route.slice(1);
    const bytes = await readRegularFile(resolveUnder(bundleRoot, relative), `authored route ${route}`);
    shell.push({ route, path: relative, raw_bytes: bytes.length, gzip_bytes: gzipBytes(bytes) });
  }
  const stylesheet = await readRegularFile(path.join(bundleRoot, 'styles.css'), 'authored stylesheet');
  shell.push({ route: null, path: 'styles.css', raw_bytes: stylesheet.length, gzip_bytes: gzipBytes(stylesheet) });

  const advertised = jsonObject(
    advertisedRichRuntime,
    'advertised rich relationship resources'
  );
  const dataManifest = jsonObject(
    advertised.data_manifest,
    'advertised Explorer data-manifest resource'
  );
  const dataManifestReference = exactResourceReference(
    dataManifest.reference,
    'advertised Explorer data-manifest reference'
  );
  const explorerManifest = (
    await readBoundJsonResource(
      bundleRoot,
      dataManifestReference,
      'advertised Explorer data manifest'
    )
  ).value;
  const chunkRows = [];
  for (const [kind, rows] of Object.entries(jsonObject(explorerManifest.chunks, 'Explorer data chunks'))) {
    invariant(Array.isArray(rows), `Explorer ${kind} chunks must be an array`);
    for (const [index, row] of rows.entries()) {
      const relative = safeRelativePath(row.path, `Explorer ${kind} chunk ${index}`);
      const bytes = await readRegularFile(resolveUnder(bundleRoot, relative), `Explorer ${kind} chunk ${relative}`);
      invariant(bytes.length === row.bytes && sha256(bytes) === row.sha256, `Explorer ${kind} chunk identity differs: ${relative}`);
      let records = null;
      try {
        const parsed = JSON.parse(bytes.toString('utf8'));
        if (Array.isArray(parsed)) records = parsed.length;
        else if (Array.isArray(parsed[kind])) records = parsed[kind].length;
        else if (Array.isArray(parsed.records)) records = parsed.records.length;
      } catch (error) {
        throw new Error(`Explorer ${kind} chunk is not valid JSON (${relative}): ${error.message}`);
      }
      chunkRows.push({
        kind,
        path: relative,
        records,
        raw_bytes: bytes.length,
        gzip_bytes: gzipBytes(bytes),
        within_maximum_json_bytes: bytes.length <= limits.maximum_json_bytes
      });
    }
  }

  const runtimeMeasurement = await measureRichRelationshipRuntime(
    bundleRoot,
    lock,
    receiptValidation,
    advertisedRichRuntime
  );
  const limitFindings = [...runtimeMeasurement.findings];
  for (const row of chunkRows.filter((item) => !item.within_maximum_json_bytes)) {
    limitFindings.push({ code: 'EXPLORER-JSON-LIMIT', severity: 'blocker', path: row.path, measured: row.raw_bytes, maximum: limits.maximum_json_bytes });
  }
  return {
    total_bundle: {
      files: checksumRows.length + 1,
      raw_bytes: checksumRows.reduce((total, row) => total + row.bytes, checksumManifestBytes)
    },
    authored_shell: {
      compression_method: 'gzip-level-9-mtime-0',
      members: shell,
      raw_bytes: shell.reduce((total, row) => total + row.raw_bytes, 0),
      gzip_bytes: shell.reduce((total, row) => total + row.gzip_bytes, 0)
    },
    explorer_lazy_chunks: {
      startup_mode: explorerManifest.performance?.startup_mode ?? null,
      chunks: chunkRows,
      count: chunkRows.length,
      raw_bytes: chunkRows.reduce((total, row) => total + row.raw_bytes, 0),
      gzip_bytes: chunkRows.reduce((total, row) => total + row.gzip_bytes, 0),
      maximum_raw_bytes: Math.max(0, ...chunkRows.map((row) => row.raw_bytes))
    },
    relationship_runtime: runtimeMeasurement.measurement,
    limit_findings: limitFindings
  };
}

function axeCheck(check) {
  const relatedNodes = Array.isArray(check.relatedNodes) ? check.relatedNodes : [];
  return {
    id: check.id ?? null,
    impact: check.impact ?? null,
    message_evidence: boundedTextEvidence(check.message ?? ''),
    data_evidence: check.data === null || check.data === undefined
      ? null
      : boundedTextEvidence(JSON.stringify(canonicalValue(check.data))),
    related_nodes: relatedNodes.map((related) => ({
      target: related.target ?? null,
      html_evidence: boundedTextEvidence(related.html ?? '')
    }))
  };
}

function axeNode(node) {
  return {
    impact: node.impact ?? null,
    html_evidence: boundedTextEvidence(node.html ?? ''),
    target: node.target,
    failure_summary_evidence: node.failureSummary === null || node.failureSummary === undefined
      ? null
      : boundedTextEvidence(node.failureSummary),
    any: (node.any || []).map(axeCheck),
    all: (node.all || []).map(axeCheck),
    none: (node.none || []).map(axeCheck)
  };
}

function axeRule(rule) {
  return {
    id: rule.id,
    impact: rule.impact ?? null,
    tags: [...rule.tags],
    description: rule.description,
    help: rule.help,
    help_url: rule.helpUrl,
    nodes: rule.nodes.map(axeNode)
  };
}

function incompleteFingerprint(route, profileId, rule, node) {
  const identity = {
    route,
    profile: profileId,
    rule: rule.id,
    impact: node.impact ?? rule.impact ?? null,
    target: node.target,
    html: node.html,
    failure_summary: node.failureSummary ?? null
  };
  return sha256(Buffer.from(JSON.stringify(canonicalValue(identity)), 'utf8'));
}

function browserProfiles() {
  return [
    { id: 'desktop-js', viewport: { width: 1280, height: 720 }, javaScriptEnabled: true, mode: 'desktop', axe: true, keyboard: true, zoom_percent: 100 },
    { id: 'mobile-js', viewport: { width: 320, height: 800 }, javaScriptEnabled: true, mode: 'mobile-small-screen', axe: true, keyboard: false, zoom_percent: 100 },
    { id: 'desktop-no-js', viewport: { width: 1280, height: 720 }, javaScriptEnabled: false, mode: 'desktop', axe: false, keyboard: false, zoom_percent: 100 },
    { id: 'mobile-no-js', viewport: { width: 320, height: 800 }, javaScriptEnabled: false, mode: 'mobile-small-screen', axe: false, keyboard: false, zoom_percent: 100 },
    { id: 'zoom-200-js', viewport: { width: 640, height: 720 }, javaScriptEnabled: true, mode: 'effective-css-width-zoom-proxy', axe: false, keyboard: false, zoom_percent: 200 },
    { id: 'zoom-400-js', viewport: { width: 320, height: 720 }, javaScriptEnabled: true, mode: 'effective-css-width-zoom-proxy', axe: false, keyboard: false, zoom_percent: 400 },
    { id: 'forced-colours-js', viewport: { width: 1280, height: 720 }, javaScriptEnabled: true, mode: 'desktop', axe: false, keyboard: false, zoom_percent: 100, forcedColors: 'active' },
    { id: 'reduced-motion-js', viewport: { width: 1280, height: 720 }, javaScriptEnabled: true, mode: 'desktop', axe: false, keyboard: false, zoom_percent: 100, reducedMotion: 'reduce' }
  ];
}

async function pageStructure(page) {
  return page.evaluate(() => {
    const headings = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].map((heading) => ({
      level: Number(heading.tagName.slice(1)),
      text: (heading.textContent || '').trim()
    }));
    const landmark = (selector) => [...document.querySelectorAll(selector)].map((element) => ({
      tag: element.tagName.toLowerCase(),
      role: element.getAttribute('role'),
      label: element.getAttribute('aria-label'),
      labelled_by: element.getAttribute('aria-labelledby')
    }));
    const skips = [...document.querySelectorAll('a[href^="#"]')]
      .filter((anchor) => /skip/i.test(anchor.textContent || ''))
      .map((anchor) => ({ href: anchor.getAttribute('href'), text: (anchor.textContent || '').trim() }));
    const external = [...document.querySelectorAll('a[href^="http://"],a[href^="https://"]')].map((anchor) => anchor.href);
    const animations = document.getAnimations().map((animation) => {
      const timing = animation.effect?.getComputedTiming?.() || {};
      return {
        play_state: animation.playState,
        duration: Number.isFinite(timing.duration) ? timing.duration : null
      };
    });
    return {
      document_language: document.documentElement.lang,
      title: document.title,
      h1_count: document.querySelectorAll('h1').length,
      headings,
      landmarks: {
        header: landmark('header,[role="banner"]'),
        navigation: landmark('nav,[role="navigation"]'),
        main: landmark('main,[role="main"]'),
        footer: landmark('footer,[role="contentinfo"]')
      },
      skip_links: skips,
      external_link_count: external.length,
      external_links_sha256: external.length
        ? null
        : null,
      viewport: {
        inner_width: window.innerWidth,
        inner_height: window.innerHeight,
        document_client_width: document.documentElement.clientWidth,
        document_scroll_width: document.documentElement.scrollWidth,
        body_scroll_width: document.body?.scrollWidth ?? 0,
        horizontal_overflow: Math.max(document.documentElement.scrollWidth, document.body?.scrollWidth ?? 0) > document.documentElement.clientWidth + 1
      },
      preferences: {
        forced_colours_active: matchMedia('(forced-colors: active)').matches,
        reduced_motion: matchMedia('(prefers-reduced-motion: reduce)').matches
      },
      animations: {
        count: animations.length,
        running: animations.filter((item) => item.play_state === 'running').length,
        maximum_duration_ms: Math.max(0, ...animations.map((item) => item.duration || 0))
      }
    };
  });
}

async function storageObservation(page, context) {
  const browserStorage = await page.evaluate(async () => {
    const indexedDatabases = typeof indexedDB?.databases === 'function' ? await indexedDB.databases() : [];
    const cacheNames = typeof caches !== 'undefined' ? await caches.keys() : [];
    return {
      local_storage_entries: localStorage.length,
      session_storage_entries: sessionStorage.length,
      indexed_db_databases: indexedDatabases.length,
      cache_storage_entries: cacheNames.length
    };
  });
  const cookies = await context.cookies();
  return {
    ...browserStorage,
    cookies: cookies.length,
    cookie_identity_sha256: cookies.length
      ? sha256(Buffer.from(JSON.stringify(cookies.map((cookie) => [cookie.name, cookie.domain, cookie.path]).sort()), 'utf8'))
      : null
  };
}

async function keyboardJourney(page, context, route, baseUrl, setExpectedExternal) {
  await page.evaluate(() => document.activeElement?.blur());
  await page.keyboard.press('Tab');
  const firstTab = await page.evaluate(() => {
    const active = document.activeElement;
    if (!(active instanceof HTMLElement)) return null;
    const style = getComputedStyle(active);
    const rect = active.getBoundingClientRect();
    return {
      tag: active.tagName.toLowerCase(),
      id: active.id || null,
      class_name: active.className || null,
      href: active.getAttribute('href'),
      text: (active.textContent || '').trim(),
      visible: rect.width > 0 && rect.height > 0,
      focus_indicator: style.outlineStyle !== 'none' || style.outlineWidth !== '0px' || style.boxShadow !== 'none'
    };
  });
  const skipExpected = route !== '/404.html';
  let skipOperation = { applicable: skipExpected, activated: false, hash: null, target_focused: false };
  if (skipExpected && firstTab?.href === '#main') {
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => window.location.hash === '#main');
    skipOperation = await page.evaluate(() => ({
      applicable: true,
      activated: window.location.hash === '#main',
      hash: window.location.hash,
      target_focused: document.activeElement?.id === 'main' || Boolean(document.querySelector('main')?.contains(document.activeElement))
    }));
  }
  await page.goto(`${baseUrl}${route}`, { waitUntil: 'networkidle' });
  const focusableCount = await page.locator('a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])').count();
  const traversal = [];
  for (let index = 0; index < Math.min(focusableCount, 48); index += 1) {
    await page.keyboard.press('Tab');
    traversal.push(await page.evaluate(() => {
      const active = document.activeElement;
      return active instanceof HTMLElement
        ? `${active.tagName.toLowerCase()}#${active.id || ''}.${typeof active.className === 'string' ? active.className : ''}:${active.getAttribute('href') || ''}`
        : 'none';
    }));
  }
  let staticCatalogue = { applicable: route === '/', navigated: false, result_links: null };
  if (route === '/') {
    await page.goto(baseUrl, { waitUntil: 'networkidle' });
    const link = page.locator('a[href="./catalogue-index.html"]').first();
    await link.focus();
    await Promise.all([
      page.waitForURL(`${baseUrl}/catalogue-index.html`),
      page.keyboard.press('Enter')
    ]);
    staticCatalogue = {
      applicable: true,
      navigated: (await page.locator('h1').innerText()) === 'Static catalogue',
      result_links: await page.locator('main li a[href^="https://"]').count()
    };
  }
  const external = page.locator('a[href^="https://"],a[href^="http://"]').first();
  invariant(await external.count() === 1, `keyboard route ${route} has no external official-source link`);
  const externalHref = await external.getAttribute('href');
  invariant(externalHref && !credentialUrlFinding(externalHref), `keyboard route ${route} has a credential-bearing official-source link`);
  setExpectedExternal(externalHref);
  await external.focus();
  await Promise.all([
    page.waitForURL((url) => url.toString() === externalHref),
    page.keyboard.press('Enter')
  ]);
  return {
    first_tab: firstTab,
    skip_link: skipOperation,
    focusable_elements: focusableCount,
    traversed_elements: traversal.length,
    unique_traversed_elements: new Set(traversal).size,
    traversal_sha256: sha256(Buffer.from(JSON.stringify(traversal), 'utf8')),
    static_catalogue: staticCatalogue,
    official_source_navigation: {
      requested: describedUrl(externalHref),
      intercepted_without_external_network: true,
      terminal_url: describedUrl(page.url())
    }
  };
}

async function runObservation({ browser, AxeBuilder, profile, route, baseUrl, requestLog }) {
  const id = `${route === '/' ? 'root' : route.slice(1).replace(/[^a-z0-9]+/giu, '-')}-${profile.id}`;
  const contextOptions = {
    viewport: profile.viewport,
    javaScriptEnabled: profile.javaScriptEnabled,
    locale: 'en-GB',
    serviceWorkers: 'block',
    reducedMotion: profile.reducedMotion || 'no-preference',
    forcedColors: profile.forcedColors || 'none',
    extraHTTPHeaders: {
      'x-okf-observation': id,
      'x-okf-phase': 'initial'
    }
  };
  const context = await browser.newContext(contextOptions);
  let expectedExternal = null;
  const expectedExternalAttempts = [];
  const unexpectedExternal = [];
  await context.route('**/*', async (routeHandler) => {
    const request = routeHandler.request();
    const url = new URL(request.url());
    if (url.origin === baseUrl) {
      await routeHandler.continue();
      return;
    }
    if (expectedExternal && request.isNavigationRequest() && request.frame() === request.frame().page().mainFrame() && request.url() === expectedExternal) {
      expectedExternalAttempts.push({ url: describedUrl(request.url()), method: request.method(), resource_type: request.resourceType() });
      expectedExternal = null;
      await routeHandler.fulfill({
        status: 200,
        contentType: 'text/html; charset=utf-8',
        body: '<!doctype html><html lang="en-GB"><title>External navigation intercepted</title><body><main><h1>External navigation intercepted</h1></main></body></html>'
      });
      return;
    }
    unexpectedExternal.push({ url: describedUrl(request.url()), method: request.method(), resource_type: request.resourceType(), navigation: request.isNavigationRequest() });
    await routeHandler.abort('blockedbyclient');
  });
  const page = await context.newPage();
  const consoleEvents = [];
  const pageErrors = [];
  const failedRequests = [];
  const badResponses = [];
  page.on('console', (message) => consoleEvents.push({ type: message.type(), text: boundedTextEvidence(message.text()), location: describedUrl(message.location().url || page.url()) }));
  page.on('pageerror', (error) => pageErrors.push({ name: error.name, message: boundedTextEvidence(error.message) }));
  page.on('requestfailed', (request) => failedRequests.push({ url: describedUrl(request.url()), method: request.method(), resource_type: request.resourceType(), failure: boundedTextEvidence(request.failure()?.errorText || 'unknown') }));
  page.on('response', (response) => {
    if (response.status() >= 400) badResponses.push({ url: describedUrl(response.url()), status: response.status(), resource_type: response.request().resourceType() });
  });
  await context.addInitScript(() => {
    globalThis.__okfCspViolations = [];
    addEventListener('securitypolicyviolation', (event) => {
      globalThis.__okfCspViolations.push({
        blocked_uri: event.blockedURI,
        violated_directive: event.violatedDirective,
        effective_directive: event.effectiveDirective,
        disposition: event.disposition
      });
    });
  });
  const cdp = await context.newCDPSession(page);
  await cdp.send('Network.enable');
  const network = new Map();
  cdp.on('Network.requestWillBeSent', (event) => {
    network.set(event.requestId, {
      sequence: network.size + 1,
      url: describedUrl(event.request.url),
      method: event.request.method,
      resource_type: event.type,
      encoded_data_length: null
    });
  });
  cdp.on('Network.loadingFinished', (event) => {
    const row = network.get(event.requestId);
    if (row) row.encoded_data_length = Math.round(event.encodedDataLength);
  });
  let responseStatus = null;
  let structure = null;
  let storage = null;
  let timing = null;
  let resourceTiming = [];
  let cspViolations = [];
  let keyboard = null;
  let axe = null;
  let error = null;
  try {
    const response = await page.goto(`${baseUrl}${route}`, { waitUntil: 'networkidle' });
    responseStatus = response?.status() ?? null;
    const firstInteractive = page.locator('a[href],button:not([disabled]),input:not([disabled]),[tabindex]:not([tabindex="-1"])').first();
    await firstInteractive.waitFor({ state: 'visible' });
    timing = await page.evaluate(() => {
      const navigation = performance.getEntriesByType('navigation')[0];
      return {
        time_to_first_usable_interaction_ms: Math.round(performance.now()),
        dom_content_loaded_ms: navigation ? Math.round(navigation.domContentLoadedEventEnd) : null,
        load_event_ms: navigation ? Math.round(navigation.loadEventEnd) : null
      };
    });
    structure = await pageStructure(page);
    structure.external_links_sha256 = await page.locator('a[href^="http://"],a[href^="https://"]').evaluateAll((links) => links.map((link) => link.href).sort().join('\n')).then((value) => sha256(Buffer.from(value, 'utf8')));
    storage = await storageObservation(page, context);
    resourceTiming = await page.evaluate(() => [
      ...performance.getEntriesByType('navigation'),
      ...performance.getEntriesByType('resource')
    ].map((entry) => ({
      name: entry.name,
      initiator_type: entry.initiatorType,
      transfer_size: entry.transferSize,
      encoded_body_size: entry.encodedBodySize,
      decoded_body_size: entry.decodedBodySize,
      duration_ms: Math.round(entry.duration)
    })));
    cspViolations = profile.javaScriptEnabled
      ? (await page.evaluate(() => globalThis.__okfCspViolations || [])).map((violation) => ({
          ...violation,
          blocked_uri: describedUrl(violation.blocked_uri)
        }))
      : [];
    if (profile.axe) {
      const result = await new AxeBuilder({ page }).withTags(AXE_TAGS).analyze();
      axe = {
        tags: AXE_TAGS,
        violations: result.violations.map(axeRule),
        incomplete: result.incomplete.map(axeRule),
        passes: {
          rules: result.passes.length,
          nodes: result.passes.reduce((total, rule) => total + rule.nodes.length, 0)
        },
        inapplicable: {
          rules: result.inapplicable.length,
          nodes: result.inapplicable.reduce((total, rule) => total + rule.nodes.length, 0)
        },
        incomplete_fingerprints: result.incomplete.flatMap((rule) => rule.nodes.map((node) => ({
          fingerprint: incompleteFingerprint(route, profile.id, rule, node),
          route,
          profile: profile.id,
          rule: rule.id,
          impact: node.impact ?? rule.impact ?? null,
          target: node.target
        })))
      };
    }
    if (profile.keyboard) {
      await context.setExtraHTTPHeaders({ 'x-okf-observation': id, 'x-okf-phase': 'keyboard' });
      keyboard = await keyboardJourney(page, context, route, baseUrl, (value) => { expectedExternal = value; });
    }
  } catch (caught) {
    error = {
      name: caught instanceof Error ? caught.name : 'Error',
      message: boundedTextEvidence(caught instanceof Error ? caught.message : String(caught)),
      stack_sha256: caught instanceof Error && caught.stack ? sha256(Buffer.from(caught.stack, 'utf8')) : null
    };
  }
  await page.waitForTimeout(0).catch(() => {});
  const serverRequests = requestLog.filter((row) => row.observation_id === id);
  const initialServerRequests = serverRequests.filter((row) => row.phase === 'initial');
  const lazyRequests = serverRequests.filter((row) => /\/(?:data\/|okf-explorer\.json$)/u.test(row.path));
  const observation = {
    id,
    route,
    profile: {
      id: profile.id,
      mode: profile.mode,
      viewport: profile.viewport,
      javascript: profile.javaScriptEnabled ? 'enabled' : 'disabled',
      zoom_percent: profile.zoom_percent,
      zoom_method: profile.zoom_percent === 100 ? 'browser-default' : 'effective-CSS-width automated reflow proxy; manual browser-control zoom remains outside this runner',
      forced_colours: profile.forcedColors || 'none',
      reduced_motion: profile.reducedMotion || 'no-preference'
    },
    http_status: responseStatus,
    timing,
    structure,
    keyboard,
    axe,
    security: {
      csp_violations: cspViolations,
      console: consoleEvents,
      page_errors: pageErrors,
      failed_requests: failedRequests,
      error_responses: badResponses,
      storage,
      expected_external_navigation_attempts: expectedExternalAttempts,
      unexpected_external_requests: unexpectedExternal
    },
    network: {
      server_requests: serverRequests,
      initial_request_count: initialServerRequests.length,
      initial_raw_bytes: initialServerRequests.reduce((total, row) => total + row.raw_bytes, 0),
      initial_transferred_body_bytes: initialServerRequests.reduce((total, row) => total + row.transferred_body_bytes, 0),
      cdp_requests: [...network.values()].sort((left, right) => left.sequence - right.sequence),
      cdp_encoded_bytes: [...network.values()].reduce((total, row) => total + (row.encoded_data_length || 0), 0),
      resource_timing: resourceTiming.map((row) => ({ ...row, name: describedUrl(row.name) })),
      resource_timing_transfer_bytes: resourceTiming.reduce((total, row) => total + (row.transfer_size || 0), 0),
      lazy_bundle_members_requested: lazyRequests
    },
    error
  };
  await context.close();
  return observation;
}

async function incompleteReview(reviewPath, candidate, incompleteItems) {
  const expected = new Map(incompleteItems.map((item) => [item.fingerprint, item]));
  if (!reviewPath) {
    return {
      supplied: false,
      review_sha256: null,
      items: [...expected.values()].map((item) => ({ ...item, disposition: 'unexamined' })),
      unexamined: expected.size,
      blockers: 0
    };
  }
  const { bytes, value } = await readJson(reviewPath, 'axe incomplete review');
  invariant(value.schema === 'okf-hmlr-axe-incomplete-review.v1', 'axe incomplete review schema is not supported');
  invariant(value.candidate_commit_sha === candidate.candidate_commit_sha, 'axe incomplete review candidate commit differs');
  invariant(value.release_root_sha256 === candidate.release_root_sha256, 'axe incomplete review release root differs');
  invariant(Array.isArray(value.items), 'axe incomplete review items must be an array');
  const seen = new Set();
  const rows = [];
  for (const [index, row] of value.items.entries()) {
    jsonObject(row, `axe incomplete review item ${index}`);
    invariant(SHA256_PATTERN.test(row.fingerprint), `axe incomplete review item ${index} has an invalid fingerprint`);
    invariant(expected.has(row.fingerprint), `axe incomplete review contains an unknown fingerprint: ${row.fingerprint}`);
    invariant(!seen.has(row.fingerprint), `axe incomplete review repeats fingerprint: ${row.fingerprint}`);
    invariant(['examined-no-runner-blocker', 'runner-blocker'].includes(row.disposition), `axe incomplete review item ${row.fingerprint} has an invalid disposition`);
    invariant(typeof row.rationale === 'string' && row.rationale.trim().length >= 20, `axe incomplete review item ${row.fingerprint} needs a substantive rationale`);
    invariant(typeof row.method === 'string' && row.method.trim().length >= 10, `axe incomplete review item ${row.fingerprint} needs an examination method`);
    invariant(typeof row.examined_by === 'string' && row.examined_by.trim().length >= 2, `axe incomplete review item ${row.fingerprint} needs examined_by`);
    seen.add(row.fingerprint);
    rows.push({
      ...expected.get(row.fingerprint),
      disposition: row.disposition,
      rationale: row.rationale,
      method: row.method,
      examined_by: row.examined_by
    });
  }
  const missing = [...expected.keys()].filter((fingerprint) => !seen.has(fingerprint));
  for (const fingerprint of missing) rows.push({ ...expected.get(fingerprint), disposition: 'unexamined' });
  return {
    supplied: true,
    review_sha256: sha256(bytes),
    items: rows.sort((left, right) => compareCodePoints(left.fingerprint, right.fingerprint)),
    unexamined: missing.length,
    blockers: rows.filter((row) => row.disposition === 'runner-blocker').length
  };
}

function collectFindings(observations, coverage, staticScan, bundleMeasurements, review) {
  const findings = [...staticScan.findings, ...bundleMeasurements.limit_findings];
  if (coverage.missing.length) findings.push({ code: 'COVERAGE-MISSING', severity: 'blocker', observations: coverage.missing });
  for (const observation of observations) {
    if (observation.error) findings.push({ code: 'OBSERVATION-ERROR', severity: 'blocker', observation: observation.id, detail: observation.error });
    if (observation.http_status !== 200) findings.push({ code: 'ROUTE-HTTP-STATUS', severity: 'blocker', observation: observation.id, measured: observation.http_status, expected: 200 });
    const structure = observation.structure;
    if (structure) {
      if (structure.document_language !== 'en-GB') findings.push({ code: 'DOCUMENT-LANGUAGE', severity: 'blocker', observation: observation.id, measured: structure.document_language, expected: 'en-GB' });
      if (structure.h1_count !== 1 || structure.landmarks.main.length !== 1 || !structure.title.trim()) findings.push({ code: 'SEMANTIC-STRUCTURE', severity: 'blocker', observation: observation.id, h1_count: structure.h1_count, main_landmarks: structure.landmarks.main.length, title_present: Boolean(structure.title.trim()) });
      if (structure.headings.some((heading, index) => index > 0 && heading.level > structure.headings[index - 1].level + 1)) findings.push({ code: 'HEADING-LEVEL-SKIP', severity: 'blocker', observation: observation.id });
      if (structure.viewport.horizontal_overflow) findings.push({ code: 'HORIZONTAL-OVERFLOW', severity: 'blocker', observation: observation.id, viewport: structure.viewport });
      if (observation.profile.forced_colours === 'active' && !structure.preferences.forced_colours_active) findings.push({ code: 'FORCED-COLOURS-COVERAGE', severity: 'blocker', observation: observation.id });
      if (observation.profile.reduced_motion === 'reduce' && !structure.preferences.reduced_motion) findings.push({ code: 'REDUCED-MOTION-COVERAGE', severity: 'blocker', observation: observation.id });
    }
    if (observation.keyboard) {
      const keyboard = observation.keyboard;
      if (keyboard.skip_link.applicable && (!keyboard.first_tab?.visible || !keyboard.first_tab?.focus_indicator || keyboard.first_tab?.href !== '#main' || !keyboard.skip_link.activated || !keyboard.skip_link.target_focused)) findings.push({ code: 'SKIP-LINK-KEYBOARD', severity: 'blocker', observation: observation.id, first_tab: keyboard.first_tab, skip_link: keyboard.skip_link });
      if (keyboard.focusable_elements > 0 && keyboard.unique_traversed_elements < Math.min(4, keyboard.focusable_elements)) findings.push({ code: 'KEYBOARD-TRAVERSAL', severity: 'blocker', observation: observation.id, focusable: keyboard.focusable_elements, unique_traversed: keyboard.unique_traversed_elements });
      if (keyboard.static_catalogue.applicable && (!keyboard.static_catalogue.navigated || keyboard.static_catalogue.result_links < 1)) findings.push({ code: 'STATIC-CATALOGUE-KEYBOARD', severity: 'blocker', observation: observation.id, detail: keyboard.static_catalogue });
      if (observation.security.expected_external_navigation_attempts.length !== 1) findings.push({ code: 'OFFICIAL-SOURCE-KEYBOARD', severity: 'blocker', observation: observation.id, attempts: observation.security.expected_external_navigation_attempts.length });
    }
    const severeAxe = observation.axe?.violations.filter((rule) => ['critical', 'serious'].includes(rule.impact)) || [];
    if (severeAxe.length) findings.push({ code: 'AXE-CRITICAL-OR-SERIOUS', severity: 'blocker', observation: observation.id, rules: severeAxe.map((rule) => rule.id) });
    const security = observation.security;
    if (security.console.some((row) => row.type === 'error') || security.page_errors.length || security.failed_requests.length || security.error_responses.length) findings.push({ code: 'BROWSER-OR-REQUEST-FAILURE', severity: 'blocker', observation: observation.id, console_errors: security.console.filter((row) => row.type === 'error').length, page_errors: security.page_errors.length, failed_requests: security.failed_requests.length, error_responses: security.error_responses.length });
    if (security.unexpected_external_requests.length) findings.push({ code: 'UNEXPECTED-EXTERNAL-RUNTIME-REQUEST', severity: 'blocker', observation: observation.id, requests: security.unexpected_external_requests });
    if (security.csp_violations.length) findings.push({ code: 'CSP-VIOLATION', severity: 'blocker', observation: observation.id, violations: security.csp_violations });
    if (security.storage && Object.entries(security.storage).some(([key, value]) => key !== 'cookie_identity_sha256' && value !== 0)) findings.push({ code: 'BROWSER-STORAGE', severity: 'blocker', observation: observation.id, storage: security.storage });
    for (const row of observation.network.cdp_requests) {
      const queryReason = row.url.query_names.map(normaliseQueryKey).find((key) => SENSITIVE_QUERY_KEYS.has(key));
      if (queryReason) findings.push({ code: 'CREDENTIAL-OR-SIGNED-RUNTIME-URL', severity: 'blocker', observation: observation.id, reason: queryReason, url: row.url });
    }
  }
  if (review.unexamined) findings.push({ code: 'AXE-INCOMPLETE-UNEXAMINED', severity: 'blocker', count: review.unexamined });
  if (review.blockers) findings.push({ code: 'AXE-INCOMPLETE-REVIEW-BLOCKER', severity: 'blocker', count: review.blockers });
  return findings;
}

async function writeEvidence(output, evidence) {
  await mkdir(path.dirname(output), { recursive: true });
  const parent = await realpath(path.dirname(output));
  const target = path.join(parent, path.basename(output));
  invariant(target === output, 'evidence output parent resolves through a symbolic link or alias');
  await writeFile(target, canonicalJson(evidence), { encoding: 'utf8', mode: 0o644, flag: 'wx' });
}

function claimBoundary() {
  return {
    evidence_kind: 'automated-authored-site-candidate-observations',
    land_registry_g6_decision: 'not-made-by-runner',
    human_accessibility_audit: 'not-performed-by-runner',
    representative_user_or_assistive_technology_testing: 'not-performed-by-runner',
    wcag_conformance: 'not-claimed',
    separately_required_receipts: [
      'pinned Explorer v0.6.1 product journeys',
      'pinned Explorer v0.6.1 search-calibration journeys',
      'independent decision for all four Land Registry G6 checks'
    ]
  };
}

async function fullRun(options, preflight) {
  const { internal } = preflight;
  const startedAt = new Date().toISOString();
  const staticScan = await staticSecurityScan(
    internal.bundleRoot,
    internal.checksums.rows,
    internal.lock.limits?.maximum_json_bytes
  );
  let bundleMeasurements;
  try {
    bundleMeasurements = await measureBundle(
      internal.bundleRoot,
      internal.checksums.rows,
      internal.lock,
      internal.richRuntimeValidation,
      internal.advertisedRichRuntime
    );
  } catch (error) {
    bundleMeasurements = {
      total_bundle: { files: internal.checksums.file_count, raw_bytes: internal.checksums.raw_bytes },
      authored_shell: null,
      explorer_lazy_chunks: null,
      relationship_runtime: null,
      limit_findings: [{ code: 'BUNDLE-MEASUREMENT-ERROR', severity: 'blocker', detail: boundedTextEvidence(error.message) }]
    };
  }
  const executionUnsafe = staticScan.findings.some((finding) => finding.severity === 'blocker');
  const expectedCoverage = REQUIRED_ROUTES.flatMap((route) => browserProfiles().map((profile) => `${route}|${profile.id}`));
  const observations = [];
  const runnerErrors = [];
  let browserEnvironment = null;
  const requestLog = [];
  if (!executionUnsafe) {
    const playwrightModule = await import(pathToFileURL(internal.toolchain.playwright.entry).href);
    const axeModule = await import(pathToFileURL(internal.toolchain.axe.entry).href);
    const chromium = playwrightModule.default?.chromium || playwrightModule['module.exports']?.chromium || playwrightModule.chromium;
    const AxeBuilder = axeModule.AxeBuilder || axeModule.default?.AxeBuilder || axeModule['module.exports']?.AxeBuilder;
    invariant(chromium && typeof chromium.launch === 'function', 'could not load Chromium from the pinned Playwright package');
    invariant(typeof AxeBuilder === 'function', 'could not load AxeBuilder from the pinned axe package');
    const staticSite = staticServer(internal.bundleRoot, requestLog);
    await new Promise((resolve, reject) => {
      staticSite.server.once('error', reject);
      staticSite.server.listen(0, '127.0.0.1', resolve);
    });
    const address = staticSite.server.address();
    invariant(address && typeof address === 'object' && address.address === '127.0.0.1', 'authored-site server did not bind loopback');
    const baseUrl = `http://127.0.0.1:${address.port}`;
    staticSite.setOrigin(baseUrl);
    let browser;
    try {
      browser = await chromium.launch({ headless: !options.headed });
      browserEnvironment = {
        browser: 'Chromium',
        browser_version: browser.version(),
        headless: !options.headed,
        served_origin: baseUrl,
        operating_system: { platform: platform(), release: release(), architecture: arch() },
        locale: 'en-GB'
      };
      for (const route of REQUIRED_ROUTES) {
        for (const profile of browserProfiles()) {
          try {
            observations.push(await runObservation({ browser, AxeBuilder, profile, route, baseUrl, requestLog }));
          } catch (error) {
            runnerErrors.push({ observation: `${route}|${profile.id}`, message: boundedTextEvidence(error.message), stack_sha256: error.stack ? sha256(Buffer.from(error.stack, 'utf8')) : null });
          }
        }
      }
    } catch (error) {
      runnerErrors.push({ observation: null, message: boundedTextEvidence(error.message), stack_sha256: error.stack ? sha256(Buffer.from(error.stack, 'utf8')) : null });
    } finally {
      if (browser) await browser.close();
      await new Promise((resolve, reject) => staticSite.server.close((error) => error ? reject(error) : resolve()));
    }
  }
  const observedCoverage = observations.map((row) => `${row.route}|${row.profile.id}`);
  const missing = expectedCoverage.filter((value) => !observedCoverage.includes(value));
  const coverage = {
    required_routes: REQUIRED_ROUTES,
    required_profiles: browserProfiles().map((profile) => ({
      id: profile.id,
      viewport: profile.viewport,
      javascript: profile.javaScriptEnabled ? 'enabled' : 'disabled',
      axe: profile.axe,
      keyboard: profile.keyboard,
      zoom_percent: profile.zoom_percent,
      forced_colours: profile.forcedColors || 'none',
      reduced_motion: profile.reducedMotion || 'no-preference'
    })),
    expected_observations: expectedCoverage.length,
    recorded_observations: observations.length,
    missing
  };
  const incompleteItems = observations.flatMap((observation) => observation.axe?.incomplete_fingerprints || []);
  let review;
  try {
    review = await incompleteReview(options.incompleteReview, preflight.evidence.candidate, incompleteItems);
  } catch (error) {
    review = {
      supplied: Boolean(options.incompleteReview),
      review_sha256: null,
      items: incompleteItems.map((item) => ({ ...item, disposition: 'unexamined' })),
      unexamined: incompleteItems.length,
      blockers: 0,
      error: boundedTextEvidence(error.message)
    };
    runnerErrors.push({ observation: 'axe-incomplete-review', message: boundedTextEvidence(error.message), stack_sha256: error.stack ? sha256(Buffer.from(error.stack, 'utf8')) : null });
  }
  const findings = collectFindings(observations, coverage, staticScan, bundleMeasurements, review);
  for (const error of runnerErrors) findings.push({ code: 'RUNNER-ERROR', severity: 'blocker', ...error });
  const postChecksums = await verifyBundleChecksums(internal.bundleRoot, preflight.evidence.candidate.release_root_sha256);
  invariant(postChecksums.checksums_sha256 === preflight.evidence.candidate.checksums_sha256, 'bundle checksum manifest changed during browser observation');
  const postCandidateGit = await verifyGitIdentity(internal.repositoryRoot, preflight.evidence.candidate.candidate_commit_sha, 'candidate repository after observation');
  const postExplorerGit = await verifyGitIdentity(internal.explorerRoot, preflight.evidence.consumer.source_commit, 'Explorer checkout after observation');
  const blockerCount = findings.filter((finding) => finding.severity === 'blocker').length;
  return {
    schema: 'okf-hmlr-authored-site-browser-quality-observations.v1',
    claim_boundary: claimBoundary(),
    candidate: preflight.evidence.candidate,
    consumer: preflight.evidence.consumer,
    toolchain: preflight.evidence.toolchain,
    execution: {
      started_at: startedAt,
      completed_at: new Date().toISOString(),
      browser_environment: browserEnvironment,
      candidate_unchanged: !postCandidateGit.source_dirty && postChecksums.release_root_sha256 === preflight.evidence.candidate.release_root_sha256,
      consumer_unchanged: !postExplorerGit.source_dirty,
      static_execution_withheld: executionUnsafe
    },
    coverage,
    static_security: staticScan,
    measurements: bundleMeasurements,
    observations,
    axe_incomplete_review: review,
    findings,
    review_state: {
      automated_journeys: 'not-decided-by-runner',
      manual_accessibility_journeys: 'not-decided-by-runner',
      security_critical_zero: 'not-decided-by-runner',
      performance_budgets: 'not-decided-by-runner',
      independent_g6_review: 'not-run'
    },
    limitations: [
      'This is automated candidate evidence, not a Land Registry G6 decision.',
      'The runner does not perform a human accessibility audit, representative-user study or assistive-technology assessment.',
      'No WCAG conformance claim follows from axe or the automated keyboard, reflow, preference and zoom-proxy observations.',
      'The 200% and 400% observations use equivalent effective CSS widths; manual browser-control zoom remains for independent review.',
      'Official-source keyboard activation is intercepted inside Playwright; the runner records navigation intent without contacting the external service.',
      'Explorer v0.6.1 product and search-calibration receipts remain separately required and are not executed or inferred here.'
    ],
    terminal: {
      outcome: blockerCount ? 'runner-failed-closed' : 'automated-observations-recorded-without-g6-decision',
      blocker_findings: blockerCount,
      exit_code: blockerCount ? 1 : 0
    }
  };
}

async function main(argv = process.argv.slice(2)) {
  let options;
  try {
    options = parseArguments(argv);
    if (options.help) {
      process.stdout.write(HELP);
      return 0;
    }
    const preflight = await performPreflight(options);
    if (options.preflightOnly) {
      const evidence = {
        schema: 'okf-hmlr-authored-site-browser-quality-preflight.v1',
        claim_boundary: claimBoundary(),
        ...preflight.evidence,
        terminal: {
          outcome: 'identity-and-checksum-preflight-recorded',
          browser_observations: 'not-run',
          g6_decision: 'not-made',
          exit_code: 0
        }
      };
      await writeEvidence(options.output, evidence);
      process.stdout.write(canonicalJson({ output: options.output, terminal: evidence.terminal }));
      return 0;
    }
    const evidence = await fullRun(options, preflight);
    await writeEvidence(options.output, evidence);
    process.stdout.write(canonicalJson({ output: options.output, terminal: evidence.terminal }));
    return evidence.terminal.exit_code;
  } catch (error) {
    const terminal = {
      outcome: 'runner-failed-closed-before-evidence',
      error: {
        ...boundedTextEvidence(error instanceof Error ? error.message : String(error)),
        summary: safeDiagnostic(error instanceof Error ? error.message : String(error))
      },
      exit_code: 1
    };
    process.stderr.write(canonicalJson(terminal));
    return 1;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === RUNNER_PATH) {
  process.exitCode = await main();
}

export {
  axeNode,
  boundedGunzip,
  canonicalJson,
  cspBaseline,
  cspDirectives,
  credentialUrlFinding,
  htmlAttributes,
  main,
  measureRichRelationshipRuntime,
  normaliseQueryKey,
  parseArguments,
  projectRichRelationshipRow,
  reconcileObservedRuntimeMaxima,
  resolveAdvertisedRichRelationshipRuntime,
  safeRelativePath,
  safeRelativeResourcePath,
  validateRichRuntimeBuildReceipt,
  verifyBundleChecksums
};
