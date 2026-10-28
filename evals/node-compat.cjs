/**
 * Filet de sécurité pour promptfoo 0.124 (local + CI).
 *
 * Node 26 **conserve** le promisify custom de `child_process.execFile`
 * (vérifié en 26.12.1 ; cf. `lib/child_process.js` en v26.0.0) :
 * `util.promisify(execFile)` résout bien `{stdout, stderr}` et ce shim est
 * sans effet sur un runtime stock.
 *
 * Le symptôme « Python 3 not found » (promptfoo valide l'interpréteur via
 * `result.stdout.trim()`, donc sur `undefined`) apparaît quand un hook
 * `NODE_OPTIONS` (proxy, instrumentation) remplace `child_process.execFile`
 * sans reporter `promisify.custom`. Ce shim réinstalle le contrat attendu ;
 * il est chargé par `--require`, donc avant les `--import`, et ne protège que
 * des hooks installés après lui.
 *
 * Chargement (cf. scripts npm) :
 *   NODE_OPTIONS="--require ./node-compat.cjs" promptfoo eval ...
 */
"use strict";

const childProcess = require("node:child_process");
const util = require("node:util");

// Node ≤ 25 expose déjà le promisify custom attendu : ne rien patcher.
const nodeMajor = Number.parseInt(process.versions.node.split(".")[0], 10);
if (nodeMajor >= 26) {
  const originalExecFile = childProcess.execFile;

  const execFileAsync = (file, args, options) =>
    new Promise((resolve, reject) => {
      originalExecFile(file, args, options, (error, stdout, stderr) => {
        if (error) {
          error.stdout = stdout;
          error.stderr = stderr;
          reject(error);
          return;
        }
        resolve({ stdout, stderr });
      });
    });

  const patchedExecFile = (...args) => originalExecFile.apply(this, args);
  patchedExecFile[util.promisify.custom] = execFileAsync;

  Object.defineProperty(childProcess, "execFile", {
    value: patchedExecFile,
    writable: true,
    configurable: true,
    enumerable: true,
  });
}
