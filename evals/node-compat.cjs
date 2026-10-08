/**
 * Compat Node ≥ 26 pour promptfoo 0.124 (local + CI future-proof).
 *
 * Node 26 a retiré le promisify custom de `child_process.execFile` :
 * `util.promisify(execFile)` résout stdout (string) au lieu de
 * `{stdout, stderr}`, et promptfoo lève « Python 3 not found » en validant
 * l'interpréteur (`result.stdout.trim()` sur `undefined`). Ce shim réinstalle
 * le contrat documenté de Node ≤ 24, sans effet fonctionnel sur ces versions.
 *
 * Chargement (cf. scripts npm) :
 *   NODE_OPTIONS="--require ./node-compat.cjs" promptfoo eval ...
 */
"use strict";

const childProcess = require("node:child_process");
const util = require("node:util");

function execFileAsync(file, args, options) {
  return new Promise((resolve, reject) => {
    childProcess.execFile(file, args, options, (error, stdout, stderr) => {
      if (error) {
        error.stdout = stdout;
        error.stderr = stderr;
        reject(error);
        return;
      }
      resolve({ stdout, stderr });
    });
  });
}

const originalExecFile = childProcess.execFile;
function patchedExecFile(...args) {
  return originalExecFile.apply(this, args);
}
patchedExecFile[util.promisify.custom] = execFileAsync;

Object.defineProperty(childProcess, "execFile", {
  value: patchedExecFile,
  writable: true,
  configurable: true,
  enumerable: true,
});
