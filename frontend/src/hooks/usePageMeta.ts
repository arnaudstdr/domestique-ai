import { useEffect } from "react";

interface PageMeta {
  title: string;
  description?: string;
  /** Contenu du meta robots (ex. `noindex`). Absent → retire la balise. */
  robots?: string | null;
}

function upsertMeta(name: string, content: string | null): void {
  const existing = document.querySelector(`meta[name="${name}"]`);
  if (content === null) {
    existing?.remove();
    return;
  }
  const meta = existing ?? document.createElement("meta");
  if (!existing) {
    meta.setAttribute("name", name);
    document.head.appendChild(meta);
  }
  meta.setAttribute("content", content);
}

/**
 * Titre et métadonnées de la page courante (SPA). `robots` permet de poser un
 * `noindex` sur les pages d'auth et applicatives ; les crawlers qui exécutent
 * le JS le respectent, et robots.txt couvre les autres.
 */
export function usePageMeta({ title, description, robots }: PageMeta): void {
  useEffect(() => {
    const previousTitle = document.title;
    const previousRobots = document
      .querySelector('meta[name="robots"]')
      ?.getAttribute("content");
    document.title = title;
    if (description !== undefined) upsertMeta("description", description);
    upsertMeta("robots", robots ?? null);
    // Restaure le titre à la navigation suivante (SPA) : les pages qui ne
    // posent pas de meta gardent le titre par défaut d'index.html.
    return () => {
      document.title = previousTitle;
      upsertMeta("robots", previousRobots ?? null);
    };
  }, [title, description, robots]);
}
