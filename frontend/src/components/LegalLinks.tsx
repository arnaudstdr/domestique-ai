import { Link } from "react-router-dom";

/** Liens vers les pages légales (pages publiques, pied du profil). */
export default function LegalLinks({ className }: { className?: string }) {
  return (
    <nav
      aria-label="Informations légales"
      className={`flex flex-wrap items-center justify-center gap-x-3 gap-y-1 text-[11px] text-muted ${className ?? ""}`}
    >
      <Link to="/mentions-legales" className="transition-colors hover:text-accent">
        Mentions légales
      </Link>
      <span aria-hidden="true">·</span>
      <Link to="/cgu" className="transition-colors hover:text-accent">
        CGU
      </Link>
      <span aria-hidden="true">·</span>
      <Link to="/confidentialite" className="transition-colors hover:text-accent">
        Confidentialité
      </Link>
    </nav>
  );
}
