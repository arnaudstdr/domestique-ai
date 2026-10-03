import { Link } from "react-router-dom";
import LegalLinks from "../components/LegalLinks";
import { usePageMeta } from "../hooks/usePageMeta";

/** Page 404 de l'application (route inconnue d'un utilisateur connecté). */
export default function NotFound() {
  usePageMeta({ title: "Page introuvable — DomestiqueAI", robots: "noindex" });

  return (
    <div className="card mx-auto max-w-md space-y-3 p-6 text-center">
      <p className="label-eyebrow">Erreur 404</p>
      <h1 className="font-display text-xl font-extrabold tracking-tight">
        Page introuvable
      </h1>
      <p className="text-sm text-fg-soft">
        Cette page n'existe pas ou a été déplacée.
      </p>
      <Link to="/" className="btn-primary inline-flex">
        Revenir au tableau de bord
      </Link>
      <LegalLinks className="pt-2" />
    </div>
  );
}
