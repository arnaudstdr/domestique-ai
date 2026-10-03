import ReactMarkdown from "react-markdown";
import { Link } from "react-router-dom";
import remarkGfm from "remark-gfm";
import { LEGAL_DOCS, LEGAL_UPDATED_AT, LEGAL_VERSION, type LegalDoc } from "../legal";
import { usePageMeta } from "../hooks/usePageMeta";
import LegalLinks from "./LegalLinks";

/** Page publique de document légal (rendu Markdown + chrome commun). */
export default function LegalLayout({ doc }: { doc: LegalDoc }) {
  usePageMeta({
    title: `${doc.title} — DomestiqueAI`,
    description: doc.description,
  });

  return (
    <div className="min-h-screen bg-surface text-fg">
      <div className="mx-auto max-w-3xl px-4 py-8">
        <Link
          to="/login"
          className="text-xs text-muted transition-colors hover:text-accent"
        >
          ← Retour à l'application
        </Link>
        <h1 className="mt-4 font-display text-2xl font-extrabold tracking-tight">
          {doc.title}
        </h1>
        <p className="mt-1 text-xs text-muted">
          Version {LEGAL_VERSION} — mise à jour le {LEGAL_UPDATED_AT}
        </p>

        <article className="legal-prose mt-6">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{doc.content}</ReactMarkdown>
        </article>

        <div className="mt-10 border-t border-border/[0.06] pt-6">
          <nav className="flex flex-wrap gap-3 text-xs text-muted" aria-label="Autres documents">
            {Object.values(LEGAL_DOCS)
              .filter((other) => other.key !== doc.key)
              .map((other) => (
                <Link
                  key={other.key}
                  to={other.path}
                  className="transition-colors hover:text-accent"
                >
                  {other.title}
                </Link>
              ))}
          </nav>
          <LegalLinks className="mt-4 justify-start" />
        </div>
      </div>
    </div>
  );
}
