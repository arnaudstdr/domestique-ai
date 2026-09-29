// Doc-guardian OpenCode plugin (V2) — rappelle de mettre à jour le guide AGENTS.md
// du dossier quand un fichier de ce dossier est modifié.
//
// Pourquoi : la doc de référence du repo est éclatée en plusieurs `AGENTS.md`
// (un par sous-paquet). Un `AGENTS.md` imbriqué n'est chargé que quand l'agent
// explore le dossier correspondant, et rien ne garantit qu'il pense à le mettre
// à jour après un changement de code.
//
// Principe : après une écriture (`write` / `edit` / `patch`) sur un fichier du
// repo, on injecte un rappel d'une ligne dans le résultat du tool si un
// `AGENTS.md` existe dans le dossier (ou un dossier parent) du fichier modifié,
// et si ce guide n'a pas déjà été touché dans la session.
//
// Générique : aucun chemin codé en dur — tout dossier possédant un `AGENTS.md`
// est surveillé, y compris `frontend/` ou un futur `packages/*`.

import { existsSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";

/** Tools qui modifient un fichier, avec la clé d'input portant le chemin. */
const WRITE_TOOLS = {
  write: "filePath",
  edit: "filePath",
  patch: "filePath",
  multi_edit: "filePath",
};

const GUIDE = "AGENTS.md";

/** Remonte depuis `startDir` jusqu'à (et y compris) `root`, cherche le guide le plus proche. */
function findNearestGuide(startDir, root) {
  let dir = resolve(startDir);
  const stop = resolve(root);
  // Borné : on ne remonte jamais au-dessus de la racine du repo.
  for (let i = 0; i < 64; i += 1) {
    const candidate = join(dir, GUIDE);
    if (existsSync(candidate)) return candidate;
    if (dir === stop) return undefined;
    const parent = dirname(dir);
    if (parent === dir) return undefined;
    // Sécurité : si `stop` n'est pas un ancêtre de `dir`, on s'arrête dès la sortie.
    if (!(dir === stop || dir.startsWith(stop + sep))) return undefined;
    dir = parent;
  }
  return undefined;
}

/** Ajoute `line` à la fin du texte du premier contenu texte du résultat. */
function appendNotice(result, line) {
  if (!result || typeof result !== "object") return;
  const content = Array.isArray(result.content) ? result.content : undefined;
  if (!content) return;
  for (let i = content.length - 1; i >= 0; i -= 1) {
    const part = content[i];
    if (part && part.type === "text" && typeof part.text === "string") {
      part.text = `${part.text}\n\n${line}`;
      return;
    }
  }
  content.push({ type: "text", text: line });
}

export default {
  id: "agents-guide",
  async setup(ctx) {
    const root = resolve(ctx.location?.directory ?? process.cwd());

    // Guides déjà signalés (ou déjà édités) pendant cette session de plugin.
    const already = new Set();

    await ctx.tool.hook("execute.after", (event) => {
      if (event.status !== "completed") return;

      const key = WRITE_TOOLS[event.tool];
      if (!key) return;

      const input = event.input;
      if (!input || typeof input !== "object") return;

      const filePath = input[key];
      if (typeof filePath !== "string" || !filePath) return;

      const absolute = resolve(root, filePath);

      // Éditer un guide lui-même : rien à rappeler.
      if (absolute.endsWith(sep + GUIDE)) return;

      const guide = findNearestGuide(dirname(absolute), root);
      if (!guide) return;

      const rel = relative(root, guide);
      if (already.has(rel)) return;
      already.add(rel);

      appendNotice(
        event.result,
        `[agents-guide] Ce changement touche « ${relative(root, dirname(absolute)) || "."} ». ` +
          `Pense à mettre à jour \`${rel}\` dans le même commit s'il décrit ` +
          `le comportement, un endpoint, un schéma DB ou une convention modifiés.`,
      );
    });
  },
};
